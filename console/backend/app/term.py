"""Web 终端（P1.2）：浏览器 ⇄ 控制台 ⇄ 目标机 SSH。

- 认证：WS query token（同一 JWT 体系；WS 无法带 Authorization 头）
- 安全：命令黑名单（默认高危模式），命中即拦截并审计；全量操作写 hs:events
- 审计：asciinema v2 录屏（控制台本地 TERM_RECORD_DIR）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time

import asyncssh
import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi import Depends
from redis.asyncio import Redis

from . import deps, hooks, users_store
from .config import settings
from .crypto import decrypt
from .keys import CRED_PREFIX, NODE_PREFIX

log = logging.getLogger("hengshu.term")

router = APIRouter()

RECORD_DIR = os.getenv("TERM_RECORD_DIR", "D:/hengshu/recordings")
# 高危命令模式（分号分隔，逐行输入时命中即拦截）
BLACKLIST = [re.compile(p, re.I) for p in os.getenv(
    "TERM_BLACKLIST",
    r"rm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\s+/(\s|$);mkfs;"
    r"dd\s+if=.*of=/dev/(sd|nvme|hd);:>\s*/dev/sd;"
    r"shutdown;reboot;poweroff;init\s+0;halt;"
    r"curl.*\|\s*(ba)?sh;wget.*\|\s*(ba)?sh;"
    r">\s*/dev/(sd|nvme)",
).split(";")]
MAX_LINE = 4096


async def _ws_auth(token: str | None, r: Redis) -> str:
    """WS 认证：JWT + 用户状态 + admin 角色（Web 终端仅 admin，方案 §3.6）。"""
    if not token:
        raise PermissionError("missing token")
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except Exception as e:  # noqa: BLE001
        raise PermissionError("bad token") from e
    sub = payload.get("sub", "unknown")
    user = await users_store.get_user(r, sub)
    if user is not None:
        if user.get("enabled", "1") != "1":
            raise PermissionError("user disabled")
        if users_store.ROLE_RANK.get(user.get("role", "viewer"), 0) < users_store.ROLE_RANK["admin"]:
            raise PermissionError("terminal requires admin role")
    return sub


async def _audit(r: Redis, level: str, msg: str) -> None:
    try:
        await r.lpush("hs:events", json.dumps(
            {"ts": int(time.time() * 1000), "level": level, "msg": msg}, ensure_ascii=False))
        await r.ltrim("hs:events", 0, 499)
    except Exception as e:  # noqa: BLE001
        log.warning("audit write failed: %s", e)


class _Recorder:
    """asciinema v2 录屏。"""

    def __init__(self, path: str, cols: int, rows: int) -> None:
        self.path = path
        self.t0 = time.time()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.f = open(path, "w", encoding="utf-8")  # noqa: SIM115
        self.f.write(json.dumps({"version": 2, "width": cols, "height": rows,
                                 "timestamp": int(self.t0),
                                 "env": {"TERM": "xterm-256color", "SHELL": "/bin/bash"}}) + "\n")

    def event(self, kind: str, data: str) -> None:
        if not data:
            return
        self.f.write(json.dumps([round(time.time() - self.t0, 6), kind, data],
                                ensure_ascii=False) + "\n")
        self.f.flush()

    def close(self) -> None:
        try:
            self.f.close()
        except Exception:  # noqa: BLE001
            pass


@router.websocket("/api/term/ws")
async def term_ws(ws: WebSocket) -> None:
    q = ws.query_params
    r = deps.get()
    try:
        user = await _ws_auth(q.get("token"), r)
    except PermissionError as e:
        await ws.close(code=4401, reason=str(e))
        return
    node_id = q.get("node_id", "")
    cols = int(q.get("cols", "80"))
    rows = int(q.get("rows", "24"))
    await ws.accept()
    r: Redis = deps.get()

    blob = await r.get(f"{CRED_PREFIX}{node_id}")
    conn_meta = await r.hgetall(f"hs:conn:{node_id}")
    if not blob or not conn_meta:
        await ws.send_json({"type": "err", "msg": f"{node_id} 无已存凭证/连接信息，请先安装纳管"})
        await ws.close()
        return
    try:
        secret = decrypt(blob)
        username = conn_meta.get("username", "root")
        if "PRIVATE KEY" in secret:
            connect_kw = {"username": username,
                          "client_keys": [asyncssh.import_private_key(secret)]}
        else:
            connect_kw = {"username": username, "password": secret}
    except Exception as e:  # noqa: BLE001
        await ws.send_json({"type": "err", "msg": f"凭证解析失败: {e}"})
        await ws.close()
        return

    host = conn_meta.get("host", node_id)
    ssh_port = int(conn_meta.get("port", "22"))
    rec_path = os.path.join(RECORD_DIR, f"{int(time.time())}_{node_id}_{user}.cast")
    rec: _Recorder | None = None
    conn = None
    proc = None
    try:
        conn = await asyncio.wait_for(
            asyncssh.connect(host, port=ssh_port,
                             known_hosts=None, **connect_kw), timeout=15)
        proc = await conn.create_process(
            term_type="xterm-256color", term_size=(rows, cols),
            env={"LANG": "C.UTF-8"})
        rec = _Recorder(rec_path, cols, rows)
        await _audit(r, "AUDIT", f"term ▸ {user} 连接 {node_id}（录屏 {os.path.basename(rec_path)}）")
        await ws.send_json({"type": "ready", "node": node_id, "rec": os.path.basename(rec_path)})

        async def ssh_to_ws() -> None:
            async for data in proc.stdout:
                rec.event("o", data)
                await ws.send_json({"type": "out", "data": data})

        async def ws_to_ssh() -> None:
            line = ""
            async for raw in ws.iter_text():
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = msg.get("type")
                if t == "in":
                    data = msg.get("data", "")
                    rec.event("i", data)
                    line += data
                    if len(line) > MAX_LINE:
                        line = line[-MAX_LINE:]
                    if "\r" in data or "\n" in data:
                        for one in re.split(r"[\r\n]+", line):
                            one = one.strip()
                            if not one:
                                continue
                            await _audit(r, "AUDIT", f"term ▸ {user}@{node_id} $ {one[:200]}")
                            if any(p.search(one) for p in BLACKLIST):
                                await _audit(r, "WARN", f"term ▸ 拦截高危命令: {one[:120]}")
                                await hooks.emit("terminal.blocked", {
                                    "node_id": node_id, "user": user, "cmd": one[:200]})
                                await ws.send_json(
                                    {"type": "blocked", "cmd": one[:120],
                                     "msg": "⛔ 高危命令已被策略拦截（已审计）\r\n"})
                                return  # 关闭会话（防绕过拼接）
                        line = ""
                    proc.stdin.write(data)
                elif t == "resize":
                    proc.change_terminal_size(msg.get("rows", rows), msg.get("cols", cols))

        out_task = asyncio.create_task(ssh_to_ws())
        in_task = asyncio.create_task(ws_to_ssh())
        done, pending = await asyncio.wait({out_task, in_task}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            if t.exception():
                raise t.exception()
    except (WebSocketDisconnect, asyncssh.Error, asyncio.TimeoutError, OSError) as e:
        try:
            await ws.send_json({"type": "err", "msg": str(e) or type(e).__name__})
        except Exception:  # noqa: BLE001
            pass
    finally:
        if proc:
            proc.close()
        if conn:
            conn.close()
        if rec:
            rec.close()
        await _audit(r, "AUDIT", f"term ▸ {user} 断开 {node_id}")
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


@router.get("/api/term/recordings")
async def list_recordings(_=Depends(deps.get)) -> dict:
    files = []
    if os.path.isdir(RECORD_DIR):
        for f in sorted(os.listdir(RECORD_DIR), reverse=True)[:200]:
            p = os.path.join(RECORD_DIR, f)
            files.append({"name": f, "size": os.path.getsize(p),
                          "mtime": int(os.path.getmtime(p))})
    return {"recordings": files}
