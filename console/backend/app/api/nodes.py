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
from ..license_guard import check_node_cap, require_active_base
from ..security import current_user, require_role

# fire-and-forget 后台任务持引用（防 GC，RUF006）
_BG: set = set()

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
            "models_mount": conn.get("models_mount", ""),
            "arch": h.get("arch", ""),
            "nics": json.loads(h.get("nics", "[]") or "[]"),
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


@router.put("/{node_id}/conn", dependencies=[Depends(require_role("operator")), Depends(require_active_base)])
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


@router.post("", dependencies=[Depends(require_role("operator")), Depends(require_active_base)])
async def add_node(req: NodeAddReq, r=Depends(deps.get)) -> dict:
    await check_node_cap(r)
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


@router.post("/{node_id}/install", dependencies=[Depends(require_role("operator")), Depends(require_active_base)])
async def install_node(node_id: str, req: NodeAddReq, r=Depends(deps.get)) -> dict:
    """存凭证 → SSH 推送安装 Agent 容器 → 心跳自动上线。"""
    from .. import ssh_installer
    from ..config import settings

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
    # 纳管成功后自动预置平台自研镜像（image-server 等；失败不阻塞纳管）
    if result.get("ok", True):
        import asyncio

        from .images import auto_seed_node
        t = asyncio.create_task(auto_seed_node(r, node_id))
        _BG.add(t)
        t.add_done_callback(_BG.discard)
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
    except Exception as e:
        out = {"stopping": True, "note": f"连接中断（预期内）: {type(e).__name__}"}
    await hooks.emit("node.stop_agent", {"node_id": node_id, "result": out})
    return {"node_id": node_id, **out}


# ---------------- 节点真机运维（SSH 直连，不依赖 Agent 在线） ----------------

async def _evt(r, level: str, msg: str) -> None:
    """事件流（驾驶舱/事件页可见）。"""
    pipe = r.pipeline()
    pipe.rpush("hs:events", json.dumps(
        {"ts": round(time.time(), 3), "level": level, "msg": msg}, ensure_ascii=False))
    pipe.ltrim("hs:events", -500, -1)
    await pipe.execute()


async def _ssh_connect(r, node_id: str):
    """从 hs:conn + hs:cred 解密凭证建立 SSH 连接（用完由调用方 async with 管理）。"""
    import asyncssh
    conn = await r.hgetall(f"hs:conn:{node_id}")
    blob = await r.get(f"{CRED_PREFIX}{node_id}")
    if not conn.get("host") or not blob:
        raise HTTPException(400, f"节点 {node_id} 无连接档案/凭证，无法 SSH")
    secret = decrypt(blob)
    kw: dict = {"host": conn["host"], "port": int(conn.get("port", 22)),
                "username": conn.get("username", "root"), "known_hosts": None,
                "connect_timeout": 10}
    if "PRIVATE KEY" in secret:
        kw["client_keys"] = [asyncssh.import_private_key(secret)]
    else:
        kw["password"] = secret
    return asyncssh.connect(**kw)


@router.post("/{node_id}/docker/restart",
             dependencies=[Depends(require_role("operator")), Depends(require_active_base)])
async def restart_docker(node_id: str, r=Depends(deps.get)) -> dict:
    """重启节点 Docker 守护进程（hengshu 容器均带 --restart unless-stopped，会自动回场）。"""
    await _evt(r, "INFO", f"node ▸ {node_id} 重启 Docker 服务（运行中容器将短暂中断，自动回场）…")
    try:
        async with await _ssh_connect(r, node_id) as c:
            # systemctl 优先，service 兜底；setsid 挂后台——连接可能随 docker 停止而断
            out = await c.run(
                "sudo -n sh -c 'setsid systemctl restart docker >/dev/null 2>&1 || "
                "setsid service docker restart >/dev/null 2>&1' ; echo issued", timeout=15)
            if "issued" not in (out.stdout or ""):
                raise RuntimeError((out.stderr or "restart 命令未受理").strip()[:200])
    except HTTPException:
        raise
    except Exception as e:  # SSH 断开也可能是 docker 已重启成功的表现
        await _evt(r, "WARN", f"node ▸ {node_id} Docker 重启指令下发后连接中断（通常即重启生效）: {type(e).__name__}")
        return {"ok": True, "node": node_id, "note": "指令已下发，连接中断（docker 重启中断连属正常），稍后节点自动回场"}
    await _evt(r, "OK", f"node ▸ {node_id} Docker 重启指令已下发，Agent 数十秒内自动回场")
    return {"ok": True, "node": node_id}


async def _cleanup_node_host(r, node_id: str) -> dict:
    """删除节点时真机清理：kill 所有 hengshu 相关容器 + 清数据卷 + 清镜像暂存。返回清理报告。"""
    report: dict = {"cleaned": [], "errors": []}
    try:
        async with await _ssh_connect(r, node_id) as c:
            out = await c.run(
                "names=$(docker ps -a --format '{{.Names}}' | grep -E '^(hs-|hengshu-)' || true); "
                "echo \"found:$names\"; "
                "if [ -n \"$names\" ]; then echo \"$names\" | xargs -r docker rm -f 2>&1; fi; "
                "docker volume rm hengshu-agent-data >/dev/null 2>&1 || true; "
                "rm -rf /tmp/hengshu-img/* >/dev/null 2>&1 || true; "
                "echo done", timeout=60)
            txt = (out.stdout or "") + (out.stderr or "")
            found = ""
            for line in txt.splitlines():
                if line.startswith("found:"):
                    found = line[6:].strip()
            report["cleaned"] = list(found.split())
            if "done" not in txt:
                report["errors"].append(txt.strip()[:200])
    except Exception as e:
        report["errors"].append(f"{type(e).__name__}: {e}")
    return report


@router.delete("/{node_id}", dependencies=[Depends(require_role("operator")), Depends(require_active_base)])
async def remove_node(node_id: str, r=Depends(deps.get)) -> dict:
    """删除节点：先 SSH 真机清理 hengshu 容器/数据卷/暂存（还原真机状态），再清平台登记+写墓碑。"""
    h = await r.hgetall(f"{NODE_PREFIX}{node_id}")
    online = bool(h) and int(h.get("last_seen", "0")) > time.time() - 15
    # 真机清理（在线/有凭证时；失败不阻塞删除，报告带出）
    report = await _cleanup_node_host(r, node_id)
    if report["cleaned"]:
        await _evt(r, "OK", f"node ▸ {node_id} 真机清理完成，已移除容器: {', '.join(report['cleaned'])}")
    for err in report["errors"]:
        await _evt(r, "WARN", f"node ▸ {node_id} 真机清理异常（需人工检查）: {err}")
    await r.delete(f"{NODE_PREFIX}{node_id}", f"{CRED_PREFIX}{node_id}",
                   f"hs:conn:{node_id}")
    # 清理该节点在模型集合里的实例登记（实例 hash 本身随 TTL 过期）
    for mk in await r.keys("hs:model:*"):
        for iid in await r.smembers(mk):
            if iid.startswith(f"{node_id}:"):
                await r.srem(mk, iid)
    # 墓碑持久（防失联旧 Agent 复活）；重新添加/安装时清除
    await r.set(f"hs:node_del:{node_id}", "1")
    return {"removed": node_id, "agent_notified": online,
            "cleanup": report,
            "msg": "真机 hengshu 容器已清理" if report["cleaned"] and not report["errors"]
                   else "平台登记已删；真机清理有异常，见事件流"}


class NcclTestIn(BaseModel):
    nodes: list[str] = Field(min_length=2)          # 参与节点（≥2）
    nics: dict[str, str] = {}                       # 节点 → 网卡名（异名双网口各自指定）
    image: str = "docker.1ms.run/vllm/vllm-openai:latest"  # 自带 torch+NCCL
    master_port: int = 29500
    cmd: str = ""                                   # 自定义测试指令（覆盖默认 torchrun）
    env: dict[str, str] = {}                        # 附加 NCCL_* 环境变量（RoCE 调参）


@router.post("/nccl-test", dependencies=[Depends(require_role("operator"))])
async def nccl_test(req: NcclTestIn, r=Depends(deps.get)) -> dict:
    """多机 NCCL all_reduce 测试：master=首个节点，master_addr=该节点在其所选网卡上的 IP。"""
    import asyncio as _aio

    # 每节点解析：agent 地址 + 该节点所选网卡的 IP
    node_info: dict[str, dict] = {}
    for nid in req.nodes:
        nic = req.nics.get(nid, "")
        if not nic:
            raise HTTPException(400, f"节点 {nid} 未指定网卡")
        h = await r.hgetall(f"{NODE_PREFIX}{nid}")
        conn = await r.hgetall(f"hs:conn:{nid}")
        addr = (f"http://{conn['host']}:9100" if conn.get("host")
                else h.get("agent_addr"))
        if not addr:
            raise HTTPException(400, f"节点 {nid} 无 Agent 地址（未上线）")
        try:
            nics = json.loads(h.get("nics", "[]"))
        except ValueError:
            nics = []
        nic_ip = next((n["ip"] for n in nics
                       if n["name"] == nic and n.get("up")), "")
        if not nic_ip:
            raise HTTPException(400, f"节点 {nid} 网卡 {nic} 不在线或不存在（先看节点网卡列表）")
        node_info[nid] = {"addr": addr, "ip": nic_ip, "nic": nic}

    master_ip = node_info[req.nodes[0]]["ip"]
    world = len(req.nodes)

    async def _one(rank: int, nid: str) -> dict:
        info = node_info[nid]
        payload = {"rank": rank, "world": world, "master_addr": master_ip,
                   "master_port": req.master_port, "nic": info["nic"], "image": req.image,
                   "cmd": req.cmd, "env": req.env}
        try:
            async with httpx.AsyncClient(timeout=170.0, trust_env=False) as c:
                resp = await c.post(f"{info['addr']}/nccl-test", json=payload)
                d = resp.json()
        except Exception as e:
            d = {"ok": False, "tail": [f"{type(e).__name__}: {e}"]}
        d["node"] = nid
        d["ip"] = info["ip"]
        d["nic"] = info["nic"]
        return d

    results = await _aio.gather(*(_one(i, nid) for i, nid in enumerate(req.nodes)))
    ok = all(x.get("ok") for x in results)
    bw = ""
    for x in results:
        for ln in x.get("tail", []):
            if "bus_bandwidth_GBps" in ln:
                bw = ln.strip()
    return {"ok": ok, "master": req.nodes[0], "master_ip": master_ip,
            "bandwidth": bw, "results": results}


class NetTestIn(BaseModel):
    nodes: list[str] = Field(min_length=2)
    nics: dict[str, str] = {}     # 节点 → 网卡名（取该网卡 IP 作为 ping 目标）


async def _node_agent_and_ip(r, nid: str, nic: str) -> tuple[str, str]:
    h = await r.hgetall(f"{NODE_PREFIX}{nid}")
    conn = await r.hgetall(f"hs:conn:{nid}")
    addr = (f"http://{conn['host']}:9100" if conn.get("host") else h.get("agent_addr", ""))
    if not addr:
        raise HTTPException(400, f"节点 {nid} 无 Agent 地址（未上线）")
    try:
        nics = json.loads(h.get("nics", "[]"))
    except ValueError:
        nics = []
    ip = next((n["ip"] for n in nics if n["name"] == nic and n.get("up")), "")
    if not ip:
        raise HTTPException(400, f"节点 {nid} 网卡 {nic} 不在线或不存在")
    return addr, ip


@router.post("/net-test", dependencies=[Depends(require_role("operator"))])
async def net_test(req: NetTestIn, r=Depends(deps.get)) -> dict:
    """连通性测试（ping）：master 与其余节点所选网卡 IP 逐对互 ping。先于 NCCL。"""
    import asyncio as _aio
    infos = {nid: await _node_agent_and_ip(r, nid, req.nics.get(nid, ""))
             for nid in req.nodes}
    pairs = [(a, b) for i, a in enumerate(req.nodes) for b in req.nodes[i + 1:]]
    results = []

    async def _ping(a: str, b: str) -> dict:
        a_addr, _ = infos[a]
        _, b_ip = infos[b]
        try:
            async with httpx.AsyncClient(timeout=60.0, trust_env=False) as c:
                d = (await c.post(f"{a_addr}/net/ping", json={"target_ip": b_ip})).json()
        except Exception as e:
            d = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        d["dir"] = f"{a} → {b}"
        return d

    for a, b in pairs:
        results += await _aio.gather(_ping(a, b), _ping(b, a))
    return {"ok": all(x.get("ok") and not x.get("loss_pct") for x in results), "pairs": results}


class SshMeshIn(BaseModel):
    nodes: list[str] = Field(min_length=2)


@router.post("/ssh-mesh", dependencies=[Depends(require_role("operator"))])
async def ssh_mesh(req: SshMeshIn, r=Depends(deps.get)) -> dict:
    """节点间免密互通：各节点生成 ed25519 密钥对，公钥互投到 authorized_keys。"""
    import asyncssh
    pubs: dict[str, str] = {}
    errors: dict[str, str] = {}

    async def _connect(nid: str):
        conn = await r.hgetall(f"hs:conn:{nid}")
        blob = await r.get(f"{CRED_PREFIX}{nid}")
        if not conn.get("host") or not blob:
            raise HTTPException(400, f"节点 {nid} 无连接信息/凭证")
        secret = decrypt(blob)
        kw = {"known_hosts": None, "login_timeout": 15}
        if secret.startswith("-----BEGIN"):
            kw["client_keys"] = [asyncssh.import_private_key(secret)]
        else:
            kw["password"] = secret
        return await asyncssh.connect(conn["host"], port=int(conn.get("port", "22")),
                                      username=conn.get("username", "root"), **kw)

    for nid in req.nodes:
        try:
            async with await _connect(nid) as c:
                await c.run("mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
                            "([ -f ~/.ssh/id_ed25519 ] || ssh-keygen -t ed25519 -N '' "
                            "-f ~/.ssh/id_ed25519 -q)", check=True)
                pubs[nid] = (await c.run("cat ~/.ssh/id_ed25519.pub", check=True)).stdout.strip()
        except Exception as e:
            errors[nid] = f"{type(e).__name__}: {e}"
    if len(pubs) < 2:
        raise HTTPException(502, f"公钥收集失败: {errors}")

    for nid in pubs:
        try:
            async with await _connect(nid) as c:
                for pub in pubs.values():
                    await c.run("touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && "
                                f"(grep -qxF '{pub}' ~/.ssh/authorized_keys || "
                                f"echo '{pub}' >> ~/.ssh/authorized_keys)", check=True)
        except Exception as e:
            errors[nid] = f"{type(e).__name__}: {e}"
    return {"ok": not errors, "meshed": sorted(pubs), "errors": errors}


@router.post("/{node_id}/nccl-env", dependencies=[Depends(require_role("operator"))])
async def nccl_env(node_id: str, r=Depends(deps.get)) -> dict:
    """NCCL 环境部署：转发 Agent（镜像就位+自检；拉镜像可能数十分钟）。"""
    h = await r.hgetall(f"{NODE_PREFIX}{node_id}")
    conn = await r.hgetall(f"hs:conn:{node_id}")
    addr = (f"http://{conn['host']}:9100" if conn.get("host") else h.get("agent_addr", ""))
    if not addr:
        raise HTTPException(400, f"节点 {node_id} 无 Agent 地址（未上线）")
    try:
        async with httpx.AsyncClient(timeout=1900.0, trust_env=False) as c:
            return (await c.post(f"{addr}/nccl-env/deploy", json={})).json()
    except Exception as e:
        raise HTTPException(502, f"无法连接节点 Agent（{addr}）：{type(e).__name__} {e}") from e


class NicConfigIn(BaseModel):
    name: str
    ip_cidr: str = ""
    up: bool | None = None


@router.post("/{node_id}/nic", dependencies=[Depends(require_role("operator"))])
async def nic_config(node_id: str, req: NicConfigIn, r=Depends(deps.get)) -> dict:
    """配置节点网卡（设 IP / up/down）：转发 Agent，回最新网卡清单。"""
    h = await r.hgetall(f"{NODE_PREFIX}{node_id}")
    conn = await r.hgetall(f"hs:conn:{node_id}")
    addr = (f"http://{conn['host']}:9100" if conn.get("host") else h.get("agent_addr", ""))
    if not addr:
        raise HTTPException(400, f"节点 {node_id} 无 Agent 地址（未上线）")
    try:
        async with httpx.AsyncClient(timeout=60.0, trust_env=False) as c:
            return (await c.post(f"{addr}/nic/config", json=req.model_dump())).json()
    except Exception as e:
        raise HTTPException(502, f"无法连接节点 Agent（{addr}）：{type(e).__name__} {e}") from e
