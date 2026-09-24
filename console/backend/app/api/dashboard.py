"""仪表盘聚合 API：KPI + 事件流（前端 001 驾驶舱页的数据源）。"""
from __future__ import annotations

import json
import math
import time

from fastapi import APIRouter, Depends

from .. import deps
from ..keys import EVENTS_KEY, INST_PREFIX, NODE_PREFIX, SESS_PREFIX
from ..security import current_user

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"],
                   dependencies=[Depends(current_user)])


@router.get("/summary")
async def summary(r=Depends(deps.get)) -> dict:
    """集群级 KPI：节点/实例/会话数 + 加权 GPU/VRAM + 事件流。"""
    node_keys = await r.keys(f"{NODE_PREFIX}*")
    now = time.time()
    nodes_online = 0
    for k in node_keys:
        h = await r.hgetall(k)
        if h and int(h.get("last_seen", "0")) > now - 15:
            nodes_online += 1

    inst_keys = await r.keys(f"{INST_PREFIX}*")
    insts = [await r.hgetall(k) for k in inst_keys]
    insts = [h for h in insts if h]
    running = [h for h in insts if h.get("status") == "running"]

    def wavg(field: str) -> float:
        vals = [float(h[field]) for h in running if field in h]
        return round(sum(vals) / len(vals), 1) if vals else 0.0

    sess_keys = await r.keys(f"{SESS_PREFIX}*")

    events_raw = await r.lrange(EVENTS_KEY, -30, -1)
    events = [json.loads(e) for e in events_raw]

    return {
        "nodes_total": len(node_keys),
        "nodes_online": nodes_online,
        "services_total": len(insts),
        "services_running": len(running),
        "sessions": len(sess_keys),
        "gpu_util_weighted": wavg("m_gpu_util"),
        "vram_weighted": wavg("m_vram"),
        "ttft_avg": wavg("m_ttft"),
        "tokens_per_s_total": round(sum(float(h.get("m_tps", 0)) for h in running), 1),
        "events": events,
    }


@router.get("/qps-series")
async def qps_series(r=Depends(deps.get), points: int = 48) -> dict:
    """QPS 时序骨架：P2 由 Router 打点入库；P0 返回实例数×心跳频率的模拟曲线供前端联调。"""
    inst_keys = await r.keys(f"{INST_PREFIX}*")
    n = len(inst_keys)
    base = n * 3.1
    import math
    series = [round(base + math.sin(i / 3.2) * base * 0.4, 1) for i in range(points)]
    return {"model": "simulated(P2 接 Router 实测)", "series": series}
