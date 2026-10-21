"""两层授权 API：指纹展示 / 激活 / 状态。

license JSON 存 Redis hs:license:file，console/router/HSE 三处共读。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from libs import hslicense

from .. import deps
from ..security import current_user, require_role

LICENSE_KEY = "hs:license:file"

router = APIRouter(prefix="/api/license", tags=["license"])


async def _state(r) -> hslicense.LicenseState:
    text = await r.get(LICENSE_KEY)
    if not text:
        return hslicense.empty_license()
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return hslicense.empty_license()
    return hslicense.verify_license(doc, hslicense.cluster_fingerprint())


@router.get("/status")
async def status(_: str = Depends(current_user), r=Depends(deps.get)) -> dict:
    st = await _state(r)
    return {
        "ok": st.ok, "reason": st.reason, "customer": st.customer,
        "base_mode": st.base_mode, "base_expires": st.base_expires,
        "max_nodes": st.max_nodes, "version_cap": st.version_cap,
        "edition": st.edition, "caps": st.caps, "engine_expires": st.engine_expires,
        "base_in_grace": st.base_in_grace,
        "fingerprint": hslicense.cluster_fingerprint(),
    }


class ActivateReq(BaseModel):
    license_text: str


@router.post("/activate", dependencies=[Depends(require_role("admin"))])
async def activate(req: ActivateReq, r=Depends(deps.get)) -> dict:
    try:
        doc = json.loads(req.license_text)
    except json.JSONDecodeError:
        raise HTTPException(400, "license 不是合法 JSON") from None
    st = hslicense.verify_license(doc, hslicense.cluster_fingerprint())
    if not st.ok:
        raise HTTPException(400, f"license 校验失败: {st.reason}") from None
    await r.set(LICENSE_KEY, json.dumps(doc, ensure_ascii=False))
    return {"ok": True, "customer": st.customer, "edition": st.edition,
            "caps": st.caps, "base_mode": st.base_mode,
            "base_expires": st.base_expires}


@router.delete("", dependencies=[Depends(require_role("admin"))])
async def deactivate(r=Depends(deps.get)) -> dict:
    await r.delete(LICENSE_KEY)
    return {"removed": True}
