"""认证：argon2 密码校验 + JWT（HS256）+ 用户存储角色鉴权。"""
from __future__ import annotations

import time

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import deps, users_store
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
    r=Depends(deps.get),
) -> str:
    """校验 JWT + 用户存储状态（禁用/删除即时生效）。返回用户名。"""
    if cred is None:
        raise HTTPException(401, "missing token")
    try:
        payload = jwt.decode(cred.credentials, settings.secret_key,
                             algorithms=["HS256"])
        username = str(payload["sub"])
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid token") from None
    user = await users_store.get_user(r, username)
    if user is not None and user.get("enabled", "1") != "1":
        raise HTTPException(401, "user disabled")
    return username


async def _role_of(r, username: str) -> str:
    user = await users_store.get_user(r, username)
    # 未入库（如纯 env 种子前的旧 token）按 admin 兼容
    return (user or {}).get("role", "admin")


def require_role(min_role: str):
    """依赖工厂：角色不足 → 403。用法 dependencies=[Depends(require_role('operator'))]"""
    async def _guard(
        cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
        r=Depends(deps.get),
    ) -> str:
        username = await current_user(cred, r)
        role = await _role_of(r, username)
        if users_store.ROLE_RANK.get(role, 0) < users_store.ROLE_RANK[min_role]:
            raise HTTPException(403, f"需要 {min_role} 角色（当前 {role}）")
        return username
    return _guard
