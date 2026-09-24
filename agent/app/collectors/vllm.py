"""vLLM 指标插件：GET /metrics（Prometheus 文本）解析核心指标。"""
from __future__ import annotations

import re

import httpx

from .base import Sample

_FRAMEWORK = "vllm"


def _extract(text: str, pattern: str) -> float:
    m = re.search(pattern, text)
    return float(m.group(1)) if m else 0.0


class VllmCollector:
    framework = _FRAMEWORK

    async def probe(self, base_url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(f"{base_url}/health")
                return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def collect(self, base_url: str) -> Sample:
        async with httpx.AsyncClient(timeout=5.0) as c:
            text = (await c.get(f"{base_url}/metrics")).text
        # vLLM 0.8.x 口径：prompt/generation throughput、running/waiting reqs
        tps = _extract(text, r"vllm:generation_tokens_total[^\n]*?(\d+)\.?\d*\n")
        running = _extract(text, r"vllm:num_requests_running[^\n]*?(\d+)\n")
        waiting = _extract(text, r"vllm:num_requests_waiting[^\n]*?(\d+)\n")
        return Sample(
            queue=waiting,
            tokens_per_s=tps,
            sessions=int(running),
            ttft=0.0,   # TTFT 由 Router 侧实测回填更准；P2 打通 histogram
        )
