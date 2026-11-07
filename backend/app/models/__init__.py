"""Importing this package registers every model on `Base.metadata` (used by Alembic)."""

from sqlalchemy import DDL, event

from app.db.base import Base
from app.db.ddl import (
    APPEND_ONLY_TABLES,
    CREATE_APPEND_ONLY_FUNCTION,
    create_append_only_triggers,
)
from app.models.agent import Agent
from app.models.assignment import AgentStoreAssignment
from app.models.debt import Debt, DebtAllocation
from app.models.enums import (
    CANCELLABLE_ORDER_STATUSES,
    DELIVERABLE_ORDER_STATUSES,
    RESERVING_ORDER_STATUSES,
    DebtSource,
    OrderStatus,
    OtpStatus,
    PaymentMethod,
    ProductUnit,
    TransactionType,
)
from app.models.order import Order, OrderItem
from app.models.payment_otp import PaymentOtpVerification
from app.models.product import Product
from app.models.store import Store
from app.models.transaction import LedgerTransaction
from app.models.wholesaler import Wholesaler

# Keep `metadata.create_all()` (tests, local bootstrap) identical to the migrations.
for _table_name in APPEND_ONLY_TABLES:
    _table = Base.metadata.tables[_table_name]
    # DDL() applies %-formatting; escape the plpgsql RAISE placeholders.
    event.listen(
        _table,
        "after_create",
        DDL(CREATE_APPEND_ONLY_FUNCTION.replace("%", "%%")),  # type: ignore[no-untyped-call]
    )
    for _stmt in create_append_only_triggers(_table_name):
        event.listen(_table, "after_create", DDL(_stmt))  # type: ignore[no-untyped-call]

__all__ = [
    "CANCELLABLE_ORDER_STATUSES",
    "DELIVERABLE_ORDER_STATUSES",
    "RESERVING_ORDER_STATUSES",
    "Agent",
    "AgentStoreAssignment",
    "Base",
    "Debt",
    "DebtAllocation",
    "DebtSource",
    "LedgerTransaction",
    "Order",
    "OrderItem",
    "OrderStatus",
    "OtpStatus",
    "PaymentMethod",
    "PaymentOtpVerification",
    "Product",
    "ProductUnit",
    "Store",
    "TransactionType",
    "Wholesaler",
]
