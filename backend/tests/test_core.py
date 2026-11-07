from __future__ import annotations

import time
import uuid
from decimal import Decimal

from app.core.money import format_amount
from app.core.security import generate_otp_code, hash_otp_code, verify_otp_code
from app.core.uuid7 import uuid7, uuid7_timestamp


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


def test_format_amount() -> None:
    assert format_amount(Decimal("1250000.5")) == "1 250 000.50"
    assert format_amount(Decimal("-80.00"), "UZS") == "-80.00 UZS"
    assert format_amount(Decimal("0")) == "0.00"
