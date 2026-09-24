"""控制台后端主程序：API 聚合 + 前端静态托管（无状态，可多实例部署）。"""
from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from . import deps
from .api import auth, dashboard, nodes, rules, services, tenants
from .config import settings

app = FastAPI(title="衡枢 Console", version="0.1.0")


@app.on_event("startup")
async def _startup() -> None:
    deps.init(settings.redis_url)


@app.on_event("shutdown")
async def _shutdown() -> None:
    await deps.close()


@app.get("/api/healthz")
async def healthz() -> dict:
    return {"ok": True, "service": "hengshu-console"}


for m in (auth, nodes, services, dashboard, tenants, rules):
    app.include_router(m.router)

# 前端 SPA：打包产物由 Dockerfile 拷入 static/。
# 需要 history 路由回退（/login 等前端路径返回 index.html），不能只用 StaticFiles。
static_dir = Path(os.getenv("STATIC_DIR", settings.static_dir))
if static_dir.exists():
    from fastapi.responses import FileResponse

    _spa_index = static_dir / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        candidate = (static_dir / full_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(static_dir.resolve()):
            return FileResponse(candidate)
        return FileResponse(_spa_index)


if __name__ == "__main__":
    uvicorn.run(app, host=settings.console_host, port=settings.console_port)
