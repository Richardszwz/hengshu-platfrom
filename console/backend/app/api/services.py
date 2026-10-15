"""模型服务 API：实例列表 + 启停/日志指令下发（Agent 接收端已实现）。"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import deps
from ..keys import INST_PREFIX, MODEL_PREFIX, NODE_PREFIX
from ..security import current_user, require_role

router = APIRouter(prefix="/api/services", tags=["services"],
                   dependencies=[Depends(current_user)])

SPEC_PREFIX = "hs:svcspec:"


class StartServiceReq(BaseModel):
    name: str = Field(description="服务/模型名，容器名 hs-svc-{name}")
    image: str = "vllm/vllm-openai:latest"
    start_cmd: str
    port: int
    workdir: str = "/workspace"
    env: dict[str, str] = {}
    # 卷挂载：["宿主机路径:容器路径[:ro]"]——模型权重靠它进容器
    volumes: list[str] = []


@router.get("")
async def list_services(r=Depends(deps.get)) -> dict:
    model_keys = await r.keys(f"{MODEL_PREFIX}*")
    services = []
    for mk in model_keys:
        model = mk.removeprefix(MODEL_PREFIX)
        for iid in await r.smembers(mk):
            h = await r.hgetall(f"{INST_PREFIX}{iid}")
            if not h:
                continue
            spec = await r.hgetall(f"{SPEC_PREFIX}{h.get('node', '')}:{model}")
            services.append({
                "model": model,
                "inst_id": iid,
                "node": h.get("node", ""),
                "port": int(h.get("port", "0")),
                "framework": h.get("framework", ""),
                "status": h.get("status", "unknown"),
                "metrics": {k: float(v) for k, v in h.items() if k.startswith("m_")},
                "spec": spec or None,
            })
    return {"services": sorted(services, key=lambda x: (x["model"], x["inst_id"]))}


async def _agent_addr(node_id: str, r) -> str:
    h = await r.hgetall(f"{NODE_PREFIX}{node_id}")
    if not h:
        raise HTTPException(404, "node offline")
    # 优先 hs:conn 的 SSH 主机地址（LAN 可达）；agent_addr 可能是容器网桥 IP
    conn = await r.hgetall(f"hs:conn:{node_id}")
    if conn.get("host"):
        return f"http://{conn['host']}:9100"
    return h.get("agent_addr", f"http://{node_id}:9100")


@router.get("/{node_id}/specs")
async def list_specs(node_id: str, r=Depends(deps.get)) -> dict:
    """该节点全部已存启动模板（对话框按模型名预填用）。"""
    import json as _json
    out = []
    for k in await r.keys(f"{SPEC_PREFIX}{node_id}:*"):
        h = await r.hgetall(k)
        if not h:
            continue
        try:
            env = _json.loads(h.get("env_json", "{}"))
            vols = _json.loads(h.get("volumes_json", "[]"))
        except ValueError:
            env, vols = {}, []
        out.append({"name": h.get("name", ""), "image": h.get("image", ""),
                    "start_cmd": h.get("start_cmd", ""), "port": int(h.get("port", "8100")),
                    "env": env, "volumes": vols})
    return {"specs": sorted(out, key=lambda x: x["name"])}


@router.post("/{node_id}/start", dependencies=[Depends(require_role("operator"))])
async def launch_service(node_id: str, req: StartServiceReq, r=Depends(deps.get)) -> dict:
    """启动新服务（完整 StartRequest 下发 Agent），并持久化 spec 供重启复用。"""
    addr = await _agent_addr(node_id, r)
    try:
        async with httpx.AsyncClient(timeout=120.0, trust_env=False) as c:
            resp = await c.post(f"{addr}/services/start", json=req.model_dump())
    except httpx.HTTPError as e:
        raise HTTPException(502, f"无法连接节点 Agent（{addr}）：{type(e).__name__} {e}") from e
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, resp.text[:200])
    import json as _json
    import time as _time
    from ..keys import EVENTS_KEY
    # 事件流留痕：下发受理（后续进度由 Agent 写：开始拉取/启动完成/失败）
    async with r.pipeline() as p:
        p.rpush(EVENTS_KEY, _json.dumps({
            "ts": round(_time.time(), 3), "level": "INFO",
            "msg": f"svc ▸ 控制台下发启动指令 {node_id}/{req.name}（异步受理）"},
            ensure_ascii=False))
        p.ltrim(EVENTS_KEY, -200, -1)
        await p.execute()
    spec = req.model_dump()
    scalar = {k: str(v) for k, v in spec.items() if k not in ("env", "volumes")}
    scalar["env_json"] = _json.dumps(req.env)
    scalar["volumes_json"] = _json.dumps(req.volumes)
    if req.volumes:
        # 记住该节点的模型卷挂载，下次启动对话框自动预填（防 X86 路径套到 Spark 这类错配）
        await r.hset(f"hs:conn:{node_id}", "models_mount", req.volumes[0])
    await r.hset(f"{SPEC_PREFIX}{node_id}:{req.name}", mapping=scalar)
    return resp.json()


@router.post("/{node_id}/{name}/stop", dependencies=[Depends(require_role("operator"))])
async def stop_service(node_id: str, name: str, r=Depends(deps.get)) -> dict:
    addr = await _agent_addr(node_id, r)
    async with httpx.AsyncClient(timeout=10.0, trust_env=False) as c:
        resp = await c.post(f"{addr}/services/stop/{name}")
    return resp.json()


@router.delete("/{node_id}/{name}", dependencies=[Depends(require_role("operator"))])
async def delete_service(node_id: str, name: str, r=Depends(deps.get)) -> dict:
    """删除服务：尽力停容器 → 清实例登记/模型集合。spec 保留作启动模板（重建时对话框预填用）。"""
    stopped = None
    try:
        addr = await _agent_addr(node_id, r)
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as c:
            # Agent 删除端点会同时移除其本地实例登记（防心跳复活）
            stopped = (await c.post(f"{addr}/services/delete/{name}")).json()
    except Exception as e:  # noqa: BLE001 —— 节点离线/Agent 不可达也要能清登记
        stopped = f"agent 不可达（{type(e).__name__}），仅清登记"
    # 实例 key 直接删（不等 TTL）
    iid_keys = [k for k in await r.keys(f"{INST_PREFIX}{node_id}:*")
                if (await r.hget(k, "model")) == name]
    if iid_keys:
        await r.delete(*iid_keys)
        for k in iid_keys:
            iid = k.removeprefix(INST_PREFIX)
            await r.srem(f"{MODEL_PREFIX}{name}", iid)
    return {"deleted": name, "node": node_id, "agent": stopped}


@router.post("/{node_id}/{name}/start", dependencies=[Depends(require_role("operator"))])
async def start_service(node_id: str, name: str, r=Depends(deps.get)) -> dict:
    """重启已停服务：优先用持久化 spec；无 spec 走旧版裸指令（兼容 mock）。"""
    spec = await r.hgetall(f"{SPEC_PREFIX}{node_id}:{name}")
    addr = await _agent_addr(node_id, r)
    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as c:
        if spec:
            import json as _json
            payload = {
                "name": name,
                "image": spec.get("image", "vllm/vllm-openai:latest"),
                "start_cmd": spec.get("start_cmd", ""),
                "port": int(spec.get("port", "0")),
                "workdir": spec.get("workdir", "/workspace"),
                "env": _json.loads(spec.get("env_json", "{}")),
                "volumes": _json.loads(spec.get("volumes_json", "[]")),
            }
            resp = await c.post(f"{addr}/services/start", json=payload)
        else:
            resp = await c.post(f"{addr}/services/start/{name}")
    return resp.json()


@router.get("/{node_id}/{name}/logs")
async def service_logs(node_id: str, name: str, tail: int = 100,
                       r=Depends(deps.get)) -> dict:
    addr = await _agent_addr(node_id, r)
    async with httpx.AsyncClient(timeout=10.0, trust_env=False) as c:
        resp = await c.get(f"{addr}/services/{name}/logs", params={"tail": tail})
    return resp.json()
