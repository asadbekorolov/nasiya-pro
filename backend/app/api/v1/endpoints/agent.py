"""Sales agent endpoints: `/agent/*`. An agent only sees and acts for the stores
assigned to them (`agent_store_assignments`)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.api.deps import (
    AgentDep,
    LedgerServiceDep,
    OrderServiceDep,
    OtpNotifierDep,
    OtpServiceDep,
    SessionDep,
    SettingsDep,
    ensure_agent_serves_store,
)
from app.core.exceptions import NotificationDeliveryError
from app.core.money import ZERO
from app.models import AgentStoreAssignment, OrderStatus, Store, Wholesaler
from app.schemas.ledger import (
    CollectCashVerifyRequest,
    OtpIssueRequest,
    OtpIssueResponse,
    PaymentResponse,
    charge_response,
    payment_response,
)
from app.schemas.order import (
    AgentOrderCreate,
    AgentStoreRead,
    DeliveryResponse,
    OrderRead,
)
from app.services.order_service import OrderLine

router = APIRouter(prefix="/agent", tags=["agent"])


async def _assigned_store_ids(session: SessionDep, agent_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        (
            await session.scalars(
                select(AgentStoreAssignment.store_id).where(
                    AgentStoreAssignment.agent_id == agent_id
                )
            )
        ).all()
    )


@router.get("/stores", response_model=list[AgentStoreRead])
async def agent_stores(
    principal: AgentDep, session: SessionDep, ledger: LedgerServiceDep
) -> list[AgentStoreRead]:
    stores = (
        await session.scalars(
            select(Store)
            .join(AgentStoreAssignment, AgentStoreAssignment.store_id == Store.id)
            .where(
                AgentStoreAssignment.agent_id == principal.agent_id,
                Store.wholesaler_id == principal.wholesaler_id,
                Store.is_active.is_(True),
            )
            .order_by(Store.name)
        )
    ).all()
    today = await ledger.local_today(principal.wholesaler_id)
    overdue = await ledger.get_overdue_amounts(
        wholesaler_id=principal.wholesaler_id, store_ids=[s.id for s in stores], as_of=today
    )
    return [
        AgentStoreRead(
            id=s.id,
            name=s.name,
            address=s.address,
            phone=s.phone,
            current_debt=s.current_debt,
            credit_limit=s.credit_limit,
            overdue_amount=overdue.get(s.id, ZERO),
        )
        for s in stores
    ]


@router.get("/orders", response_model=list[OrderRead])
async def agent_orders(
    principal: AgentDep,
    session: SessionDep,
    orders: OrderServiceDep,
    store_id: uuid.UUID | None = None,
    status_filter: list[OrderStatus] | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[OrderRead]:
    assert principal.agent_id is not None
    if store_id is not None:
        await ensure_agent_serves_store(session, principal, store_id)
        store_ids = [store_id]
    else:
        store_ids = await _assigned_store_ids(session, principal.agent_id)
    rows = await orders.list_orders(
        wholesaler_id=principal.wholesaler_id,
        store_ids=store_ids,
        statuses=status_filter,
        limit=limit,
        offset=offset,
    )
    return [OrderRead.model_validate(o) for o in rows]


@router.post("/orders", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
async def agent_create_order(
    body: AgentOrderCreate, principal: AgentDep, session: SessionDep, orders: OrderServiceDep
) -> OrderRead:
    await ensure_agent_serves_store(session, principal, body.store_id)
    order = await orders.create_order(
        wholesaler_id=principal.wholesaler_id,
        store_id=body.store_id,
        agent_id=principal.agent_id,
        lines=[OrderLine(line.product_id, line.quantity) for line in body.lines],
        note=body.note,
        submit=body.submit,
        idempotency_key=body.idempotency_key,
        actor_telegram_user_id=principal.telegram_user_id,
    )
    return OrderRead.model_validate(order)


@router.post("/orders/{order_id}/deliver", response_model=DeliveryResponse)
async def agent_deliver_order(
    order_id: uuid.UUID, principal: AgentDep, session: SessionDep, orders: OrderServiceDep
) -> DeliveryResponse:
    """The agent hands the goods over: stock is written off and the debt is billed."""
    current = await orders.get_order(wholesaler_id=principal.wholesaler_id, order_id=order_id)
    await ensure_agent_serves_store(session, principal, current.store_id)
    result = await orders.deliver_order(
        wholesaler_id=principal.wholesaler_id,
        order_id=order_id,
        actor_telegram_user_id=principal.telegram_user_id,
    )
    return DeliveryResponse(
        order=OrderRead.model_validate(result.order), charge=charge_response(result.charge)
    )


@router.post(
    "/collect-cash/request-otp",
    response_model=OtpIssueResponse,
    status_code=status.HTTP_201_CREATED,
)
async def agent_request_otp(
    body: OtpIssueRequest,
    principal: AgentDep,
    session: SessionDep,
    settings: SettingsDep,
    otp_service: OtpServiceDep,
    notifier: OtpNotifierDep,
) -> OtpIssueResponse:
    """The agent received cash: a 4-digit code is sent to the store owner's Telegram.
    The code itself is never returned to the agent."""
    assert principal.agent_id is not None
    await ensure_agent_serves_store(session, principal, body.store_id)
    issued = await otp_service.issue(
        wholesaler_id=principal.wholesaler_id,
        store_id=body.store_id,
        agent_id=principal.agent_id,
        amount=body.amount,
        method=body.method,
    )
    currency = await session.scalar(
        select(Wholesaler.currency).where(Wholesaler.id == principal.wholesaler_id)
    )
    try:
        message_id = await notifier.send_payment_otp(
            chat_id=issued.store_telegram_user_id,
            code=issued.code,
            amount=issued.verification.amount,
            currency=currency or settings.default_currency,
            agent_name=issued.agent_name,
            ttl_seconds=settings.otp_ttl_seconds,
        )
    except NotificationDeliveryError:
        await otp_service.cancel(issued.verification.id)
        raise
    if message_id is not None:
        await otp_service.attach_message_id(issued.verification.id, message_id)
    return OtpIssueResponse.model_validate(issued.verification)


@router.post(
    "/collect-cash/verify-otp",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def agent_verify_otp(
    body: CollectCashVerifyRequest,
    principal: AgentDep,
    otp_service: OtpServiceDep,
    ledger: LedgerServiceDep,
) -> PaymentResponse:
    """The agent enters the code from the store; on success the payment is posted and
    open debts are closed FIFO. Safe to retry."""
    assert principal.agent_id is not None
    otp = await otp_service.verify(
        wholesaler_id=principal.wholesaler_id,
        verification_id=body.verification_id,
        agent_id=principal.agent_id,
        code=body.code,
    )
    result = await ledger.record_payment(
        wholesaler_id=principal.wholesaler_id,
        store_id=otp.store_id,
        amount=otp.amount,
        method=otp.payment_method,
        agent_id=principal.agent_id,
        otp_verification_id=otp.id,
        idempotency_key=body.idempotency_key,
        description=body.description,
        actor_telegram_user_id=principal.telegram_user_id,
    )
    return payment_response(result)
