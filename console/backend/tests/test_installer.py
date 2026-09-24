"""SSH 安装器指纹 TOFU 测试（fakeredis + 假连接，无网络）。"""
import asyncio

import fakeredis.aioredis
import pytest

from app import ssh_installer


class _FakeKey:
    def get_fingerprint(self):
        return "AA:BB:CC:DD:EE:FF"


class _FakeConn:
    def get_server_host_key(self):
        return _FakeKey()


def _r():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


def test_tofu_first_trust_records():
    r = _r()
    fp = asyncio.run(ssh_installer._fingerprint_tofu(r, "n1", "10.0.0.1", _FakeConn()))
    assert fp == "AABBCCDDEEFF"
    stored = asyncio.run(r.get("hs:fp:n1"))
    assert stored == "AABBCCDDEEFF"


def test_tofu_same_fp_passes():
    r = _r()
    asyncio.run(r.set("hs:fp:n1", "AABBCCDDEEFF"))
    fp = asyncio.run(ssh_installer._fingerprint_tofu(r, "n1", "10.0.0.1", _FakeConn()))
    assert fp == "AABBCCDDEEFF"  # 不抛异常


def test_tofu_changed_fp_aborts():
    r = _r()
    asyncio.run(r.set("hs:fp:n1", "000000000000"))
    with pytest.raises(RuntimeError, match="fingerprint mismatch"):
        asyncio.run(ssh_installer._fingerprint_tofu(r, "n1", "10.0.0.1", _FakeConn()))


def test_install_invalid_pem_graceful():
    """回归：私钥格式错误必须返回结构化 error，不能 500（import 在 try 内）。"""
    r = _r()
    out = asyncio.run(ssh_installer.install(
        r, node_id="n9", host="127.0.0.1", ssh_port=2299, username="root",
        private_key="-----BEGIN OPENSSH PRIVATE KEY-----\ngarbage\n-----END-----",
        agent_image="hengshu/agent:latest", redis_advertise="redis://x:6379/0"))
    assert "error" in out
    assert "私钥格式无法解析" in out["error"]
    assert any("失败" in s for s in out["steps"])
