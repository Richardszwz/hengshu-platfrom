"""基本 License 执法守卫测试（license_guard + 节点上限）。"""
import asyncio
import json
import sys
import time
from pathlib import Path

import fakeredis.aioredis
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from libs import hslicense as L


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def keypair(monkeypatch):
    sk, pk = L.gen_keypair()
    monkeypatch.setenv("HS_LICENSE_PUBKEY", pk.decode())
    return sk, pk


def _lic_doc(sk, expires=None, max_nodes=8):
    payload = {
        "customer": "T", "fingerprint": L.cluster_fingerprint(),
        "base": {"mode": "subscription", "max_nodes": max_nodes,
                 "expires": expires or int(time.time()) + 86400, "version_cap": "1.x"},
        "engine": {"edition": "STD", "caps": ["keyword"], "expires": 0},
    }
    return L.sign_payload(payload, sk)


@pytest.fixture()
def r():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


class TestRequireActiveBase:
    def test_missing_license_403(self, r):
        from app.license_guard import require_active_base
        with pytest.raises(HTTPException) as ei:
            _run(require_active_base(r))
        assert ei.value.status_code == 403 and "未激活" in ei.value.detail

    def test_valid_license_passes(self, r, keypair):
        sk, _ = keypair
        _run(r.set("hs:license:file", json.dumps(_lic_doc(sk))))
        from app.license_guard import require_active_base
        st = _run(require_active_base(r))
        assert st.ok

    def test_expired_in_grace_passes(self, r, keypair):
        sk, _ = keypair
        # 刚过期 1 小时（宽限 30 天内）→ 放行
        _run(r.set("hs:license:file",
                   json.dumps(_lic_doc(sk, expires=int(time.time()) - 3600))))
        from app.license_guard import require_active_base
        st = _run(require_active_base(r))
        assert st.base_in_grace

    def test_expired_beyond_grace_403(self, r, keypair):
        sk, _ = keypair
        _run(r.set("hs:license:file",
                   json.dumps(_lic_doc(sk, expires=int(time.time()) - 31 * 86400))))
        from app.license_guard import require_active_base
        with pytest.raises(HTTPException) as ei:
            _run(require_active_base(r))
        assert ei.value.status_code == 403 and "宽限期" in ei.value.detail


class TestNodeCap:
    def test_cap_blocks_when_full(self, r, keypair):
        sk, _ = keypair
        _run(r.set("hs:license:file", json.dumps(_lic_doc(sk, max_nodes=2))))
        for nid in ("N1", "N2"):
            _run(r.hset(f"hs:node:{nid}", mapping={"status": "online",
                                                   "last_seen": str(int(time.time()))}))
        from app.license_guard import check_node_cap
        with pytest.raises(HTTPException) as ei:
            _run(check_node_cap(r))
        assert ei.value.status_code == 403 and "上限" in ei.value.detail

    def test_cap_allows_when_space(self, r, keypair):
        sk, _ = keypair
        _run(r.set("hs:license:file", json.dumps(_lic_doc(sk, max_nodes=8))))
        _run(r.hset("hs:node:N1", mapping={"status": "online",
                                           "last_seen": str(int(time.time()))}))
        from app.license_guard import check_node_cap
        _run(check_node_cap(r))  # 不抛即过

    def test_tombstoned_nodes_not_counted(self, r, keypair):
        sk, _ = keypair
        _run(r.set("hs:license:file", json.dumps(_lic_doc(sk, max_nodes=2))))
        _run(r.hset("hs:node:N1", mapping={"status": "online",
                                           "last_seen": str(int(time.time()))}))
        _run(r.hset("hs:node:N2", mapping={"status": "online",
                                           "last_seen": str(int(time.time()))}))
        _run(r.set("hs:node_del:N2", "1"))  # 墓碑 → 不计
        from app.license_guard import check_node_cap
        _run(check_node_cap(r))  # N2 被墓碑排除，未达上限
