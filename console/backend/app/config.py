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
    secret_key: str = "dev-only-insecure-secret-change-me"  # noqa: S105  # 开发默认；生产必须 SECRET_KEY 环境变量覆盖
    token_ttl: int = 12 * 3600
    # 平台版本（与 pyproject.toml / git tag 同步）
    platform_version: str = "1.0.1"

    # 凭证主密钥（派生 AES-GCM 密钥；与 Router/Agent 同值）
    master_key: str = "dev-only-insecure-master-change-me"

    # SSH 推送安装
    agent_image: str = "hengshu/agent:latest"        # 镜像仓库路径（气隙=load tar）
    redis_advertise: str = ""                        # Agent 回连 Redis 地址；空=取 redis_url
    install_tar_dir: str = ""                        # 气隙 tar 目录（P5），空=仓库模式


settings = Settings()
