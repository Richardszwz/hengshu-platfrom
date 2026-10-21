"""规则路由 API 骨架（P2 实装 type→模型规则表 + 默认池兜底）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import deps
from ..license_guard import require_active_base
from ..security import current_user, require_role

router = APIRouter(prefix="/api/rules", tags=["rules"],
                   dependencies=[Depends(current_user)])

RULES_KEY = "hs:rules"


@router.get("")
async def list_rules(r=Depends(deps.get)) -> dict:
    rows = await r.hgetall(RULES_KEY)
    return {"rules": [{"type": k, "model": v} for k, v in rows.items()]}


@router.put("/{rule_type}", dependencies=[Depends(require_role("operator")), Depends(require_active_base)])
async def upsert_rule(rule_type: str, model: str, r=Depends(deps.get)) -> dict:
    await r.hset(RULES_KEY, rule_type, model)
    return {"type": rule_type, "model": model}


@router.delete("/{rule_type}", dependencies=[Depends(require_role("operator")), Depends(require_active_base)])
async def delete_rule(rule_type: str, r=Depends(deps.get)) -> dict:
    await r.hdel(RULES_KEY, rule_type)
    return {"removed": rule_type}
