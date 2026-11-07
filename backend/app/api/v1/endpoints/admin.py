"""Wholesaler admin endpoints: `/admin/*`."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.api.deps import (
    AdminDep,
    EventNotifierDep,
    LedgerServiceDep,
    OrderServiceDep,
    PdfServiceDep,
    SessionDep,
)
from app.api.v1.endpoints.common import default_period, pdf_response
from app.core.exceptions import NotFoundError
from app.core.money import ZERO
from app.core.uuid7 import uuid7
from app.db.uow import atomic
from app.models import Agent, AgentStoreAssignment, OrderStatus, Store, Wholesaler
from app.schemas.ledger import (
    AdjustmentCreate,
    AdjustmentResponse,
    AgingReportResponse,
    AgingRowRead,
    ChainVerificationResponse,
    PaymentCreate,
    PaymentResponse,
    StatementResponse,
    StoreBalanceResponse,
    TransactionRead,
    adjustment_response,
    charge_response,
    payment_response,
)
from app.schemas.order import (
    ApproveOrderRequest,
    AssignmentResponse,
    CancelOrderRequest,
    DeliveryResponse,
    OrderRead,
)

router = APIRouter(prefix="/admin", tags=["admin"])


# --- reports ---------------------------------------------------------------------------


@router.get("/aging-report", response_model=AgingReportResponse)
async def aging_report(
    admin: AdminDep,
    ledger: LedgerServiceDep,
    as_of: date | None = None,
    store_id: uuid.UUID | None = None,
) -> AgingReportResponse:
    """Open receivables by days past due: current, 1-30, 31-60, 61-90, 90+."""
    effective = as_of or await ledger.local_today(admin.wholesaler_id)
    rows = await ledger.get_aging_report(
        wholesaler_id=admin.wholesaler_id, as_of=effective, store_id=store_id
    )
    return AgingReportResponse(
        as_of=effective,
        rows=[
            AgingRowRead(
                store_id=r.store_id, store_name=r.store_name, buckets=r.buckets, total=r.total
            )
            for r in rows
        ],
        grand_total=sum((r.total for r in rows), ZERO),
    )


# --- orders ----------------------------------------------------------------------------


@router.get("/orders", response_model=list[OrderRead])
async def admin_orders(
    admin: AdminDep,
    orders: OrderServiceDep,
    store_id: uuid.UUID | None = None,
    status_filter: list[OrderStatus] | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[OrderRead]:
    rows = await orders.list_orders(
        wholesaler_id=admin.wholesaler_id,
        store_ids=[store_id] if store_id else None,
        statuses=status_filter,
        limit=limit,
        offset=offset,
    )
    return [OrderRead.model_validate(o) for o in rows]


@router.post("/orders/{order_id}/approve", response_model=OrderRead)
async def approve_order(
    order_id: uuid.UUID, body: ApproveOrderRequest, admin: AdminDep, orders: OrderServiceDep
) -> OrderRead:
    order = await orders.approve_order(
        wholesaler_id=admin.wholesaler_id,
        order_id=order_id,
        enforce_credit_limit=body.enforce_credit_limit,
    )
    return OrderRead.model_validate(order)


@router.post("/orders/{order_id}/ship", response_model=OrderRead)
async def ship_order(order_id: uuid.UUID, admin: AdminDep, orders: OrderServiceDep) -> OrderRead:
    order = await orders.ship_order(wholesaler_id=admin.wholesaler_id, order_id=order_id)
    return OrderRead.model_validate(order)


@router.post("/orders/{order_id}/deliver", response_model=DeliveryResponse)
async def deliver_order(
    order_id: uuid.UUID,
    admin: AdminDep,
    orders: OrderServiceDep,
    events: EventNotifierDep,
    background: BackgroundTasks,
) -> DeliveryResponse:
    result = await orders.deliver_order(
        wholesaler_id=admin.wholesaler_id,
        order_id=order_id,
        actor_telegram_user_id=admin.telegram_user_id,
    )
    if result.charge.created:
        background.add_task(events.order_delivered, order_id)
    return DeliveryResponse(
        order=OrderRead.model_validate(result.order), charge=charge_response(result.charge)
    )


@router.post("/orders/{order_id}/cancel", response_model=OrderRead)
async def cancel_order(
    order_id: uuid.UUID, body: CancelOrderRequest, admin: AdminDep, orders: OrderServiceDep
) -> OrderRead:
    order = await orders.cancel_order(
        wholesaler_id=admin.wholesaler_id, order_id=order_id, reason=body.reason
    )
    return OrderRead.model_validate(order)


# --- money -----------------------------------------------------------------------------


@router.post(
    "/stores/{store_id}/payments",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_payment(
    store_id: uuid.UUID,
    body: PaymentCreate,
    admin: AdminDep,
    ledger: LedgerServiceDep,
    events: EventNotifierDep,
    background: BackgroundTasks,
) -> PaymentResponse:
    """Money received directly by the wholesaler (bank transfer, cash at the warehouse)."""
    result = await ledger.record_payment(
        wholesaler_id=admin.wholesaler_id,
        store_id=store_id,
        amount=body.amount,
        method=body.method,
        idempotency_key=body.idempotency_key,
        description=body.description,
        actor_telegram_user_id=admin.telegram_user_id,
    )
    if result.created:
        background.add_task(events.payment_received, result.transaction.id)
    return payment_response(result)


@router.post(
    "/stores/{store_id}/adjustments",
    response_model=AdjustmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_adjustment(
    store_id: uuid.UUID, body: AdjustmentCreate, admin: AdminDep, ledger: LedgerServiceDep
) -> AdjustmentResponse:
    result = await ledger.record_adjustment(
        wholesaler_id=admin.wholesaler_id,
        store_id=store_id,
        amount=body.amount,
        reason=body.reason,
        due_date=body.due_date,
        incurred_at=body.incurred_at,
        idempotency_key=body.idempotency_key,
        actor_telegram_user_id=admin.telegram_user_id,
    )
    return adjustment_response(result)


@router.get("/stores/{store_id}/balance", response_model=StoreBalanceResponse)
async def store_balance(
    store_id: uuid.UUID, admin: AdminDep, ledger: LedgerServiceDep
) -> StoreBalanceResponse:
    store = await ledger.get_store(admin.wholesaler_id, store_id)
    return StoreBalanceResponse(
        store_id=store.id,
        current_debt=store.current_debt,
        credit_limit=store.credit_limit,
        available_credit=max(store.credit_limit - max(store.current_debt, ZERO), ZERO),
    )


@router.get("/stores/{store_id}/statement", response_model=StatementResponse)
async def store_statement(
    store_id: uuid.UUID,
    admin: AdminDep,
    ledger: LedgerServiceDep,
    session: SessionDep,
    date_from: date | None = None,
    date_to: date | None = None,
) -> StatementResponse:
    start, end = await default_period(ledger, admin.wholesaler_id, date_from, date_to)
    tz_name = await session.scalar(
        select(Wholesaler.timezone).where(Wholesaler.id == admin.wholesaler_id)
    )
    tz = ZoneInfo(tz_name or "UTC")
    st = await ledger.get_statement(
        wholesaler_id=admin.wholesaler_id,
        store_id=store_id,
        period_from=datetime.combine(start, time.min, tzinfo=tz),
        period_to=datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz),
    )
    return StatementResponse(
        store_id=st.store.id,
        store_name=st.store.name,
        period_from=st.period_from,
        period_to=st.period_to,
        opening_balance=st.opening_balance,
        total_charges=st.total_charges,
        total_payments=st.total_payments,
        total_adjustments=st.total_adjustments,
        closing_balance=st.closing_balance,
        transactions=[TransactionRead.model_validate(t) for t in st.transactions],
    )


@router.get(
    "/stores/{store_id}/statement.pdf",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def store_statement_pdf(
    store_id: uuid.UUID,
    admin: AdminDep,
    ledger: LedgerServiceDep,
    pdf: PdfServiceDep,
    date_from: date | None = None,
    date_to: date | None = None,
) -> StreamingResponse:
    start, end = await default_period(ledger, admin.wholesaler_id, date_from, date_to)
    document = await pdf.build(
        wholesaler_id=admin.wholesaler_id, store_id=store_id, date_from=start, date_to=end
    )
    return pdf_response(document)


@router.get("/stores/{store_id}/ledger/verify", response_model=ChainVerificationResponse)
async def verify_ledger(
    store_id: uuid.UUID, admin: AdminDep, ledger: LedgerServiceDep
) -> ChainVerificationResponse:
    v = await ledger.verify_chain(wholesaler_id=admin.wholesaler_id, store_id=store_id)
    return ChainVerificationResponse(
        store_id=v.store_id,
        ok=v.ok,
        transactions_checked=v.transactions_checked,
        broken_at_sequence=v.broken_at_sequence,
        reason=v.reason,
    )


# --- agent assignments -------------------------------------------------------------------


async def _ensure_tenant_members(
    session: SessionDep, wholesaler_id: uuid.UUID, agent_id: uuid.UUID, store_id: uuid.UUID
) -> None:
    agent = await session.scalar(
        select(Agent.id).where(Agent.id == agent_id, Agent.wholesaler_id == wholesaler_id)
    )
    store = await session.scalar(
        select(Store.id).where(Store.id == store_id, Store.wholesaler_id == wholesaler_id)
    )
    if agent is None or store is None:
        raise NotFoundError("Agent or store not found")


@router.put("/agents/{agent_id}/stores/{store_id}", response_model=AssignmentResponse)
async def assign_store(
    agent_id: uuid.UUID, store_id: uuid.UUID, admin: AdminDep, session: SessionDep
) -> AssignmentResponse:
    async with atomic(session):
        await _ensure_tenant_members(session, admin.wholesaler_id, agent_id, store_id)
        await session.execute(
            insert(AgentStoreAssignment)
            .values(
                id=uuid7(), wholesaler_id=admin.wholesaler_id, agent_id=agent_id, store_id=store_id
            )
            .on_conflict_do_nothing(index_elements=["agent_id", "store_id"])
        )
    return AssignmentResponse(agent_id=agent_id, store_id=store_id, assigned=True)


@router.delete("/agents/{agent_id}/stores/{store_id}", response_model=AssignmentResponse)
async def unassign_store(
    agent_id: uuid.UUID, store_id: uuid.UUID, admin: AdminDep, session: SessionDep
) -> AssignmentResponse:
    async with atomic(session):
        await _ensure_tenant_members(session, admin.wholesaler_id, agent_id, store_id)
        await session.execute(
            delete(AgentStoreAssignment).where(
                AgentStoreAssignment.agent_id == agent_id,
                AgentStoreAssignment.store_id == store_id,
                AgentStoreAssignment.wholesaler_id == admin.wholesaler_id,
            )
        )
    return AssignmentResponse(agent_id=agent_id, store_id=store_id, assigned=False)
