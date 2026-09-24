"""Agent 主程序：心跳 → Redis（节点 + 实例指标 TTL），暴露本地控制 API。

职责（P0 范围）：
- 周期心跳：节点信息 + 该节点全部实例的框架级指标（统一 m_ 口径）
- 控制 API：/services/start /stop /logs（控制台远程启停的接收端）
- 架构感知：GPU 主机指标 x86→pynvml / ARM(Jetson)→留接口降级
"""
from __future__ import annotations

import asyncio
import logging
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
from .service_ctl import ServiceCtl, StartRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("agent")

INSTANCES: list[dict] = [
    # 演示配置：P1 起由控制台下发（模型/框架/端口/start_cmd）
    {"model": "Qwen3-30B-A3B", "framework": "vllm",    "port": 8100},
    {"model": "Qwen3-8B",      "framework": "vllm",    "port": 8101},
    {"model": "Llama-3.3-70B", "framework": "tgi",     "port": 8102},
    {"model": "Qwen3-4B",      "framework": "trt-llm", "port": 8103},
]


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
    asyncio.create_task(heartbeat_loop())


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "node": settings.node_id, "arch": __import__("platform").machine()}


@app.post("/services/start")
async def start(req: StartRequest) -> dict:
    return {"result": await ctl.start(req)}


@app.post("/services/stop/{name}")
async def stop(name: str) -> dict:
    return {"result": await ctl.stop(name)}


@app.get("/services/{name}/logs")
async def logs(name: str, tail: int = 100) -> dict:
    return {"lines": await ctl.logs(name, tail)}


async def heartbeat_loop() -> None:
    r = redis()
    ttl = settings.heartbeat_interval * 5
    node_key = f"hs:node:{settings.node_id}"
    while True:
        try:
            sysm = gpu_sample()
            base = {
                "hostname": socket.gethostname(),
                "arch": __import__("platform").machine(),
                "m_temp": str(sysm.get("temp", 0.0)),
                "m_power": str(sysm.get("power", 0.0)),
                "last_seen": str(int(time.time())),
            }
            pipe = r.pipeline()
            pipe.hset(node_key, mapping=base)
            pipe.expire(node_key, ttl)
            for inst in INSTANCES:
                port = inst["port"]
                iid = f"{settings.node_id}:{port}"
                url = f"http://{settings.node_id}:{port}"
                sample = await _collect(inst, url)
                merged = Sample(
                    gpu_util=sysm["gpu_util"] if sample.gpu_util == 0 else sample.gpu_util,
                    vram=sysm["vram"] if sample.vram == 0 else sample.vram,
                    queue=sample.queue, ttft=sample.ttft,
                    tokens_per_s=sample.tokens_per_s, sessions=sample.sessions,
                )
                pipe.hset(f"hs:inst:{iid}", mapping={
                    "model": inst["model"], "framework": inst["framework"],
                    "url": url, "status": "running",
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
