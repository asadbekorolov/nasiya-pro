from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from urllib.parse import urlencode

import pytest

from app.core.exceptions import AuthenticationError
from app.core.security import (
    generate_otp_code,
    hash_otp_code,
    validate_telegram_init_data,
    verify_otp_code,
)
from app.core.uuid7 import uuid7, uuid7_timestamp

BOT_TOKEN = "123456:TEST-TOKEN"


def make_init_data(user_id: int, *, token: str = BOT_TOKEN, auth_date: int | None = None) -> str:
    fields = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAH-test",
        "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":")),
    }
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_uuid7_is_version_7_and_monotonic() -> None:
    ids = [uuid7() for _ in range(5000)]
    assert all(u.version == 7 and u.variant == uuid.RFC_4122 for u in ids)
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
    assert abs(uuid7_timestamp(ids[0]).timestamp() - time.time()) < 5


def test_otp_hash_roundtrip() -> None:
    vid = uuid7()
    code = generate_otp_code(4)
    assert len(code) == 4 and code.isdigit()
    h = hash_otp_code(code, verification_id=vid, secret="s" * 32)
    assert verify_otp_code(code, verification_id=vid, secret="s" * 32, expected=h)
    assert not verify_otp_code(code, verification_id=uuid7(), secret="s" * 32, expected=h)
    assert not verify_otp_code(code, verification_id=vid, secret="x" * 32, expected=h)


def test_init_data_valid() -> None:
    user = validate_telegram_init_data(
        make_init_data(42), bot_token=BOT_TOKEN, max_age_seconds=3600
    )
    assert user.id == 42


def test_init_data_bad_signature_and_expiry() -> None:
    with pytest.raises(AuthenticationError):
        validate_telegram_init_data(
            make_init_data(42, token="999:OTHER"), bot_token=BOT_TOKEN, max_age_seconds=3600
        )
    with pytest.raises(AuthenticationError, match="expired"):
        validate_telegram_init_data(
            make_init_data(42, auth_date=int(time.time()) - 7200),
            bot_token=BOT_TOKEN,
            max_age_seconds=3600,
        )
