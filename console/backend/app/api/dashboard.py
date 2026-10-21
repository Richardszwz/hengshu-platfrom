"""仪表盘聚合 API：KPI + 事件流 + 采样时序（真实数据，无 mock 曲线）。

series 端点自采样设计：每次轮询把当前集群快照差分/追加进 Redis 环形缓冲，
前端拿到的 60s 曲线全部来自运行中的 Router/Agent 真实计数。
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends

from .. import deps
from ..keys import (
    EVENTS_KEY,
    INST_PREFIX,
    NODE_PREFIX,
    SER_QPS_KEY,
    SER_TPS_KEY,
    SER_TTFT_KEY,
    SER_UTIL_PREFIX,
    SESS_PREFIX,
)
from ..security import current_user

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"],
                   dependencies=[Depends(current_user)])
_SERIES_CAP = 60


async def _hgetall_many(r, keys: list[str]) -> list[dict]:
    """批量 hgetall（pipeline），避免 N 次往返。"""
    if not keys:
        return []
    pipe = r.pipeline()
    for k in keys:
        pipe.hgetall(k)
    return [h for h in await pipe.execute() if h]


async def _sess_by_model(r) -> dict[str, int]:
    """扫描会话 key 按模型聚合（P2 由租户/模型维度直接统计）。"""
    keys = await r.keys(f"{SESS_PREFIX}*")
    if not keys:
        return {}
    pipe = r.pipeline()
    for k in keys:
        pipe.hget(k, "model")
    models = await pipe.execute()
    out: dict[str, int] = {}
    for m in models:
        if m:
            out[m] = out.get(m, 0) + 1
    return out


@router.get("/summary")
async def summary(r=Depends(deps.get)) -> dict:
    """集群级 KPI：节点/实例/会话数 + 加权 GPU/VRAM + 事件流。"""
    node_keys = await r.keys(f"{NODE_PREFIX}*")
    now = time.time()
    nodes_online = 0
    agent_addrs: dict[str, str] = {}
    node_gpus: dict[str, str] = {}
    for k in node_keys:
        nid = k.removeprefix(NODE_PREFIX)
        if await r.get(f"hs:node_del:{nid}"):  # 墓碑节点不计入（已删除，旧 Agent 可能仍在写）
            continue
        h = await r.hgetall(k)
        if h and int(h.get("last_seen", "0")) > now - 15:
            nodes_online += 1
        if h:
            agent_addrs[nid] = h.get("agent_addr", f"http://{nid}:9100")
            node_gpus[nid] = h.get("gpu_name", "")

    inst_keys = await r.keys(f"{INST_PREFIX}*")
    insts = await _hgetall_many(r, inst_keys)
    running = [h for h in insts if h.get("status") == "running"]

    def wavg(field: str) -> float:
        vals = [float(h[field]) for h in running if field in h]
        return round(sum(vals) / len(vals), 1) if vals else 0.0

    sess_keys = await r.keys(f"{SESS_PREFIX}*")

    events_raw = await r.lrange(EVENTS_KEY, -30, -1)
    events = []
    for e in events_raw:
        try:
            d = json.loads(e)
        except json.JSONDecodeError:
            continue
        ts = d.get("ts") or d.get("time") or ""
        events.append({"level": d.get("level", "INFO"),
                       "time": time.strftime("%H:%M:%S", time.localtime(ts))
                               if isinstance(ts, (int, float)) else str(ts),
                       "text": d.get("msg") or d.get("text", "")})

    from ..license_guard import current_license_state
    _lic = await current_license_state(r)

    return {
        "nodes_total": len(agent_addrs),  # 墓碑过滤后的真实节点数
        "nodes_online": nodes_online,
        "services_total": len(insts),
        "services_running": len(running),
        "sessions": len(sess_keys),
        "gpu_util_weighted": wavg("m_gpu_util"),
        "vram_weighted": wavg("m_vram"),
        "ttft_avg": wavg("m_ttft"),
        "tokens_per_s_total": round(sum(float(h.get("m_tps", 0)) for h in running), 1),
        "sess_by_model": await _sess_by_model(r),
        "agent_addrs": agent_addrs,
        "node_gpus": node_gpus,
        "events": events,
        "license": {"ok": _lic.ok, "reason": _lic.reason, "customer": _lic.customer,
                    "base_mode": _lic.base_mode, "base_expires": _lic.base_expires,
                    "base_in_grace": _lic.base_in_grace, "edition": _lic.edition},
    }


@router.get("/series")
async def series(r=Depends(deps.get), points: int = _SERIES_CAP) -> dict:
    """采样时序：QPS(Router 计数差分) / TTFT / TPS / 单节点 GPU util。

    每次调用把当前快照追加进环形缓冲（容量 60），前端每 3s 轮询即得滑动窗口。
    """
    now = time.time()

    # --- QPS：请求时间戳滑动窗口（近 10s 请求数/10——稀疏流量下也可见）---
    reqts = [float(x) for x in await r.lrange("hs:stat:reqts", -600, -1)]
    qps = round(len([t for t in reqts if t > now - 10]) / 10.0, 2)
    pipe = r.pipeline()

    # --- 集群 TTFT / TPS / 节点 util 快照 ---
    inst_keys = await r.keys(f"{INST_PREFIX}*")
    insts = [h for h in await _hgetall_many(r, inst_keys) if h.get("status") == "running"]
    ttft = round(sum(float(h.get("m_ttft", 0)) for h in insts)
                 / len(insts), 1) if insts else 0.0
    tps = round(sum(float(h.get("m_tps", 0)) for h in insts), 1)

    node_utils: dict[str, float] = {}
    node_keys = await r.keys(f"{NODE_PREFIX}*")
    for nk in node_keys:
        nid = nk.removeprefix(NODE_PREFIX)
        node_insts = [h for h in insts if h.get("node") == nid]
        if node_insts:
            node_utils[nid] = round(sum(float(h.get("m_gpu_util", 0))
                                        for h in node_insts) / len(node_insts), 1)

    pipe.rpush(SER_QPS_KEY, qps)
    pipe.ltrim(SER_QPS_KEY, -points, -1)
    pipe.rpush(SER_TTFT_KEY, ttft)
    pipe.ltrim(SER_TTFT_KEY, -points, -1)
    pipe.rpush(SER_TPS_KEY, tps)
    pipe.ltrim(SER_TPS_KEY, -points, -1)
    for nid, u in node_utils.items():
        pipe.rpush(f"{SER_UTIL_PREFIX}{nid}", u)
        pipe.ltrim(f"{SER_UTIL_PREFIX}{nid}", -points, -1)
    await pipe.execute()

    async def _lrange(key: str) -> list[float]:
        rows = await r.lrange(key, -points, -1)
        return [float(x) for x in rows]

    out = {"qps": await _lrange(SER_QPS_KEY), "ttft": await _lrange(SER_TTFT_KEY),
           "tps": await _lrange(SER_TPS_KEY),
           "node_utils": {nid: await _lrange(f"{SER_UTIL_PREFIX}{nid}")
                          for nid in node_utils}}
    return out
