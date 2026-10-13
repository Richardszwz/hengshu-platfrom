"""心跳墓碑：hs:node_del:{id} 存在 → Agent 自杀（docker rm -f 自身容器）。"""
import asyncio

import fakeredis.aioredis
import pytest

from app import main


def test_heartbeat_tombstone_selfdestruct(monkeypatch):
    called = {}

    async def fake_exec(*a, **kw):
        called["cmd"] = a

        class P:
            async def wait(self):
                return 0
        return P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(main.os, "_exit",
                        lambda code: (_ for _ in ()).throw(SystemExit(code)))
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/docker")
    monkeypatch.setattr(main.settings, "node_id", "S1")

    async def run():
        fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
        main._redis = fake
        await fake.set("hs:node_del:S1", "1")
        with pytest.raises(SystemExit):
            await main.heartbeat_loop()

    asyncio.run(run())
    assert called["cmd"] == ("docker", "rm", "-f", "hengshu-agent")
