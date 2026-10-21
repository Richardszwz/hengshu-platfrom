"""ENT 裁判层：小 LLM 对请求做 YES/NO 判定（OpenAI 兼容 chat 端点 + LRU 缓存）。

免训练（NVIDIA Blueprint intent 同款思路）：裁判问题可配，模型只答 YES/NO。
裁判服务不可用 → None（judge 规则整体跳过，诚实降级）。
默认建议小模型（Qwen3-1.7B/4B 级），调用耗时 100ms~1s，结果缓存 10min。
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import OrderedDict

import httpx


class JudgeClient:
    def __init__(self, url: str = "", model: str = "",
                 timeout: float = 20.0, cache_size: int = 1024) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._cache: OrderedDict[str, tuple[bool, float]] = OrderedDict()
        self._cache_size = cache_size
        self._lock = asyncio.Lock()

    def configure(self, url: str, model: str) -> None:
        if url.rstrip("/") != self.url or model != self.model:
            self.url = url.rstrip("/")
            self.model = model
            self._cache.clear()

    @property
    def available(self) -> bool:
        return bool(self.url and self.model)

    async def ask(self, question: str, prompt: str) -> tuple[bool | None, str]:
        """→ (True/False/None, 原始回答)。None=不可用或回答无法解析。"""
        if not self.available:
            return None, "judge_down"
        key = f"{question}:{hash(prompt)}"
        async with self._lock:
            hit = self._cache.get(key)
            if hit is not None and time.time() - hit[1] < 600:
                self._cache.move_to_end(key)
                return hit[0], "cached"
        q = (f"{question}\n\n用户请求：\n{prompt[:2000]}\n\n"
             "只允许回答一个词：YES 或 NO。\n/no_think")  # Qwen3 系关思考直答；他系模型视作普通文本无害
        try:
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as c:
                d = (await c.post(f"{self.url}/v1/chat/completions",
                                  json={"model": self.model, "temperature": 0,
                                        "max_tokens": 512,
                                        "messages": [{"role": "user", "content": q}]})).json()
            ans = (d["choices"][0]["message"]["content"] or "").strip()
        except Exception:  # noqa: BLE001
            return None, "judge_down"
        # 推理模型加固：剥 <think> 思考块（含被 max_tokens 截断的未闭合块）
        ans = re.sub(r"<think>.*?</think>", " ", ans, flags=re.S | re.I)
        ans = re.sub(r"<think>.*$", " ", ans, flags=re.S | re.I).strip()
        verdict: bool | None
        up = ans.upper()
        # 优先看结尾（推理模型结论在最后），再看开头，最后全文找
        tail = up[-24:]
        if "YES" in tail or "是" in tail:
            verdict = True
        elif "NO" in tail or "否" in tail:
            verdict = False
        elif up.startswith("YES") or up.startswith("是"):
            verdict = True
        elif up.startswith("NO") or up.startswith("否"):
            verdict = False
        elif "YES" in up:
            verdict = True
        elif "NO" in up:
            verdict = False
        else:
            return None, f"judge_unclear:{ans[:24]}"
        async with self._lock:
            self._cache[key] = (verdict, time.time())
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return verdict, ans[:20]
