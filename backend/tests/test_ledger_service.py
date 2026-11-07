from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import (
    CreditLimitExceededError,
    IdempotencyConflictError,
    InvalidAmountError,
    InvalidOrderStateError,
    NotFoundError,
)
from app.models import (
    Debt,
    LedgerTransaction,
    Order,
    OrderStatus,
    PaymentMethod,
    Store,
    TransactionType,
)
from app.services.ledger_service import LedgerService
from tests.conftest import World, end_read

D = Decimal


async def _store(session: AsyncSession, store_id) -> Store:  # type: ignore[no-untyped-def]
    store = await session.scalar(
        select(Store).where(Store.id == store_id).execution_options(populate_existing=True)
    )
    await end_read(session)
    assert store is not None
    return store


# --- delivery ----------------------------------------------------------------------------


async def test_deliver_order_creates_debt_and_charge(session, world: World, make_order) -> None:
    order = await make_order("150000.00")
    ledger = LedgerService(session)

    result = await ledger.deliver_order(wholesaler_id=world.wholesaler.id, order_id=order.id)

    assert result.created is True
    assert result.store_balance == D("150000.00")
    assert result.transaction.type == TransactionType.CHARGE
    assert result.transaction.amount == D("150000.00")
    assert result.transaction.balance_before == D("0.00")
    assert result.transaction.balance_after == D("150000.00")
    assert result.transaction.sequence_no == 1
    assert result.transaction.prev_hash is None
    assert result.debt.initial_amount == result.debt.remaining_amount == D("150000.00")
    assert result.debt.is_closed is False
    assert result.debt.origin_transaction_id == result.transaction.id
    # default term 14 days in Asia/Tashkent
    assert (result.debt.due_date - result.debt.incurred_at.date()).days in (14, 15)

    refreshed = await session.get(Order, order.id, populate_existing=True)
    assert refreshed is not None and refreshed.status == OrderStatus.DELIVERED
    assert refreshed.delivered_at is not None
    await end_read(session)
    assert (await _store(session, world.store.id)).current_debt == D("150000.00")


async def test_deliver_is_idempotent(session, world: World, make_order) -> None:
    order = await make_order("500.00")
    ledger = LedgerService(session)
    first = await ledger.deliver_order(wholesaler_id=world.wholesaler.id, order_id=order.id)
    second = await ledger.deliver_order(wholesaler_id=world.wholesaler.id, order_id=order.id)

    assert second.created is False
    assert second.transaction.id == first.transaction.id
    assert second.debt.id == first.debt.id
    count = await session.scalar(
        select(func.count())
        .select_from(LedgerTransaction)
        .where(LedgerTransaction.store_id == world.store.id)
    )
    await end_read(session)
    assert count == 1
    assert (await _store(session, world.store.id)).current_debt == D("500.00")


@pytest.mark.parametrize("status", [OrderStatus.PENDING, OrderStatus.DRAFT])
async def test_deliver_rejects_non_deliverable_status(
    session, world: World, make_order, status: OrderStatus
) -> None:
    order = await make_order("100.00", status=status)
    with pytest.raises(InvalidOrderStateError):
        await LedgerService(session).deliver_order(
            wholesaler_id=world.wholesaler.id, order_id=order.id
        )
    assert (await _store(session, world.store.id)).current_debt == D("0.00")


async def test_deliver_is_tenant_scoped(session, world: World, make_order) -> None:
    order = await make_order("100.00")
    import uuid

    with pytest.raises(NotFoundError):
        await LedgerService(session).deliver_order(wholesaler_id=uuid.uuid4(), order_id=order.id)


async def test_credit_limit_enforced_when_requested(session, world: World, make_order) -> None:
    order = await make_order("1000000.01")  # limit is 1 000 000.00
    with pytest.raises(CreditLimitExceededError):
        await LedgerService(session).deliver_order(
            wholesaler_id=world.wholesaler.id, order_id=order.id, enforce_credit_limit=True
        )
    # without enforcement the goods are already handed over -> billed
    res = await LedgerService(session).deliver_order(
        wholesaler_id=world.wholesaler.id, order_id=order.id
    )
    assert res.store_balance == D("1000000.01")


# --- payments / FIFO -------------------------------------------------------------------


async def test_payment_closes_debts_fifo(session, world: World, make_order) -> None:
    ledger = LedgerService(session)
    debts = []
    for total in ("100.00", "200.00", "300.00"):
        order = await make_order(total)
        debts.append(
            (await ledger.deliver_order(wholesaler_id=world.wholesaler.id, order_id=order.id)).debt
        )

    result = await ledger.record_payment(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        amount=D("250.00"),
        method=PaymentMethod.CASH,
    )

    assert result.transaction.amount == D("-250.00")
    assert result.transaction.balance_after == D("350.00")
    assert result.store_balance == D("350.00")
    assert result.advance_amount == D("0.00")
    assert [(a.debt_id, a.amount, a.closed) for a in result.allocations] == [
        (debts[0].id, D("100.00"), True),
        (debts[1].id, D("150.00"), False),
    ]

    rows = {
        d.id: d
        for d in (
            await session.scalars(
                select(Debt)
                .where(Debt.store_id == world.store.id)
                .execution_options(populate_existing=True)
            )
        ).all()
    }
    await end_read(session)
    assert rows[debts[0].id].is_closed and rows[debts[0].id].remaining_amount == D("0.00")
    assert rows[debts[0].id].closed_at is not None
    assert rows[debts[1].id].remaining_amount == D("50.00")
    assert rows[debts[2].id].remaining_amount == D("300.00")


async def test_overpayment_becomes_advance_and_offsets_next_charge(
    session, world: World, make_order
) -> None:
    ledger = LedgerService(session)
    o1 = await make_order("100.00")
    await ledger.deliver_order(wholesaler_id=world.wholesaler.id, order_id=o1.id)

    pay = await ledger.record_payment(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        amount="180.00",
        method=PaymentMethod.BANK_TRANSFER,
    )
    assert pay.store_balance == D("-80.00")
    assert pay.advance_amount == D("80.00")

    o2 = await make_order("50.00")
    charge = await ledger.deliver_order(wholesaler_id=world.wholesaler.id, order_id=o2.id)
    assert charge.advance_applied == D("50.00")
    assert charge.debt.is_closed is True
    assert charge.store_balance == D("-30.00")

    o3 = await make_order("70.00")
    charge = await ledger.deliver_order(wholesaler_id=world.wholesaler.id, order_id=o3.id)
    assert charge.advance_applied == D("30.00")
    assert charge.debt.remaining_amount == D("40.00")
    assert charge.store_balance == D("40.00")


async def test_payment_idempotency(session, world: World, make_order) -> None:
    ledger = LedgerService(session)
    order = await make_order("1000.00")
    await ledger.deliver_order(wholesaler_id=world.wholesaler.id, order_id=order.id)

    kwargs = dict(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        amount=D("400.00"),
        method=PaymentMethod.CARD,
        idempotency_key="pay-test-key-0001",
    )
    first = await ledger.record_payment(**kwargs)
    second = await ledger.record_payment(**kwargs)
    assert first.created and not second.created
    assert first.transaction.id == second.transaction.id
    assert second.store_balance == D("600.00")
    assert [a.amount for a in second.allocations] == [D("400.00")]

    with pytest.raises(IdempotencyConflictError):
        await ledger.record_payment(**{**kwargs, "amount": D("401.00")})


@pytest.mark.parametrize("bad", ["0", "-5", "10.001", "abc"])
async def test_payment_rejects_invalid_amounts(session, world: World, bad: str) -> None:
    with pytest.raises(InvalidAmountError):
        await LedgerService(session).record_payment(
            wholesaler_id=world.wholesaler.id,
            store_id=world.store.id,
            amount=bad,
            method=PaymentMethod.CASH,
        )


async def test_float_amounts_are_rejected(session, world: World) -> None:
    with pytest.raises(InvalidAmountError):
        await LedgerService(session).record_payment(
            wholesaler_id=world.wholesaler.id,
            store_id=world.store.id,
            amount=0.1,  # type: ignore[arg-type]
            method=PaymentMethod.CASH,
        )


# --- concurrency -----------------------------------------------------------------------


async def test_concurrent_operations_serialize_on_store_lock(
    session, sessionmaker: async_sessionmaker[AsyncSession], world: World, make_order
) -> None:
    orders = [await make_order("100.00") for _ in range(5)]

    async def deliver(order_id) -> None:  # type: ignore[no-untyped-def]
        async with sessionmaker() as s:
            await LedgerService(s).deliver_order(
                wholesaler_id=world.wholesaler.id, order_id=order_id
            )

    async def pay(i: int) -> None:
        async with sessionmaker() as s:
            await LedgerService(s).record_payment(
                wholesaler_id=world.wholesaler.id,
                store_id=world.store.id,
                amount=D("30.00"),
                method=PaymentMethod.CASH,
                idempotency_key=f"concurrent-{world.store.id}-{i}",
            )

    await asyncio.gather(
        *(deliver(o.id) for o in orders),
        *(deliver(o.id) for o in orders),  # duplicate deliveries race too
        *(pay(i) for i in range(10)),
    )

    store = await _store(session, world.store.id)
    assert store.current_debt == D("200.00")  # 5*100 - 10*30

    seqs = (
        await session.scalars(
            select(LedgerTransaction.sequence_no)
            .where(LedgerTransaction.store_id == world.store.id)
            .order_by(LedgerTransaction.sequence_no)
        )
    ).all()
    open_total = await session.scalar(
        select(func.sum(Debt.remaining_amount)).where(
            Debt.store_id == world.store.id, Debt.is_closed.is_(False)
        )
    )
    await end_read(session)
    assert seqs == list(range(1, 16))  # 5 charges + 10 payments, no gaps
    assert open_total == D("200.00")

    verification = await LedgerService(session).verify_chain(
        wholesaler_id=world.wholesaler.id, store_id=world.store.id
    )
    assert verification.ok, verification.reason


# --- append-only & tamper evidence -------------------------------------------------------


async def test_ledger_rows_cannot_be_updated_or_deleted(session, world: World, make_order) -> None:
    order = await make_order("10.00")
    res = await LedgerService(session).deliver_order(
        wholesaler_id=world.wholesaler.id, order_id=order.id
    )
    tx_id = res.transaction.id  # failed statements below roll back and expire ORM state
    for stmt in (
        "UPDATE transactions SET amount = 1 WHERE id = :id",
        "DELETE FROM transactions WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with session.begin():
                await session.execute(text(stmt), {"id": tx_id})
    with pytest.raises(DBAPIError, match="append-only"):
        async with session.begin():
            await session.execute(text("DELETE FROM debt_allocations"))


async def test_verify_chain_detects_tampering(session, world: World, make_order) -> None:
    ledger = LedgerService(session)
    order = await make_order("100.00")
    await ledger.deliver_order(wholesaler_id=world.wholesaler.id, order_id=order.id)
    pay = await ledger.record_payment(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        amount="40.00",
        method=PaymentMethod.CASH,
    )
    ok = await ledger.verify_chain(wholesaler_id=world.wholesaler.id, store_id=world.store.id)
    await end_read(session)
    assert ok.ok and ok.transactions_checked == 2

    # Simulate a DBA bypassing the trigger and editing history.
    async with session.begin():
        await session.execute(text("ALTER TABLE transactions DISABLE TRIGGER USER"))
        await session.execute(
            text("UPDATE transactions SET description = 'edited' WHERE id = :id"),
            {"id": pay.transaction.id},
        )
        await session.execute(text("ALTER TABLE transactions ENABLE TRIGGER USER"))

    session.expunge_all()
    broken = await LedgerService(session).verify_chain(
        wholesaler_id=world.wholesaler.id, store_id=world.store.id
    )
    await end_read(session)
    assert not broken.ok
    assert broken.broken_at_sequence == 2
    assert "entry_hash" in (broken.reason or "")


# --- adjustments, statement, aging -------------------------------------------------------


async def test_opening_balance_adjustment_and_negative_adjustment(session, world: World) -> None:
    ledger = LedgerService(session)
    past = datetime.now(UTC) - timedelta(days=60)
    opening = await ledger.record_adjustment(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        amount="5000.00",
        reason="Opening balance on onboarding",
        incurred_at=past,
        due_date=(past + timedelta(days=10)).date(),
        idempotency_key="opening-balance-0001",
    )
    assert opening.debt is not None and opening.debt.remaining_amount == D("5000.00")
    replay = await ledger.record_adjustment(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        amount="5000.00",
        reason="Opening balance on onboarding",
        idempotency_key="opening-balance-0001",
    )
    assert not replay.created and replay.debt is not None and replay.debt.id == opening.debt.id

    writeoff = await ledger.record_adjustment(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        amount="-1000.00",
        reason="Returned goods",
    )
    assert writeoff.store_balance == D("4000.00")
    assert writeoff.allocations[0].amount == D("1000.00")

    with pytest.raises(InvalidAmountError):
        await ledger.record_adjustment(
            wholesaler_id=world.wholesaler.id, store_id=world.store.id, amount="0", reason="x"
        )


async def test_statement_and_aging(session, world: World, make_order) -> None:
    ledger = LedgerService(session)
    past = datetime.now(UTC) - timedelta(days=100)
    await ledger.record_adjustment(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        amount="1000.00",
        reason="Opening balance",
        incurred_at=past,
        due_date=(past + timedelta(days=5)).date(),  # ~95 days overdue
    )
    order = await make_order("300.00")
    await ledger.deliver_order(wholesaler_id=world.wholesaler.id, order_id=order.id)
    await ledger.record_payment(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        amount="200.00",
        method=PaymentMethod.CASH,
    )

    now = datetime.now(UTC)
    st = await ledger.get_statement(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        period_from=now - timedelta(days=1),
        period_to=now + timedelta(days=1),
    )
    await end_read(session)
    assert st.opening_balance == D("0.00")
    assert st.total_adjustments == D("1000.00")
    assert st.total_charges == D("300.00")
    assert st.total_payments == D("200.00")
    assert st.closing_balance == D("1100.00")
    assert len(st.transactions) == 3

    aging = await ledger.get_aging_report(wholesaler_id=world.wholesaler.id, as_of=date.today())
    await end_read(session)
    assert len(aging) == 1
    row = aging[0]
    assert row.total == D("1100.00")
    assert row.buckets["90_plus"] == D("800.00")  # FIFO paid 200 off the oldest debt
    assert row.buckets["current"] == D("300.00")
