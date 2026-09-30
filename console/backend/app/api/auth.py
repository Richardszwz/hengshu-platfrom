"""认证 API：登录签发 JWT。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import deps
from ..security import issue_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginReq(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(req: LoginReq, r=Depends(deps.get)) -> dict:
    from .. import users_store
    from .settings_api import get_admin_hash

    user = await users_store.get_user(r, req.username)
    if user is not None:
        # 正式用户：禁用 → 401
        if user.get("enabled", "1") != "1":
            raise HTTPException(401, "invalid credentials")
        if not verify_password(req.password, user["password_hash"]):
            raise HTTPException(401, "invalid credentials")
        await users_store.touch_login(r, req.username)
        return issue_token(req.username)
    # 引导回退：用户库为空且匹配 env/设置覆盖哈希 → 放行（启动钩子里会种子入库）
    from ..config import settings as _s
    if req.username != _s.admin_username:
        raise HTTPException(401, "invalid credentials")
    effective = await get_admin_hash(r)
    if not verify_password(req.password, effective):
        raise HTTPException(401, "invalid credentials")
    return issue_token(req.username)
