from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.enums import DebtSource, OtpStatus, PaymentMethod, TransactionType
from app.schemas.common import IdempotencyKey, Money, ORMModel, PositiveMoney

# --- Read models -----------------------------------------------------------------------


class TransactionRead(ORMModel):
    id: uuid.UUID
    store_id: uuid.UUID
    sequence_no: int
    type: TransactionType
    amount: Money
    balance_before: Money
    balance_after: Money
    payment_method: PaymentMethod | None
    order_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    description: str | None
    entry_hash: str
    created_at: datetime


class DebtRead(ORMModel):
    id: uuid.UUID
    store_id: uuid.UUID
    order_id: uuid.UUID | None
    source: DebtSource
    initial_amount: Money
    remaining_amount: Money
    is_closed: bool
    incurred_at: datetime
    due_date: date
    closed_at: datetime | None


class AllocationRead(ORMModel):
    debt_id: uuid.UUID
    order_id: uuid.UUID | None
    amount: Money
    remaining_after: Money
    closed: bool


# --- Delivery ----------------------------------------------------------------------------


class DeliverOrderRequest(BaseModel):
    enforce_credit_limit: bool = False


class ChargeResponse(BaseModel):
    transaction: TransactionRead
    debt: DebtRead
    store_balance: Money
    advance_applied: Money
    created: bool


# --- Payments ----------------------------------------------------------------------------


class PaymentCreate(BaseModel):
    amount: PositiveMoney
    method: PaymentMethod
    idempotency_key: IdempotencyKey | None = None
    description: str | None = Field(default=None, max_length=500)


class PaymentResponse(BaseModel):
    transaction: TransactionRead
    allocations: list[AllocationRead]
    store_balance: Money
    advance_amount: Money
    created: bool


class AdjustmentCreate(BaseModel):
    amount: Money = Field(description="Signed: >0 increases debt, <0 decreases debt")
    reason: str = Field(min_length=3, max_length=500)
    due_date: date | None = None
    incurred_at: datetime | None = None
    idempotency_key: IdempotencyKey | None = None


class AdjustmentResponse(BaseModel):
    transaction: TransactionRead
    debt: DebtRead | None
    allocations: list[AllocationRead]
    store_balance: Money
    created: bool


# --- Payment OTP -------------------------------------------------------------------------


class OtpIssueRequest(BaseModel):
    store_id: uuid.UUID
    amount: PositiveMoney
    method: PaymentMethod = PaymentMethod.CASH


class OtpIssueResponse(ORMModel):
    id: uuid.UUID
    store_id: uuid.UUID
    amount: Money
    payment_method: PaymentMethod
    status: OtpStatus
    max_attempts: int
    expires_at: datetime


class OtpConfirmRequest(BaseModel):
    code: str = Field(pattern=r"^\d{4,8}$")
    idempotency_key: IdempotencyKey | None = None
    description: str | None = Field(default=None, max_length=500)


# --- Reports -----------------------------------------------------------------------------


class StatementResponse(BaseModel):
    store_id: uuid.UUID
    store_name: str
    period_from: datetime
    period_to: datetime
    opening_balance: Money
    total_charges: Money
    total_payments: Money
    total_adjustments: Money
    closing_balance: Money
    transactions: list[TransactionRead]


class AgingRowRead(BaseModel):
    store_id: uuid.UUID
    store_name: str
    buckets: dict[str, Decimal]
    total: Money


class AgingReportResponse(BaseModel):
    as_of: date
    rows: list[AgingRowRead]
    grand_total: Money


class ChainVerificationResponse(BaseModel):
    store_id: uuid.UUID
    ok: bool
    transactions_checked: int
    broken_at_sequence: int | None
    reason: str | None


class StoreBalanceResponse(BaseModel):
    store_id: uuid.UUID
    current_debt: Money
    credit_limit: Money
    available_credit: Money
