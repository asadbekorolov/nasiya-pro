"""Settings as a hosting platform (Render) provides them: env vars only, no .env file."""

from __future__ import annotations

import pytest

from app.core.config import DEFAULT_FRONTEND_URL, Settings, derive_secret

TOKEN = "123456789:" + "A" * 35

MANAGED = (
    "TELEGRAM_BOT_TOKEN",
    "BOT_TOKEN",
    "OTP_SECRET",
    "WEBAPP_URL",
    "FRONTEND_URL",
    "CORS_ORIGINS",
    "BOT_MODE",
    "BOT_WEBHOOK_BASE_URL",
    "BOT_WEBHOOK_SECRET",
    "RENDER_EXTERNAL_URL",
    "OTP_DELIVERY",
)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in MANAGED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    return monkeypatch


def load() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_bot_token_alias(env: pytest.MonkeyPatch) -> None:
    env.setenv("BOT_TOKEN", TOKEN)
    assert load().telegram_bot_token.get_secret_value() == TOKEN


def test_telegram_bot_token_name_still_works(env: pytest.MonkeyPatch) -> None:
    env.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    assert load().telegram_bot_token.get_secret_value() == TOKEN


def test_missing_bot_token_is_reported(env: pytest.MonkeyPatch) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="telegram_bot_token"):
        load()


def test_otp_secret_is_derived_from_the_token_when_unset(env: pytest.MonkeyPatch) -> None:
    env.setenv("BOT_TOKEN", TOKEN)
    first = load().otp_secret.get_secret_value()
    assert first == load().otp_secret.get_secret_value()  # stable across restarts
    assert first == derive_secret(TOKEN, "otp")
    assert len(first) == 64
    assert TOKEN not in first

    env.setenv("BOT_TOKEN", TOKEN[:-1] + "B")
    assert load().otp_secret.get_secret_value() != first


def test_explicit_otp_secret_wins(env: pytest.MonkeyPatch) -> None:
    env.setenv("BOT_TOKEN", TOKEN)
    env.setenv("OTP_SECRET", "x" * 40)
    assert load().otp_secret.get_secret_value() == "x" * 40


def test_frontend_url_defaults(env: pytest.MonkeyPatch) -> None:
    env.setenv("BOT_TOKEN", TOKEN)
    settings = load()
    assert settings.cors_origins == [DEFAULT_FRONTEND_URL]
    assert settings.webapp_url == DEFAULT_FRONTEND_URL
    assert settings.webapp_buttons_enabled


def test_explicit_urls_and_comma_separated_cors(env: pytest.MonkeyPatch) -> None:
    env.setenv("BOT_TOKEN", TOKEN)
    env.setenv("WEBAPP_URL", "https://app.example.uz")
    env.setenv("CORS_ORIGINS", "https://app.example.uz, https://nasiya-pro.pages.dev")
    settings = load()
    assert settings.webapp_url == "https://app.example.uz"
    assert settings.cors_origins == ["https://app.example.uz", DEFAULT_FRONTEND_URL]


def test_json_cors_and_disabled_frontend(env: pytest.MonkeyPatch) -> None:
    env.setenv("BOT_TOKEN", TOKEN)
    env.setenv("FRONTEND_URL", "")
    env.setenv("CORS_ORIGINS", '["https://a.example.uz"]')
    settings = load()
    assert settings.cors_origins == ["https://a.example.uz"]
    assert settings.webapp_url is None


def test_render_webhook_needs_only_the_token(env: pytest.MonkeyPatch) -> None:
    env.setenv("BOT_TOKEN", TOKEN)
    env.setenv("BOT_MODE", "webhook")
    env.setenv("RENDER_EXTERNAL_URL", "https://nasiya-pro-api.onrender.com/")
    settings = load()
    assert settings.bot_webhook_base_url == "https://nasiya-pro-api.onrender.com"
    secret = settings.bot_webhook_secret
    assert secret is not None
    assert secret.get_secret_value() == derive_secret(TOKEN, "webhook")
    assert secret.get_secret_value() != settings.otp_secret.get_secret_value()


def test_explicit_webhook_base_url_wins(env: pytest.MonkeyPatch) -> None:
    env.setenv("BOT_TOKEN", TOKEN)
    env.setenv("BOT_MODE", "webhook")
    env.setenv("RENDER_EXTERNAL_URL", "https://nasiya-pro-api.onrender.com")
    env.setenv("BOT_WEBHOOK_BASE_URL", "https://api.example.uz")
    assert load().bot_webhook_base_url == "https://api.example.uz"
