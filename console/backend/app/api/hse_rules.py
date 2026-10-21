"""HSE 路由规则 CRUD（STD：keyword/context/structure 条件 + model/tier 动作）。

规则存 Redis hs:hse:rules（LIST of JSON），引擎 2s 热更。
"""
from __future__ import annotations

import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import deps
from ..security import current_user, require_role
from ..license_guard import require_active_base, check_node_cap, require_engine_cap

RULES_KEY = "hs:hse:rules"

router = APIRouter(prefix="/api/hse/rules", tags=["hse-rules"],
                   dependencies=[Depends(current_user)])


async def _load(r) -> list[dict]:
    raw = await r.lrange(RULES_KEY, 0, -1)
    out = []
    for line in raw:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


async def _save(r, rules: list[dict]) -> None:
    pipe = r.pipeline()
    pipe.delete(RULES_KEY)
    for doc in rules:
        pipe.rpush(RULES_KEY, json.dumps(doc, ensure_ascii=False))
    await pipe.execute()


@router.get("")
async def list_rules(r=Depends(deps.get)) -> dict:
    rules = await _load(r)
    return {"rules": rules}


class RuleIn(BaseModel):
    name: str
    enabled: bool = True
    priority: int = 100
    when: dict = {}
    action: dict = {}


@router.post("", dependencies=[Depends(require_role("operator")), Depends(require_active_base)])
async def create_rule(req: RuleIn, r=Depends(deps.get)) -> dict:
    doc = req.model_dump()
    doc["id"] = uuid.uuid4().hex[:8]
    doc["ts"] = int(time.time())
    rules = await _load(r)
    rules.append(doc)
    await _save(r, rules)
    return doc


# ---------- 模型参数（cost 同优先级决胜；threshold 为 PRO 语义路由预留） ----------
MODELCFG_KEY = "hs:hse:modelcfg"


@router.get("/modelcfg")
async def get_modelcfg(r=Depends(deps.get)) -> dict:
    raw = await r.hgetall(MODELCFG_KEY)
    out = {}
    for m, c in raw.items():
        try:
            out[m] = json.loads(c)
        except json.JSONDecodeError:
            continue
    return {"models": out}


class ModelCfgIn(BaseModel):
    cost: float = 0.0
    threshold: float = 0.0


@router.put("/modelcfg/{model}", dependencies=[Depends(require_role("operator")), Depends(require_active_base)])
async def put_modelcfg(model: str, req: ModelCfgIn, r=Depends(deps.get)) -> dict:
    doc = {"cost": req.cost, "threshold": req.threshold}
    await r.hset(MODELCFG_KEY, model, json.dumps(doc))
    return {"model": model, **doc}


# ---------- 引擎配置（embedding 端点 / α 激进度） ----------
CONFIG_KEY = "hs:hse:config"


@router.get("/config")
async def get_config(r=Depends(deps.get)) -> dict:
    h = await r.hgetall(CONFIG_KEY)
    return {"config": {
        "embedding_url": h.get("embedding_url", ""),
        "embedding_model": h.get("embedding_model", "bge-m3"),
        "alpha": float(h.get("alpha", 0.5) or 0.5),
        "judge_url": h.get("judge_url", ""),
        "judge_model": h.get("judge_model", ""),
    }}


class ConfigIn(BaseModel):
    embedding_url: str = ""
    embedding_model: str = "bge-m3"
    alpha: float = 0.5
    judge_url: str = ""
    judge_model: str = ""


@router.put("/config", dependencies=[Depends(require_role("operator")), Depends(require_engine_cap("alpha"))])
async def put_config(req: ConfigIn, r=Depends(deps.get)) -> dict:
    mapping = {
        "embedding_url": req.embedding_url,
        "embedding_model": req.embedding_model,
        "alpha": str(max(0.0, min(1.0, req.alpha))),
        "judge_url": req.judge_url,
        "judge_model": req.judge_model,
    }
    # 裁判配置属 ENT：写非空 judge_* 需 judge 能力位
    if req.judge_url or req.judge_model:
        await require_engine_cap("judge")(r)
    await r.hset(CONFIG_KEY, mapping=mapping)
    return {"ok": True}


# ---------- 决策回放（PRO route-replay） ----------
@router.get("/replay")
async def get_replay(limit: int = 100, r=Depends(deps.get)) -> dict:
    raw = await r.lrange("hs:hse:replay", -min(limit, 500), -1)
    out = []
    for line in reversed(raw):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"replay": out}


# ---------- 决策统计（ENT auto 轻量版：replay 聚合，规则效果仪表盘） ----------
@router.get("/stats")
async def get_stats(r=Depends(deps.get)) -> dict:
    raw = await r.lrange("hs:hse:replay", 0, -1)
    per_rule: dict[str, dict] = {}
    total = hit = shadow = 0
    for line in raw:
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        total += 1
        name = e.get("rule_name") or "（未命中）"
        st = per_rule.setdefault(name, {"hits": 0, "conf_sum": 0.0,
                                        "lat_sum": 0.0, "shadow": 0, "last_ts": 0})
        if e.get("shadow"):
            shadow += 1
            st["shadow"] += 1
        if e.get("matched_rule"):
            hit += 1
            st["hits"] += 1
            st["conf_sum"] += float(e.get("confidence") or 0)
            st["lat_sum"] += float(e.get("latency_ms") or 0)
            st["last_ts"] = max(st["last_ts"], e.get("ts") or 0)
    rules_out = [{
        "rule_name": name,
        "hits": st["hits"],
        "avg_conf": round(st["conf_sum"] / st["hits"], 3) if st["hits"] else 0,
        "avg_latency_ms": round(st["lat_sum"] / st["hits"], 1) if st["hits"] else 0,
        "shadow_hits": st["shadow"],
        "last_hit": st["last_ts"],
    } for name, st in sorted(per_rule.items(), key=lambda kv: -kv[1]["hits"])]
    return {"total": total, "hit": hit, "shadow": shadow,
            "hit_rate": round(hit / total, 3) if total else 0,
            "rules": rules_out}


# ---------- 单规则写操作（参数化路径须最后注册，避免吃掉 /config 等字面量路由） ----------
@router.put("/{rule_id}", dependencies=[Depends(require_role("operator")), Depends(require_active_base)])
async def update_rule(rule_id: str, req: RuleIn, r=Depends(deps.get)) -> dict:
    rules = await _load(r)
    for i, doc in enumerate(rules):
        if doc.get("id") == rule_id:
            nd = req.model_dump()
            nd["id"] = rule_id
            nd["ts"] = doc.get("ts", int(time.time()))
            rules[i] = nd
            await _save(r, rules)
            return nd
    raise HTTPException(404, "规则不存在")


@router.delete("/{rule_id}", dependencies=[Depends(require_role("operator")), Depends(require_active_base)])
async def delete_rule(rule_id: str, r=Depends(deps.get)) -> dict:
    rules = await _load(r)
    n = len(rules)
    rules = [d for d in rules if d.get("id") != rule_id]
    if len(rules) == n:
        raise HTTPException(404, "规则不存在")
    await _save(r, rules)
    return {"removed": rule_id}
