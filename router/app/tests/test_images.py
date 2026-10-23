"""图像生成服务路由测试：kind=image 实例池 + /v1/images/generations 直通。"""
import asyncio
import json
import sys
from pathlib import Path

import fakeredis.aioredis
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _seed(r):
    async def go():
        # chat 实例
        await r.hset("hs:inst:N1:8100", mapping={
            "model": "Qwen3-8B", "framework": "vllm", "url": "http://n1:8100",
            "status": "running", "kind": "chat", "m_queue": "0"})
        await r.sadd("hs:model:Qwen3-8B", "N1:8100")
        # image 实例
        await r.hset("hs:inst:N2:8200", mapping={
            "model": "Qwen-Image", "framework": "custom", "url": "http://n2:8200",
            "status": "running", "kind": "image", "m_queue": "0"})
        await r.sadd("hs:model:Qwen-Image", "N2:8200")
        # custom 实例（只纳管）
        await r.hset("hs:inst:N3:8300", mapping={
            "model": "my-svc", "framework": "custom", "url": "http://n3:8300",
            "status": "running", "kind": "custom", "m_queue": "0"})
        await r.sadd("hs:model:my-svc", "N3:8300")
    asyncio.run(go())


def test_kind_pool_separation():
    from app.registry import Registry
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    _seed(r)
    reg = Registry(r)

    async def go():
        chat_pool = await reg.all_instances()
        assert {i.inst_id for i in chat_pool} == {"N1:8100"}          # image/custom 不进对话池
        assert [i.inst_id for i in await reg.candidates("Qwen-Image")] == []  # 指定名也不进
        imgs = await reg.image_instances()
        assert [i.inst_id for i in imgs] == ["N2:8200"]
        assert [i.inst_id for i in await reg.image_instances("Qwen-Image")] == ["N2:8200"]
        # auto/*/空 同义：不指定模型 → 全图像池（workbuddy 填 auto 也必须路由）
        assert [i.inst_id for i in await reg.image_instances("auto")] == ["N2:8200"]
        assert [i.inst_id for i in await reg.image_instances("*")] == ["N2:8200"]
        assert await reg.image_instances("no-such") == []
    asyncio.run(go())


def test_images_endpoint_route(monkeypatch):
    """图像端点：选中 image 实例并直通（直接调端点函数，绕过 startup）。"""
    import app.main as m
    from app.registry import Registry

    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    _seed(r)
    asyncio.run(r.hset("hs:tokens", "k1", "t1"))
    asyncio.run(r.hset("hs:tenant:t1", mapping={"name": "t1", "qps_limit": "0", "allowed_models": ""}))
    m._redis = r
    m.registry = Registry(r)

    class _Resp:
        status_code = 200

        def json(self):
            return {"created": 1, "data": [{"b64_json": "xx"}]}

    class _FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def post(self, url, json=None):
            assert url == "http://n2:8200/v1/images/generations"
            assert json["prompt"] == "一只猫"
            return _Resp()

    monkeypatch.setattr(m.httpx, "AsyncClient", _FakeClient)

    async def go():
        return await m.images(m.ImageReq(model="Qwen-Image", prompt="一只猫"),
                              {"token": "k1", "checked": False})
    resp = asyncio.run(go())
    body = json.loads(resp.body)
    assert body["instance"] == "N2:8200"


def test_images_403_when_whitelist_excludes(monkeypatch):
    from fastapi import HTTPException

    import app.main as m
    from app.registry import Registry

    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    _seed(r)
    asyncio.run(r.hset("hs:tokens", "k1", "t1"))
    asyncio.run(r.hset("hs:tenant:t1", mapping={
        "name": "t1", "qps_limit": "0", "allowed_models": "Qwen3-8B"}))
    m._redis = r
    m.registry = Registry(r)

    async def go():
        await m.images(m.ImageReq(model="Qwen-Image", prompt="x"),
                       {"token": "k1", "checked": False})
    with pytest.raises(HTTPException) as ei:
        asyncio.run(go())
    assert ei.value.status_code == 503  # 白名单过滤后无图像实例
