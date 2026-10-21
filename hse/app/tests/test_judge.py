"""ENT 裁判测试：YES/NO 判定 / 不可用降级 / 能力位 / 缓存。"""
import asyncio
import json
import sys
from pathlib import Path

import fakeredis.aioredis

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from hse.app.engine import Engine
from hse.app.judge import JudgeClient
from hse.app.signals import ReqCtx


def _run(coro):
    return asyncio.run(coro)


def ctx(prompt="设计一个分布式缓存一致性协议并证明其正确性", **kw):
    base = {"prompt": prompt, "all_text": prompt, "ctx_tokens": 50, "hints": {}}
    base.update(kw)
    return ReqCtx(**base)


class _FakeJudge:
    url = "fake"
    model = "fake"

    def __init__(self, verdict=True):
        self.verdict = verdict
        self.calls = 0

    def configure(self, url, model):
        pass

    async def ask(self, question, prompt):
        self.calls += 1
        return self.verdict, "YES" if self.verdict else "NO"


def _engine(rule, judge):
    async def make():
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.rpush("hs:hse:rules", json.dumps(rule, ensure_ascii=False))
        e = Engine(r)
        e.judge = judge
        return e
    return _run(make())


CAPS_ENT = ["keyword", "context", "structure", "semantic", "replay", "alpha",
            "judge", "affinity", "auto"]


def test_judge_yes_hits():
    rule = {"id": "j1", "name": "难题裁判", "enabled": True, "priority": 100,
            "when": {"judge": {"question": "这个请求难吗？"}},
            "action": {"model": "M-strong"}}
    out = _run(_engine(rule, _FakeJudge(True)).decide(ctx(), CAPS_ENT))
    assert out["decision"]["model"] == "M-strong"
    assert "judge:YES" in out["signals"]
    assert out["confidence"] >= 0.85


def test_judge_no_misses():
    rule = {"id": "j1", "name": "难题裁判", "enabled": True, "priority": 100,
            "when": {"judge": {}}, "action": {"model": "M-strong"}}
    out = _run(_engine(rule, _FakeJudge(False)).decide(ctx(), CAPS_ENT))
    assert out["matched_rule"] is None


def test_judge_down_skips():
    rule = {"id": "j1", "name": "难题裁判", "enabled": True, "priority": 100,
            "when": {"judge": {}}, "action": {"model": "M"}}

    class _Down(_FakeJudge):
        async def ask(self, q, p):
            return None, "judge_down"
    out = _run(_engine(rule, _Down()).decide(ctx(), CAPS_ENT))
    assert out["matched_rule"] is None
    assert any("judge_down" in s for s in out["skipped_unlicensed"])


def test_judge_cap_required():
    rule = {"id": "j1", "name": "难题裁判", "enabled": True, "priority": 100,
            "when": {"judge": {}}, "action": {"model": "M"}}
    out = _run(_engine(rule, _FakeJudge(True)).decide(ctx(), ["keyword"]))
    assert out["matched_rule"] is None and "难题裁判" in out["skipped_unlicensed"]


def test_judge_client_parse_and_cache():
    """真实 HTTP 路径：假 chat 端点回 YES/NO，第二次走缓存。"""
    import socket
    import threading
    import time as _t

    import uvicorn
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    calls = {"n": 0}

    @app.post("/v1/chat/completions")
    async def chat(_: dict):
        calls["n"] += 1
        return {"choices": [{"message": {"content": "YES"}}]}

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(50):
        try:
            TestClient(app)
            break
        except Exception:
            _t.sleep(0.05)
    _t.sleep(0.5)

    async def go():
        jc = JudgeClient(f"http://127.0.0.1:{port}", "fake-model", timeout=5)
        v1, _ = await jc.ask("难吗？", "证明哥德巴赫猜想")
        v2, raw2 = await jc.ask("难吗？", "证明哥德巴赫猜想")
        assert v1 is True and v2 is True
        assert raw2 == "cached"          # 第二次命中缓存
        assert calls["n"] == 1           # 只打了一次 HTTP
    try:
        _run(go())
    finally:
        srv.should_exit = True
