from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field

from app.models.enums import OrderStatus, PaymentMethod, ProductUnit
from app.schemas.common import IdempotencyKey, Money, ORMModel
from app.schemas.ledger import ChargeResponse

Quantity = Annotated[Decimal, Field(gt=0, max_digits=15, decimal_places=3)]


# --- catalog -----------------------------------------------------------------------------


class ProductRead(ORMModel):
    id: uuid.UUID
    sku: str | None
    name: str
    description: str | None
    unit: ProductUnit
    price: Money
    available_quantity: Decimal
    image_url: str | None


# --- orders --------------------------------------------------------------------------------


class OrderLineIn(BaseModel):
    product_id: uuid.UUID
    quantity: Quantity


class OrderCreate(BaseModel):
    lines: list[OrderLineIn] = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=1000)
    submit: bool = Field(default=True, description="false = keep as draft")
    idempotency_key: IdempotencyKey | None = None


class AgentOrderCreate(OrderCreate):
    store_id: uuid.UUID


class OrderItemRead(ORMModel):
    id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    unit: str
    unit_price: Money
    quantity: Decimal
    line_total: Money


class OrderRead(ORMModel):
    id: uuid.UUID
    order_number: int
    store_id: uuid.UUID
    agent_id: uuid.UUID | None
    status: OrderStatus
    total_amount: Money
    note: str | None
    cancel_reason: str | None
    items: list[OrderItemRead]
    created_at: datetime
    submitted_at: datetime | None
    approved_at: datetime | None
    shipped_at: datetime | None
    delivered_at: datetime | None
    cancelled_at: datetime | None


class ApproveOrderRequest(BaseModel):
    enforce_credit_limit: bool = True


class CancelOrderRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class DeliveryResponse(BaseModel):
    order: OrderRead
    charge: ChargeResponse


# --- store / agent views -------------------------------------------------------------------


class LastPaymentRead(BaseModel):
    amount: Money
    method: PaymentMethod
    paid_at: datetime


class StoreMeResponse(BaseModel):
    store_id: uuid.UUID
    store_name: str
    wholesaler_id: uuid.UUID
    wholesaler_name: str
    currency: str
    current_debt: Money
    credit_limit: Money
    available_credit: Money
    overdue_amount: Money
    last_payment: LastPaymentRead | None


class AgentStoreRead(BaseModel):
    id: uuid.UUID
    name: str
    address: str | None
    phone: str | None
    current_debt: Money
    credit_limit: Money
    overdue_amount: Money


class AssignmentResponse(BaseModel):
    agent_id: uuid.UUID
    store_id: uuid.UUID
    assigned: bool


class MeResponse(BaseModel):
    role: str
    wholesaler_id: uuid.UUID
    agent_id: uuid.UUID | None
    store_id: uuid.UUID | None
