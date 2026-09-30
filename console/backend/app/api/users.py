"""用户管理 API（admin 专属）+ 自助改密。"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import deps, users_store
from ..security import current_user, hash_password, require_role, verify_password
from .settings_api import get_admin_hash

router = APIRouter(prefix="/api/users", tags=["users"])

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\-]{2,32}$")


@router.get("", dependencies=[Depends(require_role("admin"))])
async def list_users(r=Depends(deps.get)) -> dict:
    keys = await r.keys(f"{users_store.USER_PREFIX}*")
    users = []
    for k in keys:
        h = await r.hgetall(k)
        users.append({
            "username": k.split(":", 2)[-1],
            "role": h.get("role", "viewer"),
            "enabled": h.get("enabled", "1") == "1",
            "created": int(h.get("created", "0")),
            "last_login": int(h.get("last_login", "0")),
        })
    return {"users": sorted(users, key=lambda u: u["username"])}


class CreateUserReq(BaseModel):
    username: str
    password: str = Field(min_length=6)
    role: str = "viewer"


@router.post("", dependencies=[Depends(require_role("admin"))])
async def create_user(req: CreateUserReq, r=Depends(deps.get)) -> dict:
    if not USERNAME_RE.match(req.username):
        raise HTTPException(400, "用户名须为 2-32 位字母/数字/_/-")
    if req.role not in users_store.ROLE_RANK:
        raise HTTPException(400, f"角色须为 {'/'.join(users_store.ROLE_RANK)}")
    if await users_store.get_user(r, req.username):
        raise HTTPException(409, "用户名已存在")
    await users_store.create_user(r, req.username, hash_password(req.password), req.role)
    return {"created": req.username, "role": req.role}


class UpdateUserReq(BaseModel):
    role: str | None = None
    enabled: bool | None = None


@router.put("/{username}", dependencies=[Depends(require_role("admin"))])
async def update_user(username: str, req: UpdateUserReq,
                      me: str = Depends(current_user), r=Depends(deps.get)) -> dict:
    user = await users_store.get_user(r, username)
    if not user:
        raise HTTPException(404, "用户不存在")
    if username == me and (req.enabled is False or (req.role and req.role != "admin")):
        raise HTTPException(400, "不能禁用/降级自己")
    mapping: dict[str, str] = {}
    if req.role is not None:
        if req.role not in users_store.ROLE_RANK:
            raise HTTPException(400, "非法角色")
        mapping["role"] = req.role
    if req.enabled is not None:
        mapping["enabled"] = "1" if req.enabled else "0"
    if mapping:
        await r.hset(f"{users_store.USER_PREFIX}{username}", mapping=mapping)
    return {"updated": username, **mapping}


@router.delete("/{username}", dependencies=[Depends(require_role("admin"))])
async def delete_user(username: str, me: str = Depends(current_user),
                      r=Depends(deps.get)) -> dict:
    if username == me:
        raise HTTPException(400, "不能删除自己")
    if not await users_store.get_user(r, username):
        raise HTTPException(404, "用户不存在")
    await r.delete(f"{users_store.USER_PREFIX}{username}")
    return {"deleted": username}


class ChangePwdReq(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6)


@router.post("/me/password")
async def change_my_password(req: ChangePwdReq, me: str = Depends(current_user),
                             r=Depends(deps.get)) -> dict:
    """任何登录用户改自己的密码（写入用户库；admin 种子前回退 env/设置覆盖哈希）。"""
    user = await users_store.get_user(r, me)
    current = user["password_hash"] if user else await get_admin_hash(r)
    if not current or not verify_password(req.old_password, current):
        raise HTTPException(403, "原密码错误")
    if user:
        await r.hset(f"{users_store.USER_PREFIX}{me}", "password_hash", hash_password(req.new_password))
    else:
        # env 种子账户：落库转正式用户（admin 角色）
        await users_store.create_user(r, me, hash_password(req.new_password), "admin")
    return {"ok": True, "msg": "密码已更新，请重新登录"}
