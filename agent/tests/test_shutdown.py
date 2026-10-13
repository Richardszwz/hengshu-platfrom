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


def test_start_async_accept_and_dedupe(monkeypatch):
    """服务启动改为受理制：立即返回 accepted；同名进行中重复提交被拒。"""
    import asyncio

    async def hang(req):
        await asyncio.sleep(30)
        return "ok"

    monkeypatch.setattr(main.ctl, "start", hang)
    with TestClient(main.app) as c:
        body = {"name": "m1", "start_cmd": "sleep 9", "port": 8000}
        r = c.post("/services/start", json=body)
        assert r.status_code == 200 and r.json()["accepted"] == "m1"
        # 后台任务仍在挂起 → 重复提交提示进行中
        import time as _t
        _t.sleep(0.3)
        r2 = c.post("/services/start", json=body)
        assert "进行中" in r2.json()["note"]
