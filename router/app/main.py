"""衡枢 Router：OpenAI 兼容 API 网关。

- POST /v1/chat/completions（含 stream=true SSE 透传）
- session_id 粘性路由：保 KV cache 前缀；实例失联自动迁移并重放历史
- API Key 鉴权（P0 演示单 key；P2 接多租户表）

P0 的 Mock 说明：模拟节点的 /v1/chat/completions 由 scripts/mock_agent.py 内置 HTTP
服务提供，Router 对上游完全按真实 vLLM/TGI 协议代理——与真机接入零差异。
"""
from __future__ import annotations

import json
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
    ctx.update(tid=tid, tenant=t.get("name", tid),
               qps_limit=int(t.get("qps_limit", "0")), checked=True)
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


# ---------- OpenAI 兼容 ----------

class ChatReq(BaseModel):
    model: str
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
            "data": [{"id": n, "object": "model", "created": 0, "owned_by": "hengshu"}
                     for n in names]}


async def resolve_model(model: str, rule_type: str | None) -> str:
    """规则路由：type 命中 hs:rules 映射则改写目标模型（可单测）。"""
    if rule_type:
        mapped = await redis().hget("hs:rules", rule_type)
        if mapped:
            return mapped
    return model


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
    # 规则路由：X-Type / body.type 命中 hs:rules 映射表则改写目标模型
    rule_type = x_type or req.type
    model = await resolve_model(req.model, rule_type)
    candidates = await registry.candidates(model)
    if not candidates:
        raise HTTPException(503, f"model '{model}' has no live instance"
                                  + (f" (type '{rule_type}' 映射)" if rule_type and model != req.model else ""))

    bound = await sessions.get_binding(sid)
    bound_id = bound[1] if bound and bound[0] == model else None
    if bound_id and all(i.inst_id != bound_id for i in candidates):
        bound_id = None                       # 原实例已死 → 迁移

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
        try:
            resp = await _proxy(inst, payload, req.stream)
            await sessions.bind(sid, model, inst.inst_id)
            await sessions.append_history(sid, req.messages)
            await _event("OK", f"route ▸ sess#{sid[:8]} → {inst.inst_id} score+sticky")
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
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            last_err = f"{inst.inst_id}: {e}"
            await _event("WARN", f"retry ▸ sess#{sid[:8]} away from {inst.inst_id} ({e})")
            # 迁移：重放历史给下一实例
            hist = await sessions.history(sid)
            payload["messages"] = hist + req.messages if hist else req.messages
            continue

    raise HTTPException(502, f"all instances failed. last: {last_err}")


async def _proxy(inst: Instance, payload: dict, stream: bool):
    """非流式→收全 JSON；流式→攒迭代器（首包后即视为成功）。"""
    client = httpx.AsyncClient(timeout=settings.upstream_timeout)
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
