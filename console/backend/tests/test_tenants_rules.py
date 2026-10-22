"""租户/规则 API + 终端黑名单/录屏格式测试。"""
from __future__ import annotations

import json
import os
import sys

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from app import deps
from app.main import app
from app.security import current_user


@pytest.fixture()
def client():
    import asyncio
    from pathlib import Path

    from app.security import hash_password, issue_token

    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app.dependency_overrides[deps.get] = lambda: fake
    # 不覆盖 current_user：全链路走真实 JWT+用户库（admin token 见下）
    asyncio.run(fake.hset("hs:user:admin", mapping={
        "password_hash": hash_password("x12345"), "role": "admin",
        "enabled": "1", "created": "1"}))
    # 写操作挂基本 License 守卫：现场生成密钥对签发测试 license
    # （不依赖 scripts/dev_keys——该目录 gitignored，CI 上不存在；
    #   公钥经 HS_LICENSE_PUBKEY 环境变量注入，hslicense 验签时读取）
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from libs import hslicense as L
    sk, pk = L.gen_keypair()
    os.environ["HS_LICENSE_PUBKEY"] = pk.decode()
    lic = L.sign_payload({
        "customer": "test", "fingerprint": L.cluster_fingerprint(),
        "base": {"mode": "perpetual", "max_nodes": 0, "expires": 0, "version_cap": "1.x"},
        "engine": {"edition": "ENT", "caps": L.ALL_CAPS, "expires": 0}}, sk)
    asyncio.run(fake.set("hs:license:file", json.dumps(lic)))
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + issue_token("admin")["access_token"]
        yield c
    app.dependency_overrides.clear()


def test_tenant_crud(client) -> None:
    import asyncio
    fake = app.dependency_overrides[deps.get]()
    r = client.post("/api/tenants", json={"tenant_id": "team-idx", "qps_limit": 50})
    assert r.status_code == 200
    key1 = r.json()["api_key"]
    assert key1.startswith("hs-tnt-")
    # 反查索引：router 靠它认证租户 Key
    assert asyncio.run(fake.hget("hs:tokens", key1)) == "team-idx"
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


def test_remove_online_node_sends_tombstone(client) -> None:
    """在线节点删除 → 200 + 墓碑写入（Agent 下拍自杀）；离线节点 → 直接清登记。"""
    import asyncio
    import time as _t
    fake = app.dependency_overrides[deps.get]()

    # 在线节点
    asyncio.run(fake.hset("hs:node:live-node", mapping={"last_seen": str(int(_t.time()))}))
    r = client.delete("/api/nodes/live-node")
    assert r.status_code == 200
    assert r.json()["agent_notified"] is True
    assert asyncio.run(fake.get("hs:node_del:live-node")) == "1"
    assert asyncio.run(fake.exists("hs:node:live-node")) == 0

    # 离线节点（带 conn/模型集合残留）
    past = str(int(_t.time()) - 100)
    asyncio.run(fake.hset("hs:node:dead-node", mapping={"last_seen": past}))
    asyncio.run(fake.hset("hs:conn:dead-node", mapping={"host": "1.1.1.1", "port": "22", "username": "u"}))
    asyncio.run(fake.sadd("hs:model:qwen3-30b", "dead-node:8001", "gpu-x:8002"))
    r = client.delete("/api/nodes/dead-node")
    assert r.status_code == 200
    assert r.json()["agent_notified"] is False
    assert asyncio.run(fake.exists("hs:node:dead-node", "hs:conn:dead-node")) == 0
    assert asyncio.run(fake.smembers("hs:model:qwen3-30b")) == {"gpu-x:8002"}


def test_reinstall_fallback_conn_and_cred(client, monkeypatch) -> None:
    """重装捷径：空 hostname+空密码 → 回退 hs:conn 连接信息 + hs:cred 解密凭证。"""
    import asyncio
    import app.ssh_installer as si
    from app.crypto import encrypt

    fake = app.dependency_overrides[deps.get]()
    asyncio.run(fake.hset("hs:conn:S1", mapping={"host": "10.0.0.9", "port": "22", "username": "dgx"}))
    asyncio.run(fake.set("hs:cred:S1", encrypt("secret-pw")))

    captured: dict = {}

    async def fake_install(r, **kw):
        captured.update(kw)
        return {"status": "OK", "msg": "done"}

    monkeypatch.setattr(si, "install", fake_install)
    r = client.post("/api/nodes/S1/install", json={"node_id": "S1"})
    assert r.status_code == 200
    assert captured["host"] == "10.0.0.9" and captured["username"] == "dgx"
    assert captured["password"] == "secret-pw"

    # 无存档节点 → 400 明确指引
    r2 = client.post("/api/nodes/NOPE/install", json={"node_id": "NOPE"})
    assert r2.status_code == 400


def test_settings_webhook_and_password(client) -> None:
    """系统设置：webhook 读写 / 改密后旧密码失效新密码生效。"""
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert r.json()["webhook_source"] in ("env", "redis", "none")

    r = client.put("/api/settings/webhook", json={"url": "https://hooks.example.com/x"})
    assert r.status_code == 200
    assert client.get("/api/settings").json()["webhook_url"] == "https://hooks.example.com/x"
    assert client.put("/api/settings/webhook", json={"url": "ftp://x"}).status_code == 400

    # 改密（fixture 种子 admin 密码 x12345），走用户库端点
    assert client.post("/api/users/me/password", json={
        "old_password": "wrong", "new_password": "newpass123"}).status_code == 403
    assert client.post("/api/users/me/password", json={
        "old_password": "x12345", "new_password": "newpass123"}).status_code == 200
    assert client.post("/api/auth/login", json={
        "username": "admin", "password": "newpass123"}).status_code == 200
    assert client.post("/api/auth/login", json={
        "username": "admin", "password": "x12345"}).status_code == 401


def test_users_crud_and_role_guard(client) -> None:
    """用户管理：CRUD + 角色越权 403 + 禁用即失效 + 不能删/改自己。"""
    from app.security import issue_token

    users = client.get("/api/users").json()["users"]
    assert [u["username"] for u in users] == ["admin"] and users[0]["role"] == "admin"

    r = client.post("/api/users", json={"username": "v1", "password": "view123", "role": "viewer"})
    assert r.status_code == 200
    assert client.post("/api/users", json={"username": "v1", "password": "view123", "role": "viewer"}).status_code == 409
    assert client.post("/api/users", json={"username": "bad name!", "password": "view123", "role": "viewer"}).status_code == 400

    vt = issue_token("v1")["access_token"]
    assert client.get("/api/dashboard/summary", headers={"Authorization": f"Bearer {vt}"}).status_code == 200
    assert client.post("/api/tenants", headers={"Authorization": f"Bearer {vt}"},
                       json={"name": "t", "qps_limit": 5}).status_code == 403
    assert client.get("/api/settings", headers={"Authorization": f"Bearer {vt}"}).status_code == 403

    assert client.put("/api/users/v1", json={"enabled": False}).status_code == 200
    assert client.get("/api/dashboard/summary", headers={"Authorization": f"Bearer {vt}"}).status_code == 401

    assert client.delete("/api/users/admin").status_code == 400
    assert client.put("/api/users/admin", json={"role": "viewer"}).status_code == 400

    assert client.delete("/api/users/v1").status_code == 200
    assert client.delete("/api/users/v1").status_code == 404


def test_stop_agent_guards(client) -> None:
    """远程停止 Agent：离线节点 404；旧版 Agent（无 agent_addr）400 指引重装。"""
    import asyncio
    import time as _t
    fake = app.dependency_overrides[deps.get]()
    asyncio.run(fake.hset("hs:node:S9", mapping={"last_seen": str(int(_t.time()))}))

    r = client.post("/api/nodes/S9/stop-agent")
    assert r.status_code == 400 and "重装" in r.json()["detail"]
    assert client.post("/api/nodes/nope/stop-agent").status_code == 404


def test_tombstone_hides_and_add_clears(client) -> None:
    """墓碑节点从列表隐藏；重新添加清除墓碑。"""
    import asyncio
    import time as _t
    fake = app.dependency_overrides[deps.get]()
    asyncio.run(fake.hset("hs:node:ghost", mapping={"last_seen": str(int(_t.time()))}))

    # 删除（在线）→ 墓碑 + 列表隐藏
    r = client.delete("/api/nodes/ghost")
    assert r.json()["agent_notified"] is True
    assert all(n["node_id"] != "ghost" for n in client.get("/api/nodes").json()["nodes"])
    # 心跳复活也不显示
    asyncio.run(fake.hset("hs:node:ghost", mapping={"last_seen": str(int(_t.time()))}))
    assert all(n["node_id"] != "ghost" for n in client.get("/api/nodes").json()["nodes"])
    # 重新添加 → 墓碑清除 → 可见
    r = client.post("/api/nodes", json={"node_id": "ghost", "hostname": "1.2.3.4",
                                        "username": "u", "password": "p123456"})
    assert r.status_code == 200
    assert asyncio.run(fake.get("hs:node_del:ghost")) is None


def test_node_logs_endpoint(client) -> None:
    """节点系统日志：读 hs:nodelog:{id}，新→旧。"""
    import asyncio
    import json as _j
    fake = app.dependency_overrides[deps.get]()
    asyncio.run(fake.rpush("hs:nodelog:X86-9",
                           _j.dumps({"ts": 1.0, "level": "INFO", "msg": "受理"}),
                           _j.dumps({"ts": 2.0, "level": "OK", "msg": "完成"})))
    r = client.get("/api/nodes/X86-9/logs")
    assert r.status_code == 200
    lines = r.json()["lines"]
    assert [l["msg"] for l in lines] == ["完成", "受理"]  # 新→旧
    assert client.get("/api/nodes/none/logs").json()["lines"] == []


def test_delete_service_clears_registry(client) -> None:
    """删除服务：实例 key/模型集合清掉，spec 保留作启动模板（Agent 不可达时仅清登记也成立）。"""
    import asyncio
    fake = app.dependency_overrides[deps.get]()
    asyncio.run(fake.hset("hs:node:N1", mapping={"last_seen": "9999999999"}))
    asyncio.run(fake.hset("hs:inst:N1:8100", mapping={"model": "qwen", "status": "running"}))
    asyncio.run(fake.sadd("hs:model:qwen", "N1:8100"))
    asyncio.run(fake.hset("hs:svcspec:N1:qwen", mapping={"image": "x"}))

    r = client.delete("/api/services/N1/qwen")
    assert r.status_code == 200 and r.json()["deleted"] == "qwen"
    assert asyncio.run(fake.keys("hs:inst:N1:*")) == []
    assert asyncio.run(fake.smembers("hs:model:qwen")) == set()
    # spec 保留作启动模板（删除服务后重建可在对话框自动回填调好的参数）
    assert asyncio.run(fake.exists("hs:svcspec:N1:qwen")) == 1


def test_update_conn(client) -> None:
    """改连接：更新 hs:conn + 可选换凭证；未登记 404；留空密码不动旧凭证。"""
    import asyncio
    from app.crypto import decrypt, encrypt
    fake = app.dependency_overrides[deps.get]()
    asyncio.run(fake.hset("hs:conn:N1", mapping={"host": "1.1.1.1", "port": "22", "username": "old"}))
    asyncio.run(fake.set("hs:cred:N1", encrypt("old-pw")))

    r = client.put("/api/nodes/N1/conn", json={
        "hostname": "2.2.2.2", "ssh_port": 2222, "username": "new"})
    assert r.status_code == 200 and r.json()["credential_updated"] is False
    conn = asyncio.run(fake.hgetall("hs:conn:N1"))
    assert conn == {"host": "2.2.2.2", "port": "2222", "username": "new"}
    assert decrypt(asyncio.run(fake.get("hs:cred:N1"))) == "old-pw"  # 未动

    r = client.put("/api/nodes/N1/conn", json={
        "hostname": "2.2.2.2", "username": "new", "password": "new-pw"})
    assert r.json()["credential_updated"] is True
    assert decrypt(asyncio.run(fake.get("hs:cred:N1"))) == "new-pw"

    assert client.put("/api/nodes/NOPE/conn", json={
        "hostname": "x", "username": "u"}).status_code == 404
