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


def test_service_delete_deregisters(monkeypatch):
    """删除服务：停容器 + 从 INSTANCES 移除（防心跳复活）。"""
    async def fake_stop(name):
        return "container stopped"
    monkeypatch.setattr(main.ctl, "stop", fake_stop)
    saved = {}
    monkeypatch.setattr(main, "_save_services", lambda: saved.setdefault("ok", True))
    monkeypatch.setattr(main, "_push_event", lambda *a: asyncio.sleep(0))
    main.INSTANCES[:] = [{"model": "m1", "port": 8000}, {"model": "m2", "port": 8001}]
    with TestClient(main.app) as c:
        r = c.post("/services/delete/m1")
        assert r.json()["deregistered"] is True
    assert [x["model"] for x in main.INSTANCES] == ["m2"]
    assert saved.get("ok")
    main.INSTANCES[:] = []


def test_watch_started_captures_crash_logs(monkeypatch):
    """巡检：容器未存活 → 日志尾部写事件流（节点系统日志）。"""
    pushed = []

    async def fake_push(level, msg):
        pushed.append((level, msg))

    async def fast_sleep(_):
        return None

    monkeypatch.setattr(main, "_push_event", fake_push)
    monkeypatch.setattr(main.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(main, "is_running", _false)
    monkeypatch.setattr(main.ctl, "logs", _fake_logs)

    req = main.StartRequest(name="m", start_cmd="x", port=8000)
    asyncio.run(main._watch_started(req))
    assert any("容器未存活" in m for _, m in pushed)
    assert any("torch.OutOfMemoryError" in m for _, m in pushed)


async def _false(_):
    return False


async def _fake_logs(name, tail):
    return ["line1", "torch.OutOfMemoryError: CUDA out of memory"]
