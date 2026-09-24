"""规则路由解析单测：X-Type/body.type → hs:rules 映射改写目标模型。"""
from __future__ import annotations

import asyncio

import fakeredis.aioredis

from app import main


def test_resolve_model_by_rule():
    async def run():
        fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
        main._redis = fake
        await fake.hset("hs:rules", "code", "Qwen3-Coder-30B")
        assert await main.resolve_model("Qwen3-8B", "code") == "Qwen3-Coder-30B"
    asyncio.run(run())


def test_resolve_model_miss_falls_back():
    async def run():
        fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
        main._redis = fake
        assert await main.resolve_model("Qwen3-8B", "unknown-type") == "Qwen3-8B"
        assert await main.resolve_model("Qwen3-8B", None) == "Qwen3-8B"
    asyncio.run(run())
