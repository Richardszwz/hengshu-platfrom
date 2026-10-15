"""衡枢 Router 配置。"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redis_url: str = "redis://localhost:6379/0"
    router_host: str = "0.0.0.0"
    router_port: int = 9000
    router_api_key: str = "demo-api-key"

    # 实例心跳 TTL（秒）：超过即视为失联，路由绕开
    instance_ttl: int = 15
    # 会话绑定滑动过期（秒）
    session_ttl: int = 24 * 3600
    # 会话历史消息上限（重放用）
    session_history_cap: int = 200
    # 代理到实例的超时
    upstream_timeout: float = 1800.0  # 慢后端（GB10 跑 27B 推理模型 ~9tok/s，长思维链需 6 分钟+）
    # 粘性系数（方案书：粘性 ×0.7，负载域下乘性降低 → 显著偏好原实例）
    sticky_factor: float = 0.7
    # 单次请求失败后重试的候选数（不含首选用例）
    max_retry: int = 1


settings = Settings()
