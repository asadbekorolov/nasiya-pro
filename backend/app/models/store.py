from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, LegalDetailsMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Store(UUIDPrimaryKeyMixin, LegalDetailsMixin, TimestampMixin, Base):
    """Retail shop buying on credit (nasiya) from a wholesaler.

    `current_debt` is a denormalised running balance owned exclusively by
    `LedgerService`; it always equals `balance_after` of the store's latest ledger
    transaction. A negative value is an advance (prepayment) held by the wholesaler.
    """

    __tablename__ = "stores"

    wholesaler_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wholesalers.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    contact_name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(32))
    address: Mapped[str | None] = mapped_column(Text)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)

    credit_limit: Mapped[Decimal] = mapped_column(default=Decimal("0.00"), server_default="0")
    current_debt: Mapped[Decimal] = mapped_column(default=Decimal("0.00"), server_default="0")
    # Overrides wholesalers.default_payment_term_days when set.
    payment_term_days: Mapped[int | None] = mapped_column(SmallInteger)

    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())

    __table_args__ = (
        UniqueConstraint("wholesaler_id", "telegram_user_id"),
        CheckConstraint("credit_limit >= 0", name="credit_limit_non_negative"),
        CheckConstraint(
            "payment_term_days IS NULL OR payment_term_days BETWEEN 0 AND 365",
            name="payment_term_range",
        ),
        Index("ix_stores_telegram_user_id", "telegram_user_id"),
    )
