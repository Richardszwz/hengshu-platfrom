"""PRO 语义层测试：例句 KNN / α 阈值 / 影子模式 / replay（embedding 客户端打桩）。"""
import asyncio
import json
import sys
from pathlib import Path

import fakeredis.aioredis

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from hse.app.engine import Engine, _sparse_score
from hse.app.signals import ReqCtx


def _run(coro):
    return asyncio.run(coro)


def ctx(prompt="这个函数报错了帮我看看", **kw):
    base = {"prompt": prompt, "all_text": prompt, "ctx_tokens": 50, "hints": {}}
    base.update(kw)
    return ReqCtx(**base)


class _FakeEmbedder:
    """固定向量：prompt 与例句的相似度由词重合决定（够用于阈值判定）。"""
    url = "fake"
    model = "fake"

    def __init__(self):
        self.calls = 0

    async def embed(self, text):
        self.calls += 1
        # 用字集合构造伪向量（两维：含"代码/报错"与否）
        v = [1.0 if ("代码" in text or "函数" in text or "报错" in text) else 0.0,
             1.0 if "天气" in text else 0.0, 0.1]
        n = sum(x * x for x in v) ** 0.5
        return [x / n for x in v]


def _engine_with(rules, cfg=None):
    async def make():
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        for doc in rules:
            await r.rpush("hs:hse:rules", json.dumps(doc, ensure_ascii=False))
        for k, v in (cfg or {}).items():
            await r.hset("hs:hse:config", k, str(v))
        e = Engine(r)
        e.embedder = _FakeEmbedder()
        return e
    return _run(make())


CAPS_PRO = ["keyword", "context", "structure", "semantic", "replay", "alpha"]


def test_sparse_score():
    assert _sparse_score("帮我调试这个报错", "调试报错") > 0.5
    assert _sparse_score("今天天气不错", "调试报错") < 0.3


def test_semantic_hit_and_score():
    e = _engine_with([{
        "id": "s1", "name": "代码语义", "enabled": True, "priority": 100,
        "when": {"semantic": {"examples": ["帮我写代码", "调试这个报错"], "threshold": 0.5}},
        "action": {"model": "M-code"}}])
    out = _run(e.decide(ctx(), CAPS_PRO))
    assert out["decision"]["model"] == "M-code"
    assert out["confidence"] < 1.0  # 语义命中带真实置信度
    assert any(s.startswith("sem:") for s in out["signals"])


def test_semantic_miss_unrelated():
    e = _engine_with([{
        "id": "s1", "name": "代码语义", "enabled": True, "priority": 100,
        "when": {"semantic": {"examples": ["帮我写代码", "调试这个报错"], "threshold": 0.6}},
        "action": {"model": "M-code"}}])
    out = _run(e.decide(ctx(prompt="今天天气怎么样"), CAPS_PRO))
    assert out["matched_rule"] is None


def test_semantic_cap_required():
    e = _engine_with([{
        "id": "s1", "name": "代码语义", "enabled": True, "priority": 100,
        "when": {"semantic": {"examples": ["帮我写代码"]}}, "action": {"model": "M"}}])
    out = _run(e.decide(ctx(), ["keyword"]))  # 无 semantic 能力位
    assert out["matched_rule"] is None and "代码语义" in out["skipped_unlicensed"]


def test_embed_down_skips_rule():
    class _Down:
        url = ""
        model = "x"

        async def embed(self, text):
            return None
    async def make():
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.rpush("hs:hse:rules", json.dumps({
            "id": "s1", "name": "代码语义", "enabled": True, "priority": 100,
            "when": {"semantic": {"examples": ["写代码"]}}, "action": {"model": "M"}}, ensure_ascii=False))
        e = Engine(r)
        e.embedder = _Down()
        return e
    e = _run(make())
    out = _run(e.decide(ctx(), CAPS_PRO))
    assert out["matched_rule"] is None


def test_alpha_raises_threshold_when_conservative():
    # 固定语义分 0.65：α=1.0 → 阈值 0.5 过；α=0.0 → 阈值 0.7 不过
    rule = {"id": "s1", "name": "代码语义", "enabled": True, "priority": 100,
            "when": {"semantic": {"examples": ["帮我写代码"], "threshold": 0.6}},
            "action": {"model": "M-code"}}

    def with_score(e, score):
        async def fake(sem, c):
            return score, "x"
        e._semantic_score = fake  # type: ignore
        return e

    e1 = with_score(_engine_with([rule], {"alpha": 1.0}), 0.65)
    hit = _run(e1.decide(ctx(), CAPS_PRO))["matched_rule"]
    e0 = with_score(_engine_with([rule], {"alpha": 0.0}), 0.65)
    miss = _run(e0.decide(ctx(), CAPS_PRO))["matched_rule"]
    assert hit == "s1" and miss is None


def test_shadow_rule_records_but_no_decision():
    e = _engine_with([{
        "id": "sh1", "name": "影子规则", "enabled": True, "priority": 100, "shadow": True,
        "when": {"keywords": ["报错"]}, "action": {"model": "M"}}])
    out = _run(e.decide(ctx(), CAPS_PRO))
    assert out["shadow_hit"] is True
    assert out["decision"]["model"] is None  # 不执行


def test_replay_written_for_hit_and_shadow():
    async def go():
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.rpush("hs:hse:rules", json.dumps({
            "id": "a", "name": "A", "enabled": True, "priority": 100,
            "when": {"keywords": ["报错"]}, "action": {"model": "M"}}, ensure_ascii=False))
        e = Engine(r)
        e.embedder = _FakeEmbedder()
        await e.decide(ctx(), CAPS_PRO)
        raw = await r.lrange("hs:hse:replay", 0, -1)
        assert len(raw) == 1
        doc = json.loads(raw[0])
        assert doc["rule_name"] == "A" and doc["shadow"] is False
    _run(go())


def test_no_replay_without_cap():
    async def go():
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.rpush("hs:hse:rules", json.dumps({
            "id": "a", "name": "A", "enabled": True, "priority": 100,
            "when": {"keywords": ["报错"]}, "action": {"model": "M"}}, ensure_ascii=False))
        e = Engine(r)
        e.embedder = _FakeEmbedder()
        await e.decide(ctx(), ["keyword"])  # 无 replay 能力位
        assert await r.llen("hs:hse:replay") == 0
    _run(go())
