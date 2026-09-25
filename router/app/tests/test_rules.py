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


def test_tenant_auth_and_rate_limit() -> None:
    """租户 key：hs:tokens 反查 → 限额校验 → 滑动窗口 429；token 计量落 hs:tenant:*.tokens_total。"""
    async def run():
        import time as _t
        fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
        main._redis = fake
        await fake.hset("hs:tokens", "sk-tenant-abc", "t1")
        await fake.hset("hs:tenant:t1", mapping={
            "name": "团队甲", "qps_limit": "2", "tokens_total": "0"})

        # 认证通过 + 限额读取
        ctx = await main.auth_tenant({"token": "sk-tenant-abc", "checked": False})
        assert ctx["tid"] == "t1" and ctx["qps_limit"] == 2
        # 未知 key → 401
        try:
            await main.auth_tenant({"token": "sk-wrong", "checked": False})
            assert False, "should 401"
        except main.HTTPException as e:
            assert e.status_code == 401
        # 滑动窗口：limit=2 → 第 3 次超限
        assert await main._rate_ok("t1", 2) is True
        assert await main._rate_ok("t1", 2) is True
        assert await main._rate_ok("t1", 2) is False
        # 计量
        await main._record_usage("t1", {"prompt_tokens": 11, "completion_tokens": 22})
        assert int(await fake.hget("hs:tenant:t1", "tokens_total")) == 33
        await main._record_usage("", {"prompt_tokens": 5})  # admin 通道不落计量
        assert int(await fake.hget("hs:tenant:t1", "tokens_total")) == 33
        # 1 秒窗过期后放行（把时间戳往前拨）
        await fake.delete("hs:rl:t1")
        assert await main._rate_ok("t1", 2) is True
    asyncio.run(run())
