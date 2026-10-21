"""衡枢 Router：OpenAI 兼容 API 网关。

- POST /v1/chat/completions（含 stream=true SSE 透传）
- session_id 粘性路由：保 KV cache 前缀；实例失联自动迁移并重放历史
- API Key 鉴权（P0 演示单 key；P2 接多租户表）

P0 的 Mock 说明：模拟节点的 /v1/chat/completions 由 scripts/mock_agent.py 内置 HTTP
服务提供，Router 对上游完全按真实 vLLM/TGI 协议代理——与真机接入零差异。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid

import httpx
import redis.asyncio as aioredis
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from .balancer import pick
from .config import settings
from .keys import EVENTS_KEY, STAT_REQ_KEY
from .registry import Instance, Registry
from .session import SessionStore

log = logging.getLogger("router")

app = FastAPI(title="衡枢 Router", version="0.1.0")
_r: aioredis.Redis | None = None


@app.on_event("startup")
async def _startup() -> None:
    global _redis, registry, sessions
    _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    registry = Registry(_redis)
    sessions = SessionStore(_redis)


# ---------- 依赖 ----------

def redis() -> aioredis.Redis:
    assert _redis is not None
    return _redis


registry: Registry
sessions: SessionStore


def auth(authorization: str = Header(default="")) -> dict:
    """认证并返回租户上下文：admin key → 无限流；租户 key → hs:tokens 反查。
    （同步依赖内直接查 Redis——aioredis 是惰性连接，请求期同步调用不安全，
     故此处只做 admin 快速通道；租户通道由 chat() 异步二次校验。）"""
    token = authorization.removeprefix("Bearer ").strip()
    if token == settings.router_api_key:
        return {"tid": "", "tenant": "admin", "qps_limit": 0, "checked": True}
    return {"tid": "", "tenant": "", "qps_limit": 0, "checked": False, "token": token}


async def auth_tenant(ctx: dict) -> dict:
    """异步租户认证（auth 依赖的补全）：校验租户 key 并回读限额。"""
    if ctx.get("checked"):
        return ctx
    token = ctx.get("token", "")
    if not token:
        raise HTTPException(401, "invalid api key")
    r = redis()
    tid = await r.hget("hs:tokens", token)
    if not tid:
        raise HTTPException(401, "invalid api key")
    t = await r.hgetall(f"hs:tenant:{tid}")
    if not t:
        raise HTTPException(403, "tenant not found")
    allowed = {m.strip() for m in (t.get("allowed_models") or "").split(",") if m.strip()}
    ctx.update(tid=tid, tenant=t.get("name", tid),
               qps_limit=int(t.get("qps_limit", "0")), checked=True,
               allowed_models=allowed)
    return ctx


async def _rate_ok(tid: str, limit: int) -> bool:
    """租户 QPS 滑动窗口（zset，1 秒窗）：超限额 → False。"""
    r = redis()
    now = time.time()
    key = f"hs:rl:{tid}"
    async with r.pipeline() as p:
        p.zremrangebyscore(key, 0, now - 1.0)
        p.zadd(key, {f"{now}-{uuid.uuid4().hex[:8]}": now})
        p.zcard(key)
        p.expire(key, 5)
        _, _, cnt, _ = await p.execute()
    return cnt <= limit


async def _record_usage(tid: str, usage: dict) -> None:
    """计量：usage {prompt_tokens, completion_tokens} → hs:tenant:{tid}.tokens_total。"""
    if not tid or not usage:
        return
    total = int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
    if total > 0:
        await redis().hincrby(f"hs:tenant:{tid}", "tokens_total", total)


# ---------- 前缀亲和（ENT affinity，Dynamo KV-aware 思路的轻量版） ----------
_caps_cache: tuple[float, set[str]] = (0.0, set())


async def _engine_caps() -> set[str]:
    """从已激活 license 读引擎能力位（60s 缓存；license 由 console 激活时验签，可信）。"""
    global _caps_cache
    now = time.time()
    if now - _caps_cache[0] < 60:
        return _caps_cache[1]
    try:
        raw = await redis().get("hs:license:file")
        caps = set((json.loads(raw).get("engine") or {}).get("caps") or []) if raw else set()
    except Exception:  # noqa: BLE001
        caps = _caps_cache[1]  # 抖动用旧缓存
    _caps_cache = (now, caps)
    return caps


def _prefix_hash(messages: list[dict]) -> str:
    """前缀指纹：首条消息前 64 字符 sha1——同前缀请求大概率共享 KV cache block。"""
    if not messages:
        return ""
    first = messages[0].get("content") or ""
    if isinstance(first, list):
        first = " ".join(str(b.get("text", "")) for b in first if isinstance(b, dict))
    first = str(first)[:64]
    if len(first) < 16:      # 太短的前缀没有亲和价值
        return ""
    return hashlib.sha1(first.encode()).hexdigest()[:16]


# ---------- OpenAI 兼容 ----------

class ChatReq(BaseModel):
    model: str = "auto"  # 缺省/“auto”/“*” → 全自动：规则命中走规则，否则全池按负载挑实例
    messages: list[dict]
    session_id: str | None = None
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    type: str | None = None  # 规则路由：类型直达（hs:rules 映射），缺省按 model


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/v1/models")
async def models(_: None = Depends(auth)) -> dict:
    names = await registry.all_models()
    return {"object": "list",
            "data": ([{"id": "auto", "object": "model", "created": 0,
                       "owned_by": "hengshu", "description": "不指定模型：按负载自动选择"}]
                     + [{"id": n, "object": "model", "created": 0, "owned_by": "hengshu"}
                        for n in names])}


async def resolve_model(model: str, rule_type: str | None) -> str:
    """规则路由：type 命中 hs:rules 映射则改写目标模型（可单测）。

    model 缺省/auto/* 表示不指定：规则命中 → 映射模型；否则原样返回 'auto'
    （chat 里对 auto 走全池负载选择）。
    """
    if rule_type:
        mapped = await redis().hget("hs:rules", rule_type)
        if mapped:
            return mapped
    return model


def _msg_text(content) -> str:
    """OpenAI 消息 content 可能是 str 或多模态块列表——统一抽文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(b.get("text", "")) for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    return ""


async def hse_decide(messages: list[dict]) -> tuple[str | None, str | None]:
    """调 HSE 引擎取路由决策；引擎未配置/失联/未命中 → (None, None)（降级默认池）。

    返回 (model, tier)：model 优先；tier 走档位池，档位无实例回退全池。
    """
    if not settings.hse_url:
        return None, None
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    prompt = _msg_text(last_user.get("content")) if last_user else ""
    all_text = "\n".join(_msg_text(m.get("content")) for m in messages)
    body = {"prompt": prompt, "all_text": all_text,
            "ctx_tokens": max(1, len(all_text) // 4), "hints": {}}
    try:
        async with httpx.AsyncClient(timeout=settings.hse_timeout, trust_env=False) as c:
            d = (await c.post(f"{settings.hse_url}/v1/route-decision", json=body)).json()
        if d.get("degraded"):
            return None, None
        dec = d.get("decision") or {}
        return dec.get("model"), dec.get("tier")
    except Exception:  # noqa: BLE001
        return None, None  # 引擎失联/超时 → 默认池（fail-open）


@app.post("/v1/chat/completions")
async def chat(req: ChatReq, x_session_id: str | None = Header(default=None),
               x_type: str | None = Header(default=None),
               ctx: dict = Depends(auth)) -> Response:
    ctx = await auth_tenant(ctx)
    # 租户 QPS 限流（admin key 无限额）
    if ctx["qps_limit"] > 0 and not await _rate_ok(ctx["tid"], ctx["qps_limit"]):
        raise HTTPException(429, f"tenant '{ctx['tenant']}' QPS limit exceeded ({ctx['qps_limit']}/s)")
    sid = x_session_id or req.session_id or uuid.uuid4().hex
    await redis().incr(STAT_REQ_KEY)  # QPS 统计：由控制台按时间差分
    # 请求时间戳流（控制台算滑动窗口 QPS——瞬时差分在稀疏流量下恒为 0）
    _r0 = redis()
    await _r0.rpush("hs:stat:reqts", str(time.time()))
    await _r0.ltrim("hs:stat:reqts", -600, -1)
    # 规则路由：X-Type / body.type 命中 hs:rules 映射表则改写目标模型
    rule_type = x_type or req.type
    model = await resolve_model(req.model, rule_type)
    # HSE 智能路由：接口类型未命中且未指定模型时，问引擎（引擎失联自动降级）
    decided: str | None = None
    decided_tier: str | None = None
    if model in ("", "auto", "*"):
        decided, decided_tier = await hse_decide(req.messages)
        if decided:
            model = decided
    if model in ("", "auto", "*"):
        if decided_tier:
            candidates = await registry.tier_instances(decided_tier)
            if not candidates:
                # 档位无在线实例 → 回退全池（引擎决策是建议不是命令）
                candidates = await registry.all_instances()
        else:
            candidates = await registry.all_instances()   # 不指定模型：全池按负载挑
        if not candidates:
            raise HTTPException(503, "no live instance in cluster"
                                     + (f" (type '{rule_type}' 无规则映射)" if rule_type else ""))
    else:
        candidates = await registry.candidates(model)
        # HSE 指定的模型无在线实例 → 回退全池（引擎决策是建议不是命令）
        if not candidates and decided:
            candidates = await registry.all_instances()
            model = "auto"
    if not candidates:
        raise HTTPException(503, f"model '{model}' has no live instance"
                                 + (f" (type '{rule_type}' 映射)" if rule_type and model != req.model else ""))

    # 租户模型白名单（ENT 多租户隔离）：过滤出该租户被授权的实例
    allowed = ctx.get("allowed_models") or set()
    if allowed:
        candidates = [c for c in candidates if c.model in allowed]
        if not candidates:
            raise HTTPException(403, f"租户 '{ctx['tenant']}' 未被授权使用模型 "
                                     f"'{model}'（白名单: {', '.join(sorted(allowed))}）")
        # 白名单兜底后对外模型名以实际实例为准
        model = candidates[0].model if model in ("", "auto", "*") else model

    bound = await sessions.get_binding(sid)
    bound_id = bound[1] if bound and bound[0] == model else None
    if bound_id and all(i.inst_id != bound_id for i in candidates):
        bound_id = None                       # 原实例已死 → 迁移

    # 前缀亲和（ENT affinity）：同前缀请求优先回原实例（KV cache 命中概率高）
    phash = ""
    if "affinity" in await _engine_caps():
        phash = _prefix_hash(req.messages)
        if phash:
            aff = await redis().get(f"hs:affinity:{phash}")
            if aff and any(c.inst_id == aff for c in candidates):
                candidates = (  # 亲和实例置顶（pick 在同负载下取前）
                    [c for c in candidates if c.inst_id == aff]
                    + [c for c in candidates if c.inst_id != aff])

    payload = {"model": model, "messages": req.messages,
               "stream": req.stream}
    if req.stream:
        # 让 vLLM/OpenAI 兼容上游在末块回传 usage（流式计量用）
        payload["stream_options"] = {"include_usage": True}
    if req.temperature is not None:
        payload["temperature"] = req.temperature
    if req.max_tokens is not None:
        payload["max_tokens"] = req.max_tokens

    last_err: str | None = None
    tried: set[str] = set()
    for _attempt in range(settings.max_retry + 1):
        pool = [i for i in candidates if i.inst_id not in tried]
        if not pool:
            break
        inst = pick(pool, bound_id)
        tried.add(inst.inst_id)
        # 上游真实模型名：实例注册的 served_model 优先（服务名 ≠ vllm --served-model-name 时改写，防上游 404）
        payload["model"] = inst.served_model or inst.model
        try:
            resp = await _proxy(inst, payload, req.stream)
            await sessions.bind(sid, model, inst.inst_id)
            await sessions.append_history(sid, req.messages)
            if phash:  # 亲和回写（30min TTL，随流量滚动）
                await redis().setex(f"hs:affinity:{phash}", 1800, inst.inst_id)
            # 注：不再逐请求写 route ▸ 事件——每秒数十条会把审计/告警淹出环形缓冲
            if not req.stream:
                body = json.loads(resp)
                body["session_id"] = sid
                body["instance"] = inst.inst_id
                await _record_usage(ctx["tid"], body.get("usage"))
                return JSONResponse(body)
            return StreamingResponse(
                _sse_with_footer(resp, sid, inst.inst_id, ctx["tid"]),
                media_type="text/event-stream",
            )
        except (httpx.HTTPError, json.JSONDecodeError, OSError) as e:  # OSError 覆盖 ConnectionResetError 等裸网络异常
            last_err = f"{inst.inst_id}: {type(e).__name__} {e}"
            log.warning("retry away from %s: %r (payload messages=%d, stream=%s)",
                        inst.inst_id, e, len(payload.get("messages", [])), req.stream)
            await _event("WARN", f"retry ▸ sess#{sid[:8]} away from {inst.inst_id} ({type(e).__name__} {e})")
            # 迁移：重放历史给下一实例
            hist = await sessions.history(sid)
            payload["messages"] = hist + req.messages if hist else req.messages
            continue

    raise HTTPException(502, f"all instances failed. last: {last_err}")


async def _proxy(inst: Instance, payload: dict, stream: bool):
    """非流式→收全 JSON；流式→攒迭代器（首包后即视为成功）。"""
    client = httpx.AsyncClient(timeout=settings.upstream_timeout, trust_env=False)  # 集群内网流量不走系统代理
    if not stream:
        r = await client.post(f"{inst.url}/v1/chat/completions", json=payload)
        r.raise_for_status()
        return r.text
    resp_it = client.stream("POST", f"{inst.url}/v1/chat/completions", json=payload)
    return _SSE(client, resp_it)


class _SSE:
    """流式上游句柄：迭代 yield SSE 行，关闭时释放连接。"""

    def __init__(self, client: httpx.AsyncClient, it):
        self.client, self.it = client, it

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        async with self.it as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                yield line + "\n"
        await self.client.aclose()


async def _sse_with_footer(sse: _SSE, sid: str, inst_id: str, tid: str = ""):
    usage_seen: dict = {}
    async for chunk in sse:
        # 流式计量：vLLM/OpenAI 在 stream_options.include_usage 下于末块返回 usage
        if '"usage"' in chunk and '"prompt_tokens"' in chunk:
            for ln in chunk.splitlines():
                if ln.startswith("data: ") and '"usage"' in ln:
                    try:
                        usage_seen = json.loads(ln[6:]).get("usage") or {}
                    except json.JSONDecodeError:
                        pass
        yield chunk
    if usage_seen:
        await _record_usage(tid, usage_seen)
    yield f"data: {json.dumps({'hengshu_meta': {'session_id': sid, 'instance': inst_id}})}\n\n"


async def _event(level: str, msg: str) -> None:
    r = redis()
    line = json.dumps({"ts": round(time.time(), 3), "level": level, "msg": msg},
                      ensure_ascii=False)
    async with r.pipeline() as p:
        p.rpush(EVENTS_KEY, line)
        p.ltrim(EVENTS_KEY, -200, -1)
        await p.execute()


if __name__ == "__main__":
    uvicorn.run(app, host=settings.router_host, port=settings.router_port)
