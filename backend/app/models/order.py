from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import QUANTITY, Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_type
from app.models.enums import OrderStatus


class Order(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orders"

    wholesaler_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wholesalers.id", ondelete="RESTRICT")
    )
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id", ondelete="RESTRICT"))
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), index=True
    )
    # Human-friendly number shown in the Mini App / on documents ("#10423").
    order_number: Mapped[int] = mapped_column(BigInteger, Identity(start=10001), unique=True)
    status: Mapped[OrderStatus] = mapped_column(
        enum_type(OrderStatus, "order_status"),
        default=OrderStatus.PENDING,
        server_default=OrderStatus.PENDING.value,
    )
    total_amount: Mapped[Decimal] = mapped_column(default=Decimal("0.00"), server_default="0")
    note: Mapped[str | None] = mapped_column(Text)
    cancel_reason: Mapped[str | None] = mapped_column(Text)

    # Who placed the order (store owner or agent) and a client-side retry key.
    created_by_telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))

    submitted_at: Mapped[datetime | None]
    approved_at: Mapped[datetime | None]
    shipped_at: Mapped[datetime | None]
    delivered_at: Mapped[datetime | None]
    cancelled_at: Mapped[datetime | None]

    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by="OrderItem.id",
    )

    __table_args__ = (
        UniqueConstraint("wholesaler_id", "idempotency_key"),
        CheckConstraint("total_amount >= 0", name="total_non_negative"),
        CheckConstraint(
            "status IN ('draft', 'cancelled') OR submitted_at IS NOT NULL",
            name="submitted_at_matches",
        ),
        CheckConstraint(
            "status NOT IN ('approved', 'shipped', 'delivered') OR approved_at IS NOT NULL",
            name="approved_at_matches",
        ),
        CheckConstraint(
            "(status = 'delivered') = (delivered_at IS NOT NULL)", name="delivered_at_matches"
        ),
        CheckConstraint(
            "(status = 'cancelled') = (cancelled_at IS NOT NULL)", name="cancelled_at_matches"
        ),
        Index("ix_orders_wholesaler_status_created", "wholesaler_id", "status", "created_at"),
        Index("ix_orders_store_created", "store_id", "created_at"),
    )


class OrderItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "order_items"

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), index=True
    )
    # Snapshots: catalog edits must never change historical orders.
    product_name: Mapped[str] = mapped_column(String(255))
    unit: Mapped[str] = mapped_column(String(32))
    unit_price: Mapped[Decimal]
    quantity: Mapped[Decimal] = mapped_column(QUANTITY)
    line_total: Mapped[Decimal]

    order: Mapped[Order] = relationship(back_populates="items", lazy="raise")

    __table_args__ = (
        UniqueConstraint("order_id", "product_id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
        CheckConstraint("line_total >= 0", name="line_total_non_negative"),
        CheckConstraint(
            "line_total = round(unit_price * quantity, 2)", name="line_total_consistent"
        ),
    )
