"""模型服务 API：实例列表 + 启停/日志指令下发（Agent 接收端已实现）。"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import deps
from ..keys import INST_PREFIX, MODEL_PREFIX, NODE_PREFIX
from ..security import current_user

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
    return h.get("agent_addr", f"http://{node_id}:9100")


@router.post("/{node_id}/start")
async def launch_service(node_id: str, req: StartServiceReq, r=Depends(deps.get)) -> dict:
    """启动新服务（完整 StartRequest 下发 Agent），并持久化 spec 供重启复用。"""
    addr = await _agent_addr(node_id, r)
    async with httpx.AsyncClient(timeout=120.0) as c:
        resp = await c.post(f"{addr}/services/start", json=req.model_dump())
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, resp.text[:200])
    import json as _json
    spec = req.model_dump()
    scalar = {k: str(v) for k, v in spec.items() if k not in ("env", "volumes")}
    scalar["env_json"] = _json.dumps(req.env)
    scalar["volumes_json"] = _json.dumps(req.volumes)
    await r.hset(f"{SPEC_PREFIX}{node_id}:{req.name}", mapping=scalar)
    return resp.json()


@router.post("/{node_id}/{name}/stop")
async def stop_service(node_id: str, name: str, r=Depends(deps.get)) -> dict:
    addr = await _agent_addr(node_id, r)
    async with httpx.AsyncClient(timeout=10.0) as c:
        resp = await c.post(f"{addr}/services/stop/{name}")
    return resp.json()


@router.post("/{node_id}/{name}/start")
async def start_service(node_id: str, name: str, r=Depends(deps.get)) -> dict:
    """重启已停服务：优先用持久化 spec；无 spec 走旧版裸指令（兼容 mock）。"""
    spec = await r.hgetall(f"{SPEC_PREFIX}{node_id}:{name}")
    addr = await _agent_addr(node_id, r)
    async with httpx.AsyncClient(timeout=120.0) as c:
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
    async with httpx.AsyncClient(timeout=10.0) as c:
        resp = await c.get(f"{addr}/services/{name}/logs", params={"tail": tail})
    return resp.json()
