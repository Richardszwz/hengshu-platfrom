"""衡枢两层授权核（console / router / HSE 引擎共用）。

License 文件 = JSON：{customer, fingerprint, base:{mode,max_nodes,expires,version_cap},
                      engine:{edition,caps[],expires}, sig}
签名：Ed25519 对 canonical JSON（不含 sig 字段）签名，base64url 存放。
校验：签名 → 指纹 → 有效期/版本范围 → 返回结构化结果与能力位。

气隙设计：全程离线；私钥只在我方签发工具（scripts/gen_license.py），
产品内只嵌公钥（环境变量 HS_LICENSE_PUBKEY 可覆盖，便于换钥）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import subprocess
import time
from dataclasses import dataclass, field

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

# 内嵌验签公钥（与 license 签发工具的 license_private.pem 配对；换钥=替换此处或设 HS_LICENSE_PUBKEY）
_DEV_PUBKEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAtOpgHPtWkSWTpiYUxHV8UNHO5i0TIEVqjaYs/esOf2M=
-----END PUBLIC KEY-----
"""

ALL_CAPS = ["keyword", "context", "structure",
            "semantic", "replay", "alpha",
            "judge", "affinity", "auto", "tenant_isolation", "image_route"]
EDITION_CAPS = {
    "STD": ["keyword", "context", "structure"],
    "PRO": ["keyword", "context", "structure", "semantic", "replay", "alpha", "image_route"],
    "ENT": ALL_CAPS,
}


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def canonical(payload: dict) -> bytes:
    """除 sig 外的规范化 JSON（排序键、紧凑、UTF-8）。"""
    p = {k: v for k, v in payload.items() if k != "sig"}
    return json.dumps(p, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


# ---------------- 密钥 ----------------

def gen_keypair() -> tuple[bytes, bytes]:
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()
    sk_pem = sk.private_bytes(serialization.Encoding.PEM,
                              serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption())
    pk_pem = pk.public_bytes(serialization.Encoding.PEM,
                             serialization.PublicFormat.SubjectPublicKeyInfo)
    return sk_pem, pk_pem


def sign_payload(payload: dict, sk_pem: bytes) -> dict:
    sk = serialization.load_pem_private_key(sk_pem, password=None)
    sig = sk.sign(canonical(payload))
    out = dict(payload)
    out["sig"] = _b64u(sig)
    return out


def _pubkey() -> Ed25519PublicKey:
    pem = os.getenv("HS_LICENSE_PUBKEY", "").strip()
    raw = (pem.encode() if pem else _DEV_PUBKEY_PEM)
    return serialization.load_pem_public_key(raw)


# ---------------- 集群指纹 ----------------

def cluster_fingerprint() -> str:
    """机器指纹：优先 Linux machine-id，其次 Windows 主板 UUID，再兜底 MAC+hostname。"""
    parts: list[str] = []
    try:
        if platform.system() != "Windows":
            parts.append(open("/etc/machine-id").read().strip())
        else:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",  # noqa: S607  # Windows 系统自带
                 "(Get-CimInstance Win32_ComputerSystemProduct).UUID"],
                capture_output=True, text=True, timeout=10)
            parts.append(out.stdout.strip())
    except Exception:
        pass
    if not parts or not parts[0]:
        import uuid
        parts.append(f"{uuid.getnode():012x}")
        parts.append(platform.node())
    return "sha256:" + hashlib.sha256("|".join(parts).encode()).hexdigest()


# ---------------- 校验 ----------------

@dataclass
class LicenseState:
    ok: bool
    reason: str = ""                # 空=有效；否则失效原因（展示用）
    customer: str = ""
    base_mode: str = ""             # subscription | perpetual
    base_expires: int = 0
    max_nodes: int = 0
    version_cap: str = ""
    edition: str = "STD"
    caps: list[str] = field(default_factory=lambda: EDITION_CAPS["STD"])
    engine_expires: int = 0
    grace_days: int = 30

    @property
    def base_in_grace(self) -> bool:
        return (not self.ok and self.reason == "base_expired"
                and time.time() < self.base_expires + self.grace_days * 86400)

    def has_cap(self, cap: str) -> bool:
        return self.ok and cap in self.caps


def verify_license(doc: dict, fingerprint: str | None = None,
                   now: float | None = None) -> LicenseState:
    """完整校验一份 license。fingerprint 缺省用本机指纹。"""
    now = now or time.time()
    st = LicenseState(ok=False)
    try:
        pk = _pubkey()
        pk.verify(_b64u_dec(doc.get("sig", "")), canonical(doc))
    except Exception:
        st.reason = "sig_invalid"
        return st
    st.customer = str(doc.get("customer", ""))
    fp = str(doc.get("fingerprint", ""))
    if fingerprint and fp and fp != fingerprint:
        st.reason = "fingerprint_mismatch"
        return st
    base = doc.get("base") or {}
    st.base_mode = str(base.get("mode", "subscription"))
    st.max_nodes = int(base.get("max_nodes", 0) or 0)
    st.version_cap = str(base.get("version_cap", ""))
    st.base_expires = int(base.get("expires", 0) or 0)
    if st.base_mode == "subscription" and st.base_expires and now > st.base_expires:
        st.reason = "base_expired"
        return st
    eng = doc.get("engine") or {}
    st.edition = str(eng.get("edition", "STD"))
    st.engine_expires = int(eng.get("expires", 0) or 0)
    caps = eng.get("caps") or []
    caps = [c for c in caps if c in ALL_CAPS]
    # caps 不得超过 edition 上限（防签发笔误放大权限）
    st.caps = [c for c in caps if c in EDITION_CAPS.get(st.edition, [])]
    if st.engine_expires and now > st.engine_expires:
        # 引擎包到期 → 仅降级 STD（base 仍有效时）
        st.edition = "STD"
        st.caps = EDITION_CAPS["STD"]
    st.ok = True
    return st


def empty_license() -> LicenseState:
    """无 license 时的默认态：未激活（管理面提示；流量面由调用方决定放行策略）。"""
    return LicenseState(ok=False, reason="missing")
