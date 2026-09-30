"""控制台用户存储（多用户模式）：Redis hash hs:user:{username}。

角色（数值越大权限越高）：
  viewer   只读（看板/列表/审计）
  operator 运维（节点/服务/规则/租户的增删改）
  admin    全部（另含用户管理/系统设置/Web 终端）

首次启动从环境变量 ADMIN_PASSWORD_HASH 种子出 admin 账户。
"""
from __future__ import annotations

import time

USER_PREFIX = "hs:user:"
ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}


async def get_user(r, username: str) -> dict | None:
    h = await r.hgetall(f"{USER_PREFIX}{username}")
    return h or None


async def create_user(r, username: str, password_hash: str, role: str) -> None:
    await r.hset(f"{USER_PREFIX}{username}", mapping={
        "password_hash": password_hash,
        "role": role,
        "enabled": "1",
        "created": str(int(time.time())),
    })


async def seed_admin(r, env_hash: str, env_username: str = "admin") -> None:
    """用户库为空且 env 有初始哈希 → 种子 admin（幂等）。"""
    if await r.keys(f"{USER_PREFIX}*"):
        return
    if not env_hash:
        return
    await create_user(r, env_username, env_hash, "admin")


async def touch_login(r, username: str) -> None:
    await r.hset(f"{USER_PREFIX}{username}", "last_login", str(int(time.time())))
