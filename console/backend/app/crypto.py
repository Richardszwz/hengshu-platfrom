"""凭证加密：AES-GCM，密钥 = HKDF-SHA256(MASTER_KEY)。

SSH 密码/私钥落 Redis 前经此加密；控制台无状态部署时所有实例用同一
MASTER_KEY（环境变量注入，方案书 v1.2 决策）即可解密。
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .config import settings

_NONCE_LEN = 12


def _key() -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32,
                salt=b"hengshu-cred-v1", info=None).derive(
                settings.master_key.encode())


def encrypt(plaintext: str) -> str:
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(_key()).encrypt(nonce, plaintext.encode(), None)
    return base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt(blob: str) -> str:
    raw = base64.urlsafe_b64decode(blob.encode())
    nonce, ct = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
    return AESGCM(_key()).decrypt(nonce, ct, None).decode()
