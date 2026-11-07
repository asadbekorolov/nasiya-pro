"""Telegram Mini App (TWA) authentication.

The Mini App sends `Telegram.WebApp.initData` (a URL-encoded query string signed by
Telegram) with every request:

    Authorization: tma <initData>

Validation follows the Bot API spec ("Validating data received via the Mini App"):

    secret_key       = HMAC_SHA256(key="WebAppData", msg=<bot_token>)
    data_check_string = "\\n".join(f"{k}={v}" for k, v in sorted(fields without "hash"))
    valid            = HMAC_SHA256(key=secret_key, msg=data_check_string) == hash

Only this module knows the wire format; role resolution lives in `app.api.deps`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Annotated, Any
from urllib.parse import parse_qsl

from fastapi import Depends, Header

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError

AUTH_SCHEME = "tma"
# Tolerate small clock differences between Telegram and this server.
MAX_FUTURE_SKEW_SECONDS = 60


@dataclass(frozen=True, slots=True)
class TelegramWebAppUser:
    id: int
    first_name: str | None
    last_name: str | None
    username: str | None
    language_code: str | None
    is_bot: bool
    auth_date: int
    query_id: str | None
    raw: dict[str, Any]


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def compute_init_data_hash(fields: dict[str, str], bot_token: str) -> str:
    """Signature Telegram puts into the `hash` field (exposed for tests/tools)."""
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()) if k != "hash")
    return hmac.new(_secret_key(bot_token), data_check_string.encode(), hashlib.sha256).hexdigest()


def validate_telegram_init_data(
    init_data: str,
    *,
    bot_token: str,
    max_age_seconds: int,
    now: float | None = None,
) -> TelegramWebAppUser:
    if not init_data:
        raise AuthenticationError("Missing Telegram init data")

    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise AuthenticationError("Telegram init data is malformed") from exc
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        # Duplicate keys would make "which value was signed" ambiguous.
        raise AuthenticationError("Telegram init data has duplicate fields")

    fields = dict(pairs)
    received_hash = fields.pop("hash", None)
    if not received_hash:
        raise AuthenticationError("Telegram init data has no hash")
    expected = compute_init_data_hash(fields, bot_token)
    if not hmac.compare_digest(expected, received_hash.lower()):
        raise AuthenticationError("Telegram init data signature is invalid")

    try:
        auth_date = int(fields["auth_date"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Telegram init data has no valid auth_date") from exc
    current = time.time() if now is None else now
    if auth_date - current > MAX_FUTURE_SKEW_SECONDS:
        raise AuthenticationError("Telegram init data is dated in the future")
    if current - auth_date > max_age_seconds:
        raise AuthenticationError("Telegram init data has expired")

    try:
        user = json.loads(fields["user"])
        user_id = int(user["id"])
    except (KeyError, ValueError, TypeError) as exc:
        raise AuthenticationError("Telegram init data has no valid user") from exc
    if user_id <= 0:
        raise AuthenticationError("Telegram init data has no valid user")

    return TelegramWebAppUser(
        id=user_id,
        first_name=user.get("first_name"),
        last_name=user.get("last_name"),
        username=user.get("username"),
        language_code=user.get("language_code"),
        is_bot=bool(user.get("is_bot", False)),
        auth_date=auth_date,
        query_id=fields.get("query_id"),
        raw=user,
    )


def parse_authorization_header(authorization: str | None) -> str:
    if not authorization:
        raise AuthenticationError("Missing Authorization header")
    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() != AUTH_SCHEME or not credentials.strip():
        raise AuthenticationError(f"Expected 'Authorization: {AUTH_SCHEME} <initData>'")
    return credentials.strip()


async def get_telegram_user(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> TelegramWebAppUser:
    """FastAPI dependency: a verified Telegram user (no database access)."""
    user = validate_telegram_init_data(
        parse_authorization_header(authorization),
        bot_token=settings.telegram_bot_token.get_secret_value(),
        max_age_seconds=settings.telegram_init_data_ttl_seconds,
    )
    if user.is_bot:
        raise AuthenticationError("Bots cannot sign in")
    return user


TelegramUserDep = Annotated[TelegramWebAppUser, Depends(get_telegram_user)]
