from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.api.deps import (
    AgentDep,
    LedgerServiceDep,
    OtpNotifierDep,
    OtpServiceDep,
    OwnerDep,
    PrincipalDep,
    SessionDep,
    SettingsDep,
)
from app.core.exceptions import InvalidAmountError, NotificationDeliveryError
from app.core.money import ZERO
from app.models import Wholesaler
from app.schemas.ledger import (
    AdjustmentCreate,
    AdjustmentResponse,
    AgingReportResponse,
    AgingRowRead,
    AllocationRead,
    ChainVerificationResponse,
    ChargeResponse,
    DebtRead,
    DeliverOrderRequest,
    OtpConfirmRequest,
    OtpIssueRequest,
    OtpIssueResponse,
    PaymentCreate,
    PaymentResponse,
    StatementResponse,
    StoreBalanceResponse,
    TransactionRead,
)
from app.services.ledger_service import AdjustmentResult, ChargeResult, PaymentResult

router = APIRouter(tags=["ledger"])


# --- mappers ---------------------------------------------------------------------------


def _charge_response(r: ChargeResult) -> ChargeResponse:
    return ChargeResponse(
        transaction=TransactionRead.model_validate(r.transaction),
        debt=DebtRead.model_validate(r.debt),
        store_balance=r.store_balance,
        advance_applied=r.advance_applied,
        created=r.created,
    )


def _payment_response(r: PaymentResult) -> PaymentResponse:
    return PaymentResponse(
        transaction=TransactionRead.model_validate(r.transaction),
        allocations=[AllocationRead.model_validate(a) for a in r.allocations],
        store_balance=r.store_balance,
        advance_amount=r.advance_amount,
        created=r.created,
    )


def _adjustment_response(r: AdjustmentResult) -> AdjustmentResponse:
    return AdjustmentResponse(
        transaction=TransactionRead.model_validate(r.transaction),
        debt=DebtRead.model_validate(r.debt) if r.debt else None,
        allocations=[AllocationRead.model_validate(a) for a in r.allocations],
        store_balance=r.store_balance,
        created=r.created,
    )


# --- delivery --------------------------------------------------------------------------


@router.post("/orders/{order_id}/deliver", response_model=ChargeResponse)
async def deliver_order(
    order_id: uuid.UUID,
    body: DeliverOrderRequest,
    principal: PrincipalDep,
    ledger: LedgerServiceDep,
) -> ChargeResponse:
    result = await ledger.deliver_order(
        wholesaler_id=principal.wholesaler_id,
        order_id=order_id,
        actor_telegram_user_id=principal.telegram_user_id,
        enforce_credit_limit=body.enforce_credit_limit,
    )
    return _charge_response(result)


# --- payments --------------------------------------------------------------------------


@router.post(
    "/stores/{store_id}/payments",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_payment_by_owner(
    store_id: uuid.UUID,
    body: PaymentCreate,
    owner: OwnerDep,
    ledger: LedgerServiceDep,
) -> PaymentResponse:
    """Owner records money received directly (bank transfer, cash at the warehouse)."""
    result = await ledger.record_payment(
        wholesaler_id=owner.wholesaler_id,
        store_id=store_id,
        amount=body.amount,
        method=body.method,
        idempotency_key=body.idempotency_key,
        description=body.description,
        actor_telegram_user_id=owner.telegram_user_id,
    )
    return _payment_response(result)


@router.post(
    "/payments/otp",
    response_model=OtpIssueResponse,
    status_code=status.HTTP_201_CREATED,
)
async def request_payment_otp(
    body: OtpIssueRequest,
    agent: AgentDep,
    otp_service: OtpServiceDep,
    notifier: OtpNotifierDep,
    session: SessionDep,
    settings: SettingsDep,
) -> OtpIssueResponse:
    """Agent collected cash: send a confirmation code to the store's Telegram."""
    assert agent.agent_id is not None
    issued = await otp_service.issue(
        wholesaler_id=agent.wholesaler_id,
        store_id=body.store_id,
        agent_id=agent.agent_id,
        amount=body.amount,
        method=body.method,
    )
    currency = await session.scalar(
        select(Wholesaler.currency).where(Wholesaler.id == agent.wholesaler_id)
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
    "/payments/otp/{verification_id}/confirm",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def confirm_payment_otp(
    verification_id: uuid.UUID,
    body: OtpConfirmRequest,
    agent: AgentDep,
    otp_service: OtpServiceDep,
    ledger: LedgerServiceDep,
) -> PaymentResponse:
    """Agent enters the code from the store; on success the payment is posted.
    Safe to retry: a consumed code returns the original payment."""
    assert agent.agent_id is not None
    otp = await otp_service.verify(
        wholesaler_id=agent.wholesaler_id,
        verification_id=verification_id,
        agent_id=agent.agent_id,
        code=body.code,
    )
    result = await ledger.record_payment(
        wholesaler_id=agent.wholesaler_id,
        store_id=otp.store_id,
        amount=otp.amount,
        method=otp.payment_method,
        agent_id=agent.agent_id,
        otp_verification_id=otp.id,
        idempotency_key=body.idempotency_key,
        description=body.description,
        actor_telegram_user_id=agent.telegram_user_id,
    )
    return _payment_response(result)


@router.post(
    "/stores/{store_id}/adjustments",
    response_model=AdjustmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_adjustment(
    store_id: uuid.UUID,
    body: AdjustmentCreate,
    owner: OwnerDep,
    ledger: LedgerServiceDep,
) -> AdjustmentResponse:
    result = await ledger.record_adjustment(
        wholesaler_id=owner.wholesaler_id,
        store_id=store_id,
        amount=body.amount,
        reason=body.reason,
        due_date=body.due_date,
        incurred_at=body.incurred_at,
        idempotency_key=body.idempotency_key,
        actor_telegram_user_id=owner.telegram_user_id,
    )
    return _adjustment_response(result)


# --- reads -----------------------------------------------------------------------------


@router.get("/stores/{store_id}/balance", response_model=StoreBalanceResponse)
async def get_store_balance(
    store_id: uuid.UUID, principal: PrincipalDep, ledger: LedgerServiceDep
) -> StoreBalanceResponse:
    store = await ledger.get_store(principal.wholesaler_id, store_id)
    return StoreBalanceResponse(
        store_id=store.id,
        current_debt=store.current_debt,
        credit_limit=store.credit_limit,
        available_credit=store.credit_limit - max(store.current_debt, ZERO),
    )


@router.get("/stores/{store_id}/statement", response_model=StatementResponse)
async def get_statement(
    store_id: uuid.UUID,
    owner: OwnerDep,
    ledger: LedgerServiceDep,
    session: SessionDep,
    date_from: date = Query(..., description="Inclusive, wholesaler's local date"),
    date_to: date = Query(..., description="Inclusive, wholesaler's local date"),
) -> StatementResponse:
    if date_to < date_from:
        raise InvalidAmountError("date_to must not be before date_from")
    tz_name = await session.scalar(
        select(Wholesaler.timezone).where(Wholesaler.id == owner.wholesaler_id)
    )
    tz = ZoneInfo(tz_name or "UTC")
    period_from = datetime.combine(date_from, time.min, tzinfo=tz)
    period_to = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz)

    st = await ledger.get_statement(
        wholesaler_id=owner.wholesaler_id,
        store_id=store_id,
        period_from=period_from,
        period_to=period_to,
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


@router.get("/reports/aging", response_model=AgingReportResponse)
async def get_aging_report(
    owner: OwnerDep,
    ledger: LedgerServiceDep,
    as_of: date | None = None,
    store_id: uuid.UUID | None = None,
) -> AgingReportResponse:
    rows = await ledger.get_aging_report(
        wholesaler_id=owner.wholesaler_id, as_of=as_of, store_id=store_id
    )
    effective = as_of or await ledger.local_today(owner.wholesaler_id)
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


@router.get("/stores/{store_id}/ledger/verify", response_model=ChainVerificationResponse)
async def verify_ledger_chain(
    store_id: uuid.UUID, owner: OwnerDep, ledger: LedgerServiceDep
) -> ChainVerificationResponse:
    v = await ledger.verify_chain(wholesaler_id=owner.wholesaler_id, store_id=store_id)
    return ChainVerificationResponse(
        store_id=v.store_id,
        ok=v.ok,
        transactions_checked=v.transactions_checked,
        broken_at_sequence=v.broken_at_sequence,
        reason=v.reason,
    )
