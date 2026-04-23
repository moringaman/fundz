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

    # Primary database URL — used directly when set as a single variable.
    # On Railway, the managed Postgres add-on exposes individual PGHOST/PGPORT/etc.
    # variables. We build the URL from those when DATABASE_URL is not set, so an
    # empty PGPORT (or any missing piece) doesn't cause a URL parse error at startup.
    database_url: Optional[str] = None
    pghost: Optional[str] = None
    pgport: Optional[str] = None
    pguser: Optional[str] = None
    pgpassword: Optional[str] = None
    pgdatabase: Optional[str] = None

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            url = self.database_url
            # Ensure asyncpg driver prefix
            if url.startswith("postgres://"):
                url = "postgresql+asyncpg://" + url[len("postgres://"):]
            elif url.startswith("postgresql://") and "+asyncpg" not in url:
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            return url
        if self.pghost and self.pguser and self.pgpassword and self.pgdatabase:
            port = self.pgport or "5432"
            return (
                f"postgresql+asyncpg://{self.pguser}:{self.pgpassword}"
                f"@{self.pghost}:{port}/{self.pgdatabase}"
            )
        # Local docker-compose fallback
        return "postgresql+asyncpg://postgres:postgres@db:5432/phemex_ai_trader"

    redis_url: str = "redis://redis:6379/0"

    phemex_api_key: Optional[str] = None
    phemex_api_secret: Optional[str] = None
    phemex_testnet: bool = True

    hyperliquid_wallet_address: Optional[str] = None
    hyperliquid_wallet_key: Optional[str] = None

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
