"""Application settings loaded from environment variables / `.env`."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---
    app_name: str = "Nasiya Pro"
    environment: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=list)

    # --- Database ---
    database_url: str = "postgresql+asyncpg://nasiya:nasiya@localhost:5432/nasiya_pro"
    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=20, ge=0)
    db_pool_timeout: int = Field(default=30, ge=1)
    db_pool_recycle: int = Field(default=1800, ge=60)
    db_echo: bool = False
    db_statement_timeout_ms: int = Field(default=15_000, ge=0)
    db_lock_timeout_ms: int = Field(default=5_000, ge=0)
    db_use_pgbouncer: bool = False
    # Session TimeZone for SQL-side date math. Empty => keep the server default.
    db_session_timezone: str = "UTC"

    # --- Telegram ---
    telegram_bot_token: SecretStr
    telegram_init_data_ttl_seconds: int = Field(default=86_400, ge=60)

    # --- Payment OTP ---
    otp_secret: SecretStr
    otp_ttl_seconds: int = Field(default=180, ge=30, le=3600)
    otp_max_attempts: int = Field(default=3, ge=1, le=10)
    otp_code_length: int = Field(default=4, ge=4, le=8)
    otp_required_for_agent_payments: bool = True

    # --- Business defaults ---
    default_currency: str = Field(default="UZS", min_length=3, max_length=3)
    default_timezone: str = "Asia/Tashkent"

    @model_validator(mode="after")
    def _validate_secrets(self) -> Settings:
        if len(self.otp_secret.get_secret_value()) < 32:
            raise ValueError("OTP_SECRET must be at least 32 characters long")
        if self.environment == "production" and self.debug:
            raise ValueError("DEBUG must be false in production")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
