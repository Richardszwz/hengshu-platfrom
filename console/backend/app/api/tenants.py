"""租户 API 骨架（P2 实装隔离/限流/用量；P0 仅列表结构）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import deps
from ..keys import TENANT_PREFIX
from ..security import current_user

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
