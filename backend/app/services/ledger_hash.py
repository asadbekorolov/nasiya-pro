"""Canonical hashing of ledger rows (tamper evidence for the append-only ledger)."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from app.models.enums import PaymentMethod, TransactionType

HASH_VERSION = "v1"


def _money(value: Decimal) -> str:
    return "0.00" if value == 0 else f"{value:.2f}"


def _opt(value: object | None) -> str:
    return "" if value is None else str(value)


def _ts(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("ledger timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def compute_entry_hash(
    *,
    prev_hash: str | None,
    transaction_id: uuid.UUID,
    wholesaler_id: uuid.UUID,
    store_id: uuid.UUID,
    sequence_no: int,
    tx_type: TransactionType,
    amount: Decimal,
    balance_before: Decimal,
    balance_after: Decimal,
    payment_method: PaymentMethod | None,
    order_id: uuid.UUID | None,
    agent_id: uuid.UUID | None,
    otp_verification_id: uuid.UUID | None,
    description: str | None,
    created_at: datetime,
) -> str:
    parts = [
        HASH_VERSION,
        _opt(prev_hash),
        str(transaction_id),
        str(wholesaler_id),
        str(store_id),
        str(sequence_no),
        TransactionType(tx_type).value,
        _money(amount),
        _money(balance_before),
        _money(balance_after),
        "" if payment_method is None else PaymentMethod(payment_method).value,
        _opt(order_id),
        _opt(agent_id),
        _opt(otp_verification_id),
        _opt(description),
        _ts(created_at),
    ]
    # Unit separator (0x1F) cannot appear in any field except free-text description,
    # where it is escaped, so the encoding is unambiguous.
    payload = "\x1f".join(p.replace("\x1f", "\\x1f") for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
