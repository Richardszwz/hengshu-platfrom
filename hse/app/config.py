"""HSE 配置。"""
from __future__ import annotations

import os


class Settings:
    redis_url: str = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    port: int = int(os.getenv("HSE_PORT", "9100"))
    rules_key: str = "hs:hse:rules"          # LIST of JSON rule docs（console 维护）
    license_key: str = "hs:license:file"     # license JSON 文本（console 激活写入）
    rules_cache_ttl: float = 2.0
    license_cache_ttl: float = 5.0
    decision_event: bool = os.getenv("HSE_DECISION_EVENT", "1") == "1"


settings = Settings()
