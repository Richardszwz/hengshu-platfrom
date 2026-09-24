"""节点 API：列表（读 Redis 心跳）+ SSH 添加骨架（P1 实装推送安装）。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import deps
from ..crypto import encrypt
from ..keys import CRED_PREFIX, INST_PREFIX, NODE_PREFIX
from ..security import current_user

router = APIRouter(prefix="/api/nodes", tags=["nodes"], dependencies=[Depends(current_user)])


class NodeAddReq(BaseModel):
    node_id: str = Field(min_length=1, max_length=64)
    hostname: str
    ssh_port: int = 22
    username: str
    password: str | None = None
    private_key: str | None = None


@router.get("")
async def list_nodes(r=Depends(deps.get)) -> dict:
    keys = await r.keys(f"{NODE_PREFIX}*")
    nodes = []
    for k in keys:
        h = await r.hgetall(k)
        if not h:
            continue
        iid = k.removeprefix(NODE_PREFIX)
        insts = await r.keys(f"{INST_PREFIX}{iid}:*")
        nodes.append({
            "node_id": iid,
            "hostname": h.get("hostname", ""),
            "arch": h.get("arch", ""),
            "status": "online" if int(h.get("last_seen", "0")) > time.time() - 15 else "offline",
            "last_seen": int(h.get("last_seen", "0")),
            "instances": len(insts),
        })
    return {"nodes": sorted(nodes, key=lambda x: x["node_id"])}


@router.post("")
async def add_node(req: NodeAddReq, r=Depends(deps.get)) -> dict:
    if not req.password and not req.private_key:
        raise HTTPException(400, "password or private_key required")
    blob = encrypt(req.private_key or req.password or "")
    await r.set(f"{CRED_PREFIX}{req.node_id}", blob)
    # P1：asyncssh 指纹确认 → 推送安装 Agent（容器/裸机双路径）→ 首次心跳自动上线
    return {"node_id": req.node_id, "credential": "stored(encrypted)",
            "next": "P1: push-install agent via SSH"}


@router.delete("/{node_id}")
async def remove_node(node_id: str, r=Depends(deps.get)) -> dict:
    await r.delete(f"{NODE_PREFIX}{node_id}", f"{CRED_PREFIX}{node_id}")
    return {"removed": node_id}
