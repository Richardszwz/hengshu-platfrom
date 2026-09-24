"""认证 API：登录签发 JWT。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..security import issue_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginReq(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(req: LoginReq) -> dict:
    if req.username != __import__("app.config", fromlist=["settings"]).settings.admin_username:
        raise HTTPException(401, "invalid credentials")
    if not verify_password(req.password):
        raise HTTPException(401, "invalid credentials")
    return issue_token(req.username)
