"""Mock Agent：无 GPU 开发机演示用。

模拟 4 个异构节点（x86 L40S×2 + Jetson ARM + 边缘 ARM）的 Agent 心跳与
OpenAI 兼容服务（SSE 流式），直接写 Redis，让控制台/Router 全链路可跑。

用法：python scripts/mock_agent.py            # 起 4 mock 节点 + 心跳
      另开终端：python scripts/mock_agent.py --serve  # mock 推理端点(8001-8004)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import random
import socket
import time

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
# mock 推理端点注册到 Redis 的地址（开发机无 Docker DNS，默认 127.0.0.1 + 端口区分节点）
MOCK_URL_HOST = os.getenv("MOCK_URL_HOST", "127.0.0.1")

NODE_TTL = 15

MOCK_NODES = [
    {"node_id": "gpu-node-a", "hostname": "l40s-server-01", "arch": "x86_64", "gpu_name": "NVIDIA L40S", "framework": "vllm"},
    {"node_id": "gpu-node-b", "hostname": "l40s-server-02", "arch": "x86_64", "gpu_name": "NVIDIA L40S", "framework": "tgi"},
    {"node_id": "gpu-node-c", "hostname": "jetson-agx-01", "arch": "aarch64", "gpu_name": "Jetson AGX Orin", "framework": "trt-llm"},
    {"node_id": "gpu-node-d", "hostname": "edge-nano-01", "arch": "aarch64", "gpu_name": "Jetson Orin Nano", "framework": "ollama"},
]

MOCK_MODELS = ["qwen3-30b", "qwen2.5-coder-32b", "qwen2.5-vl-72b"]

PHASES = ["Kernels", "KV cache", "Sampling", "Prefill", "Decode"]


def inst_id(node_id: str, port: int) -> str:
    return f"{node_id}:{port}"  # 与 router/agent 约定一致：hs:inst:{node_id}:{port}


def heartbeat(r: redis.Redis, t: float) -> None:
    for i, n in enumerate(MOCK_NODES):
        nid = n["node_id"]
        model = MOCK_MODELS[i % len(MOCK_MODELS)]
        # 缓慢波动 + 节点 c(ARM) 周期性高峰
        wave = 0.5 + 0.5 * math.sin(t / 17 + i * 1.3)
        gpu = min(99.0, 20 + wave * 60 + (20 if nid == "gpu-node-c" and math.sin(t / 40) > 0.7 else 0))
        vram = 35 + wave * 40
        ttft = 180 + gpu * 6 + random.uniform(-30, 30)
        tps = max(50.0, 900 - gpu * 6 + random.uniform(-60, 60))
        key = f"hs:node:{nid}"
        r.hset(key, mapping={
            "hostname": n["hostname"], "arch": n["arch"], "gpu_name": n["gpu_name"],
            "frameworks": n["framework"], "status": "online",
            "last_seen": int(time.time()),
        })
        r.expire(key, NODE_TTL)
        iid = inst_id(nid, 8001 + i)
        ikey = f"hs:inst:{iid}"
        r.hset(ikey, mapping={
            "node": nid, "port": 8001 + i, "framework": n["framework"],
            "model": model, "status": "running",
            "url": f"http://{MOCK_URL_HOST}:{8001 + i}",
            "m_gpu_util": round(gpu, 1), "m_vram": round(vram, 1),
            "m_ttft": round(max(90.0, ttft), 0), "m_tps": round(tps, 1),
            "m_queue": round(max(0.0, (gpu - 70) / 10), 1),
        })
        r.expire(ikey, NODE_TTL)
        r.sadd(f"hs:model:{model}", iid)


def event_tick(r: redis.Redis) -> None:
    pool = [
        ("INFO", "心跳续约 {n} · 实例健康"),
        ("OK", "实例 {n} 完成健康探测"),
        ("INFO", "会话绑定 s-{k} → 实例 {n}"),
        ("WARN", "实例 {n} 队列饱和预警 queue={q}"),
        ("INFO", "配置热加载生效 · 规则表 v{q}"),
        ("OK", "模型 qwen3-30b 新实例注册 {n}"),
        ("ERROR", "实例 {n} 心跳超时(>{q}s)，摘除流量"),
    ]
    lvl, tpl = random.choice(pool)
    text = tpl.format(n=random.choice(MOCK_NODES)["node_id"],
                      k=f"{random.randint(1000, 9999)}", q=random.randint(2, 8))
    ev = {"level": lvl, "time": time.strftime("%H:%M:%S"), "text": text}
    r.rpush("hs:events", json.dumps(ev, ensure_ascii=False))
    r.ltrim("hs:events", -64, -1)


async def heartbeat_loop() -> None:
    r = redis.from_url(REDIS_URL, decode_responses=True)
    print(f"[mock] 心跳循环启动 → {REDIS_URL}")
    t0 = time.time()
    tick = 0
    while True:
        heartbeat(r, time.time() - t0)
        tick += 1
        if tick % 4 == 0:
            event_tick(r)
        await asyncio.sleep(3.0)


# ---------- mock 推理端点（SSE 流式 + /metrics 模拟） ----------

async def run_mock_server(port: int, node: dict, model: str) -> None:
    from aiohttp import web

    async def chat(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
        })
        await resp.prepare(request)
        for w in ["衡枢", "流式", "路由", "正常", "✅"]:
            await resp.write(f"data: {json.dumps({'choices': [{'delta': {'content': w}}]}, ensure_ascii=False)}\n\n".encode())
            await asyncio.sleep(0.08)
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp

    async def metrics(request: web.Request) -> web.Response:
        return web.Response(text="mock metrics")

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat)
    app.router.add_get("/metrics", metrics)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    print(f"[mock] {node['node_id']}({node['arch']}) {model} 推理端点 :{port}")


async def main(serve: bool) -> None:
    tasks = [asyncio.create_task(heartbeat_loop())]
    if serve:
        import aiohttp  # noqa: F401  延迟导入，纯心跳模式无需
        for i, n in enumerate(MOCK_NODES):
            tasks.append(asyncio.create_task(
                run_mock_server(8001 + i, n, MOCK_MODELS[i % len(MOCK_MODELS)])))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--serve", action="store_true", help="同时起 mock 推理端点(8001-8004)")
    args = p.parse_args()
    try:
        asyncio.run(main(args.serve))
    except KeyboardInterrupt:
        print("\n[mock] 退出")
