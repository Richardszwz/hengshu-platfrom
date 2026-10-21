"""HSE 决策管线：规则加载（Redis 热更）→ 能力位过滤 → 逐优先级求值 → 决策。"""
from __future__ import annotations

import json
import time
from typing import Any

from .config import settings
from .signals import EVALUATORS, SIGNAL_CAPS, ReqCtx


class Engine:
    def __init__(self, redis) -> None:
        self.r = redis
        self._rules: list[dict] = []
        self._rules_at = 0.0

    async def rules(self) -> list[dict]:
        now = time.time()
        if now - self._rules_at > settings.rules_cache_ttl:
            try:
                raw = await self.r.lrange(settings.rules_key, 0, -1)
                rules = []
                for line in raw:
                    try:
                        rules.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
                # 优先级大者先；同优先级按录入序
                rules.sort(key=lambda d: int(d.get("priority", 0)), reverse=True)
                self._rules = rules
                self._rules_at = now
            except Exception:  # noqa: BLE001
                pass  # Redis 抖动时用旧缓存
        return self._rules

    async def decide(self, ctx: ReqCtx, caps: list[str]) -> dict[str, Any]:
        t0 = time.perf_counter()
        rules = await self.rules()
        skipped: list[str] = []
        for rule in rules:
            if not rule.get("enabled", True):
                continue
            when = rule.get("when") or {}
            # 能力位检查：规则用到的信号须全部授权
            need = {SIGNAL_CAPS[k] for k in when if k in SIGNAL_CAPS}
            if not need.issubset(set(caps)):
                skipped.append(rule.get("name") or rule.get("id", "?"))
                continue
            notes: list[str] = []
            fired: list[str] = []
            ok = True
            for sig_name, ev in EVALUATORS.items():
                passed, note = ev(when, ctx)
                if not passed:
                    ok = False
                    break
                if note:
                    fired.append(note)
                    notes.append(note)
            if ok:
                action = rule.get("action") or {}
                return {
                    "decision": {"model": action.get("model") or None,
                                 "tier": action.get("tier") or None},
                    "confidence": 1.0,
                    "matched_rule": rule.get("id"),
                    "rule_name": rule.get("name", ""),
                    "signals": fired,
                    "skipped_unlicensed": skipped,
                    "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
                }
        return {
            "decision": {"model": None, "tier": None},
            "confidence": 0.0,
            "matched_rule": None,
            "rule_name": "",
            "signals": [],
            "skipped_unlicensed": skipped,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }
