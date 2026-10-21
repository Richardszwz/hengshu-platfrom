"""模型服务 API：实例列表 + 启停/日志指令下发（Agent 接收端已实现）。"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import deps
from ..keys import INST_PREFIX, MODEL_PREFIX, NODE_PREFIX
from ..license_guard import require_active_base
from ..security import current_user, require_role

router = APIRouter(prefix="/api/services", tags=["services"],
                   dependencies=[Depends(current_user)])

SPEC_PREFIX = "hs:svcspec:"

# fire-and-forget 后台任务持引用（防 GC，RUF006）
_BG_TASKS: set = set()


def _fire(coro):
    import asyncio as _a
    t = _a.create_task(coro)
    _BG_TASKS.add(t)
    t.add_done_callback(_BG_TASKS.discard)
    return t


class StartServiceReq(BaseModel):
    name: str = Field(description="服务/模型名，容器名 hs-svc-{name}")
    image: str = "vllm/vllm-openai:latest"
    start_cmd: str
    port: int
    workdir: str = "/workspace"
    env: dict[str, str] = {}
    # 卷挂载：["宿主机路径:容器路径[:ro]"]——模型权重靠它进容器
    volumes: list[str] = []
    # 档位别名（HSE 路由）："" 不分档 | fast | medium | strong
    tier: str = ""
    # 服务类型：chat（默认）| embedding | image（图像生成，走 /v1/images/generations）| custom（只纳管不进路由池）
    kind: str = "chat"
    # 自定义框架健康探测路径（空=类型默认）
    health_path: str = ""


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


@router.post("/{node_id}/start", dependencies=[Depends(require_role("operator")), Depends(require_active_base)])
async def launch_service(node_id: str, req: StartServiceReq, r=Depends(deps.get)) -> dict:
    """启动新服务：先存 spec，后台 ensure 模型（缺则他机拷贝/在线拉取）→ 就绪后下发 Agent。"""
    import json as _json
    import re as _re
    import time as _time

    from .. import model_dist
    from ..keys import EVENTS_KEY

    spec = req.model_dump()
    scalar = {k: str(v) for k, v in spec.items() if k not in ("env", "volumes")}
    scalar["env_json"] = _json.dumps(req.env)
    scalar["volumes_json"] = _json.dumps(req.volumes)
    if req.volumes:
        # 记住该节点的模型卷挂载，下次启动对话框自动预填（防 X86 路径套到 Spark 这类错配）
        await r.hset(f"hs:conn:{node_id}", "models_mount", req.volumes[0])
    await r.hset(f"{SPEC_PREFIX}{node_id}:{req.name}", mapping=scalar)

    async def _ensure_then_start() -> None:
        # /models/<dir> 约定：从启动命令解析模型目录名；无引用则跳过分发
        m = _re.search(r"/models/([^\s/:'\"]+)", req.start_cmd)
        try:
            if m:
                src = await model_dist.ensure_model(r, node_id, m.group(1))
                await model_dist._emit(r, "OK",
                                       f"svc ▸ {node_id}/{req.name} 模型就绪（{src}），下发启动",
                                       node=node_id)
            addr = await _agent_addr(node_id, r)
            async with httpx.AsyncClient(timeout=120.0, trust_env=False) as c:
                await c.post(f"{addr}/services/start", json=req.model_dump())
        except Exception as e:
            await model_dist._emit(r, "ERR",
                                   f"svc ▸ {node_id}/{req.name} 启动前准备失败: {e}",
                                   node=node_id)

    _fire(_ensure_then_start())
    async with r.pipeline() as p:
        p.rpush(EVENTS_KEY, _json.dumps({
            "ts": round(_time.time(), 3), "level": "INFO",
            "msg": f"svc ▸ 控制台受理 {node_id}/{req.name}（模型检查/分发 → 自动启动）"},
            ensure_ascii=False))
        p.ltrim(EVENTS_KEY, -200, -1)
        await p.execute()
    return {"accepted": req.name, "note": "模型检查/分发中，就绪后自动启动（进度见集群事件流）"}


@router.get("/models/inventory")
async def models_inventory(r=Depends(deps.get)) -> dict:
    """集群模型清单：{节点: [模型目录...]}（/models 约定，Agent 实扫宿主机目录）。"""
    from .. import model_dist
    return {"inventory": await model_dist.inventory(r)}


@router.post("/{node_id}/{name}/stop", dependencies=[Depends(require_role("operator"))])
async def stop_service(node_id: str, name: str, r=Depends(deps.get)) -> dict:
    addr = await _agent_addr(node_id, r)
    async with httpx.AsyncClient(timeout=10.0, trust_env=False) as c:
        resp = await c.post(f"{addr}/services/stop/{name}")
    return resp.json()


@router.delete("/{node_id}/{name}", dependencies=[Depends(require_role("operator")), Depends(require_active_base)])
async def delete_service(node_id: str, name: str, r=Depends(deps.get)) -> dict:
    """删除服务：尽力停容器 → 清实例登记/模型集合。spec 保留作启动模板（重建时对话框预填用）。"""
    stopped = None
    try:
        addr = await _agent_addr(node_id, r)
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as c:
            # Agent 删除端点会同时移除其本地实例登记（防心跳复活）
            stopped = (await c.post(f"{addr}/services/delete/{name}")).json()
    except Exception as e:
        stopped = f"agent 不可达（{type(e).__name__}），仅清登记"
    # 实例 key 直接删（不等 TTL）
    iid_keys = [k for k in await r.keys(f"{INST_PREFIX}{node_id}:*")
                if (await r.hget(k, "model")) == name]
    if iid_keys:
        await r.delete(*iid_keys)
        for k in iid_keys:
            iid = k.removeprefix(INST_PREFIX)
            await r.srem(f"{MODEL_PREFIX}{name}", iid)
    # TP 组：连带停掉各节点 ray 容器
    grp = await r.get(f"hs:svcgroup:{name}")
    if grp:
        import asyncio as _aio
        import json as _json
        nodes = _json.loads(grp).get("nodes", [])

        async def _ray_stop(nid: str) -> None:
            try:
                addr = await _agent_addr(nid, r)
                async with httpx.AsyncClient(timeout=15.0, trust_env=False) as c:
                    await c.post(f"{addr}/ray/stop/{name}")
            except Exception:
                pass
        await _aio.gather(*(_ray_stop(n) for n in nodes))
        await r.delete(f"hs:svcgroup:{name}")
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
                "tier": spec.get("tier", ""),
                "kind": spec.get("kind", "chat"),
                "health_path": spec.get("health_path", ""),
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


class MultiStartReq(BaseModel):
    """多机 TP 并行启动：一个模型横跨多节点（Ray + vllm --tensor-parallel-size=N）。"""
    name: str
    nodes: list[str] = Field(min_length=2)          # 首个 = master（推理入口所在）
    nics: dict[str, str] = {}                       # 节点 → 网卡名（NCCL/Ray 走该链路）
    model_dir: str                                  # /models 下的目录名（各节点同名）
    image: str = "docker.1ms.run/vllm/vllm-openai:latest"
    port: int = 8100
    env: dict[str, str] = {}
    tier: str = ""                                # 档位别名（HSE 路由）
    kind: str = "chat"                            # 服务类型（同 StartServiceReq）
    health_path: str = ""


@router.post("/multi-start", dependencies=[Depends(require_role("operator")), Depends(require_active_base)])
async def multi_start(req: MultiStartReq, r=Depends(deps.get)) -> dict:
    """受理制：模型分发 → Ray head/worker → 集齐 → master 起 vllm TP=N → 注册实例。"""
    import asyncio as _aio
    import json as _json

    from .. import model_dist

    async def _emit(level: str, msg: str) -> None:
        await model_dist._emit(r, level, msg, node=req.nodes[0])

    async def _bg() -> None:
        world = len(req.nodes)
        # ① 节点信息（agent 地址 + 网卡 IP）
        infos: dict[str, dict] = {}
        for nid in req.nodes:
            addr = await _agent_addr(nid, r)
            h = await r.hgetall(f"{NODE_PREFIX}{nid}")
            try:
                nics = _json.loads(h.get("nics", "[]"))
            except ValueError:
                nics = []
            nic = req.nics.get(nid, "")
            ip = next((x["ip"] for x in nics if x["name"] == nic and x.get("up")), "")
            if not ip:
                await _emit("ERR", f"multi ▸ {nid} 网卡 {nic} 无可用 IP，中止")
                return
            infos[nid] = {"addr": addr, "ip": ip, "nic": nic}
        master = req.nodes[0]
        m_ip = infos[master]["ip"]

        # ② 模型分发（并行 ensure）
        await _emit("INFO", f"multi ▸ {req.name} 模型分发检查 ×{world}（{req.model_dir}）")
        try:
            await _aio.gather(*(model_dist.ensure_model(r, nid, req.model_dir)
                                for nid in req.nodes))
        except Exception as e:
            await _emit("ERR", f"multi ▸ 模型分发失败: {type(e).__name__}: {e}")
            return

        # ③ Ray：head 先起，worker 并行跟上
        async def _ray(nid: str, role: str) -> dict:
            info = infos[nid]
            payload = {"name": req.name, "role": role, "image": req.image,
                       "nic": info["nic"], "nic_ip": info["ip"],
                       "master_addr": m_ip, "env": req.env}
            try:
                async with httpx.AsyncClient(timeout=120.0, trust_env=False) as c:
                    return (await c.post(f"{info['addr']}/ray/start", json=payload)).json()
            except Exception as e:
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}

        head = await _ray(master, "head")
        if not head.get("ok"):
            await _emit("ERR", f"multi ▸ ray head 启动失败: {head.get('error','')[:150]}")
            return
        await _emit("OK", f"multi ▸ ray head 已起 {master}({m_ip})")
        workers = await _aio.gather(*(_ray(nid, "worker") for nid in req.nodes[1:]))
        bad = [n for n, w in zip(req.nodes[1:], workers, strict=False) if not w.get("ok")]
        if bad:
            await _emit("ERR", f"multi ▸ ray worker 失败节点: {bad}")
            return

        # ④ 等 ray 集齐 world 节点
        for _ in range(24):
            try:
                async with httpx.AsyncClient(timeout=15.0, trust_env=False) as c:
                    on = (await c.get(f"{infos[master]['addr']}/ray/nodes",
                                      params={"name": req.name})).json().get("online", 0)
            except Exception:
                on = 0
            if on >= world:
                break
            await _aio.sleep(5)
        else:
            await _emit("ERR", f"multi ▸ ray 集齐超时（{on}/{world}）")
            return
        await _emit("OK", f"multi ▸ ray 集群就绪 {world} 节点")

        # ⑤ master 起 vllm TP=world（正常服务管控链路，实例注册不变）
        tp_cmd = (f"vllm serve /models/{req.model_dir} --port {req.port} "
                  f"--served-model-name {req.name} --tensor-parallel-size {world} "
                  f"--distributed-executor-backend ray")
        svc_env = dict(req.env)
        svc_env.update({"VLLM_HOST_IP": m_ip,
                        "NCCL_SOCKET_IFNAME": infos[master]["nic"]})
        spec = {"name": req.name, "image": req.image, "start_cmd": tp_cmd,
                "port": req.port, "workdir": "/workspace", "env": svc_env,
                "volumes": [f"{model_dist.models_root(await r.hgetall(f'hs:conn:{master}'))}:/models:ro"],
                "tier": getattr(req, "tier", ""),
                "kind": getattr(req, "kind", "chat"),
                "health_path": getattr(req, "health_path", "")}
        try:
            async with httpx.AsyncClient(timeout=120.0, trust_env=False) as c:
                resp = await c.post(f"{infos[master]['addr']}/services/start", json=spec)
            if resp.status_code != 200:
                raise RuntimeError(resp.text[:200])
        except Exception as e:
            await _emit("ERR", f"multi ▸ vllm 下发失败: {e}")
            return
        # 持久化 spec + TP 组档案（删除时连带清 ray 容器）
        scalar = {k: str(v) for k, v in spec.items() if k not in ("env", "volumes")}
        scalar["env_json"] = _json.dumps(spec["env"])
        scalar["volumes_json"] = _json.dumps(spec["volumes"])
        await r.hset(f"{SPEC_PREFIX}{master}:{req.name}", mapping=scalar)
        await r.set(f"hs:svcgroup:{req.name}",
                    _json.dumps({"nodes": req.nodes, "master": master}))
        await _emit("OK", f"multi ▸ {req.name} TP×{world} 已受理（{master}:{req.port}，加载数分钟）")

    _fire(_bg())
    return {"accepted": req.name, "tp": len(req.nodes),
            "note": f"TP×{len(req.nodes)} 部署中：分发→Ray→vllm（进度见集群事件流）"}
