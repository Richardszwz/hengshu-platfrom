"""HSE 信号与决策管线测试。"""
import asyncio
import json
import sys
from pathlib import Path

import fakeredis.aioredis

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from hse.app.engine import Engine
from hse.app.signals import ReqCtx, eval_context, eval_keyword, eval_structure


def ctx(prompt="帮我写个 python 快排", **kw):
    base = {"prompt": prompt, "all_text": prompt, "ctx_tokens": 50, "hints": {}}
    base.update(kw)
    return ReqCtx(**base)


class TestSignals:
    def test_keyword_hit_last_user(self):
        ok, note = eval_keyword({"keywords": ["python"]}, ctx())
        assert ok and note == "kw:python"

    def test_keyword_miss(self):
        ok, _ = eval_keyword({"keywords": ["java"]}, ctx())
        assert not ok

    def test_keyword_scope_all(self):
        c = ctx(prompt="继续", all_text="之前聊过 python\n继续")
        ok, _ = eval_keyword({"keywords": ["python"], "keyword_scope": "all"}, c)
        assert ok
        ok2, _ = eval_keyword({"keywords": ["python"], "keyword_scope": "last_user"}, c)
        assert not ok2

    def test_context_range(self):
        ok, _ = eval_context({"ctx_min": 10, "ctx_max": 100}, ctx())
        assert ok
        ok2, _ = eval_context({"ctx_min": 100}, ctx())
        assert not ok2

    def test_structure_code_block(self):
        ok, _ = eval_structure({"has_code_block": True}, ctx(prompt="看这个 ```python\nprint(1)```"))
        assert ok
        ok2, _ = eval_structure({"has_code_block": True}, ctx(prompt="没有代码"))
        assert not ok2

    def test_structure_lang(self):
        ok, _ = eval_structure({"lang": "zh"}, ctx(prompt="你好世界你好世界"))
        assert ok
        ok2, _ = eval_structure({"lang": "zh"}, ctx(prompt="hello world this is a long sentence"))
        assert not ok2


def _run(coro):
    return asyncio.run(coro)


class TestEngine:
    def _engine(self, rules):
        async def make():
            r = fakeredis.aioredis.FakeRedis(decode_responses=True)
            for doc in rules:
                await r.rpush("hs:hse:rules", json.dumps(doc, ensure_ascii=False))
            return Engine(r)
        return _run(make())

    def _rule(self, name, prio, when, model):
        return {"id": name, "name": name, "enabled": True, "priority": prio,
                "when": when, "action": {"model": model}}

    def test_priority_first_match(self):
        e = self._engine([
            self._rule("低优", 10, {"keywords": ["python"]}, "M-low"),
            self._rule("高优", 100, {"keywords": ["python"]}, "M-high"),
        ])
        out = _run(e.decide(ctx(), ["keyword", "context", "structure"]))
        assert out["decision"]["model"] == "M-high" and out["matched_rule"] == "高优"

    def test_miss_returns_null(self):
        e = self._engine([self._rule("r", 1, {"keywords": ["java"]}, "M")])
        out = _run(e.decide(ctx(), ["keyword"]))
        assert out["matched_rule"] is None and out["decision"]["model"] is None

    def test_unlicensed_signal_skips_rule(self):
        e = self._engine([
            self._rule("需context", 100, {"keywords": ["python"], "ctx_min": 10}, "M1"),
            self._rule("纯keyword", 10, {"keywords": ["python"]}, "M2"),
        ])
        # 只授权 keyword：带 ctx 条件的规则应被跳过，落到纯 keyword 规则
        out = _run(e.decide(ctx(), ["keyword"]))
        assert out["decision"]["model"] == "M2"
        assert "需context" in out["skipped_unlicensed"]

    def test_disabled_rule_ignored(self):
        e = self._engine([
            {"id": "off", "name": "off", "enabled": False, "priority": 999,
             "when": {"keywords": ["python"]}, "action": {"model": "M"}},
        ])
        out = _run(e.decide(ctx(), ["keyword"]))
        assert out["matched_rule"] is None

    def test_tier_action(self):
        e = self._engine([
            {"id": "t", "name": "t", "enabled": True, "priority": 1,
             "when": {"ctx_min": 10}, "action": {"tier": "strong"}},
        ])
        out = _run(e.decide(ctx(), ["context"]))
        assert out["decision"]["tier"] == "strong" and out["decision"]["model"] is None

    def test_cost_tiebreak_same_priority(self):
        async def make():
            import fakeredis.aioredis as fr
            r = fr.FakeRedis(decode_responses=True)
            for doc in [
                {"id": "a", "name": "去贵模型", "enabled": True, "priority": 100,
                 "when": {"keywords": ["python"]}, "action": {"model": "M-expensive"}},
                {"id": "b", "name": "去便宜模型", "enabled": True, "priority": 100,
                 "when": {"keywords": ["python"]}, "action": {"model": "M-cheap"}},
            ]:
                await r.rpush("hs:hse:rules", json.dumps(doc, ensure_ascii=False))
            await r.hset("hs:hse:modelcfg", "M-expensive", json.dumps({"cost": 1.0}))
            await r.hset("hs:hse:modelcfg", "M-cheap", json.dumps({"cost": 0.2}))
            return Engine(r)
        e = _run(make())
        out = _run(e.decide(ctx(), ["keyword"]))
        # 同优先级同命中 → 成本小者胜
        assert out["decision"]["model"] == "M-cheap" and out["cost"] == 0.2
