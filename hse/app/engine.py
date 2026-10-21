"""HSE 决策管线：规则加载（Redis 热更）→ 能力位过滤 → 同步信号 → 语义求值 → 决策。

PRO 增量：
- semantic 例句 KNN（稠密 cosine×0.8 + 稀疏 bigram 重合×0.2，阈值三级解析）
- α 激进度（hs:hse:config alpha，偏移语义阈值）
- 影子模式（rule.shadow：只记录不执行）
- route-replay（replay 能力位：决策全量写 hs:hse:replay）
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from .config import settings
from .embedding import EmbedClient, cosine
from .judge import JudgeClient
from .signals import EVALUATORS, SIGNAL_CAPS, ReqCtx

_SPLIT_RE = re.compile(r"[\s，。！？、；：,.!?;:'\"（）()\[\]{}<>《》]+")


def _bigrams(text: str) -> set[str]:
    out: set[str] = set()
    for p in (x for x in _SPLIT_RE.split(text) if x):
        if len(p) == 1:
            out.add(p)
        else:
            out.update(p[i:i + 2] for i in range(len(p) - 1))
    return out


def _sparse_score(prompt: str, example: str) -> float:
    """稀疏重合：例句 bigram 在提问中的覆盖率（0..1）。"""
    eb = _bigrams(example.lower())
    if not eb:
        return 0.0
    pb = _bigrams(prompt.lower())
    return len(eb & pb) / len(eb)


class Engine:
    def __init__(self, redis) -> None:
        self.r = redis
        self._rules: list[dict] = []
        self._rules_at = 0.0
        self._cfg_at = 0.0
        self._cfg: dict[str, Any] = {}
        self.embedder = EmbedClient("", "bge-m3")
        self.judge = JudgeClient()

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
            except Exception:
                pass  # Redis 抖动时用旧缓存
        return self._rules

    async def config(self) -> dict[str, Any]:
        now = time.time()
        if now - self._cfg_at > 5.0:
            try:
                h = await self.r.hgetall("hs:hse:config")
                self._cfg = h or {}
                url = self._cfg.get("embedding_url", "")
                model = self._cfg.get("embedding_model", "bge-m3")
                if url != self.embedder.url or model != self.embedder.model:
                    self.embedder.configure(url, model)
                self.judge.configure(self._cfg.get("judge_url", ""),
                                     self._cfg.get("judge_model", ""))
            except Exception:
                pass
            self._cfg_at = now
        return self._cfg

    def _threshold(self, sem: dict, target: str, modelcfg: dict, alpha: float) -> float:
        """阈值三级解析：规则阈值 > 模型阈值(modelcfg) > 默认 0.62；α 偏移 ±0.1。"""
        base = sem.get("threshold")
        if base is None:
            base = (modelcfg.get(target) or {}).get("threshold") or 0.62
        eff = float(base) + (0.5 - alpha) * 0.2
        return max(0.30, min(0.95, eff))

    async def _semantic_score(self, sem: dict, ctx: ReqCtx) -> tuple[float | None, str]:
        """稠密×0.8 + 稀疏×0.2；embedding 不可用 → (None, 'embed_down')。"""
        examples = [e for e in (sem.get("examples") or []) if e.strip()][:8]
        if not examples:
            return None, ""
        pv = await self.embedder.embed(ctx.prompt)
        if pv is None:
            return None, "embed_down"
        best = 0.0
        best_ex = ""
        for ex in examples:
            ev = await self.embedder.embed(ex)
            if ev is None:
                continue
            dense = cosine(pv, ev)
            score = dense * 0.8 + _sparse_score(ctx.prompt, ex) * 0.2
            if score > best:
                best, best_ex = score, ex
        return best, best_ex[:30]

    async def _replay(self, ctx: ReqCtx, out: dict, shadow: bool = False) -> None:
        try:
            dec = out.get("decision") or {}
            item = json.dumps({
                "ts": round(time.time(), 3),
                "prompt_head": ctx.prompt[:80],
                "prompt_hash": hash(ctx.prompt) & 0xFFFFFFFF,
                "matched_rule": out.get("matched_rule"),
                "rule_name": out.get("rule_name", ""),
                "target": dec.get("model") or dec.get("tier"),
                "confidence": out.get("confidence"),
                "score": out.get("score"),
                "signals": out.get("signals", []),
                "shadow": shadow,
                "latency_ms": out.get("latency_ms"),
            }, ensure_ascii=False)
            pipe = self.r.pipeline()
            pipe.rpush("hs:hse:replay", item)
            pipe.ltrim("hs:hse:replay", -500, -1)
            await pipe.execute()
        except Exception:
            pass

    async def decide(self, ctx: ReqCtx, caps: list[str]) -> dict[str, Any]:
        t0 = time.perf_counter()
        rules = await self.rules()
        cfg = await self.config()
        alpha = float(cfg.get("alpha", 0.5) or 0.5)
        # 模型成本/阈值配置
        try:
            raw_cfg = await self.r.hgetall("hs:hse:modelcfg")
            costs = {m: float(json.loads(c).get("cost", 0)) for m, c in raw_cfg.items()}
            modelcfg = {m: json.loads(c) for m, c in raw_cfg.items()}
        except Exception:
            costs, modelcfg = {}, {}

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
            fired: list[str] = []
            ok = True
            for _sig_name, ev in EVALUATORS.items():
                passed, note = ev(when, ctx)
                if not passed:
                    ok = False
                    break
                if note:
                    fired.append(note)
            if not ok:
                continue
            # 语义条件（PRO）：异步求值，embedding 不可用则规则跳过
            score: float | None = None
            conf = 1.0
            sem = when.get("semantic")
            if sem:
                action0 = rule.get("action") or {}
                thr = self._threshold(sem, str(action0.get("model") or action0.get("tier") or ""),
                                      modelcfg, alpha)
                score, _ex = await self._semantic_score(sem, ctx)
                if score is None:
                    skipped.append(f"{rule.get('name')}(embed_down)")
                    continue
                if score < thr:
                    continue
                conf = round(score, 4)
                fired.append(f"sem:{conf:.2f}>={thr:.2f}")
            # 裁判条件（ENT）：最贵的信号放最后；裁判不可用/回答不清 → 规则跳过
            jcfg = when.get("judge")
            if jcfg is not None:
                verdict, raw = await self.judge.ask(
                    jcfg.get("question") or "该请求是否需要强推理/代码能力的大模型来回答？",
                    ctx.prompt)
                if verdict is None:
                    skipped.append(f"{rule.get('name')}({raw})")
                    continue
                if not verdict:
                    continue
                conf = max(conf, 0.85)
                fired.append("judge:YES")
            action = rule.get("action") or {}
            prio = int(rule.get("priority", 0))
            target = action.get("model") or action.get("tier") or ""
            cost = costs.get(str(target), 0.0)
            # 先比优先级；同优先级比置信度；再同比成本（小者优先）
            key = (prio, conf, -cost)
            if best is None or key > best["key"]:
                best = {"key": key, "rule": rule, "action": action,
                        "signals": fired, "cost": cost, "conf": conf,
                        "score": score}
        latency = round((time.perf_counter() - t0) * 1000, 2)

        if best is not None:
            rule = best["rule"]
            action = best["action"]
            out = {
                "decision": {"model": action.get("model") or None,
                             "tier": action.get("tier") or None},
                "confidence": best["conf"],
                "matched_rule": rule.get("id"),
                "rule_name": rule.get("name", ""),
                "signals": best["signals"],
                "score": best["score"],
                "cost": best["cost"],
                "skipped_unlicensed": skipped,
                "latency_ms": latency,
            }
            if rule.get("shadow"):
                # 影子模式：只记录不执行
                out["shadow_hit"] = True
                out["decision"] = {"model": None, "tier": None}
                if "replay" in caps:
                    await self._replay(ctx, out, shadow=True)
                return out
            if "replay" in caps:
                await self._replay(ctx, out)
            return out

        out = {
            "decision": {"model": None, "tier": None},
            "confidence": 0.0,
            "matched_rule": None,
            "rule_name": "",
            "signals": [],
            "skipped_unlicensed": skipped,
            "latency_ms": latency,
        }
        if "replay" in caps:
            await self._replay(ctx, out)
        return out
