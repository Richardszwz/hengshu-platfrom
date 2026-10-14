"""节点 API：列表（读 Redis 心跳）+ SSH 添加骨架（P1 实装推送安装）。"""
from __future__ import annotations

import json
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import deps, hooks
from ..crypto import decrypt, encrypt
from ..keys import CRED_PREFIX, INST_PREFIX, NODE_PREFIX
from ..security import current_user, require_role

router = APIRouter(prefix="/api/nodes", tags=["nodes"], dependencies=[Depends(current_user)])


class NodeAddReq(BaseModel):
    node_id: str = Field(min_length=1, max_length=64)
    hostname: str = ""            # 重装可留空 → 回退 hs:conn 已存连接信息
    ssh_port: int = 22
    username: str = ""
    password: str | None = None
    private_key: str | None = None


@router.get("")
async def list_nodes(r=Depends(deps.get)) -> dict:
    keys = await r.keys(f"{NODE_PREFIX}*")
    nodes = []
    seen: set[str] = set()
    for k in keys:
        iid = k.removeprefix(NODE_PREFIX)
        # 墓碑节点不展示（已删除；旧版 Agent 可能仍在瞎写心跳）
        if await r.get(f"hs:node_del:{iid}"):
            continue
        h = await r.hgetall(k)
        if not h:
            continue
        seen.add(iid)
        insts = await r.keys(f"{INST_PREFIX}{iid}:*")
        conn = await r.hgetall(f"hs:conn:{iid}")  # SSH 连接信息（改连接对话框预填）
        nodes.append({
            "node_id": iid,
            "hostname": h.get("hostname", ""),
            "ssh_host": conn.get("host", ""),
            "ssh_port": int(conn.get("port", "22")),
            "ssh_user": conn.get("username", ""),
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
        if await r.get(f"hs:node_del:{nid}"):  # 墓碑节点不展示
            continue
        conn = await r.hgetall(ck)
        nodes.append({
            "node_id": nid,
            "hostname": conn.get("host", ""),
            "ssh_host": conn.get("host", ""),
            "ssh_port": int(conn.get("port", "22")),
            "ssh_user": conn.get("username", ""),
            "arch": "",
            "status": "pending",
            "last_seen": 0,
            "instances": 0,
        })
    return {"nodes": sorted(nodes, key=lambda x: x["node_id"])}


class ConnUpdateReq(BaseModel):
    hostname: str = Field(min_length=1)
    ssh_port: int = 22
    username: str = Field(min_length=1)
    password: str | None = None      # 留空 = 不换凭证
    private_key: str | None = None


@router.put("/{node_id}/conn", dependencies=[Depends(require_role("operator"))])
async def update_conn(node_id: str, req: ConnUpdateReq, r=Depends(deps.get)) -> dict:
    """更新已登记节点的 SSH 连接信息（IP/端口/用户）与凭证（可选）。"""
    exists = await r.exists(f"hs:conn:{node_id}") or await r.exists(f"{NODE_PREFIX}{node_id}")
    if not exists:
        raise HTTPException(404, "节点未登记")
    await r.hset(f"hs:conn:{node_id}", mapping={
        "host": req.hostname, "port": str(req.ssh_port), "username": req.username})
    cred_updated = False
    if req.password or req.private_key:
        await r.set(f"{CRED_PREFIX}{node_id}", encrypt(req.private_key or req.password or ""))
        cred_updated = True
    return {"updated": node_id, "host": req.hostname, "credential_updated": cred_updated}


@router.post("", dependencies=[Depends(require_role("operator"))])
async def add_node(req: NodeAddReq, r=Depends(deps.get)) -> dict:
    if not req.password and not req.private_key:
        raise HTTPException(400, "password or private_key required")
    blob = encrypt(req.private_key or req.password or "")
    await r.set(f"{CRED_PREFIX}{req.node_id}", blob)
    await r.delete(f"hs:node_del:{req.node_id}")  # 重新添加 → 清墓碑
    # 连接元数据（Web 终端/重装复用；无密钥明文）
    await r.hset(f"hs:conn:{req.node_id}", mapping={
        "host": req.hostname, "port": str(req.ssh_port), "username": req.username})
    return {"node_id": req.node_id, "credential": "stored(encrypted)",
            "next": "POST /api/nodes/{node_id}/install 推送安装 Agent"}


@router.post("/{node_id}/install", dependencies=[Depends(require_role("operator"))])
async def install_node(node_id: str, req: NodeAddReq, r=Depends(deps.get)) -> dict:
    """存凭证 → SSH 推送安装 Agent 容器 → 心跳自动上线。"""
    from ..config import settings
    from .. import ssh_installer

    if req.node_id != node_id:
        raise HTTPException(400, "node_id mismatch")
    # 重装捷径：请求缺连接信息/凭证时回退已存值（hs:conn / hs:cred）
    conn = await r.hgetall(f"hs:conn:{node_id}")
    if not req.hostname and conn.get("host"):
        req = req.model_copy(update={"hostname": conn["host"],
                                     "ssh_port": int(conn.get("port", "22")),
                                     "username": req.username or conn.get("username", "")})
    if not req.hostname or not req.username:
        raise HTTPException(400, "缺少连接信息且无存档，请完整填写 IP/SSH 用户")
    await r.delete(f"hs:node_del:{node_id}")  # 重装 → 清墓碑
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
    result = await ssh_installer.install(
        r, node_id=node_id, host=req.hostname, ssh_port=req.ssh_port,
        username=req.username, password=req.password,
        private_key=req.private_key,
        agent_image=settings.agent_image,
        redis_advertise=redis_advertise,
        tar_dir=settings.install_tar_dir or None)
    # Webhook 通知（HENGSHU_WEBHOOK_URL 设置时启用）
    await hooks.emit("install.result", {"node_id": node_id, **result})
    return result


@router.get("/{node_id}/logs")
async def node_logs(node_id: str, tail: int = 100, r=Depends(deps.get)) -> dict:
    """节点系统日志：Agent 生命周期事件（受理/拉镜像/成败/自杀等），新→旧。"""
    raw = await r.lrange(f"hs:nodelog:{node_id}", -tail, -1)
    lines = []
    for line in reversed(raw):
        try:
            lines.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"lines": lines}


@router.post("/{node_id}/stop-agent", dependencies=[Depends(require_role("operator"))])
async def stop_agent(node_id: str, r=Depends(deps.get)) -> dict:
    """远程停止目标机 Agent（调 agent /shutdown，其自杀会删容器防 restart 拉起）。

    停止后心跳 15s 内失效，节点转离线，此时可删除。
    """
    h = await r.hgetall(f"{NODE_PREFIX}{node_id}")
    if not h:
        raise HTTPException(404, "node not online")
    addr = h.get("agent_addr")
    # 优先 hs:conn 的 SSH 主机地址（LAN 可达）；agent_addr 可能是容器网桥 IP
    conn = await r.hgetall(f"hs:conn:{node_id}")
    if conn.get("host"):
        addr = f"http://{conn['host']}:9100"
    if not addr:
        raise HTTPException(400, "该节点 Agent 为旧版（无 agent_addr），请先用「重装」升级镜像")
    try:
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as c:
            resp = await c.post(f"{addr}/shutdown")
        out = resp.json()
    except Exception as e:  # noqa: BLE001 —— Agent 自杀可能秒断连，视为已停止
        out = {"stopping": True, "note": f"连接中断（预期内）: {type(e).__name__}"}
    await hooks.emit("node.stop_agent", {"node_id": node_id, "result": out})
    return {"node_id": node_id, **out}


@router.delete("/{node_id}", dependencies=[Depends(require_role("operator"))])
async def remove_node(node_id: str, r=Depends(deps.get)) -> dict:
    """删除节点：在线 → 写墓碑（Agent 下一拍心跳见到即自杀并删容器）；离线 → 直接清登记。"""
    h = await r.hgetall(f"{NODE_PREFIX}{node_id}")
    online = bool(h) and int(h.get("last_seen", "0")) > time.time() - 15
    await r.delete(f"{NODE_PREFIX}{node_id}", f"{CRED_PREFIX}{node_id}",
                   f"hs:conn:{node_id}")
    # 清理该节点在模型集合里的实例登记（实例 hash 本身随 TTL 过期）
    for mk in await r.keys(f"hs:model:*"):
        for iid in await r.smembers(mk):
            if iid.startswith(f"{node_id}:"):
                await r.srem(mk, iid)
    if online:
        # 墓碑：Agent（新版）下一拍读到即自杀；列表层同时过滤墓碑节点，
        # 旧版 Agent 即使瞎写也不会在控制台复活。墓碑持久，重新添加/安装时清除。
        await r.set(f"hs:node_del:{node_id}", "1")
        return {"removed": node_id, "agent_notified": True,
                "msg": "节点在线：已下发自杀墓碑并隐藏，Agent（新版）数秒内自停"}
    # 离线也写墓碑（防失联中的旧 Agent 突然恢复心跳复活）
    await r.set(f"hs:node_del:{node_id}", "1")
    return {"removed": node_id, "agent_notified": False}
