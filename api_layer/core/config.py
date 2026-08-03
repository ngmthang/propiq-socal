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

    # Error monitoring — leave empty to disable (see main.py). Get a DSN
    # from sentry.io (free tier available) when you're ready to enable it.
    SENTRY_DSN: str = ""

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

    JWT_SECRET_KEY: str = "insecure-dev-secret-change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # ML model artifacts
    AVM_MODEL_PATH: str = "/app/models/saved/avm/latest"
    LSTM_MODEL_PATH: str = "/app/models/saved/lstm/latest"
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

    SERVING_ONLY: bool = False

    @property
    def api_keys_set(self) -> set[str]:
        return {k.strip() for k in self.API_KEYS.split(",") if k.strip()}

    @property
    def cors_origins(self) -> list[str]:
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"

    def assert_production_ready(self) -> None:
        """Fail loudly at startup if deployed to production with insecure
        defaults. A silent insecure deploy is far worse than a crash - this
        turns 'forgot to set the secret' from a vulnerability into an
        obvious boot error."""
        if not self.is_production:
            return
        problems = []
        if self.JWT_SECRET_KEY == "insecure-dev-secret-change-me-in-production":
            problems.append("JWT_SECRET_KEY is still the insecure default")
        if "propiq-dev-key-change-me" in self.api_keys_set:
            problems.append("API_KEYS still contains the dev default key")
        if self.CORS_ORIGINS.strip() == "*":
            problems.append("CORS_ORIGINS is '*' - lock it to your frontend domain")
        if self.DEBUG:
            problems.append("DEBUG is true in production")
        if problems:
            raise RuntimeError(
                "Refusing to start in production with insecure config:\n  - "
                + "\n  - ".join(problems)
                + "\nSet these as environment variables in your host."
            )

@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()