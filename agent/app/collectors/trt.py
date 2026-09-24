"""TRT-LLM 指标插件：triton_metrics / trtllm_engine_metrics 双入口探测。"""
from __future__ import annotations

import re

import httpx

from .base import Sample


class TrtCollector:
    framework = "trt-llm"

    async def probe(self, base_url: str) -> bool:
        for path in ("/v2/health/ready", "/health"):
            try:
                async with httpx.AsyncClient(timeout=3.0) as c:
                    if (await c.get(f"{base_url}{path}")).status_code == 200:
                        return True
            except httpx.HTTPError:
                continue
        return False

    async def collect(self, base_url: str) -> Sample:
        async with httpx.AsyncClient(timeout=5.0) as c:
            text = (await c.get(f"{base_url}/metrics")).text
        def g(pat: str) -> float:
            m = re.search(pat, text)
            return float(m.group(1)) if m else 0.0
        return Sample(
            queue=g(r"nv_trt_llm_num_waiting_requests[^\n]*?(\d+)\n"),
            tokens_per_s=g(r"nv_trt_llm_generation_tokens_total[^\n]*?(\d+)\.?\d*\n"),
            ttft=g(r"nv_trt_llm_time_to_first_token[^\n]*?(\d+)\.?\d*\n"),
        )
