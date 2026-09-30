"""租户/规则 API + 终端黑名单/录屏格式测试。"""
from __future__ import annotations

import json

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from app import deps
from app.main import app
from app.security import current_user


@pytest.fixture()
def client():
    import asyncio
    from app.security import hash_password, issue_token

    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app.dependency_overrides[deps.get] = lambda: fake
    # 不覆盖 current_user：全链路走真实 JWT+用户库（admin token 见下）
    asyncio.run(fake.hset("hs:user:admin", mapping={
        "password_hash": hash_password("x12345"), "role": "admin",
        "enabled": "1", "created": "1"}))
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + issue_token("admin")["access_token"]
        yield c
    app.dependency_overrides.clear()


def test_tenant_crud(client) -> None:
    r = client.post("/api/tenants", json={"tenant_id": "team-a", "qps_limit": 20})
    assert r.status_code == 200
    assert r.json()["api_key"].startswith("hs-tnt-")
    # 重复创建 → 409
    assert client.post("/api/tenants", json={"tenant_id": "team-a"}).status_code == 409
    # 列表可见
    tenants = client.get("/api/tenants").json()["tenants"]
    assert any(t["tenant_id"] == "team-a" and t["qps_limit"] == "20" for t in tenants)
    # 删除
    assert client.delete("/api/tenants/team-a").status_code == 200
    assert client.delete("/api/tenants/team-a").status_code == 404


def test_tenant_id_pattern(client) -> None:
    assert client.post("/api/tenants", json={"tenant_id": "Bad_ID!"}).status_code == 422


def test_rules_crud(client) -> None:
    assert client.put("/api/rules/code", params={"model": "Qwen3-Coder-30B"}).status_code == 200
    rules = client.get("/api/rules").json()["rules"]
    assert {"type": "code", "model": "Qwen3-Coder-30B"} in rules
    assert client.delete("/api/rules/code").status_code == 200
    assert client.get("/api/rules").json()["rules"] == []


def test_term_blacklist() -> None:
    from app.term import BLACKLIST

    def blocked(cmd: str) -> bool:
        return any(p.search(cmd) for p in BLACKLIST)

    assert blocked("rm -rf /")
    assert blocked("rm -fr / ")
    assert blocked("sudo shutdown -h now")
    assert blocked("mkfs.ext4 /dev/sda1")
    assert blocked("curl http://x.sh | bash")
    assert not blocked("rm -rf ./build")
    assert not blocked("ls /")
    assert not blocked("systemctl restart hengshu-agent")


def test_recorder_v2_format(tmp_path) -> None:
    from app.term import _Recorder

    p = tmp_path / "s.cast"
    rec = _Recorder(str(p), 80, 24)
    rec.event("o", "hello")
    rec.event("i", "ls\r")
    rec.close()
    lines = p.read_text(encoding="utf-8").strip().split("\n")
    header = json.loads(lines[0])
    assert header["version"] == 2 and header["width"] == 80
    ev = json.loads(lines[1])
    assert ev[1] == "o" and ev[2] == "hello"
    assert json.loads(lines[2])[2] == "ls\r"


def test_remove_online_node_rejected(client) -> None:
    """在线节点（心跳新鲜）删除 → 409，避免 Agent 重写心跳导致'复活'。"""
    import asyncio
    import time as _t

    async def setup():
        fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
        app.dependency_overrides[deps.get] = lambda: fake
        # 在线节点：last_seen 当前时间
        await fake.hset("hs:node:live-node", mapping={"last_seen": str(int(_t.time()))})
        return fake

    fake = asyncio.run(setup())
    resp = client.delete("/api/nodes/live-node")
    assert resp.status_code == 409
    assert "在线" in resp.json()["detail"]

    async def setup_dead():
        past = str(int(_t.time()) - 100)
        await fake.hset("hs:node:dead-node", mapping={"last_seen": past})
        await fake.hset("hs:conn:dead-node", mapping={"host": "1.1.1.1", "port": "22", "username": "u"})
        await fake.sadd("hs:model:qwen3-30b", "dead-node:8001", "gpu-x:8002")

    asyncio.run(setup_dead())
    resp = client.delete("/api/nodes/dead-node")
    assert resp.status_code == 200
    assert resp.json() == {"removed": "dead-node"}
    assert asyncio.run(fake.exists("hs:node:dead-node", "hs:conn:dead-node")) == 0
    assert asyncio.run(fake.smembers("hs:model:qwen3-30b")) == {"gpu-x:8002"}


def test_settings_webhook_and_password(client) -> None:
    """系统设置：webhook 读写 / 改密后旧密码失效新密码生效（fakeredis 覆盖）。"""
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert r.json()["webhook_source"] in ("env", "redis", "none")

    # webhook 保存 → redis 覆盖生效
    r = client.put("/api/settings/webhook", json={"url": "https://hooks.example.com/x"})
    assert r.status_code == 200
    assert client.get("/api/settings").json()["webhook_url"] == "https://hooks.example.com/x"
    # 非法协议拦截
    assert client.put("/api/settings/webhook", json={"url": "ftp://x"}).status_code == 400

    # 改密：种子用户 admin 的密码是 x12345（fixture），走 /api/users/me/password
    assert client.post("/api/users/me/password", json={
        "old_password": "wrong", "new_password": "newpass123"}).status_code == 403
    assert client.post("/api/users/me/password", json={
        "old_password": "x12345", "new_password": "newpass123"}).status_code == 200
    # 新密码可登录、旧密码失效
    assert client.post("/api/auth/login", json={
        "username": "admin", "password": "newpass123"}).status_code == 200
    assert client.post("/api/auth/login", json={
        "username": "admin", "password": "x12345"}).status_code == 401


def test_users_crud_and_role_guard(client) -> None:
    """用户管理：CRUD + 角色越权 403 + 禁用即失效 + 不能删/改自己。"""
    import asyncio
    from app.security import hash_password, issue_token
    fake = app.dependency_overrides[deps.get]()

    # 列表：初始含 admin
    users = client.get("/api/users").json()["users"]
    assert [u["username"] for u in users] == ["admin"] and users[0]["role"] == "admin"

    # 创建 viewer
    r = client.post("/api/users", json={"username": "v1", "password": "view123", "role": "viewer"})
    assert r.status_code == 200
    assert client.post("/api/users", json={"username": "v1", "password": "view123", "role": "viewer"}).status_code == 409
    assert client.post("/api/users", json={"username": "bad name!", "password": "view123", "role": "viewer"}).status_code == 400

    # viewer 登录 → 能读（GET 走 current_user override 之外的真实路径这里直接发 token）
    vt = issue_token("v1")["access_token"]
    assert client.get("/api/dashboard/summary", headers={"Authorization": f"Bearer {vt}"}).status_code == 200
    # viewer 改租户 → 403（operator+）
    assert client.post("/api/tenants", headers={"Authorization": f"Bearer {vt}"},
                       json={"name": "t", "qps_limit": 5}).status_code == 403
    # viewer 改设置 → 403（admin）
    assert client.get("/api/settings", headers={"Authorization": f"Bearer {vt}"}).status_code == 403

    # 禁用 v1 → 其 token 立即 401
    assert client.put("/api/users/v1", json={"enabled": False}).status_code == 200
    assert client.get("/api/dashboard/summary", headers={"Authorization": f"Bearer {vt}"}).status_code == 401

    # 自残保护
    assert client.delete("/api/users/admin").status_code == 400
    assert client.put("/api/users/admin", json={"role": "viewer"}).status_code == 400

    # 删除
    assert client.delete("/api/users/v1").status_code == 200
    assert client.delete("/api/users/v1").status_code == 404
