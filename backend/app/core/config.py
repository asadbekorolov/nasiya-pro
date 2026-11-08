"""Application settings loaded from environment variables / `.env`."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from functools import lru_cache
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BOT_TOKEN_RE = re.compile(r"\d{5,16}:[A-Za-z0-9_-]{30,}")

# Public Mini App (Cloudflare Pages). Allowed by CORS and used as WEBAPP_URL unless
# those are set explicitly.
DEFAULT_FRONTEND_URL = "https://nasiya-pro.pages.dev"


def derive_secret(bot_token: str, purpose: str) -> str:
    """A stable secret derived from the bot token (HMAC-SHA256, hex).

    Used when OTP_SECRET / BOT_WEBHOOK_SECRET are not configured. It is as secret as the
    token itself and is the same after a restart and in every worker (unlike a random
    per-process value), and it is not public (unlike a hard-coded default). Rotating the
    bot token rotates it too: pending OTP codes and download links then expire.
    """
    return hmac.new(
        bot_token.encode(), f"nasiya-pro/{purpose}/v1".encode(), hashlib.sha256
    ).hexdigest()


def _origin(url: str) -> str:
    parts = urlsplit(url.strip())
    return f"{parts.scheme}://{parts.netloc}"


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
    # JSON list (["https://a", "https://b"]) or comma separated (https://a,https://b).
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Public URL of the Mini App frontend: added to CORS_ORIGINS and used as WEBAPP_URL
    # when that is unset. Set it empty to disable both.
    frontend_url: str | None = DEFAULT_FRONTEND_URL

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
    # TELEGRAM_BOT_TOKEN, or BOT_TOKEN as many hosting guides call it.
    telegram_bot_token: SecretStr = Field(
        validation_alias=AliasChoices("telegram_bot_token", "bot_token")
    )
    telegram_bot_username: str | None = None  # without "@", used for t.me links
    telegram_init_data_ttl_seconds: int = Field(default=86_400, ge=60)
    # Public HTTPS URL of the Mini App (frontend). Telegram only opens web_app buttons
    # over HTTPS; without it the bot still works but shows no "open app" buttons.
    webapp_url: str | None = None
    # off: API only (tests, extra API workers) · polling: dev / single process ·
    # webhook: production (Telegram POSTs updates to WEBHOOK_BASE_URL)
    bot_mode: Literal["off", "polling", "webhook"] = "off"
    # e.g. https://api.example.uz; on Render the service's own RENDER_EXTERNAL_URL.
    bot_webhook_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("bot_webhook_base_url", "render_external_url"),
    )
    # X-Telegram-Bot-Api-Secret-Token. Empty => derived from the bot token.
    bot_webhook_secret: SecretStr | None = None
    # Bot conversation (FSM) state. Empty => in process memory (one process only);
    # set it in production so every API worker shares state, e.g. redis://redis:6379/0
    redis_url: str | None = None

    # --- Payment OTP ---
    # Signs OTP codes and download links. Empty => derived from the bot token.
    otp_secret: SecretStr = SecretStr("")
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

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("["):
                return json.loads(value)
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _validate_secrets(self) -> Settings:
        token = self.telegram_bot_token.get_secret_value()
        if not self.otp_secret.get_secret_value():
            self.otp_secret = SecretStr(derive_secret(token, "otp"))
        if self.bot_webhook_secret is None or not self.bot_webhook_secret.get_secret_value():
            self.bot_webhook_secret = SecretStr(derive_secret(token, "webhook"))
        if self.frontend_url is not None:
            self.frontend_url = self.frontend_url.strip().rstrip("/") or None
        if self.webapp_url is not None:
            self.webapp_url = self.webapp_url.strip() or None
        if self.bot_webhook_base_url is not None:
            self.bot_webhook_base_url = self.bot_webhook_base_url.strip().rstrip("/") or None
        if self.frontend_url:
            if not self.frontend_url.startswith(("https://", "http://")):
                raise ValueError("FRONTEND_URL must be an absolute http(s) URL")
            origin = _origin(self.frontend_url)
            if origin not in self.cors_origins:
                self.cors_origins = [*self.cors_origins, origin]
            if self.webapp_url is None:
                self.webapp_url = self.frontend_url
        if len(self.otp_secret.get_secret_value()) < 32:
            raise ValueError("OTP_SECRET must be at least 32 characters long")
        if self.environment == "production" and self.debug:
            raise ValueError("DEBUG must be false in production")
        if self.environment in ("production", "staging") and self.otp_delivery != "telegram":
            raise ValueError("OTP_DELIVERY=log is only allowed in local/test environments")
        if self.environment in ("production", "staging") and not BOT_TOKEN_RE.fullmatch(token):
            raise ValueError("TELEGRAM_BOT_TOKEN does not look like a BotFather token")
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
