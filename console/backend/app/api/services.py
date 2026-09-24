"""模型服务 API：实例列表 + 启停/日志指令下发（Agent 接收端已实现）。"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException

from .. import deps
from ..keys import INST_PREFIX, MODEL_PREFIX, NODE_PREFIX
from ..security import current_user

router = APIRouter(prefix="/api/services", tags=["services"],
                   dependencies=[Depends(current_user)])


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
            services.append({
                "model": model,
                "inst_id": iid,
                "node": h.get("node", ""),
                "port": int(h.get("port", "0")),
                "framework": h.get("framework", ""),
                "status": h.get("status", "unknown"),
                "metrics": {k: float(v) for k, v in h.items() if k.startswith("m_")},
            })
    return {"services": sorted(services, key=lambda x: (x["model"], x["inst_id"]))}


@router.post("/{node_id}/{name}/stop")
async def stop_service(node_id: str, name: str, r=Depends(deps.get)) -> dict:
    node = await r.hgetall(f"{NODE_PREFIX}{node_id}")
    if not node:
        raise HTTPException(404, "node offline")
    async with httpx.AsyncClient(timeout=10.0) as c:
        resp = await c.post(f"http://{node_id}:9100/services/stop/{name}")
    return resp.json()


@router.get("/{node_id}/{name}/logs")
async def service_logs(node_id: str, name: str, tail: int = 100,
                       r=Depends(deps.get)) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as c:
        resp = await c.get(f"http://{node_id}:9100/services/{name}/logs",
                           params={"tail": tail})
    return resp.json()
