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
        # 模型成本配置（同优先级命中时成本小者优先；model_thresholds 为 PRO 语义预留）
        try:
            raw_cfg = await self.r.hgetall("hs:hse:modelcfg")
            costs = {m: float(json.loads(c).get("cost", 0)) for m, c in raw_cfg.items()}
        except Exception:  # noqa: BLE001
            costs = {}
        skipped: list[str] = []
        best: dict | None = None
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
            if not ok:
                continue
            action = rule.get("action") or {}
            prio = int(rule.get("priority", 0))
            target = action.get("model") or action.get("tier") or ""
            cost = costs.get(str(target), 0.0)
            # 先比优先级；同优先级比成本（小者优先）
            key = (prio, -cost)
            if best is None or key > best["key"]:
                best = {"key": key, "rule": rule, "action": action,
                        "signals": fired, "cost": cost}
        latency = round((time.perf_counter() - t0) * 1000, 2)
        if best is not None:
            action = best["action"]
            return {
                "decision": {"model": action.get("model") or None,
                             "tier": action.get("tier") or None},
                "confidence": 1.0,
                "matched_rule": best["rule"].get("id"),
                "rule_name": best["rule"].get("name", ""),
                "signals": best["signals"],
                "cost": best["cost"],
                "skipped_unlicensed": skipped,
                "latency_ms": latency,
            }
        return {
            "decision": {"model": None, "tier": None},
            "confidence": 0.0,
            "matched_rule": None,
            "rule_name": "",
            "signals": [],
            "skipped_unlicensed": skipped,
            "latency_ms": latency,
        }
