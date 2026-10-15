"""模型实例注册表：从 Redis 读取活实例并提供候选过滤。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis

from .config import settings
from .keys import INST_PREFIX, MODEL_PREFIX


@dataclass
class Instance:
    node_id: str
    port: int
    model: str
    framework: str
    url: str                       # http://host:port
    status: str = "running"        # running | starting | stopped
    metrics: dict[str, Any] = field(default_factory=dict)
    served_model: str = ""         # 上游真实 served-model-name（转发时改写 body.model）

    @property
    def inst_id(self) -> str:
        return f"{self.node_id}:{self.port}"

    @property
    def alive(self) -> bool:
        return self.status == "running"


class Registry:
    def __init__(self, redis: aioredis.Redis):
        self.r = redis

    async def candidates(self, model: str) -> list[Instance]:
        """某模型的全部活实例（有心跳即活，TTL 由 Agent 续期）。"""
        members = await self.r.smembers(f"{MODEL_PREFIX}{model}")
        if not members:
            return []
        pipe = self.r.pipeline()
        for m in members:
            pipe.hgetall(f"{INST_PREFIX}{m}")
        rows = await pipe.execute()
        out: list[Instance] = []
        for m, h in zip(members, rows):
            if not h:                       # key 过期 = 实例失联
                continue
            node_id, _, port = m.partition(":")
            out.append(Instance(
                node_id=node_id,
                port=int(port),
                model=h.get("model", model),
                framework=h.get("framework", ""),
                url=h.get("url", f"http://{node_id}:{port}"),
                status=h.get("status", "running"),
                served_model=h.get("served_model", ""),
                metrics={k: _num(v) for k, v in h.items()
                         if k.startswith("m_")},
            ))
        return [i for i in out if i.alive]

    async def all_models(self) -> list[str]:
        keys = await self.r.keys(f"{MODEL_PREFIX}*")
        return sorted(k.removeprefix(MODEL_PREFIX) for k in keys)


def _num(v: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
