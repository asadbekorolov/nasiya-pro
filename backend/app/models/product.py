from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import QUANTITY, Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_type
from app.models.enums import ProductUnit


class Product(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "products"

    wholesaler_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wholesalers.id", ondelete="RESTRICT"), index=True
    )
    sku: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[ProductUnit] = mapped_column(
        enum_type(ProductUnit, "product_unit"),
        default=ProductUnit.PIECE,
        server_default=ProductUnit.PIECE.value,
    )
    price: Mapped[Decimal]
    # On hand in the warehouse.
    stock_quantity: Mapped[Decimal] = mapped_column(
        QUANTITY, default=Decimal("0.000"), server_default="0"
    )
    # Held by approved-but-not-delivered orders. Sellable = stock - reserved.
    reserved_quantity: Mapped[Decimal] = mapped_column(
        QUANTITY, default=Decimal("0.000"), server_default="0"
    )
    image_url: Mapped[str | None] = mapped_column(String(1024))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())

    @property
    def available_quantity(self) -> Decimal:
        return self.stock_quantity - self.reserved_quantity

    __table_args__ = (
        UniqueConstraint("wholesaler_id", "sku"),
        CheckConstraint("price >= 0", name="price_non_negative"),
        CheckConstraint("stock_quantity >= 0", name="stock_non_negative"),
        CheckConstraint(
            "reserved_quantity >= 0 AND reserved_quantity <= stock_quantity",
            name="reserved_within_stock",
        ),
        Index("ix_products_wholesaler_active_name", "wholesaler_id", "is_active", "name"),
    )
