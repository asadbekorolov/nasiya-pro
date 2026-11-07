"""Application settings loaded from environment variables / `.env`."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BOT_TOKEN_RE = re.compile(r"\d{5,16}:[A-Za-z0-9_-]{30,}")


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
    telegram_bot_username: str | None = None  # without "@", used for t.me links
    telegram_init_data_ttl_seconds: int = Field(default=86_400, ge=60)
    # Public HTTPS URL of the Mini App (frontend). Telegram only opens web_app buttons
    # over HTTPS; without it the bot still works but shows no "open app" buttons.
    webapp_url: str | None = None
    # off: API only (tests, extra API workers) · polling: dev / single process ·
    # webhook: production (Telegram POSTs updates to WEBHOOK_BASE_URL)
    bot_mode: Literal["off", "polling", "webhook"] = "off"
    bot_webhook_base_url: str | None = None  # e.g. https://api.example.uz
    bot_webhook_secret: SecretStr | None = None  # X-Telegram-Bot-Api-Secret-Token

    # --- Payment OTP ---
    otp_secret: SecretStr
    otp_ttl_seconds: int = Field(default=180, ge=30, le=3600)
    otp_max_attempts: int = Field(default=3, ge=1, le=10)
    otp_code_length: int = Field(default=4, ge=4, le=8)
    otp_required_for_agent_payments: bool = True
    # "telegram" sends the code to the store via the bot; "log" writes it to the server
    # log instead (local development without a bot). "log" is rejected in production.
    otp_delivery: Literal["telegram", "log"] = "telegram"

    # --- Documents (Akt-sverka PDF) ---
    # A Unicode TTF (Cyrillic + Uzbek Latin). If unset, common system locations are
    # searched (DejaVu on Linux: `apt-get install fonts-dejavu-core`, Arial on Windows).
    pdf_font_regular_path: str | None = None
    pdf_font_bold_path: str | None = None

    # --- Business defaults ---
    default_currency: str = Field(default="UZS", min_length=3, max_length=3)
    default_timezone: str = "Asia/Tashkent"

    @model_validator(mode="after")
    def _validate_secrets(self) -> Settings:
        if len(self.otp_secret.get_secret_value()) < 32:
            raise ValueError("OTP_SECRET must be at least 32 characters long")
        if self.environment == "production" and self.debug:
            raise ValueError("DEBUG must be false in production")
        if self.environment in ("production", "staging") and self.otp_delivery != "telegram":
            raise ValueError("OTP_DELIVERY=log is only allowed in local/test environments")
        token = self.telegram_bot_token.get_secret_value()
        if self.environment in ("production", "staging") and not BOT_TOKEN_RE.fullmatch(token):
            raise ValueError("TELEGRAM_BOT_TOKEN does not look like a BotFather token")
        if self.webapp_url is not None:
            self.webapp_url = self.webapp_url.strip() or None
        if self.webapp_url and not self.webapp_url.startswith(("https://", "http://")):
            raise ValueError("WEBAPP_URL must be an absolute http(s) URL")
        if self.is_production and self.webapp_url and not self.webapp_url.startswith("https://"):
            raise ValueError("WEBAPP_URL must use https in production")
        if self.bot_mode == "webhook":
            base = self.bot_webhook_base_url or ""
            if not base.startswith("https://"):
                raise ValueError("BOT_MODE=webhook needs an https BOT_WEBHOOK_BASE_URL")
            secret = self.bot_webhook_secret.get_secret_value() if self.bot_webhook_secret else ""
            if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", secret):
                raise ValueError("BOT_WEBHOOK_SECRET must be 32-256 chars of [A-Za-z0-9_-]")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def webapp_buttons_enabled(self) -> bool:
        """Telegram rejects web_app buttons whose URL is not HTTPS."""
        return bool(self.webapp_url and self.webapp_url.startswith("https://"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
