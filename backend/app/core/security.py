"""Cryptographic helpers for payment OTP codes.

Telegram Mini App authentication lives in `app.core.telegram_auth`.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid


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
