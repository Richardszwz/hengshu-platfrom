"""会话绑定与历史重放（方案书 §3.3 简化实现）。

- 同一 session_id 的后续请求优先路由到绑定实例（保 KV cache 前缀）。
- 绑定实例失联 → 从 Redis 读取历史消息随请求重放给新实例，上下文不丢。
- 会话 key 滑动过期；历史上限防止 Redis 膨胀。
"""
from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from .config import settings
from .keys import SESS_PREFIX, SESSHIST_PREFIX


class SessionStore:
    def __init__(self, redis: aioredis.Redis):
        self.r = redis

    async def get_binding(self, session_id: str) -> tuple[str, str] | None:
        """返回 (model, bound_inst_id)；无绑定返回 None。"""
        h = await self.r.hgetall(f"{SESS_PREFIX}{session_id}")
        if not h:
            return None
        await self.r.expire(f"{SESS_PREFIX}{session_id}", settings.session_ttl)
        await self.r.expire(f"{SESSHIST_PREFIX}{session_id}", settings.session_ttl)
        return h.get("model", ""), h.get("bound", "")

    async def bind(self, session_id: str, model: str, inst_id: str) -> None:
        key = f"{SESS_PREFIX}{session_id}"
        await self.r.hset(key, mapping={"model": model, "bound": inst_id})
        await self.r.expire(key, settings.session_ttl)

    async def append_history(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        key = f"{SESSHIST_PREFIX}{session_id}"
        async with self.r.pipeline() as p:
            for msg in messages:
                p.rpush(key, json.dumps(msg, ensure_ascii=False))
            p.ltrim(key, -settings.session_history_cap, -1)
            p.expire(key, settings.session_ttl)
            await p.execute()

    async def history(self, session_id: str) -> list[dict[str, Any]]:
        rows = await self.r.lrange(f"{SESSHIST_PREFIX}{session_id}", 0, -1)
        return [json.loads(x) for x in rows]
