"""Agent 配置。"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redis_url: str = "redis://localhost:6379/0"
    agent_host: str = "0.0.0.0"
    agent_port: int = 9100
    node_id: str = "gpu-node-a"
    heartbeat_interval: float = 3.0


settings = Settings()
