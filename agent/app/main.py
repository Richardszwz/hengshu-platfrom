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
import socket
import time

import redis.asyncio as aioredis
import uvicorn
from fastapi import FastAPI

from .collectors.base import CollectorRegistry, Sample
from .collectors.gpu_sys import sample as gpu_sample, start as gpu_start
from .collectors.ollama import OllamaCollector
from .collectors.trt import TrtCollector
from .collectors.tgi import TgiCollector
from .collectors.vllm import VllmCollector
from .config import settings
from .service_ctl import ServiceCtl, StartRequest, is_running

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
    except Exception as e:  # noqa: BLE001
        log.warning("load services failed: %s", e)


def _save_services() -> None:
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        with open(_SERVICES_FILE, "w") as f:
            json.dump(INSTANCES, f, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
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
    assert _redis is not None, "redis not connected"
    return _redis


@app.on_event("startup")
async def _startup() -> None:
    global _redis
    _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    await gpu_start()  # pynvml 可用则用之，否则起 tegrastats 后台
    _load_services()   # 恢复控制台下发过的服务清单
    asyncio.create_task(heartbeat_loop())


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "node": settings.node_id, "arch": __import__("platform").machine()}


_INFLIGHT: set[str] = set()


async def _start_bg(req: StartRequest) -> None:
    """后台执行：拉镜像（可达数分钟）→ 起容器 → 登记实例。全程写事件流。"""
    await _push_event("INFO", f"svc ▸ {req.name} 开始执行：拉取镜像 {req.image}（大镜像需数分钟）")
    try:
        result = await ctl.start(req)
        inst = next((x for x in INSTANCES if x["model"] == req.name), None)
        spec = {"model": req.name,
                "framework": "vllm" if "vllm" in req.start_cmd.lower() else "custom",
                "port": req.port, "image": req.image, "start_cmd": req.start_cmd,
                "workdir": req.workdir, "env": req.env}
        if inst:
            inst.update(spec)
        else:
            INSTANCES.append(spec)
        _save_services()
        await _push_event("OK", f"svc ▸ {req.name} 启动完成: {result}")
    except Exception as e:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001 —— 事件上报失败不影响主流程
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

    asyncio.create_task(_wrap())
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

    asyncio.create_task(_kill())
    return {"stopping": True, "container": cname}


@app.get("/services/{name}/logs")
async def logs(name: str, tail: int = 100) -> dict:
    return {"lines": await ctl.logs(name, tail)}


async def heartbeat_loop() -> None:
    r = redis()
    ttl = settings.heartbeat_interval * 5
    node_key = f"hs:node:{settings.node_id}"
    tomb_key = f"hs:node_del:{settings.node_id}"
    while True:
        try:
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
                status = "running" if await is_running(inst["model"]) else "stopped"
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
                    **merged.as_metrics(),
                })
                pipe.expire(f"hs:inst:{iid}", ttl)
                pipe.sadd(f"hs:model:{inst['model']}", iid)
            await pipe.execute()
        except Exception as e:  # noqa: BLE001
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
    except Exception as e:  # noqa: BLE001
        log.debug("collect %s failed: %s", inst["framework"], e)
        return Sample()


if __name__ == "__main__":
    uvicorn.run(app, host=settings.agent_host, port=settings.agent_port)
