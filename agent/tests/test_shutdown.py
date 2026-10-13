"""Agent /shutdown：响应 stopping，自杀任务走 docker rm -f（防 restart 拉起）。"""
import asyncio

from fastapi.testclient import TestClient

from app import main


def test_shutdown_endpoint(monkeypatch):
    called = {}

    async def fake_exec(*a, **kw):
        called["cmd"] = a

        class P:
            async def wait(self):
                return 0
        return P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(main.os, "_exit", lambda code: called.setdefault("exit", code))
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/docker")

    with TestClient(main.app) as c:
        r = c.post("/shutdown")
        assert r.status_code == 200
        assert r.json()["stopping"] is True
        assert r.json()["container"] == "hengshu-agent"
        # 让事件循环跑到 0.5s 后的自杀任务
        import time
        time.sleep(1.0)

    assert called.get("cmd") == ("docker", "rm", "-f", "hengshu-agent")
    assert called.get("exit") == 0
