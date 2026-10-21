"""PRO 语义层：OpenAI 兼容 embedding 客户端 + LRU 缓存。

embedding 服务由平台模型服务提供（vllm serve bge-m3 --task embed），
引擎只发 HTTP——不绑推理库，气隙随模型分发链路走。
服务不可达 → 返回 None（语义规则整体跳过，诚实降级）。
"""
from __future__ import annotations

import asyncio
import math
import time
from collections import OrderedDict

import httpx


class EmbedClient:
    def __init__(self, url: str, model: str = "bge-m3",
                 timeout: float = 8.0, cache_size: int = 2048) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._cache: OrderedDict[str, tuple[list[float], float]] = OrderedDict()
        self._cache_size = cache_size
        self._lock = asyncio.Lock()

    def configure(self, url: str, model: str) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self._cache.clear()

    @property
    def available(self) -> bool:
        return bool(self.url)

    async def embed(self, text: str) -> list[float] | None:
        if not self.url:
            return None
        key = f"{self.model}:{hash(text)}"
        async with self._lock:
            hit = self._cache.get(key)
            if hit is not None and time.time() - hit[1] < 600:
                self._cache.move_to_end(key)
                return hit[0]
        try:
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as c:
                d = (await c.post(f"{self.url}/v1/embeddings",
                                  json={"model": self.model, "input": text})).json()
            vec = d["data"][0]["embedding"]
        except Exception:
            return None
        async with self._lock:
            self._cache[key] = (vec, time.time())
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return vec


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb + 1e-12)
