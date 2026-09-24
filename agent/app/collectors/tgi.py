"""TGI 指标插件：GET /metrics 解析（tgi_ 前缀序列）。"""
from __future__ import annotations

import re

import httpx

from .base import Sample


class TgiCollector:
    framework = "tgi"

    async def probe(self, base_url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(f"{base_url}/health")
                return r.status_code in (200, 404)   # 老版本 TGI 无 /health
        except httpx.HTTPError:
            return False

    async def collect(self, base_url: str) -> Sample:
        async with httpx.AsyncClient(timeout=5.0) as c:
            text = (await c.get(f"{base_url}/metrics")).text
        def g(pat: str) -> float:
            m = re.search(pat, text)
            return float(m.group(1)) if m else 0.0
        return Sample(
            queue=g(r"tgi_batch_inference_count[^\n]*?(\d+)\n"),
            tokens_per_s=g(r"tgi_tokens_generated_total[^\n]*?(\d+)\.?\d*\n"),
            ttft=g(r"tgi_time_to_first_token[^\n]*?(\d+)\.?\d*\n") * 1000.0,
            sessions=int(g(r"tgi_batch_current_size[^\n]*?(\d+)\n")),
        )
