"""Ollama 指标插件：/api/ps 给出运行中模型与显存占用。"""
from __future__ import annotations

import httpx

from .base import Sample


class OllamaCollector:
    framework = "ollama"

    async def probe(self, base_url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                return (await c.get(f"{base_url}/api/tags")).status_code == 200
        except httpx.HTTPError:
            return False

    async def collect(self, base_url: str) -> Sample:
        async with httpx.AsyncClient(timeout=5.0) as c:
            data = (await c.get(f"{base_url}/api/ps")).json()
        models = data.get("models", [])
        return Sample(
            vram=0.0,
            sessions=len(models),
            tokens_per_s=0.0,
        ) if not models else Sample(
            sessions=len(models),
            tokens_per_s=0.0,
        )
