"""Wholesaler admin endpoints: `/admin/*`."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, File, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from app.api.deps import (
    AdminDep,
    EventNotifierDep,
    LedgerServiceDep,
    OrderServiceDep,
    PdfServiceDep,
    SessionDep,
)
from app.api.v1.endpoints.common import (
    XLSX_MEDIA_TYPE,
    default_period,
    pdf_response,
    xlsx_template_response,
)
from app.core.exceptions import NotFoundError
from app.core.money import ZERO
from app.core.uuid7 import uuid7
from app.db.uow import atomic
from app.models import (
    Agent,
    AgentStoreAssignment,
    Order,
    OrderStatus,
    Product,
    Store,
    Wholesaler,
)
from app.schemas.importing import ImportResultResponse, import_response
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
    AdminStoreRead,
    ApproveOrderRequest,
    AssignmentResponse,
    CancelOrderRequest,
    CreditLimitUpdate,
    DeliveryResponse,
    OrderRead,
    StockItemRead,
    StockReport,
)
from app.services.excel_service import (
    MAX_FILE_BYTES,
    ExcelImportService,
    ImportFileError,
    ImportKind,
)
from app.services.ledger_service import LedgerService
from app.services.locks import lock_store

router = APIRouter(prefix="/admin", tags=["admin"])


# --- reports ---------------------------------------------------------------------------


@router.get("/aging-report", response_model=AgingReportResponse)
async def aging_report(
    admin: AdminDep,
    ledger: LedgerServiceDep,
    as_of: date | None = None,
    store_id: uuid.UUID | None = None,
    scheme: Literal["standard", "short"] = "standard",
) -> AgingReportResponse:
    """Open receivables by days past due. standard: current, 1-30, 31-60, 61-90, 90+;
    short: current, 1-15, 16-30, 30+. Rows are sorted by total, largest debtor first."""
    effective = as_of or await ledger.local_today(admin.wholesaler_id)
    rows = await ledger.get_aging_report(
        wholesaler_id=admin.wholesaler_id, as_of=effective, store_id=store_id, scheme=scheme
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


# --- stores & credit limits ---------------------------------------------------------------


async def _store_rows(
    session: SessionDep, ledger: LedgerService, wholesaler_id: uuid.UUID, stores: list[Store]
) -> list[AdminStoreRead]:
    ids = [s.id for s in stores]
    today = await ledger.local_today(wholesaler_id)
    overdue = await ledger.get_overdue_amounts(
        wholesaler_id=wholesaler_id, store_ids=ids, as_of=today
    )
    pending: dict[uuid.UUID, int] = {}
    if ids:
        rows = await session.execute(
            select(Order.store_id, func.count())
            .where(
                Order.wholesaler_id == wholesaler_id,
                Order.store_id.in_(ids),
                Order.status == OrderStatus.PENDING,
            )
            .group_by(Order.store_id)
        )
        pending = {sid: n for sid, n in rows.tuples().all()}
    return [
        AdminStoreRead(
            id=s.id,
            name=s.name,
            contact_name=s.contact_name,
            phone=s.phone,
            address=s.address,
            is_active=s.is_active,
            is_linked=s.telegram_user_id is not None,
            current_debt=s.current_debt,
            credit_limit=s.credit_limit,
            available_credit=max(s.credit_limit - max(s.current_debt, ZERO), ZERO),
            overdue_amount=overdue.get(s.id, ZERO),
            pending_orders=pending.get(s.id, 0),
        )
        for s in stores
    ]


@router.get("/stores", response_model=list[AdminStoreRead])
async def admin_stores(
    admin: AdminDep, session: SessionDep, ledger: LedgerServiceDep
) -> list[AdminStoreRead]:
    """Every store of the tenant with balance, limit, overdue debt and pending orders."""
    stores = list(
        (
            await session.scalars(
                select(Store)
                .where(Store.wholesaler_id == admin.wholesaler_id)
                .order_by(Store.is_active.desc(), Store.name)
            )
        ).all()
    )
    return await _store_rows(session, ledger, admin.wholesaler_id, stores)


@router.patch("/stores/{store_id}/credit-limit", response_model=AdminStoreRead)
async def update_credit_limit(
    store_id: uuid.UUID,
    body: CreditLimitUpdate,
    admin: AdminDep,
    session: SessionDep,
    ledger: LedgerServiceDep,
) -> AdminStoreRead:
    """Set the store's credit limit. A limit below the current debt is allowed: it only
    blocks new orders until the store pays down."""
    async with atomic(session):
        # Same row lock as order approval, so a limit change never races a credit check.
        store = await lock_store(session, admin.wholesaler_id, store_id)
        store.credit_limit = body.credit_limit
        await session.flush()
    store = await ledger.get_store(admin.wholesaler_id, store_id)
    (row,) = await _store_rows(session, ledger, admin.wholesaler_id, [store])
    return row


# --- stock -------------------------------------------------------------------------------


@router.get("/stock", response_model=StockReport)
async def stock_report(
    admin: AdminDep,
    session: SessionDep,
    threshold: Decimal = Query(
        default=Decimal("20"),
        ge=0,
        max_digits=15,
        decimal_places=3,
        description="sellable quantity at or below this counts as low stock",
    ),
    low_only: bool = False,
    include_inactive: bool = False,
) -> StockReport:
    """Warehouse levels: on hand, reserved by approved orders, and sellable."""
    stmt = select(Product).where(Product.wholesaler_id == admin.wholesaler_id)
    if not include_inactive:
        stmt = stmt.where(Product.is_active.is_(True))
    products = list((await session.scalars(stmt)).all())

    items = [
        StockItemRead(
            id=p.id,
            sku=p.sku,
            name=p.name,
            unit=p.unit,
            price=p.price,
            stock_quantity=p.stock_quantity,
            reserved_quantity=p.reserved_quantity,
            available_quantity=p.available_quantity,
            is_active=p.is_active,
            is_low=p.available_quantity <= threshold,
        )
        for p in products
    ]
    # Most urgent first: low before healthy, then least sellable, then name.
    items.sort(key=lambda i: (not i.is_low, i.available_quantity, i.name))
    low = [i for i in items if i.is_low]
    stock_value = sum((p.stock_quantity * p.price for p in products if p.is_active), ZERO)
    return StockReport(
        threshold=threshold,
        total_products=len(items),
        low_count=len(low),
        out_of_stock_count=sum(1 for i in items if i.available_quantity <= 0),
        stock_value=stock_value.quantize(Decimal("0.01")),
        items=low if low_only else items,
    )


# --- Excel import (onboarding) -------------------------------------------------------------


async def _read_upload(file: UploadFile) -> bytes:
    name = (file.filename or "").lower()
    if name.endswith(".xls") or name.endswith(".csv"):
        raise ImportFileError(
            "Faqat .xlsx fayllar qabul qilinadi. Excelda «Saqlash» -> «Excel kitobi (.xlsx)»"
        )
    data = await file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ImportFileError(f"Fayl juda katta (ko'pi bilan {MAX_FILE_BYTES // 2**20} MB)")
    return data


@router.post("/import/products", response_model=ImportResultResponse)
async def import_products(
    admin: AdminDep,
    session: SessionDep,
    ledger: LedgerServiceDep,
    file: UploadFile = File(..., description=".xlsx made from the products template"),
    dry_run: bool = Query(default=False, description="validate only, write nothing"),
) -> ImportResultResponse:
    """Upsert the catalog: rows with a known SKU update that product (name, price, stock,
    unit), new SKUs are created. Bad rows are skipped and reported by Excel row number;
    the good ones are written in one transaction."""
    data = await _read_upload(file)
    result = await ExcelImportService(session, ledger).import_products(
        wholesaler_id=admin.wholesaler_id, data=data, dry_run=dry_run
    )
    return import_response(result)


@router.post("/import/stores", response_model=ImportResultResponse)
async def import_stores(
    admin: AdminDep,
    session: SessionDep,
    ledger: LedgerServiceDep,
    file: UploadFile = File(..., description=".xlsx made from the stores template"),
    dry_run: bool = Query(default=False, description="validate only, write nothing"),
) -> ImportResultResponse:
    """Create stores; a positive opening debt is booked as a ledger adjustment. Stores
    whose name already exists are reported, never duplicated (safe to re-upload)."""
    data = await _read_upload(file)
    result = await ExcelImportService(session, ledger).import_stores(
        wholesaler_id=admin.wholesaler_id,
        data=data,
        dry_run=dry_run,
        actor_telegram_user_id=admin.telegram_user_id,
    )
    return import_response(result)


@router.get(
    "/import/template/{kind}",
    response_class=StreamingResponse,
    responses={200: {"content": {XLSX_MEDIA_TYPE: {}}}},
)
async def import_template(kind: ImportKind, admin: AdminDep) -> StreamingResponse:
    """Blank workbook to fill in (also public at /files/templates/{kind}.xlsx for
    Telegram's native downloader, which cannot send auth headers)."""
    return xlsx_template_response(kind)


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
