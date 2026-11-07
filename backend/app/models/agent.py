from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, ForeignKey, Index, String, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Agent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Sales / collection agent working for a wholesaler (via the Telegram Mini App)."""

    __tablename__ = "agents"

    wholesaler_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wholesalers.id", ondelete="RESTRICT"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(32))
    telegram_user_id: Mapped[int] = mapped_column(BigInteger)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())

    __table_args__ = (
        UniqueConstraint("wholesaler_id", "telegram_user_id"),
        Index("ix_agents_telegram_user_id", "telegram_user_id"),
    )
