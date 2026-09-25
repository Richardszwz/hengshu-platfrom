"""审计日志 API：事件流（hs:events）+ 终端录屏清单。"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends

from .. import deps
from ..security import current_user

router = APIRouter(prefix="/api/audits", tags=["audits"],
                   dependencies=[Depends(current_user)])

EVENTS_KEY = "hs:events"


@router.get("/events")
async def list_events(r=Depends(deps.get)) -> dict:
    """最近 200 条事件（新→旧），统一为 {ts, level, msg}，前端按 level 过滤。

    hs:events 存在两种历史格式：
    - 后端审计：{"ts": 1727.., "level": "AUDIT", "msg": "..."}
    - 前端演示/概览事件：{"level": "INFO", "time": "23:51:10", "text": "..."}
    """
    raw = await r.lrange(EVENTS_KEY, -200, -1)
    events = []
    today = int(time.time())
    for line in reversed(raw):
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = e.get("ts")
        if ts is None:
            # "HH:MM:SS" → 今日时间戳（仅排序展示用）
            try:
                hh, mm, ss = (int(x) for x in str(e.get("time", "")).split(":"))
                d = time.localtime()
                ts = time.mktime((d.tm_year, d.tm_mon, d.tm_mday, hh, mm, ss, 0, 0, -1))
            except (ValueError, TypeError):
                ts = today
        events.append({"ts": float(ts), "level": e.get("level", "INFO"),
                       "msg": e.get("msg") or e.get("text", "")})
    return {"events": events}
