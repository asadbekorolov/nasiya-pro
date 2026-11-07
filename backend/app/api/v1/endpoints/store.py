"""Store owner (do'konchi) endpoints: `/store/*`."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import (
    LedgerServiceDep,
    OrderServiceDep,
    PdfServiceDep,
    SessionDep,
    StoreDep,
)
from app.api.v1.endpoints.common import default_period, pdf_response
from app.core.exceptions import PermissionDeniedError
from app.core.money import ZERO
from app.models import OrderStatus, PaymentMethod, Store, Wholesaler
from app.schemas.order import (
    CancelOrderRequest,
    LastPaymentRead,
    OrderCreate,
    OrderRead,
    ProductRead,
    StoreMeResponse,
)
from app.services.order_service import OrderLine

router = APIRouter(prefix="/store", tags=["store"])

STORE_CANCELLABLE = frozenset({OrderStatus.DRAFT, OrderStatus.PENDING, OrderStatus.CANCELLED})


@router.get("/me", response_model=StoreMeResponse)
async def store_me(
    principal: StoreDep, session: SessionDep, ledger: LedgerServiceDep
) -> StoreMeResponse:
    assert principal.store_id is not None
    row = (
        await session.execute(
            select(Store, Wholesaler.name, Wholesaler.currency)
            .join(Wholesaler, Wholesaler.id == Store.wholesaler_id)
            .where(Store.id == principal.store_id)
        )
    ).one()
    store, wholesaler_name, currency = row
    today = await ledger.local_today(principal.wholesaler_id)
    overdue = await ledger.get_overdue_amounts(
        wholesaler_id=principal.wholesaler_id, store_ids=[store.id], as_of=today
    )
    last = await ledger.get_last_payment(store_id=store.id)
    return StoreMeResponse(
        store_id=store.id,
        store_name=store.name,
        wholesaler_id=principal.wholesaler_id,
        wholesaler_name=wholesaler_name,
        currency=currency,
        current_debt=store.current_debt,
        credit_limit=store.credit_limit,
        available_credit=max(store.credit_limit - max(store.current_debt, ZERO), ZERO),
        overdue_amount=overdue.get(store.id, ZERO),
        last_payment=LastPaymentRead(
            amount=-last.amount,
            method=last.payment_method or PaymentMethod.OTHER,
            paid_at=last.created_at,
        )
        if last
        else None,
    )


@router.get("/catalog", response_model=list[ProductRead])
async def store_catalog(
    principal: StoreDep,
    orders: OrderServiceDep,
    q: str | None = Query(default=None, max_length=100),
    in_stock: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ProductRead]:
    products = await orders.list_catalog(
        wholesaler_id=principal.wholesaler_id,
        search=q,
        in_stock_only=in_stock,
        limit=limit,
        offset=offset,
    )
    return [ProductRead.model_validate(p) for p in products]


@router.get("/orders", response_model=list[OrderRead])
async def store_orders(
    principal: StoreDep,
    orders: OrderServiceDep,
    status_filter: list[OrderStatus] | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[OrderRead]:
    assert principal.store_id is not None
    rows = await orders.list_orders(
        wholesaler_id=principal.wholesaler_id,
        store_ids=[principal.store_id],
        statuses=status_filter,
        limit=limit,
        offset=offset,
    )
    return [OrderRead.model_validate(o) for o in rows]


@router.post("/orders", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
async def store_create_order(
    body: OrderCreate, principal: StoreDep, orders: OrderServiceDep
) -> OrderRead:
    assert principal.store_id is not None
    order = await orders.create_order(
        wholesaler_id=principal.wholesaler_id,
        store_id=principal.store_id,
        lines=[OrderLine(line.product_id, line.quantity) for line in body.lines],
        note=body.note,
        submit=body.submit,
        idempotency_key=body.idempotency_key,
        actor_telegram_user_id=principal.telegram_user_id,
    )
    return OrderRead.model_validate(order)


@router.get("/orders/{order_id}", response_model=OrderRead)
async def store_get_order(
    order_id: uuid.UUID, principal: StoreDep, orders: OrderServiceDep
) -> OrderRead:
    order = await orders.get_order(
        wholesaler_id=principal.wholesaler_id, order_id=order_id, store_id=principal.store_id
    )
    return OrderRead.model_validate(order)


@router.post("/orders/{order_id}/submit", response_model=OrderRead)
async def store_submit_order(
    order_id: uuid.UUID, principal: StoreDep, orders: OrderServiceDep
) -> OrderRead:
    await orders.get_order(
        wholesaler_id=principal.wholesaler_id, order_id=order_id, store_id=principal.store_id
    )
    order = await orders.submit_order(wholesaler_id=principal.wholesaler_id, order_id=order_id)
    return OrderRead.model_validate(order)


@router.post("/orders/{order_id}/cancel", response_model=OrderRead)
async def store_cancel_order(
    order_id: uuid.UUID, body: CancelOrderRequest, principal: StoreDep, orders: OrderServiceDep
) -> OrderRead:
    """A store may withdraw its own order until the wholesaler approves it."""
    current = await orders.get_order(
        wholesaler_id=principal.wholesaler_id, order_id=order_id, store_id=principal.store_id
    )
    if current.status not in STORE_CANCELLABLE:
        raise PermissionDeniedError(
            "Approved orders can only be cancelled by the wholesaler", status=current.status.value
        )
    order = await orders.cancel_order(
        wholesaler_id=principal.wholesaler_id, order_id=order_id, reason=body.reason
    )
    return OrderRead.model_validate(order)


@router.get(
    "/statement.pdf",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def store_statement_pdf(
    principal: StoreDep,
    ledger: LedgerServiceDep,
    pdf: PdfServiceDep,
    date_from: date | None = None,
    date_to: date | None = None,
) -> StreamingResponse:
    """Download the official Akt-sverka for the given period (default: this month)."""
    assert principal.store_id is not None
    start, end = await default_period(ledger, principal.wholesaler_id, date_from, date_to)
    document = await pdf.build(
        wholesaler_id=principal.wholesaler_id,
        store_id=principal.store_id,
        date_from=start,
        date_to=end,
    )
    return pdf_response(document)
