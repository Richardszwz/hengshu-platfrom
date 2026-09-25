"""节点 API：列表（读 Redis 心跳）+ SSH 添加骨架（P1 实装推送安装）。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import deps
from ..crypto import decrypt, encrypt
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
    seen: set[str] = set()
    for k in keys:
        h = await r.hgetall(k)
        if not h:
            continue
        iid = k.removeprefix(NODE_PREFIX)
        seen.add(iid)
        insts = await r.keys(f"{INST_PREFIX}{iid}:*")
        nodes.append({
            "node_id": iid,
            "hostname": h.get("hostname", ""),
            "arch": h.get("arch", ""),
            "status": "online" if int(h.get("last_seen", "0")) > time.time() - 15 else "offline",
            "last_seen": int(h.get("last_seen", "0")),
            "instances": len(insts),
        })
    # 已登记但从未心跳的节点（安装中/失败）也可见
    for ck in await r.keys("hs:conn:*"):
        nid = ck.removeprefix("hs:conn:")
        if nid in seen:
            continue
        conn = await r.hgetall(ck)
        nodes.append({
            "node_id": nid,
            "hostname": conn.get("host", ""),
            "arch": "",
            "status": "pending",
            "last_seen": 0,
            "instances": 0,
        })
    return {"nodes": sorted(nodes, key=lambda x: x["node_id"])}


@router.post("")
async def add_node(req: NodeAddReq, r=Depends(deps.get)) -> dict:
    if not req.password and not req.private_key:
        raise HTTPException(400, "password or private_key required")
    blob = encrypt(req.private_key or req.password or "")
    await r.set(f"{CRED_PREFIX}{req.node_id}", blob)
    # 连接元数据（Web 终端/重装复用；无密钥明文）
    await r.hset(f"hs:conn:{req.node_id}", mapping={
        "host": req.hostname, "port": str(req.ssh_port), "username": req.username})
    return {"node_id": req.node_id, "credential": "stored(encrypted)",
            "next": "POST /api/nodes/{node_id}/install 推送安装 Agent"}


@router.post("/{node_id}/install")
async def install_node(node_id: str, req: NodeAddReq, r=Depends(deps.get)) -> dict:
    """存凭证 → SSH 推送安装 Agent 容器 → 心跳自动上线。"""
    from ..config import settings
    from .. import ssh_installer

    if req.node_id != node_id:
        raise HTTPException(400, "node_id mismatch")
    if not req.password and not req.private_key:
        # 重装场景：请求未带凭证时回退使用已存加密凭证
        blob = await r.get(f"{CRED_PREFIX}{node_id}")
        if not blob:
            raise HTTPException(400, "password or private_key required")
        cred = decrypt(blob)
        if "PRIVATE KEY" in cred:
            req = req.model_copy(update={"private_key": cred, "password": None})
        else:
            req = req.model_copy(update={"password": cred})
    blob = encrypt(req.private_key or req.password or "")
    await r.set(f"{CRED_PREFIX}{node_id}", blob)
    # 连接元数据同步刷新（Web 终端/重装复用；无密钥明文）
    await r.hset(f"hs:conn:{node_id}", mapping={
        "host": req.hostname, "port": str(req.ssh_port), "username": req.username})

    redis_advertise = settings.redis_advertise or settings.redis_url
    return await ssh_installer.install(
        r, node_id=node_id, host=req.hostname, ssh_port=req.ssh_port,
        username=req.username, password=req.password,
        private_key=req.private_key,
        agent_image=settings.agent_image,
        redis_advertise=redis_advertise,
        tar_dir=settings.install_tar_dir or None)


@router.delete("/{node_id}")
async def remove_node(node_id: str, r=Depends(deps.get)) -> dict:
    # 心跳仍活跃的节点不可删除——Agent 每周期重写心跳，删了也会"复活"
    h = await r.hgetall(f"{NODE_PREFIX}{node_id}")
    if h and int(h.get("last_seen", "0")) > time.time() - 15:
        raise HTTPException(409, f"节点 {node_id} 在线（心跳活跃），无法删除；"
                                  "如需移除请先停止目标机上的 hengshu-agent 容器")
    await r.delete(f"{NODE_PREFIX}{node_id}", f"{CRED_PREFIX}{node_id}",
                   f"hs:conn:{node_id}")
    # 清理该节点在模型集合里的实例登记（实例 hash 本身随 TTL 过期）
    for mk in await r.keys(f"hs:model:*"):
        for iid in await r.smembers(mk):
            if iid.startswith(f"{node_id}:"):
                await r.srem(mk, iid)
    return {"removed": node_id}
