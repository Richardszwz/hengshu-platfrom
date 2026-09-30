"""Webhook 通知（方案首期）：HENGSHU_WEBHOOK_URL 环境变量启用。

事件类型：
- install.result   节点安装成功/失败（含输出摘要）
- terminal.blocked Web 终端高危命令拦截

发送失败仅记日志，绝不阻塞主流程。
"""
from __future__ import annotations

import logging
import os
import time

import httpx

log = logging.getLogger("hengshu.hooks")
URL_ENV = os.getenv("HENGSHU_WEBHOOK_URL", "")


async def _resolve_url() -> str:
    """优先级：Redis hs:settings.webhook_url（系统设置页可改）> 环境变量。"""
    try:
        from . import deps
        v = await deps.get().hget("hs:settings", "webhook_url")
        if v is not None:
            return v
    except Exception:  # noqa: BLE001 —— redis 不可用时回退 env
        pass
    return URL_ENV


async def emit(kind: str, payload: dict) -> None:
    url = await _resolve_url()
    if not url:
        return
    body = {"event": kind, "ts": int(time.time()),
            "source": "hengshu-console", **payload}
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            await c.post(url, json=body)
    except Exception as e:  # noqa: BLE001 —— 通知失败不能影响主流程
        log.warning("webhook %s → %s failed: %s", kind, url, e)
