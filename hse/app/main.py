"""衡枢路由引擎 HSE —— STD 骨架。

端点：
  GET  /healthz
  GET  /v1/license/status      授权状态（console 展示）
  POST /v1/route-decision      路由决策（router 调用）
  POST /v1/rules/reload        立即重载规则（console 改规则后可选调用）

license 从 Redis hs:license:file 读取（console 激活写入），5s 缓存。
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import FastAPI
from pydantic import BaseModel

# 仓库根上 path（libs/hslicense 共享库）
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from libs import hslicense

from .config import settings
from .engine import Engine
from .signals import ReqCtx

log = logging.getLogger("hengshu.hse")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="衡枢路由引擎 HSE", version="0.1.0")
r = aioredis.Redis.from_url(settings.redis_url, decode_responses=True)
engine = Engine(r)

_lic_doc: dict | None = None
_lic_state: hslicense.LicenseState | None = None
_lic_at = 0.0


async def license_state() -> hslicense.LicenseState:
    global _lic_doc, _lic_state, _lic_at
    now = time.time()
    if now - _lic_at < settings.license_cache_ttl and _lic_state is not None:
        return _lic_state
    try:
        text = await r.get(settings.license_key)
        _lic_doc = json.loads(text) if text else None
    except Exception:
        _lic_doc = None
    if _lic_doc is None:
        _lic_state = hslicense.empty_license()
    else:
        _lic_state = hslicense.verify_license(_lic_doc, hslicense.cluster_fingerprint())
    _lic_at = now
    return _lic_state


async def _emit(level: str, msg: str) -> None:
    try:
        item = json.dumps({"ts": round(time.time(), 3), "level": level, "msg": msg},
                          ensure_ascii=False)
        pipe = r.pipeline()
        pipe.rpush("hs:events", item)
        pipe.ltrim("hs:events", -200, -1)
        await pipe.execute()
    except Exception:
        pass


class RouteReq(BaseModel):
    prompt: str = ""
    all_text: str = ""
    ctx_tokens: int = 0
    hints: dict = {}


@app.get("/healthz")
async def healthz() -> dict:
    st = await license_state()
    return {"ok": True, "service": "hse", "version": "1.1.0", "edition": st.edition,
            "license_ok": st.ok, "reason": st.reason}


@app.get("/v1/license/status")
async def license_status() -> dict:
    st = await license_state()
    return {
        "ok": st.ok, "reason": st.reason, "customer": st.customer,
        "base_mode": st.base_mode, "base_expires": st.base_expires,
        "max_nodes": st.max_nodes, "version_cap": st.version_cap,
        "edition": st.edition, "caps": st.caps,
        "engine_expires": st.engine_expires,
        "base_in_grace": st.base_in_grace,
        "fingerprint": hslicense.cluster_fingerprint(),
    }


@app.post("/v1/route-decision")
async def route_decision(req: RouteReq) -> dict:
    st = await license_state()
    if not st.ok:
        # 未激活/失效：引擎不决策，调用方（router）走默认池（fail-open）
        return {"decision": {"model": None, "tier": None}, "confidence": 0.0,
                "matched_rule": None, "degraded": True, "reason": st.reason,
                "edition": st.edition}
    ctx = ReqCtx(prompt=req.prompt, all_text=req.all_text or req.prompt,
                 ctx_tokens=req.ctx_tokens, hints=req.hints)
    out = await engine.decide(ctx, st.caps)
    out["edition"] = st.edition
    out["degraded"] = False
    if settings.decision_event and out.get("matched_rule"):
        target = out["decision"]["model"] or out["decision"]["tier"]
        sigs = ", ".join(out["signals"]) or "无条件"
        await _emit("ROUTE", f"hse ▸ 命中规则「{out['rule_name']}」→ {target}（{sigs}）")
    return out


@app.post("/v1/rules/reload")
async def rules_reload() -> dict:
    engine._rules_at = 0.0
    rules = await engine.rules()
    return {"ok": True, "rules": len(rules)}
