"""控制台后端配置。"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redis_url: str = "redis://localhost:6379/0"
    console_host: str = "0.0.0.0"
    console_port: int = 8080
    static_dir: str = "../frontend/dist"

    admin_username: str = "admin"
    # argon2 哈希（scripts/gen_admin_hash.py 生成）；空 = 拒绝登录（P0 安全兜底）
    admin_password_hash: str = ""
    # JWT 签名密钥
    secret_key: str = "dev-only-insecure-secret-change-me"
    token_ttl: int = 12 * 3600

    # 凭证主密钥（派生 AES-GCM 密钥；与 Router/Agent 同值）
    master_key: str = "dev-only-insecure-master-change-me"


settings = Settings()
