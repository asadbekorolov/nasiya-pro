from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_type
from app.models.enums import OtpStatus, PaymentMethod


class PaymentOtpVerification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Proof that the store confirmed a cash hand-over to an agent.

    The agent requests a code -> the bot sends a 4-digit code to the store owner's
    Telegram -> the store owner tells it to the agent -> the agent enters it. Only an
    HMAC of the code is stored; max 3 attempts; valid for 3 minutes.
    """

    __tablename__ = "payment_otp_verifications"

    wholesaler_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wholesalers.id", ondelete="RESTRICT")
    )
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id", ondelete="RESTRICT"))
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="RESTRICT"))

    amount: Mapped[Decimal]
    payment_method: Mapped[PaymentMethod] = mapped_column(
        enum_type(PaymentMethod, "payment_method"), default=PaymentMethod.CASH
    )

    code_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(SmallInteger, default=3, server_default="3")
    status: Mapped[OtpStatus] = mapped_column(
        enum_type(OtpStatus, "otp_status"),
        default=OtpStatus.PENDING,
        server_default=OtpStatus.PENDING.value,
    )

    expires_at: Mapped[datetime]
    verified_at: Mapped[datetime | None]
    consumed_at: Mapped[datetime | None]
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("max_attempts BETWEEN 1 AND 10", name="max_attempts_range"),
        CheckConstraint("attempts >= 0 AND attempts <= max_attempts", name="attempts_range"),
        CheckConstraint("char_length(code_hash) = 64", name="code_hash_len"),
        CheckConstraint(
            "(status IN ('verified', 'consumed')) = (verified_at IS NOT NULL)",
            name="verified_at_matches",
        ),
        CheckConstraint(
            "(status = 'consumed') = (consumed_at IS NOT NULL)", name="consumed_at_matches"
        ),
        # Only one live code per agent/store pair.
        Index(
            "uq_payment_otp_one_pending",
            "store_id",
            "agent_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_payment_otp_agent_created", "agent_id", "created_at"),
    )
