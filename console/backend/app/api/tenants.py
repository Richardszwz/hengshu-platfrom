"""租户 API：列表 + 创建/删除（P2 实装隔离/限流/用量累计）。"""
from __future__ import annotations

import secrets
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import deps
from ..keys import TENANT_PREFIX
from ..license_guard import require_active_base, require_engine_cap
from ..security import current_user, require_role

router = APIRouter(prefix="/api/tenants", tags=["tenants"],
                   dependencies=[Depends(current_user)])


@router.get("")
async def list_tenants(r=Depends(deps.get)) -> dict:
    keys = await r.keys(f"{TENANT_PREFIX}*")
    out = []
    for k in keys:
        h = await r.hgetall(k)
        out.append({"tenant_id": k.removeprefix(TENANT_PREFIX), **h})
    return {"tenants": out}


class TenantCreateReq(BaseModel):
    tenant_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,31}$")
    qps_limit: int = Field(default=10, ge=1, le=10000)
    allowed_models: list[str] = []   # ENT 模型白名单：空=不限；router 过滤非授权实例


@router.post("", dependencies=[Depends(require_role("operator")), Depends(require_active_base)])
async def create_tenant(req: TenantCreateReq, r=Depends(deps.get)) -> dict:
    key = f"{TENANT_PREFIX}{req.tenant_id}"
    if await r.exists(key):
        raise HTTPException(409, "tenant exists")
    if req.allowed_models:
        await require_engine_cap("tenant_isolation")(r)   # ENT 能力位
    api_key = f"hs-tnt-{secrets.token_urlsafe(24)}"
    await r.hset(key, mapping={
        "qps_limit": str(req.qps_limit),
        "api_key": api_key,               # 展示一次；哈希存储 P2
        "tokens_total": "0",
        "allowed_models": ",".join(req.allowed_models),
        "created": str(int(time.time())),
    })
    # Router 认证反查索引：api_key → tenant_id
    await r.hset("hs:tokens", api_key, req.tenant_id)
    return {"tenant_id": req.tenant_id, "api_key": api_key,
            "note": "api_key 仅本次返回，请妥善保存"}


class TenantUpdateReq(BaseModel):
    qps_limit: int | None = Field(default=None, ge=1, le=10000)
    allowed_models: list[str] | None = None


@router.put("/{tenant_id}", dependencies=[Depends(require_role("operator")), Depends(require_active_base)])
async def update_tenant(tenant_id: str, req: TenantUpdateReq, r=Depends(deps.get)) -> dict:
    key = f"{TENANT_PREFIX}{tenant_id}"
    if not await r.exists(key):
        raise HTTPException(404, "tenant not found")
    mapping: dict[str, str] = {}
    if req.qps_limit is not None:
        mapping["qps_limit"] = str(req.qps_limit)
    if req.allowed_models is not None:
        if req.allowed_models:
            await require_engine_cap("tenant_isolation")(r)
        mapping["allowed_models"] = ",".join(req.allowed_models)
    if mapping:
        await r.hset(key, mapping=mapping)
    return {"tenant_id": tenant_id, **mapping}


@router.delete("/{tenant_id}", dependencies=[Depends(require_role("operator")), Depends(require_active_base)])
async def delete_tenant(tenant_id: str, r=Depends(deps.get)) -> dict:
    t = await r.hgetall(f"{TENANT_PREFIX}{tenant_id}")
    if not t:
        raise HTTPException(404, "tenant not found")
    if t.get("api_key"):
        await r.hdel("hs:tokens", t["api_key"])  # 清反查索引，Key 立即失效
    await r.delete(f"{TENANT_PREFIX}{tenant_id}")
    return {"removed": tenant_id}
