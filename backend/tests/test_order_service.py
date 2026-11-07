from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import (
    CreditLimitExceededError,
    IdempotencyConflictError,
    InsufficientStockError,
    InvalidOrderError,
    InvalidOrderStateError,
    NotFoundError,
    ProductUnavailableError,
)
from app.models import LedgerTransaction, OrderStatus, Product, Store, TransactionType
from app.services.order_service import OrderLine, OrderService
from tests.conftest import World, end_read

D = Decimal


async def _product(
    session: AsyncSession, world: World, *, stock: str, price: str = "10.00", active: bool = True
) -> uuid.UUID:
    async with session.begin():
        p = Product(
            wholesaler_id=world.wholesaler.id,
            sku=f"T-{uuid.uuid4().hex[:10]}",
            name=f"Tovar {uuid.uuid4().hex[:4]}",
            price=D(price),
            stock_quantity=D(stock),
            is_active=active,
        )
        session.add(p)
        await session.flush()
        return p.id


async def _stock(session: AsyncSession, product_id: uuid.UUID) -> tuple[Decimal, Decimal]:
    row = (
        await session.execute(
            select(Product.stock_quantity, Product.reserved_quantity).where(
                Product.id == product_id
            )
        )
    ).one()
    await end_read(session)
    return row[0], row[1]


async def _debt(session: AsyncSession, store_id: uuid.UUID) -> Decimal:
    value = await session.scalar(select(Store.current_debt).where(Store.id == store_id))
    await end_read(session)
    assert value is not None
    return value


def _svc(session: AsyncSession) -> OrderService:
    return OrderService(session)


# --- creation ------------------------------------------------------------------------------


async def test_create_order_snapshots_prices(session, world: World) -> None:
    pid = await _product(session, world, stock="50", price="12500.50")
    order = await _svc(session).create_order(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        lines=[OrderLine(pid, D("3")), OrderLine(world.product.id, D("2.5"))],
        actor_telegram_user_id=world.store.telegram_user_id,
    )
    assert order.status == OrderStatus.PENDING and order.submitted_at is not None
    assert order.total_amount == D("37501.50") + D("250.00")
    assert [i.line_total for i in order.items] == [D("37501.50"), D("250.00")]
    assert order.items[0].unit_price == D("12500.50")
    # creation does not reserve anything
    assert await _stock(session, pid) == (D("50.000"), D("0.000"))


async def test_draft_then_submit(session, world: World) -> None:
    svc = _svc(session)
    draft = await svc.create_order(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        lines=[OrderLine(world.product.id, D("1"))],
        submit=False,
    )
    assert draft.status == OrderStatus.DRAFT and draft.submitted_at is None
    submitted = await svc.submit_order(wholesaler_id=world.wholesaler.id, order_id=draft.id)
    assert submitted.status == OrderStatus.PENDING
    again = await svc.submit_order(wholesaler_id=world.wholesaler.id, order_id=draft.id)
    assert again.status == OrderStatus.PENDING  # idempotent


@pytest.mark.parametrize(
    "lines_factory",
    [
        lambda pid: [],
        lambda pid: [OrderLine(pid, D("0"))],
        lambda pid: [OrderLine(pid, D("1.0001"))],
        lambda pid: [OrderLine(pid, D("1")), OrderLine(pid, D("2"))],
    ],
    ids=["empty", "zero-qty", "too-precise", "duplicate-line"],
)
async def test_invalid_order_requests(session, world: World, lines_factory) -> None:
    with pytest.raises(InvalidOrderError):
        await _svc(session).create_order(
            wholesaler_id=world.wholesaler.id,
            store_id=world.store.id,
            lines=lines_factory(world.product.id),
        )


async def test_unavailable_products(session, world: World) -> None:
    inactive = await _product(session, world, stock="10", active=False)
    for pid in (inactive, uuid.uuid4()):
        with pytest.raises(ProductUnavailableError):
            await _svc(session).create_order(
                wholesaler_id=world.wholesaler.id,
                store_id=world.store.id,
                lines=[OrderLine(pid, D("1"))],
            )


async def test_create_rejects_more_than_available(session, world: World) -> None:
    pid = await _product(session, world, stock="5")
    with pytest.raises(InsufficientStockError) as exc:
        await _svc(session).create_order(
            wholesaler_id=world.wholesaler.id,
            store_id=world.store.id,
            lines=[OrderLine(pid, D("6"))],
        )
    assert exc.value.details["shortages"][0]["available"] == "5.000"


async def test_create_is_idempotent(session, world: World) -> None:
    svc = _svc(session)
    kwargs = dict(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        lines=[OrderLine(world.product.id, D("1"))],
        idempotency_key=f"order-{uuid.uuid4().hex}",
    )
    first = await svc.create_order(**kwargs)
    second = await svc.create_order(**kwargs)
    assert first.id == second.id

    other_store = uuid.uuid4()
    with pytest.raises(IdempotencyConflictError):
        await svc.create_order(**{**kwargs, "store_id": other_store})


async def test_tenant_isolation(session, world: World) -> None:
    with pytest.raises(NotFoundError):
        await _svc(session).create_order(
            wholesaler_id=uuid.uuid4(),
            store_id=world.store.id,
            lines=[OrderLine(world.product.id, D("1"))],
        )


# --- approve / reserve -----------------------------------------------------------------


async def test_approve_reserves_and_deliver_writes_off_and_bills(session, world: World) -> None:
    pid = await _product(session, world, stock="10", price="1000.00")
    svc = _svc(session)
    order = await svc.create_order(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        agent_id=world.agent.id,
        lines=[OrderLine(pid, D("4"))],
    )

    approved = await svc.approve_order(wholesaler_id=world.wholesaler.id, order_id=order.id)
    assert approved.status == OrderStatus.APPROVED and approved.approved_at is not None
    assert await _stock(session, pid) == (D("10.000"), D("4.000"))
    assert await _debt(session, world.store.id) == D("0.00")  # not billed yet

    # approving twice does not double-reserve
    await svc.approve_order(wholesaler_id=world.wholesaler.id, order_id=order.id)
    assert await _stock(session, pid) == (D("10.000"), D("4.000"))

    shipped = await svc.ship_order(wholesaler_id=world.wholesaler.id, order_id=order.id)
    assert shipped.status == OrderStatus.SHIPPED

    result = await svc.deliver_order(wholesaler_id=world.wholesaler.id, order_id=order.id)
    assert result.order.status == OrderStatus.DELIVERED
    assert result.charge.created and result.charge.transaction.amount == D("4000.00")
    assert await _stock(session, pid) == (D("6.000"), D("0.000"))
    assert await _debt(session, world.store.id) == D("4000.00")

    # delivering again: no second write-off, no second charge
    replay = await svc.deliver_order(wholesaler_id=world.wholesaler.id, order_id=order.id)
    assert not replay.charge.created
    assert replay.charge.transaction.id == result.charge.transaction.id
    assert await _stock(session, pid) == (D("6.000"), D("0.000"))
    charges = await session.scalar(
        select(func.count())
        .select_from(LedgerTransaction)
        .where(
            LedgerTransaction.order_id == order.id,
            LedgerTransaction.type == TransactionType.CHARGE,
        )
    )
    await end_read(session)
    assert charges == 1


async def test_approval_is_all_or_nothing(session, world: World) -> None:
    plenty = await _product(session, world, stock="100")
    scarce = await _product(session, world, stock="3")
    svc = _svc(session)
    first = await svc.create_order(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        lines=[OrderLine(scarce, D("2"))],
    )
    second = await svc.create_order(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        lines=[OrderLine(plenty, D("10")), OrderLine(scarce, D("2"))],
    )
    await svc.approve_order(wholesaler_id=world.wholesaler.id, order_id=first.id)

    with pytest.raises(InsufficientStockError) as exc:
        await svc.approve_order(wholesaler_id=world.wholesaler.id, order_id=second.id)
    shortage = exc.value.details["shortages"]
    assert [s["product_id"] for s in shortage] == [str(scarce)]
    assert shortage[0]["available"] == "1.000"
    # nothing from the failed approval was reserved
    assert await _stock(session, plenty) == (D("100.000"), D("0.000"))
    assert await _stock(session, scarce) == (D("3.000"), D("2.000"))


async def test_concurrent_approvals_never_oversell(
    session, sessionmaker: async_sessionmaker[AsyncSession], world: World
) -> None:
    pid = await _product(session, world, stock="5")
    svc = _svc(session)
    orders = [
        await svc.create_order(
            wholesaler_id=world.wholesaler.id,
            store_id=world.store.id,
            lines=[OrderLine(pid, D("1"))],
        )
        for _ in range(8)
    ]

    async def approve(order_id: uuid.UUID) -> bool:
        async with sessionmaker() as s:
            try:
                await OrderService(s).approve_order(
                    wholesaler_id=world.wholesaler.id, order_id=order_id
                )
            except InsufficientStockError:
                return False
            return True

    results = await asyncio.gather(*(approve(o.id) for o in orders))
    assert results.count(True) == 5
    assert await _stock(session, pid) == (D("5.000"), D("5.000"))


async def test_credit_limit_counts_undelivered_orders(session, world: World) -> None:
    # credit limit is 1 000 000.00
    pid = await _product(session, world, stock="100", price="300000.00")
    svc = _svc(session)
    ids = []
    for qty in ("2", "1", "1"):  # 600k, 300k, 300k
        o = await svc.create_order(
            wholesaler_id=world.wholesaler.id,
            store_id=world.store.id,
            lines=[OrderLine(pid, D(qty))],
        )
        ids.append(o.id)
    await svc.approve_order(wholesaler_id=world.wholesaler.id, order_id=ids[0])
    await svc.approve_order(wholesaler_id=world.wholesaler.id, order_id=ids[1])
    with pytest.raises(CreditLimitExceededError):
        await svc.approve_order(wholesaler_id=world.wholesaler.id, order_id=ids[2])
    # the owner can consciously override
    ok = await svc.approve_order(
        wholesaler_id=world.wholesaler.id, order_id=ids[2], enforce_credit_limit=False
    )
    assert ok.status == OrderStatus.APPROVED


# --- cancel / state machine ------------------------------------------------------------


async def test_cancel_releases_reservation(session, world: World) -> None:
    pid = await _product(session, world, stock="10")
    svc = _svc(session)
    order = await svc.create_order(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        lines=[OrderLine(pid, D("7"))],
    )
    await svc.approve_order(wholesaler_id=world.wholesaler.id, order_id=order.id)
    assert await _stock(session, pid) == (D("10.000"), D("7.000"))

    cancelled = await svc.cancel_order(
        wholesaler_id=world.wholesaler.id, order_id=order.id, reason="Do'kon rad etdi"
    )
    assert cancelled.status == OrderStatus.CANCELLED
    assert cancelled.cancel_reason == "Do'kon rad etdi"
    assert await _stock(session, pid) == (D("10.000"), D("0.000"))

    with pytest.raises(InvalidOrderStateError):
        await svc.approve_order(wholesaler_id=world.wholesaler.id, order_id=order.id)


async def test_invalid_transitions(session, world: World) -> None:
    svc = _svc(session)
    draft = await svc.create_order(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        lines=[OrderLine(world.product.id, D("1"))],
        submit=False,
    )
    oid = draft.id  # expected failures below roll back and expire ORM state
    with pytest.raises(InvalidOrderStateError):
        await svc.approve_order(wholesaler_id=world.wholesaler.id, order_id=oid)
    with pytest.raises(InvalidOrderStateError):
        await svc.deliver_order(wholesaler_id=world.wholesaler.id, order_id=oid)
    with pytest.raises(InvalidOrderStateError):
        await svc.ship_order(wholesaler_id=world.wholesaler.id, order_id=oid)

    await svc.submit_order(wholesaler_id=world.wholesaler.id, order_id=oid)
    await svc.approve_order(wholesaler_id=world.wholesaler.id, order_id=oid)
    await svc.deliver_order(wholesaler_id=world.wholesaler.id, order_id=oid)
    with pytest.raises(InvalidOrderStateError):
        await svc.cancel_order(wholesaler_id=world.wholesaler.id, order_id=oid)


async def test_catalog_shows_available_quantity(session, world: World) -> None:
    pid = await _product(session, world, stock="10")
    svc = _svc(session)
    order = await svc.create_order(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        lines=[OrderLine(pid, D("4"))],
    )
    await svc.approve_order(wholesaler_id=world.wholesaler.id, order_id=order.id)
    catalog = await svc.list_catalog(wholesaler_id=world.wholesaler.id)
    item = next(p for p in catalog if p.id == pid)
    assert item.available_quantity == D("6.000")
    await end_read(session)

    hidden = await _product(session, world, stock="0")
    in_stock = await svc.list_catalog(wholesaler_id=world.wholesaler.id, in_stock_only=True)
    assert hidden not in {p.id for p in in_stock}
    assert world.product.id in {p.id for p in in_stock}
