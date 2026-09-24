"""FastAPI 共享依赖：Redis 连接。"""
from __future__ import annotations

import redis.asyncio as aioredis

_redis: aioredis.Redis | None = None


def init(redis_url: str) -> None:
    global _redis
    _redis = aioredis.from_url(redis_url, decode_responses=True)


def get() -> aioredis.Redis:
    assert _redis is not None, "redis not initialized"
    return _redis


async def close() -> None:
    if _redis is not None:
        await _redis.aclose()
