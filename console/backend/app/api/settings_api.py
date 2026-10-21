"""系统设置 API：Webhook 地址 / 管理员密码 / 运行信息（全部立即生效）。

存储：hs:settings hash
  webhook_url  覆盖环境变量 HENGSHU_WEBHOOK_URL（空串=清除覆盖回退 env）
  admin_hash   覆盖环境变量 ADMIN_PASSWORD_HASH（argon2）
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import deps, hooks
from ..config import settings
from ..security import current_user, require_role

router = APIRouter(prefix="/api/settings", tags=["settings"],
                   dependencies=[Depends(current_user)])

SETTINGS_KEY = "hs:settings"


async def get_webhook_url(r) -> str:
    v = await r.hget(SETTINGS_KEY, "webhook_url")
    if v is not None:  # redis 覆盖（含空串=显式关闭）
        return v
    return os.getenv("HENGSHU_WEBHOOK_URL", "")


async def get_admin_hash(r) -> str:
    v = await r.hget(SETTINGS_KEY, "admin_hash")
    return v or settings.admin_password_hash


@router.get("", dependencies=[Depends(require_role("admin"))])
async def get_settings(r=Depends(deps.get)) -> dict:
    webhook = await get_webhook_url(r)
    try:
        await r.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    return {
        "webhook_url": webhook,
        "webhook_source": ("redis" if await r.hexists(SETTINGS_KEY, "webhook_url")
                           else ("env" if os.getenv("HENGSHU_WEBHOOK_URL") else "none")),
        "admin_username": settings.admin_username,
        "router_port": int(os.getenv("ROUTER_PORT", "9000")),
        "router_api_key_set": bool(os.getenv("ROUTER_API_KEY")),
        "redis_ok": redis_ok,
        "install_tar_dir": settings.install_tar_dir,
        "token_ttl_hours": settings.token_ttl // 3600,
    }


class WebhookReq(BaseModel):
    url: str  # 空串 = 关闭通知


@router.put("/webhook", dependencies=[Depends(require_role("admin"))])
async def set_webhook(req: WebhookReq, r=Depends(deps.get)) -> dict:
    url = req.url.strip()
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(400, "webhook 必须是 http(s) 地址")
    await r.hset(SETTINGS_KEY, "webhook_url", url)
    return {"webhook_url": url}


@router.post("/webhook/test", dependencies=[Depends(require_role("admin"))])
async def test_webhook(r=Depends(deps.get)) -> dict:
    url = await get_webhook_url(r)
    if not url:
        raise HTTPException(400, "未配置 webhook 地址")
    await hooks.emit("settings.test", {"msg": "衡枢 webhook 连通测试"})
    return {"sent": True, "url": url}


# 改密已迁至 /api/users/me/password（多用户自助改密，写入用户库）
