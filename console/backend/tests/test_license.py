"""授权核测试：签发/校验/指纹/有效期/能力位收敛。"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from libs import hslicense as L


@pytest.fixture()
def keypair(monkeypatch):
    sk, pk = L.gen_keypair()
    monkeypatch.setenv("HS_LICENSE_PUBKEY", pk.decode())
    return sk, pk


def _doc(sk, **kw):
    payload = {
        "customer": "测试公司",
        "fingerprint": "sha256:abc",
        "base": {"mode": kw.get("mode", "subscription"), "max_nodes": 8,
                 "expires": kw.get("expires", int(time.time()) + 86400),
                 "version_cap": "1.x"},
        "engine": {"edition": kw.get("edition", "PRO"),
                   "caps": kw.get("caps", ["keyword", "semantic"]),
                   "expires": kw.get("engine_expires", int(time.time()) + 86400)},
    }
    return L.sign_payload(payload, sk)


def test_roundtrip_ok(keypair):
    sk, _ = keypair
    st = L.verify_license(_doc(sk), fingerprint="sha256:abc")
    assert st.ok and st.customer == "测试公司"
    assert st.edition == "PRO" and "semantic" in st.caps


def test_tamper_rejected(keypair):
    sk, _ = keypair
    doc = _doc(sk)
    doc["base"]["max_nodes"] = 999  # 篡改
    st = L.verify_license(doc, fingerprint="sha256:abc")
    assert not st.ok and st.reason == "sig_invalid"


def test_fingerprint_mismatch(keypair):
    sk, _ = keypair
    st = L.verify_license(_doc(sk), fingerprint="sha256:other-machine")
    assert not st.ok and st.reason == "fingerprint_mismatch"


def test_base_expired(keypair):
    sk, _ = keypair
    doc = _doc(sk, expires=int(time.time()) - 3600)
    st = L.verify_license(doc, fingerprint="sha256:abc")
    assert not st.ok and st.reason == "base_expired"
    assert st.base_in_grace  # 30 天宽限内


def test_perpetual_no_expiry(keypair):
    sk, _ = keypair
    doc = _doc(sk, mode="perpetual", expires=0)
    st = L.verify_license(doc, fingerprint="sha256:abc")
    assert st.ok


def test_engine_expired_downgrades_to_std(keypair):
    sk, _ = keypair
    doc = _doc(sk, edition="PRO", engine_expires=int(time.time()) - 3600)
    st = L.verify_license(doc, fingerprint="sha256:abc")
    assert st.ok and st.edition == "STD" and st.caps == L.EDITION_CAPS["STD"]


def test_caps_clamped_by_edition(keypair):
    sk, _ = keypair
    # STD edition 签发时混入 ENT 能力 → 校验应收敛掉
    doc = _doc(sk, edition="STD", caps=["keyword", "judge", "auto"])
    st = L.verify_license(doc, fingerprint="sha256:abc")
    assert st.ok and st.caps == ["keyword"]
