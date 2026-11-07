from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, SmallInteger, String, Text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, LegalDetailsMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Wholesaler(UUIDPrimaryKeyMixin, LegalDetailsMixin, TimestampMixin, Base):
    """Tenant: the wholesale business that owns the catalog, stores and agents."""

    __tablename__ = "wholesalers"

    name: Mapped[str] = mapped_column(String(200))
    owner_telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    owner_name: Mapped[str | None] = mapped_column(String(200))  # F.I.O, used in greetings
    phone: Mapped[str | None] = mapped_column(String(32))
    address: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(3), default="UZS", server_default="UZS")
    timezone: Mapped[str] = mapped_column(
        String(64), default="Asia/Tashkent", server_default="Asia/Tashkent"
    )
    default_payment_term_days: Mapped[int] = mapped_column(
        SmallInteger, default=14, server_default="14"
    )
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())

    __table_args__ = (
        CheckConstraint("char_length(currency) = 3", name="currency_iso"),
        CheckConstraint("default_payment_term_days BETWEEN 0 AND 365", name="payment_term_range"),
    )
