from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, false, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_type,
)
from app.models.enums import DebtSource


class Debt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One receivable per delivered order (or per positive adjustment).

    Payments close debts in FIFO order of `incurred_at`; `due_date` drives the aging
    report. Invariant (per store, checked by LedgerService on every write):

        SUM(remaining_amount WHERE NOT is_closed) == GREATEST(stores.current_debt, 0)
    """

    __tablename__ = "debts"

    wholesaler_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wholesalers.id", ondelete="RESTRICT")
    )
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id", ondelete="RESTRICT"))
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), unique=True
    )
    source: Mapped[DebtSource] = mapped_column(
        enum_type(DebtSource, "debt_source"), default=DebtSource.ORDER
    )
    # The ledger row (charge / positive adjustment) that created this debt.
    origin_transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transactions.id", ondelete="RESTRICT"), unique=True
    )

    initial_amount: Mapped[Decimal]
    remaining_amount: Mapped[Decimal]
    is_closed: Mapped[bool] = mapped_column(default=False, server_default=false())

    incurred_at: Mapped[datetime] = mapped_column(server_default=func.now())
    due_date: Mapped[date]
    closed_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint("initial_amount > 0", name="initial_positive"),
        CheckConstraint(
            "remaining_amount >= 0 AND remaining_amount <= initial_amount",
            name="remaining_in_range",
        ),
        CheckConstraint("is_closed = (remaining_amount = 0)", name="closed_iff_zero"),
        CheckConstraint("is_closed = (closed_at IS NOT NULL)", name="closed_at_matches"),
        CheckConstraint("(source = 'order') = (order_id IS NOT NULL)", name="order_source_matches"),
        # FIFO scan of a store's open debts.
        Index(
            "ix_debts_store_open_fifo",
            "store_id",
            "incurred_at",
            "id",
            postgresql_where=text("NOT is_closed"),
        ),
        # Aging report across a tenant.
        Index(
            "ix_debts_wholesaler_open_due",
            "wholesaler_id",
            "due_date",
            postgresql_where=text("NOT is_closed"),
        ),
    )


class DebtAllocation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only audit of which ledger transaction reduced which debt and by how much
    (FIFO trail). Needed to explain every closed debt during a reconciliation."""

    __tablename__ = "debt_allocations"

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transactions.id", ondelete="RESTRICT")
    )
    debt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("debts.id", ondelete="RESTRICT"), index=True
    )
    amount: Mapped[Decimal]

    __table_args__ = (
        UniqueConstraint("transaction_id", "debt_id"),
        CheckConstraint("amount > 0", name="amount_positive"),
    )
