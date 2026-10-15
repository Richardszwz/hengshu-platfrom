"""vLLM 指标插件：GET /metrics（Prometheus 文本）解析核心指标。

口径：vLLM 暴露的是**累计 counter**，直接上报会无限增长。本插件保存上一采样点，
按时间差分换算成**速率 TPS**（tok/s）与**窗口平均 TTFT**（ms）；首样本/计数器回退报 0。
"""
from __future__ import annotations

import re
import time

import httpx

from .base import Sample

_FRAMEWORK = "vllm"

# base_url → (ts, gen_tokens_total, ttft_sum_s, ttft_count)
_PREV: dict[str, tuple[float, float, float, float]] = {}


def _gauge(text: str, name: str) -> float:
    """带标签的 gauge/counter 当前值（多 engine 取最后一行）。"""
    val = 0.0
    for m in re.finditer(rf"^{re.escape(name)}\{{[^}}]*\}}\s+([\d.eE+\-]+)\s*$", text, re.M):
        val = float(m.group(1))
    return val


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

        running = _gauge(text, "vllm:num_requests_running")
        waiting = _gauge(text, "vllm:num_requests_waiting")
        gen_total = _gauge(text, "vllm:generation_tokens_total")
        ttft_sum = _gauge(text, "vllm:time_to_first_token_seconds_sum")
        ttft_cnt = _gauge(text, "vllm:time_to_first_token_seconds_count")

        now = time.perf_counter()  # 进程内差分用 perf_counter（monotonic 在部分 Windows 环境被冻结）
        tps = 0.0
        # TTFT：累计平均（sum/count）——窗口制在空闲期会归零，驾驶舱体验差
        ttft_ms = (ttft_sum / ttft_cnt * 1000.0) if ttft_cnt > 0 else 0.0
        prev = _PREV.get(base_url)
        if prev is not None:
            dt = now - prev[0]
            if dt > 0:
                tps = max(gen_total - prev[1], 0.0) / dt
        _PREV[base_url] = (now, gen_total, ttft_sum, ttft_cnt)

        return Sample(
            queue=waiting,
            tokens_per_s=round(tps, 1),
            sessions=int(running),
            ttft=round(ttft_ms, 1),
        )
