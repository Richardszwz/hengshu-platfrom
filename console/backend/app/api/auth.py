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
    from ..config import settings as _s
    from .settings_api import get_admin_hash
    if req.username != _s.admin_username:
        raise HTTPException(401, "invalid credentials")
    # 优先使用「系统设置」里改过的密码（Redis 覆盖），其次环境变量哈希
    effective = await get_admin_hash(r)
    if not verify_password(req.password, effective):
        raise HTTPException(401, "invalid credentials")
    return issue_token(req.username)
