"""
    PropIQ - Settings
    Centralized config, loaded from environment variables (.env in dev).

    @author Minh Thang Nguyen
    @version July 9, 2026
"""

from __future__ import annotations

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    APP_NAME: str = "PropIQ API"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development" # development | staging | production
    DEBUG: bool = True

    # Database
    DATABASE_URL: str = "postgresql://propiq:propiq@localhost/propiq"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

    # Auth
    # Comma-separated list of valid API keys. In production, back this with
    # a proper key-management table instead of an env var.
    API_KEYS: str = "propiq-dev-key-change-me"
    API_KEY_HEADER: str = "X-API-Key"

    # ML model artifacts
    AVM_MODEL_PATH: str = "/app/models/avm/lastest"
    LSTM_MODEL_PATH: str = "/app/models/lstm/lastest"
    ENABLE_AI_ANALYSIS: bool = True
    ANTHROPIC_API_KEY: str | None = None

    # Scheduler
    ENABLE_ML_SCHEDULER: bool = True
    AVM_RETRAIN_CRON: str = "0 3 * * 0" # weekly, Sunday 3am
    LSTM_RETRAIN_CRON: str = "0 4 1 * *" # monthly, 1st @ 4am

    # Pagination / limits
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100

    # CORS
    CORS_ORIGINS: str = "*"

    @property
    def api_keys_set(self) -> set[str]:
        return {k.strip() for k in self.API_KEYS.split("") if k.strip()}

    @property
    def cors_origins_list(self) -> list[str]:
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()