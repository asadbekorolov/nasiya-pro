from __future__ import annotations

from enum import StrEnum


class OrderStatus(StrEnum):
    """draft -> pending -> approved -> (shipped) -> delivered;  any non-final -> cancelled.

    Stock is reserved on `approved` and written off on `delivered`.
    """

    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


DELIVERABLE_ORDER_STATUSES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.APPROVED, OrderStatus.SHIPPED}
)
# Statuses that hold a stock reservation.
RESERVING_ORDER_STATUSES: frozenset[OrderStatus] = DELIVERABLE_ORDER_STATUSES
CANCELLABLE_ORDER_STATUSES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.DRAFT, OrderStatus.PENDING, OrderStatus.APPROVED, OrderStatus.SHIPPED}
)


class ProductUnit(StrEnum):
    PIECE = "pcs"
    KILOGRAM = "kg"
    LITER = "l"
    METER = "m"
    BOX = "box"
    PACK = "pack"
    BLOCK = "block"


class DebtSource(StrEnum):
    ORDER = "order"
    ADJUSTMENT = "adjustment"  # opening balances, manual corrections


class TransactionType(StrEnum):
    CHARGE = "charge"  # debt increases (delivered order)
    PAYMENT = "payment"  # debt decreases (money received)
    ADJUSTMENT = "adjustment"  # signed correction / opening balance


class PaymentMethod(StrEnum):
    CASH = "cash"
    CARD = "card"
    BANK_TRANSFER = "bank_transfer"
    OTHER = "other"


class OtpStatus(StrEnum):
    PENDING = "pending"  # code sent to the store, waiting for agent input
    VERIFIED = "verified"  # code matched, payment not yet posted
    CONSUMED = "consumed"  # payment posted to the ledger
    EXPIRED = "expired"
    LOCKED = "locked"  # max attempts exceeded
    CANCELLED = "cancelled"  # superseded by a newer code
