from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    app_name: str = "phemex-ai-trader"
    app_version: str = "1.0.0"
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8000

    database_url: str = "postgresql+asyncpg://postgres:postgres@db:5432/phemex_ai_trader"
    redis_url: str = "redis://redis:6379/0"

    phemex_api_key: Optional[str] = None
    phemex_api_secret: Optional[str] = None
    phemex_testnet: bool = True

    llm_provider: str = "openrouter"
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    azure_openai_endpoint: Optional[str] = None
    azure_openai_key: Optional[str] = None
    azure_openai_deployment: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    llm_model: str = "mistralai/mixtral-8x7b-instruct"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 1000

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 60 * 24 * 7

    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    rate_limit_per_minute: int = 120

    # Email
    mail_server_domain: str = "wx-microservice-email.herokuapp.com"
    mail_server_api_key: Optional[str] = None
    mail_to_address: str = "trading@webnostix.co.uk"
    mail_from_address: str = "trading@phemex-ai-trader.com"
    mail_daily_hour: int = 17  # 5pm


settings = Settings()
