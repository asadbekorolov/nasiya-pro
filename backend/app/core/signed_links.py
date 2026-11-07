"""Short-lived signed download links.

Telegram's `WebApp.downloadFile({url})` makes the Telegram client fetch the URL itself,
so it cannot send our `Authorization: tma ...` header. Instead an authenticated request
mints a link whose token carries the (already authorised) parameters plus an expiry,
signed with HMAC-SHA256:

    token = base64url(json(payload)) + "." + base64url(hmac(key, that))

The key is derived from OTP_SECRET with a purpose label, so a token for one purpose can
never be replayed for another and no extra secret has to be configured.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from app.core.exceptions import AuthenticationError


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _key(secret: str, purpose: str) -> bytes:
    return hmac.new(secret.encode(), f"signed-link:{purpose}".encode(), hashlib.sha256).digest()


def sign_link(
    payload: dict[str, Any],
    *,
    secret: str,
    purpose: str,
    ttl_seconds: int,
    now: float | None = None,
) -> tuple[str, int]:
    """Return (token, expires_at_unix)."""
    expires_at = int((time.time() if now is None else now) + ttl_seconds)
    body = _b64(
        json.dumps({**payload, "exp": expires_at}, separators=(",", ":"), sort_keys=True).encode()
    )
    signature = _b64(hmac.new(_key(secret, purpose), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}", expires_at


def verify_link(
    token: str, *, secret: str, purpose: str, now: float | None = None
) -> dict[str, Any]:
    try:
        body, signature = token.split(".", 1)
        expected = _b64(hmac.new(_key(secret, purpose), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, signature):
            raise AuthenticationError("Download link is invalid")
        payload: dict[str, Any] = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthenticationError("Download link is invalid") from exc
    if int(payload.get("exp", 0)) < (time.time() if now is None else now):
        raise AuthenticationError("Download link has expired")
    return payload
