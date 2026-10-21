"""Agent 主程序：心跳 → Redis（节点 + 实例指标 TTL），暴露本地控制 API。

职责（P0 范围）：
- 周期心跳：节点信息 + 该节点全部实例的框架级指标（统一 m_ 口径）
- 控制 API：/services/start /stop /logs（控制台远程启停的接收端）
- 架构感知：GPU 主机指标 x86→pynvml / ARM(Jetson)→留接口降级
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import socket
import time

import redis.asyncio as aioredis
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from . import netinfo, rayctl, service_ctl
from .collectors.base import CollectorRegistry, Sample
from .collectors.gpu_sys import sample as gpu_sample
from .collectors.gpu_sys import start as gpu_start
from .collectors.ollama import OllamaCollector
from .collectors.tgi import TgiCollector
from .collectors.trt import TrtCollector
from .collectors.vllm import VllmCollector
from .config import settings
from .service_ctl import ServiceCtl, StartRequest, container_state, is_running

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("agent")

INSTANCES: list[dict] = []
_STATE_DIR = os.getenv("AGENT_STATE_DIR", "/data")
_SERVICES_FILE = os.path.join(_STATE_DIR, "services.json")


def _load_services() -> None:
    """从持久化文件恢复实例列表（容器重启不丢）。"""
    global INSTANCES
    try:
        if os.path.isfile(_SERVICES_FILE):
            INSTANCES.extend(json.load(open(_SERVICES_FILE)))
            log.info("restored %d services from %s", len(INSTANCES), _SERVICES_FILE)
    except Exception as e:
        log.warning("load services failed: %s", e)


def _save_services() -> None:
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        with open(_SERVICES_FILE, "w") as f:
            json.dump(INSTANCES, f, ensure_ascii=False)
    except Exception as e:
        log.warning("save services failed: %s", e)


def _advertise_host() -> str:
    """实例 URL 宣告地址：env 优先，否则取本机局域网 IP（Router 需可达）。"""
    host = os.getenv("AGENT_ADVERTISE_HOST", "")
    if host:
        return host
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return socket.gethostname()


def build_collectors() -> CollectorRegistry:
    reg = CollectorRegistry()
    for c in (VllmCollector(), TgiCollector(), TrtCollector(), OllamaCollector()):
        reg.register(c)
    return reg


app = FastAPI(title="衡枢 Agent", version="0.1.0")
ctl = ServiceCtl()
collectors = build_collectors()
_redis: aioredis.Redis | None = None


def redis() -> aioredis.Redis:
    if _redis is None:
        raise RuntimeError("redis not connected")
    return _redis


# fire-and-forget 后台任务持引用集合（防 GC 回收运行中任务，RUF006）
_BG_TASKS: set[asyncio.Task] = set()


def fire_bg(coro) -> asyncio.Task:
    t = asyncio.create_task(coro)
    _BG_TASKS.add(t)
    t.add_done_callback(_BG_TASKS.discard)
    return t


@app.on_event("startup")
async def _startup() -> None:
    global _redis
    _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    await gpu_start()  # pynvml 可用则用之，否则起 tegrastats 后台
    _load_services()   # 恢复控制台下发过的服务清单
    fire_bg(heartbeat_loop())


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "node": settings.node_id, "arch": __import__("platform").machine()}


_INFLIGHT: set[str] = set()


_INST_STATUS: dict[str, str] = {}  # iid → 上次状态（崩溃转变时打事件）


def _served_model(inst: dict) -> str:
    """从启动命令解析 --served-model-name（上游真实模型名）；缺省用服务名。"""
    m = re.search(r"--served-model-name[=\s]+([^\s]+)", inst.get("start_cmd") or "")
    return m.group(1) if m else inst.get("model", "")


async def _inst_status(name: str, iid: str) -> str:
    """实例状态三态：running / stopped / crashing（重启循环）。
    转变为 crashing 时抓日志尾部打事件（前台节点系统日志可见）。"""
    st, restarts = await container_state(name)
    if st == "restarting" or (st == "running" and restarts >= 3):
        status = "crashing"
    elif st == "running":
        status = "running"
    elif st == "":
        status = "stopped"
    else:
        status = "stopped"
    prev = _INST_STATUS.get(iid)
    if status == "crashing" and prev != "crashing":
        lines = await ctl.logs(name, 8)
        await _push_event("ERR", f"svc ▸ {name} 进入崩溃循环（重启 {restarts} 次），日志尾部：")
        for ln in lines[-8:]:
            await _push_event("ERR", f"  │ {ln[:300]}")
    _INST_STATUS[iid] = status
    return status


async def _watch_started(req: StartRequest) -> None:
    """启动后巡检：15s / 2min 各查一次；容器没活就把日志尾部抓进节点系统日志。"""
    for wait in (15, 105):
        await asyncio.sleep(wait)
        if await is_running(req.name):
            return
        lines = await ctl.logs(req.name, 12)
        await _push_event("ERR", f"svc ▸ {req.name} 容器未存活（巡检 {wait}s），日志尾部：")
        for ln in lines[-12:]:
            await _push_event("ERR", f"  │ {ln[:300]}")
        return


async def _start_bg(req: StartRequest) -> None:
    """后台执行：拉镜像（可达数分钟）→ 起容器 → 登记实例。全程写事件流。"""
    service_ctl.progress_cb = _push_event  # 拉取进度 → 节点系统日志
    await _push_event("INFO", f"svc ▸ {req.name} 开始执行：拉取镜像 {req.image}（大镜像需数分钟）")
    try:
        result = await ctl.start(req)
        inst = next((x for x in INSTANCES if x["model"] == req.name), None)
        spec = {"model": req.name,
                "framework": "vllm" if "vllm" in req.start_cmd.lower() else "custom",
                "port": req.port, "image": req.image, "start_cmd": req.start_cmd,
                "workdir": req.workdir, "env": req.env, "tier": req.tier,
                "kind": req.kind, "health_path": req.health_path}
        if inst:
            inst.update(spec)
        else:
            INSTANCES.append(spec)
        _save_services()
        await _push_event("OK", f"svc ▸ {req.name} 启动完成: {result}")
        fire_bg(_watch_started(req))  # 巡检：崩溃即把容器日志抓进节点日志
    except Exception as e:
        await _push_event("ERR", f"svc ▸ {req.name} 启动失败: {e}")


async def _push_event(level: str, msg: str) -> None:
    """事件双写：集群事件流 hs:events + 节点系统日志 hs:nodelog:{id}（各留 200 条）。"""
    try:
        line = json.dumps({"ts": round(time.time(), 3), "level": level, "msg": msg},
                          ensure_ascii=False)
        r = redis()
        async with r.pipeline() as p:
            p.rpush("hs:events", line)
            p.ltrim("hs:events", -200, -1)
            p.rpush(f"hs:nodelog:{settings.node_id}", line)
            p.ltrim(f"hs:nodelog:{settings.node_id}", -200, -1)
            await p.execute()
    except Exception:
        pass


@app.post("/services/start")
async def start(req: StartRequest) -> dict:
    """受理即返回（202 语义）：镜像拉取可能数分钟，异步执行，实例就绪后由心跳上报。"""
    if req.name in _INFLIGHT:
        return {"accepted": req.name, "note": "启动已在进行中，勿重复点击"}
    _INFLIGHT.add(req.name)

    async def _wrap() -> None:
        try:
            await _start_bg(req)
        finally:
            _INFLIGHT.discard(req.name)

    fire_bg(_wrap())
    return {"accepted": req.name, "note": "启动中（镜像拉取可能需要数分钟），实例就绪后自动上线"}


@app.post("/services/stop/{name}")
async def stop(name: str) -> dict:
    return {"result": await ctl.stop(name)}


@app.post("/services/delete/{name}")
async def delete_service(name: str) -> dict:
    """删除服务：停容器 + 从动态实例表/持久化清单移除（防心跳重新注册复活）。"""
    result = await ctl.stop(name)
    before = len(INSTANCES)
    INSTANCES[:] = [x for x in INSTANCES if x["model"] != name]
    if len(INSTANCES) != before:
        _save_services()
    await _push_event("OK", f"svc ▸ {name} 已删除（容器停止 + 登记移除）")
    return {"deleted": name, "result": result, "deregistered": len(INSTANCES) != before}


@app.post("/shutdown")
async def shutdown() -> dict:
    """远程停止 Agent（控制台「停止Agent」按钮）。

    容器以 --restart unless-stopped 运行，进程退出会被 Docker 拉起，
    故通过 docker.sock 删除自身容器；无 docker 时退化为进程退出。
    """
    cname = os.getenv("AGENT_CONTAINER_NAME", "hengshu-agent")

    async def _kill() -> None:
        await asyncio.sleep(0.5)  # 先让响应发回控制台
        import shutil
        if shutil.which("docker"):
            p = await asyncio.create_subprocess_exec("docker", "rm", "-f", cname)
            await p.wait()
        os._exit(0)

    fire_bg(_kill())
    return {"stopping": True, "container": cname}


@app.get("/services/{name}/logs")
async def logs(name: str, tail: int = 100) -> dict:
    return {"lines": await ctl.logs(name, tail)}


@app.get("/nics")
async def get_nics(refresh: bool = False) -> dict:
    """宿主机网卡清单（refresh=true 强制重采，否则尽量走心跳缓存）。"""
    if refresh or not _NICS_CACHE:
        fresh = await netinfo.collect_host_nics()
        if fresh:
            _NICS_CACHE[:] = fresh
    return {"nics": _NICS_CACHE}


_NICS_CACHE: list[dict] = []


class NcclTestReq(BaseModel):
    rank: int
    world: int
    master_addr: str
    master_port: int = 29500
    nic: str                       # NCCL_SOCKET_IFNAME
    image: str                     # 本机已有、自带 torch+NCCL 的镜像（vllm-openai）
    cmd: str = ""                  # 自定义指令（覆盖默认 torchrun 行）
    env: dict = {}                 # 附加环境变量


@app.post("/nccl-test")
async def nccl_test(req: NcclTestReq) -> dict:
    """多机 NCCL all_reduce 联通/带宽测试（本节点一侧）。限时 150s。"""
    # 护栏：显存占用过高（模型服务在跑）时 NCCL 测试必 OOM，直接拒绝并给出指引
    vram_pct = gpu_sample().get("vram", 0.0)
    if vram_pct > 92:
        msg = f"显存占用 {vram_pct}%（有模型服务在跑），NCCL 测试会 OOM——先停止该节点模型服务再测"
        await _push_event("ERR", f"nccl ▸ 拒绝执行: {msg}")
        return {"ok": False, "rank": req.rank, "tail": [msg]}
    await _push_event("INFO", f"nccl ▸ rank {req.rank}/{req.world} nic={req.nic} master={req.master_addr}")
    res = await netinfo.run_nccl_test(
        rank=req.rank, world=req.world, master_addr=req.master_addr,
        master_port=req.master_port, nic=req.nic, image=req.image,
        cmd=req.cmd, env=req.env)
    lvl = "OK" if res["ok"] else "ERR"
    await _push_event(lvl, f"nccl ▸ rank {req.rank} 完成 ok={res['ok']}: "
                           + " / ".join(res["tail"][-2:])[:200])
    return res


class NetPingReq(BaseModel):
    target_ip: str
    count: int = 3


@app.post("/net/ping")
async def net_ping(req: NetPingReq) -> dict:
    """连通性测试：host 网络容器 ping 目标网卡 IP（RoCE 链路预检）。"""
    res = await netinfo.net_ping(req.target_ip, req.count)
    lvl = "OK" if res["ok"] else "ERR"
    await _push_event(lvl, f"net ▸ ping → {req.target_ip} "
                           + (f"loss={res.get('loss_pct')}% rtt={res.get('rtt_ms')}ms" if res["ok"]
                              else str(res.get("error", ""))[:120]))
    return res


class RayStartReq(BaseModel):
    name: str                      # 模型/服务名（容器名 hs-ray-{role}-{name}）
    role: str                      # head | worker
    image: str
    nic: str                       # NCCL_SOCKET_IFNAME
    nic_ip: str                    # 本节点在该网卡上的 IP（ray --node-ip-address / VLLM_HOST_IP）
    master_addr: str = ""          # worker 用：head 节点 IP
    env: dict = {}


@app.post("/ray/start")
async def ray_start(req: RayStartReq) -> dict:
    if req.role not in ("head", "worker"):
        return {"ok": False, "error": "role 须为 head/worker"}
    await _push_event("INFO", f"ray ▸ {req.role} 启动 {req.name}（nic={req.nic} ip={req.nic_ip}）")
    res = await rayctl.ray_start(name=req.name, role=req.role, image=req.image,
                                 nic=req.nic, nic_ip=req.nic_ip, env=req.env,
                                 master_addr=req.master_addr)
    await _push_event("OK" if res["ok"] else "ERR",
                      f"ray ▸ {req.role} {req.name} " + ("已启动" if res["ok"] else f"失败: {res.get('error','')[:150]}"))
    return res


@app.get("/ray/nodes")
async def ray_nodes(name: str) -> dict:
    return {"online": await rayctl.ray_nodes_online(name)}


@app.post("/ray/stop/{name}")
async def ray_stop(name: str) -> dict:
    res = await rayctl.ray_stop(name)
    if res["stopped"]:
        await _push_event("INFO", f"ray ▸ {name} 已停止（{','.join(res['stopped'])}）")
    return res


class NicConfigReq(BaseModel):
    name: str                      # 网卡名
    ip_cidr: str = ""              # 如 10.0.0.5/24（空=不改 IP）
    up: bool | None = None         # True=启用 / False=停用 / None=不动


@app.post("/nic/config")
async def nic_config(req: NicConfigReq) -> dict:
    """配置宿主机网卡（设 IP / up/down），返回最新网卡清单。"""
    res = await netinfo.nic_config(req.name, req.ip_cidr, req.up)
    if res.get("ok"):
        _NICS_CACHE[:] = res["nics"]
    await _push_event("OK" if res.get("ok") else "ERR",
                      f"nic ▸ {req.name} 配置 ip={req.ip_cidr or '-'} up={req.up}: "
                      + ("完成" if res.get("ok") else str(res.get("error", ""))[:120]))
    return res


class NcclEnvReq(BaseModel):
    image: str = "docker.1ms.run/vllm/vllm-openai:latest"


@app.post("/nccl-env/deploy")
async def nccl_env(req: NcclEnvReq) -> dict:
    """NCCL 环境部署：镜像就位 + 容器内 torch.cuda/nccl 自检（进度进节点系统日志）。"""
    await _push_event("INFO", f"nccl-env ▸ 部署开始 image={req.image}")
    res = await netinfo.nccl_env_deploy(req.image, progress_cb=_push_event)
    await _push_event("OK" if res["ok"] else "ERR",
                      f"nccl-env ▸ {'部署完成 ' + res.get('detail', '') if res['ok'] else '部署失败: ' + res.get('error', '')[:150]}")
    return res


async def heartbeat_loop() -> None:
    r = redis()
    ttl = int(settings.heartbeat_interval * 5)  # 必须 int：redis EXPIRE 拒收浮点（整条 pipeline 会失败丢实例注册）
    node_key = f"hs:node:{settings.node_id}"
    tomb_key = f"hs:node_del:{settings.node_id}"
    tick = 0
    while True:
        try:
            # 网卡采集每 ~60s 一次（一次性 host 网络容器，频繁起太重）
            if tick % 20 == 0:
                fresh = await netinfo.collect_host_nics()
                if fresh:
                    _NICS_CACHE[:] = fresh
            tick += 1
            # 删除墓碑：控制台删节点时下发，Agent 见到即自杀（容器删自身防 restart 拉起）
            if await r.get(tomb_key):
                logging.getLogger("hengshu.agent").warning("tombstone received, self-destruct")
                import shutil
                if shutil.which("docker"):
                    p = await asyncio.create_subprocess_exec(
                        "docker", "rm", "-f",
                        os.getenv("AGENT_CONTAINER_NAME", "hengshu-agent"))
                    await p.wait()
                os._exit(0)
            sysm = gpu_sample()
            base = {
                "hostname": os.getenv("NODE_HOSTNAME") or socket.gethostname(),
                "arch": __import__("platform").machine(),
                # 控制台下发服务指令/Web终端外的直连地址（ advertise IP:端口 ）
                "agent_addr": f"http://{_advertise_host()}:{settings.agent_port}",
                "m_temp": str(sysm.get("temp", 0.0)),
                "m_power": str(sysm.get("power", 0.0)),
                "nics": json.dumps(_NICS_CACHE, ensure_ascii=False),
                "last_seen": str(int(time.time())),
            }
            pipe = r.pipeline()
            pipe.hset(node_key, mapping=base)
            pipe.expire(node_key, ttl)
            host = _advertise_host()
            for inst in INSTANCES:
                port = inst["port"]
                iid = f"{settings.node_id}:{port}"
                url = f"http://{host}:{port}"
                status = await _inst_status(inst["model"], iid)
                sample = await _collect(inst, url)
                merged = Sample(
                    gpu_util=sysm["gpu_util"] if sample.gpu_util == 0 else sample.gpu_util,
                    vram=sysm["vram"] if sample.vram == 0 else sample.vram,
                    queue=sample.queue, ttft=sample.ttft,
                    tokens_per_s=sample.tokens_per_s, sessions=sample.sessions,
                )
                pipe.hset(f"hs:inst:{iid}", mapping={
                    "model": inst["model"], "framework": inst["framework"],
                    "url": url, "status": status,
                    "node": settings.node_id, "port": str(port),
                    "served_model": _served_model(inst),
                    "tier": inst.get("tier", ""),
                    "kind": inst.get("kind", "chat"),
                    "health_path": inst.get("health_path", ""),
                    **merged.as_metrics(),
                })
                pipe.expire(f"hs:inst:{iid}", ttl)
                pipe.sadd(f"hs:model:{inst['model']}", iid)
            await pipe.execute()
        except Exception as e:
            log.warning("heartbeat failed: %s", e)
        await asyncio.sleep(settings.heartbeat_interval)


async def _collect(inst: dict, url: str) -> Sample:
    c = collectors.get(inst["framework"])
    if not c:
        return Sample()
    try:
        if not await c.probe(url):
            return Sample()
        return await c.collect(url)
    except Exception as e:
        log.debug("collect %s failed: %s", inst["framework"], e)
        return Sample()


if __name__ == "__main__":
    uvicorn.run(app, host=settings.agent_host, port=settings.agent_port)


class ModelsReq(BaseModel):
    host_dir: str                  # 宿主机模型根目录（/models 约定的宿主侧）


@app.post("/models/list")
async def models_list(req: ModelsReq) -> dict:
    return {"models": await netinfo.list_models(req.host_dir)}


class ModelDlReq(BaseModel):
    repo: str                      # modelscope 仓库名（如 Qwen/Qwen3-8B）
    host_dir: str
    name: str                      # 落地目录名（=服务名约定）


@app.post("/models/download")
async def models_download(req: ModelDlReq) -> dict:
    """在线拉取模型（受理制：立即返回，进度进节点系统日志）。"""
    async def _bg() -> None:
        res = await netinfo.download_model(req.repo, req.host_dir, req.name,
                                           progress_cb=_push_event)
        await _push_event("OK" if res["ok"] else "ERR",
                          f"model ▸ 拉取 {req.repo} {'完成' if res['ok'] else '失败'}: "
                          + " / ".join(res["tail"][-2:])[:200])
    fire_bg(_bg())
    return {"accepted": req.repo, "note": "拉取中（进度见节点系统日志）"}
