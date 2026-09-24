"""控制台冒烟测试：路由注册 + 健康检查（不依赖 Redis 的用例先行）。"""
from fastapi.testclient import TestClient

from app.main import app


def test_healthz() -> None:
    with TestClient(app) as c:
        assert c.get("/api/healthz").json()["ok"] is True


def test_login_rejected_without_hash() -> None:
    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": "x"})
        assert r.status_code == 401
