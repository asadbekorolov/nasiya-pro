"""Cryptographic helpers: payment OTP codes and Telegram WebApp `initData` validation."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl

from app.core.exceptions import AuthenticationError

# --- Payment OTP -------------------------------------------------------------------


def generate_otp_code(length: int = 4) -> str:
    """Cryptographically secure numeric code, zero-padded (e.g. "0427")."""
    return f"{secrets.randbelow(10**length):0{length}d}"


def hash_otp_code(code: str, *, verification_id: uuid.UUID, secret: str) -> str:
    """HMAC-SHA256(secret, "<verification_id>:<code>").

    A 4-digit code has only 10 000 values, so a plain hash would be brute-forced
    offline instantly if the table leaked. Keying it with a server-side secret (pepper)
    and binding it to the verification id makes the stored hash useless on its own.
    """
    message = f"{verification_id}:{code}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_otp_code(code: str, *, verification_id: uuid.UUID, secret: str, expected: str) -> bool:
    candidate = hash_otp_code(code, verification_id=verification_id, secret=secret)
    return hmac.compare_digest(candidate, expected)


# --- Telegram WebApp initData ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TelegramWebAppUser:
    id: int
    first_name: str | None
    last_name: str | None
    username: str | None
    language_code: str | None
    auth_date: int
    raw: dict[str, Any]


def validate_telegram_init_data(
    init_data: str,
    *,
    bot_token: str,
    max_age_seconds: int,
    now: float | None = None,
) -> TelegramWebAppUser:
    """Validate `Telegram.WebApp.initData` as described in the Bot API docs
    ("Validating data received via the Mini App")."""
    if not init_data:
        raise AuthenticationError("Missing Telegram init data")

    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    data = dict(pairs)
    received_hash = data.pop("hash", None)
    if not received_hash:
        raise AuthenticationError("Telegram init data has no hash")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise AuthenticationError("Telegram init data signature is invalid")

    try:
        auth_date = int(data["auth_date"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Telegram init data has no valid auth_date") from exc

    current = time.time() if now is None else now
    if current - auth_date > max_age_seconds:
        raise AuthenticationError("Telegram init data has expired")

    try:
        user = json.loads(data["user"])
        user_id = int(user["id"])
    except (KeyError, ValueError, TypeError) as exc:
        raise AuthenticationError("Telegram init data has no valid user") from exc

    return TelegramWebAppUser(
        id=user_id,
        first_name=user.get("first_name"),
        last_name=user.get("last_name"),
        username=user.get("username"),
        language_code=user.get("language_code"),
        auth_date=auth_date,
        raw=user,
    )
