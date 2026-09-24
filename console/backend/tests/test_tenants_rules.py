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
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app.dependency_overrides[deps.get] = lambda: fake
    app.dependency_overrides[current_user] = lambda: "admin"
    with TestClient(app) as c:
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
