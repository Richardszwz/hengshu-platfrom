"""基本 License 执法：写操作依赖注入。

语义（设计 §3.3）：
- 未激活/签名非法/指纹不符/超宽限期 → 管理面只读：写操作 403（流量面不受此模块影响）。
- 宽限期内 → 放行但响应头带 X-License-Grace 提示。
- 买断制 → 永远放行（版本范围由启动时校验，此处不管）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from fastapi import Depends, HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from libs import hslicense  # noqa: E402

from . import deps  # noqa: E402

LICENSE_KEY = "hs:license:file"


async def current_license_state(r=None) -> hslicense.LicenseState:
    r = r or deps.get()
    text = await r.get(LICENSE_KEY)
    if not text:
        return hslicense.empty_license()
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return hslicense.empty_license()
    return hslicense.verify_license(doc, hslicense.cluster_fingerprint())


async def require_active_base(r=Depends(deps.get)) -> hslicense.LicenseState:
    """写操作守卫：基本 License 必须有效（或在宽限期）。否则 403 只读。"""
    st = await current_license_state(r)
    if st.ok:
        return st
    if st.reason == "base_expired" and st.base_in_grace:
        return st
    reason_map = {
        "missing": "未激活软件基本 License（系统设置 → 授权）",
        "sig_invalid": "License 签名非法",
        "fingerprint_mismatch": "License 与本集群指纹不符",
        "base_expired": "基本 License 已过宽限期，请续费激活",
    }
    raise HTTPException(403, f"管理面只读：{reason_map.get(st.reason, st.reason)}（流量面不受影响）")


async def check_node_cap(r) -> None:
    """纳管新节点前的节点数上限检查（max_nodes=0/空 = 不限）。"""
    st = await current_license_state(r)
    if not st.ok or not st.max_nodes:
        return
    keys = await r.keys("hs:node:*")
    alive = 0
    now = time.time()
    for k in keys:
        h = await r.hgetall(k)
        if h and not (await r.get(f"hs:node_del:{k.removeprefix('hs:node:')}")):
            alive += 1
    if alive >= st.max_nodes:
        raise HTTPException(
            403, f"已达 License 节点上限（{st.max_nodes}），请扩容授权或删除闲置节点")


def require_engine_cap(cap: str):
    """引擎能力位守卫：语义引擎部署等 PRO/ENT 功能要求对应能力位。

    基本 License 先决（同 require_active_base）；再看 engine.caps 含 cap。
    """
    async def guard(r=Depends(deps.get)) -> hslicense.LicenseState:
        st = await require_active_base(r)
        if cap not in (st.caps or []):
            raise HTTPException(
                403, f"该功能需要引擎授权能力「{cap}」（PRO/ENT License，系统设置 → 授权）")
        return st
    return guard
