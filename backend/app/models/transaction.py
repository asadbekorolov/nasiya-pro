from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin, enum_type
from app.models.enums import PaymentMethod, TransactionType


class LedgerTransaction(UUIDPrimaryKeyMixin, Base):
    """Append-only, hash-chained ledger of a store's balance with its wholesaler.

    Sign convention: `amount` is the change of the store's debt.
        charge      -> amount > 0
        payment     -> amount < 0
        adjustment  -> amount != 0 (either sign)

    `balance_after = balance_before + amount` is enforced by a CHECK constraint, and
    every row commits to the previous row of the same store through `prev_hash` /
    `entry_hash` (SHA-256), so a silently edited or deleted row is detectable.
    UPDATE / DELETE / TRUNCATE are rejected by a trigger (see `app.db.ddl`).
    """

    __tablename__ = "transactions"

    wholesaler_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wholesalers.id", ondelete="RESTRICT")
    )
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id", ondelete="RESTRICT"))
    sequence_no: Mapped[int] = mapped_column(BigInteger)

    type: Mapped[TransactionType] = mapped_column(enum_type(TransactionType, "transaction_type"))
    amount: Mapped[Decimal]
    balance_before: Mapped[Decimal]
    balance_after: Mapped[Decimal]

    payment_method: Mapped[PaymentMethod | None] = mapped_column(
        enum_type(PaymentMethod, "payment_method")
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"))
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"), index=True
    )
    otp_verification_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payment_otp_verifications.id", ondelete="RESTRICT"), unique=True
    )

    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    created_by_telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)

    prev_hash: Mapped[str | None] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64), unique=True)

    # Set by the application (not the DB) because it is part of `entry_hash`.
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("store_id", "sequence_no"),
        UniqueConstraint("wholesaler_id", "idempotency_key"),
        CheckConstraint("sequence_no >= 1", name="sequence_positive"),
        CheckConstraint("balance_after = balance_before + amount", name="balance_arithmetic"),
        CheckConstraint(
            "(type = 'charge' AND amount > 0)"
            " OR (type = 'payment' AND amount < 0)"
            " OR (type = 'adjustment' AND amount <> 0)",
            name="amount_sign_by_type",
        ),
        CheckConstraint(
            "(type = 'payment') = (payment_method IS NOT NULL)", name="payment_method_iff_payment"
        ),
        CheckConstraint("type <> 'charge' OR order_id IS NOT NULL", name="charge_has_order"),
        CheckConstraint("(sequence_no = 1) = (prev_hash IS NULL)", name="hash_chain_genesis"),
        # At most one charge per order: a second delivery can never double-bill.
        Index(
            "uq_transactions_order_charge",
            "order_id",
            unique=True,
            postgresql_where=text("type = 'charge'"),
        ),
        Index("ix_transactions_store_created", "store_id", "created_at"),
        Index("ix_transactions_wholesaler_created", "wholesaler_id", "created_at"),
    )
