"""认证：argon2 密码校验 + JWT（HS256）。"""
from __future__ import annotations

import time

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings

_hasher = PasswordHasher()
_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hash_str: str | None = None) -> bool:
    h = hash_str or settings.admin_password_hash
    if not h:
        return False
    try:
        return _hasher.verify(h, password)
    except (VerifyMismatchError, VerificationError, ValueError):
        return False


def issue_token(username: str) -> dict:
    now = int(time.time())
    payload = {"sub": username, "iat": now, "exp": now + settings.token_ttl}
    token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    return {"access_token": token, "token_type": "bearer",
            "expires_in": settings.token_ttl}


async def current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    if cred is None:
        raise HTTPException(401, "missing token")
    try:
        payload = jwt.decode(cred.credentials, settings.secret_key,
                             algorithms=["HS256"])
        return str(payload["sub"])
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid token") from None
