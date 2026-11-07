"""Ledger service: the only code allowed to change a store's balance.

Every public write method runs as ONE database transaction (see `db.uow.atomic`) and
follows the same protocol:

1. Lock the store row (`SELECT ... FOR UPDATE`). This serialises every balance change
   of that store, so concurrent deliveries / payments can never interleave.
   Lock order is always: store -> order / OTP -> debts. A single global order means two
   ledger operations can never deadlock each other.
2. Re-read locked rows with `populate_existing` so the identity map can never hand us a
   stale `current_debt`.
3. Append exactly one hash-chained row to `transactions`, mutate `debts`, update
   `stores.current_debt`.
4. Re-check the store invariant
       SUM(open debts.remaining_amount) == GREATEST(current_debt, 0)
   and abort (rollback) if it does not hold.

Sign convention: positive amount = store owes more; negative = store owes less.
A negative `current_debt` is an advance (prepayment); it is automatically offset
against the next charge.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Date, case, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock, utc_now
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    CreditLimitExceededError,
    IdempotencyConflictError,
    InactiveEntityError,
    InvalidAmountError,
    InvalidOrderStateError,
    LedgerIntegrityError,
    NotFoundError,
    OtpAlreadyUsedError,
    OtpMismatchError,
    OtpRequiredError,
)
from app.core.money import ZERO, to_money, to_positive_money
from app.core.uuid7 import uuid7
from app.db.uow import atomic
from app.models import (
    DELIVERABLE_ORDER_STATUSES,
    Agent,
    Debt,
    DebtAllocation,
    DebtSource,
    LedgerTransaction,
    Order,
    OrderItem,
    OrderStatus,
    OtpStatus,
    PaymentMethod,
    PaymentOtpVerification,
    Store,
    TransactionType,
    Wholesaler,
)
from app.services.ledger_hash import compute_entry_hash
from app.services.locks import lock_store

logger = logging.getLogger(__name__)

AGING_BUCKETS: tuple[str, ...] = ("current", "1_30", "31_60", "61_90", "90_plus")


# --- Result objects ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AllocationResult:
    debt_id: uuid.UUID
    order_id: uuid.UUID | None
    amount: Decimal
    remaining_after: Decimal
    closed: bool


@dataclass(frozen=True, slots=True)
class ChargeResult:
    transaction: LedgerTransaction
    debt: Debt
    store_balance: Decimal
    advance_applied: Decimal
    created: bool  # False => idempotent replay of an already delivered order


@dataclass(frozen=True, slots=True)
class PaymentResult:
    transaction: LedgerTransaction
    allocations: tuple[AllocationResult, ...]
    store_balance: Decimal
    advance_amount: Decimal  # part of the payment not matched to any open debt
    created: bool  # False => idempotent replay


@dataclass(frozen=True, slots=True)
class AdjustmentResult:
    transaction: LedgerTransaction
    debt: Debt | None  # set for positive adjustments
    allocations: tuple[AllocationResult, ...]  # set for negative adjustments / offsets
    store_balance: Decimal
    created: bool


@dataclass(frozen=True, slots=True)
class StatementResult:
    store: Store
    period_from: datetime
    period_to: datetime
    opening_balance: Decimal
    total_charges: Decimal
    total_payments: Decimal  # positive number (money received)
    total_adjustments: Decimal  # signed
    closing_balance: Decimal
    transactions: tuple[LedgerTransaction, ...]


@dataclass(frozen=True, slots=True)
class AgingRow:
    store_id: uuid.UUID
    store_name: str
    buckets: dict[str, Decimal]
    total: Decimal


@dataclass(frozen=True, slots=True)
class ChainVerification:
    store_id: uuid.UUID
    ok: bool
    transactions_checked: int
    broken_at_sequence: int | None = None
    reason: str | None = None


# --- Service -------------------------------------------------------------------------


class LedgerService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._clock = clock

    # ======================================================================= writes ==

    async def deliver_order(
        self,
        *,
        wholesaler_id: uuid.UUID,
        order_id: uuid.UUID,
        actor_telegram_user_id: int | None = None,
        enforce_credit_limit: bool = False,
    ) -> ChargeResult:
        """Mark an order as delivered and bill it: +current_debt, new `debts` row and a
        `charge` transaction. Idempotent: delivering an already delivered order returns
        the original charge instead of billing twice."""
        async with atomic(self.session):
            store_id = await self.session.scalar(
                select(Order.store_id).where(
                    Order.id == order_id, Order.wholesaler_id == wholesaler_id
                )
            )
            if store_id is None:
                raise NotFoundError("Order not found", order_id=str(order_id))

            store = await self._lock_store(wholesaler_id, store_id)
            order = await self.session.scalar(
                select(Order)
                .where(Order.id == order_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if order is None or order.store_id != store.id:
                raise LedgerIntegrityError("Order changed while being delivered")

            if order.status == OrderStatus.DELIVERED:
                return await self._replay_charge(order, store)
            if order.status not in DELIVERABLE_ORDER_STATUSES:
                raise InvalidOrderStateError(
                    f"Order in status '{order.status.value}' cannot be delivered",
                    status=order.status.value,
                )
            if not store.is_active:
                raise InactiveEntityError("Store is deactivated", store_id=str(store.id))

            items_total = await self.session.scalar(
                select(func.coalesce(func.sum(OrderItem.line_total), 0)).where(
                    OrderItem.order_id == order.id
                )
            )
            amount = to_money(items_total or ZERO)
            if amount <= ZERO:
                raise InvalidOrderStateError("Order has no billable items")
            if amount != order.total_amount:
                raise LedgerIntegrityError(
                    "Order total does not match the sum of its items",
                    order_total=str(order.total_amount),
                    items_total=str(amount),
                )
            if enforce_credit_limit:
                self._ensure_credit_available(store, amount)

            now = self._now()
            balance_before = store.current_debt
            tx = await self._append_transaction(
                store,
                tx_type=TransactionType.CHARGE,
                amount=amount,
                now=now,
                order_id=order.id,
                agent_id=order.agent_id,
                description=f"Order #{order.order_number}",
                actor_telegram_user_id=actor_telegram_user_id,
            )
            due_date = await self._due_date(store, now)
            debt, offset = await self._open_debt(
                store,
                tx,
                amount=amount,
                balance_before=balance_before,
                source=DebtSource.ORDER,
                order_id=order.id,
                incurred_at=now,
                due_date=due_date,
            )

            order.status = OrderStatus.DELIVERED
            order.delivered_at = now
            await self.session.flush()
            await self._assert_invariant(store)

        logger.info(
            "order delivered",
            extra={"order_id": str(order_id), "store_id": str(store.id), "amount": str(amount)},
        )
        return ChargeResult(
            transaction=tx,
            debt=debt,
            store_balance=store.current_debt,
            advance_applied=offset,
            created=True,
        )

    async def record_payment(
        self,
        *,
        wholesaler_id: uuid.UUID,
        store_id: uuid.UUID,
        amount: Decimal | str | int,
        method: PaymentMethod,
        agent_id: uuid.UUID | None = None,
        otp_verification_id: uuid.UUID | None = None,
        idempotency_key: str | None = None,
        description: str | None = None,
        actor_telegram_user_id: int | None = None,
    ) -> PaymentResult:
        """Receive money from a store and close its open debts oldest-first (FIFO).

        Any surplus over the open debts becomes an advance (negative balance).
        Payments collected by an agent must carry a VERIFIED payment OTP.
        """
        value = to_positive_money(amount)
        method = PaymentMethod(method)
        if (
            agent_id is not None
            and otp_verification_id is None
            and self.settings.otp_required_for_agent_payments
        ):
            raise OtpRequiredError("Payments collected by an agent require store confirmation")

        async with atomic(self.session):
            store = await self._lock_store(wholesaler_id, store_id)

            replay = await self._find_payment_replay(
                store, value, idempotency_key, otp_verification_id
            )
            if replay is not None:
                return replay

            if agent_id is not None:
                await self._get_active_agent(wholesaler_id, agent_id)

            otp: PaymentOtpVerification | None = None
            if otp_verification_id is not None:
                otp = await self._lock_verified_otp(
                    store=store,
                    otp_id=otp_verification_id,
                    agent_id=agent_id,
                    amount=value,
                    method=method,
                )
                agent_id = otp.agent_id

            now = self._now()
            open_debts = await self._lock_open_debts(store.id)
            tx = await self._append_transaction(
                store,
                tx_type=TransactionType.PAYMENT,
                amount=-value,
                now=now,
                payment_method=method,
                agent_id=agent_id,
                otp_verification_id=otp_verification_id,
                idempotency_key=idempotency_key,
                description=description,
                actor_telegram_user_id=actor_telegram_user_id,
            )
            allocations = await self._allocate_fifo(open_debts, tx, value, now)

            if otp is not None:
                otp.status = OtpStatus.CONSUMED
                otp.consumed_at = now

            await self.session.flush()
            await self._assert_invariant(store)

        allocated = sum((a.amount for a in allocations), ZERO)
        logger.info(
            "payment recorded",
            extra={"store_id": str(store_id), "amount": str(value), "debts": len(allocations)},
        )
        return PaymentResult(
            transaction=tx,
            allocations=tuple(allocations),
            store_balance=store.current_debt,
            advance_amount=value - allocated,
            created=True,
        )

    async def record_adjustment(
        self,
        *,
        wholesaler_id: uuid.UUID,
        store_id: uuid.UUID,
        amount: Decimal | str | int,
        reason: str,
        due_date: date | None = None,
        incurred_at: datetime | None = None,
        idempotency_key: str | None = None,
        actor_telegram_user_id: int | None = None,
    ) -> AdjustmentResult:
        """Signed manual correction, e.g. an opening balance when onboarding a store.

        * amount > 0 -> creates an `adjustment` debt (respecting any advance).
        * amount < 0 -> reduces open debts FIFO, exactly like a payment.
        The ledger itself is never edited: corrections are always new rows.
        """
        value = to_money(amount)
        if value == ZERO:
            raise InvalidAmountError("Adjustment amount must not be zero")
        reason = (reason or "").strip()
        if not reason:
            raise InvalidAmountError("Adjustment requires a reason")

        async with atomic(self.session):
            store = await self._lock_store(wholesaler_id, store_id)

            if idempotency_key is not None:
                existing = await self._find_by_idempotency_key(wholesaler_id, idempotency_key)
                if existing is not None:
                    if (
                        existing.store_id != store.id
                        or existing.type != TransactionType.ADJUSTMENT
                        or existing.amount != value
                    ):
                        raise IdempotencyConflictError(
                            "Idempotency key was already used for a different operation"
                        )
                    origin_debt = await self.session.scalar(
                        select(Debt).where(Debt.origin_transaction_id == existing.id)
                    )
                    return AdjustmentResult(
                        transaction=existing,
                        debt=origin_debt,
                        allocations=await self._load_allocations(existing.id),
                        store_balance=store.current_debt,
                        created=False,
                    )

            now = self._now()
            balance_before = store.current_debt
            debt: Debt | None = None
            allocations: list[AllocationResult] = []

            if value > ZERO:
                tx = await self._append_transaction(
                    store,
                    tx_type=TransactionType.ADJUSTMENT,
                    amount=value,
                    now=now,
                    idempotency_key=idempotency_key,
                    description=reason,
                    actor_telegram_user_id=actor_telegram_user_id,
                )
                debt, _ = await self._open_debt(
                    store,
                    tx,
                    amount=value,
                    balance_before=balance_before,
                    source=DebtSource.ADJUSTMENT,
                    order_id=None,
                    incurred_at=incurred_at or now,
                    due_date=due_date or await self._due_date(store, incurred_at or now),
                )
                allocations = list(await self._load_allocations(tx.id))
            else:
                open_debts = await self._lock_open_debts(store.id)
                tx = await self._append_transaction(
                    store,
                    tx_type=TransactionType.ADJUSTMENT,
                    amount=value,
                    now=now,
                    idempotency_key=idempotency_key,
                    description=reason,
                    actor_telegram_user_id=actor_telegram_user_id,
                )
                allocations = await self._allocate_fifo(open_debts, tx, -value, now)

            await self.session.flush()
            await self._assert_invariant(store)

        return AdjustmentResult(
            transaction=tx,
            debt=debt,
            allocations=tuple(allocations),
            store_balance=store.current_debt,
            created=True,
        )

    # ======================================================================== reads ==

    async def get_available_credit(
        self, *, wholesaler_id: uuid.UUID, store_id: uuid.UUID
    ) -> Decimal:
        store = await self.get_store(wholesaler_id, store_id)
        return store.credit_limit - max(store.current_debt, ZERO)

    async def get_statement(
        self,
        *,
        wholesaler_id: uuid.UUID,
        store_id: uuid.UUID,
        period_from: datetime,
        period_to: datetime,
    ) -> StatementResult:
        """Period statement — the data source for the Akt-sverka (reconciliation act).
        The period is the half-open interval [period_from, period_to)."""
        if period_to <= period_from:
            raise InvalidAmountError("period_to must be after period_from")
        store = await self.get_store(wholesaler_id, store_id)

        opening = await self.session.scalar(
            select(LedgerTransaction.balance_after)
            .where(
                LedgerTransaction.store_id == store.id,
                LedgerTransaction.created_at < period_from,
            )
            .order_by(LedgerTransaction.sequence_no.desc())
            .limit(1)
        )
        opening_balance = opening if opening is not None else ZERO

        rows = (
            await self.session.scalars(
                select(LedgerTransaction)
                .where(
                    LedgerTransaction.store_id == store.id,
                    LedgerTransaction.created_at >= period_from,
                    LedgerTransaction.created_at < period_to,
                )
                .order_by(LedgerTransaction.sequence_no)
            )
        ).all()

        charges = sum((t.amount for t in rows if t.type == TransactionType.CHARGE), ZERO)
        payments = sum((-t.amount for t in rows if t.type == TransactionType.PAYMENT), ZERO)
        adjustments = sum((t.amount for t in rows if t.type == TransactionType.ADJUSTMENT), ZERO)
        closing = rows[-1].balance_after if rows else opening_balance

        if opening_balance + charges - payments + adjustments != closing:
            raise LedgerIntegrityError("Statement does not reconcile", store_id=str(store.id))

        return StatementResult(
            store=store,
            period_from=period_from,
            period_to=period_to,
            opening_balance=opening_balance,
            total_charges=charges,
            total_payments=payments,
            total_adjustments=adjustments,
            closing_balance=closing,
            transactions=tuple(rows),
        )

    async def get_aging_report(
        self,
        *,
        wholesaler_id: uuid.UUID,
        as_of: date | None = None,
        store_id: uuid.UUID | None = None,
    ) -> list[AgingRow]:
        """Open receivables grouped by days past due:
        current (not yet due), 1-30, 31-60, 61-90, 90+."""
        as_of = as_of or await self.local_today(wholesaler_id)
        as_of_sql = literal(as_of, type_=Date)
        days_overdue = as_of_sql - Debt.due_date
        bucket = case(
            (Debt.due_date >= as_of_sql, "current"),
            (days_overdue <= 30, "1_30"),
            (days_overdue <= 60, "31_60"),
            (days_overdue <= 90, "61_90"),
            else_="90_plus",
        ).label("bucket")

        stmt = (
            select(Debt.store_id, Store.name, bucket, func.sum(Debt.remaining_amount))
            .join(Store, Store.id == Debt.store_id)
            .where(Debt.wholesaler_id == wholesaler_id, Debt.is_closed.is_(False))
            .group_by(Debt.store_id, Store.name, bucket)
            .order_by(Store.name)
        )
        if store_id is not None:
            stmt = stmt.where(Debt.store_id == store_id)

        names: dict[uuid.UUID, str] = {}
        buckets: dict[uuid.UUID, dict[str, Decimal]] = {}
        for sid, name, bucket_name, total in (await self.session.execute(stmt)).all():
            names[sid] = name
            buckets.setdefault(sid, dict.fromkeys(AGING_BUCKETS, ZERO))[bucket_name] += total
        report = [
            AgingRow(store_id=sid, store_name=names[sid], buckets=b, total=sum(b.values(), ZERO))
            for sid, b in buckets.items()
        ]
        return sorted(report, key=lambda r: r.total, reverse=True)

    async def list_transactions(
        self, *, store_id: uuid.UUID, limit: int = 30, before_sequence: int | None = None
    ) -> list[LedgerTransaction]:
        """Newest first, keyset-paginated on `sequence_no` (stable under inserts)."""
        stmt = (
            select(LedgerTransaction)
            .where(LedgerTransaction.store_id == store_id)
            .order_by(LedgerTransaction.sequence_no.desc())
            .limit(min(max(limit, 1), 100))
        )
        if before_sequence is not None:
            stmt = stmt.where(LedgerTransaction.sequence_no < before_sequence)
        return list((await self.session.scalars(stmt)).all())

    async def get_last_payment(self, *, store_id: uuid.UUID) -> LedgerTransaction | None:
        tx: LedgerTransaction | None = await self.session.scalar(
            select(LedgerTransaction)
            .where(
                LedgerTransaction.store_id == store_id,
                LedgerTransaction.type == TransactionType.PAYMENT,
            )
            .order_by(LedgerTransaction.sequence_no.desc())
            .limit(1)
        )
        return tx

    async def get_overdue_amounts(
        self, *, wholesaler_id: uuid.UUID, store_ids: Sequence[uuid.UUID], as_of: date
    ) -> dict[uuid.UUID, Decimal]:
        """Open debt past its due date, per store (missing store => 0)."""
        if not store_ids:
            return {}
        rows = await self.session.execute(
            select(Debt.store_id, func.sum(Debt.remaining_amount))
            .where(
                Debt.wholesaler_id == wholesaler_id,
                Debt.store_id.in_(list(store_ids)),
                Debt.is_closed.is_(False),
                Debt.due_date < as_of,
            )
            .group_by(Debt.store_id)
        )
        return {sid: total for sid, total in rows.all()}

    async def verify_chain(
        self, *, wholesaler_id: uuid.UUID, store_id: uuid.UUID
    ) -> ChainVerification:
        """Recompute the hash chain and running balance of a store from scratch."""
        store = await self.get_store(wholesaler_id, store_id)
        result = await self.session.stream_scalars(
            select(LedgerTransaction)
            .where(LedgerTransaction.store_id == store.id)
            .order_by(LedgerTransaction.sequence_no)
            .execution_options(yield_per=500)
        )

        expected_seq = 1
        prev_hash: str | None = None
        balance = ZERO
        checked = 0

        def broken(seq: int, reason: str) -> ChainVerification:
            return ChainVerification(store.id, False, checked, seq, reason)

        async for tx in result:
            if tx.sequence_no != expected_seq:
                return broken(expected_seq, "sequence gap (missing row)")
            if tx.prev_hash != prev_hash:
                return broken(tx.sequence_no, "prev_hash does not link to previous row")
            if tx.balance_before != balance:
                return broken(tx.sequence_no, "balance_before does not continue the chain")
            recomputed = self._hash_of(tx)
            if recomputed != tx.entry_hash:
                return broken(tx.sequence_no, "entry_hash mismatch (row content altered)")
            balance = tx.balance_after
            prev_hash = tx.entry_hash
            expected_seq += 1
            checked += 1

        if balance != store.current_debt:
            return ChainVerification(
                store.id, False, checked, None, "stores.current_debt differs from ledger"
            )
        return ChainVerification(store.id, True, checked)

    # ===================================================================== internals ==

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise LedgerIntegrityError("Clock must return timezone-aware datetimes")
        return now

    async def _lock_store(self, wholesaler_id: uuid.UUID, store_id: uuid.UUID) -> Store:
        return await lock_store(self.session, wholesaler_id, store_id)

    async def get_store(self, wholesaler_id: uuid.UUID, store_id: uuid.UUID) -> Store:
        store = await self.session.scalar(
            select(Store)
            .where(Store.id == store_id, Store.wholesaler_id == wholesaler_id)
            .execution_options(populate_existing=True)
        )
        if store is None:
            raise NotFoundError("Store not found", store_id=str(store_id))
        return store

    async def _get_active_agent(self, wholesaler_id: uuid.UUID, agent_id: uuid.UUID) -> Agent:
        agent = await self.session.scalar(
            select(Agent).where(Agent.id == agent_id, Agent.wholesaler_id == wholesaler_id)
        )
        if agent is None:
            raise NotFoundError("Agent not found", agent_id=str(agent_id))
        if not agent.is_active:
            raise InactiveEntityError("Agent is deactivated", agent_id=str(agent_id))
        return agent

    async def _lock_open_debts(self, store_id: uuid.UUID) -> Sequence[Debt]:
        return (
            await self.session.scalars(
                select(Debt)
                .where(Debt.store_id == store_id, Debt.is_closed.is_(False))
                .order_by(Debt.incurred_at, Debt.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()

    def _ensure_credit_available(self, store: Store, amount: Decimal) -> None:
        projected = store.current_debt + amount
        if projected > store.credit_limit:
            raise CreditLimitExceededError(
                "Credit limit exceeded",
                credit_limit=str(store.credit_limit),
                current_debt=str(store.current_debt),
                requested=str(amount),
                available=str(max(store.credit_limit - store.current_debt, ZERO)),
            )

    async def local_today(self, wholesaler_id: uuid.UUID) -> date:
        tz_name = await self.session.scalar(
            select(Wholesaler.timezone).where(Wholesaler.id == wholesaler_id)
        )
        return self._now().astimezone(ZoneInfo(tz_name or self.settings.default_timezone)).date()

    async def _due_date(self, store: Store, incurred_at: datetime) -> date:
        row = (
            await self.session.execute(
                select(Wholesaler.timezone, Wholesaler.default_payment_term_days).where(
                    Wholesaler.id == store.wholesaler_id
                )
            )
        ).one()
        term = store.payment_term_days if store.payment_term_days is not None else row[1]
        local_day = incurred_at.astimezone(ZoneInfo(row[0])).date()
        return local_day + timedelta(days=term)

    async def _append_transaction(
        self,
        store: Store,
        *,
        tx_type: TransactionType,
        amount: Decimal,
        now: datetime,
        payment_method: PaymentMethod | None = None,
        order_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        otp_verification_id: uuid.UUID | None = None,
        idempotency_key: str | None = None,
        description: str | None = None,
        actor_telegram_user_id: int | None = None,
    ) -> LedgerTransaction:
        """Append one row to the store's chain and move `current_debt`.
        Caller MUST hold the store row lock."""
        last = (
            await self.session.execute(
                select(
                    LedgerTransaction.sequence_no,
                    LedgerTransaction.entry_hash,
                    LedgerTransaction.balance_after,
                )
                .where(LedgerTransaction.store_id == store.id)
                .order_by(LedgerTransaction.sequence_no.desc())
                .limit(1)
            )
        ).first()
        sequence_no = 1 if last is None else last.sequence_no + 1
        prev_hash = None if last is None else last.entry_hash

        balance_before = store.current_debt
        balance_after = to_money(balance_before + amount)
        # Cross-check the denormalised balance against the chain head.
        if last is not None and last.balance_after != balance_before:
            raise LedgerIntegrityError(
                "stores.current_debt diverged from the ledger head", store_id=str(store.id)
            )
        if last is None and balance_before != ZERO:
            raise LedgerIntegrityError(
                "Store has a balance but no ledger history", store_id=str(store.id)
            )

        tx_id = uuid7()
        entry_hash = compute_entry_hash(
            prev_hash=prev_hash,
            transaction_id=tx_id,
            wholesaler_id=store.wholesaler_id,
            store_id=store.id,
            sequence_no=sequence_no,
            tx_type=tx_type,
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            payment_method=payment_method,
            order_id=order_id,
            agent_id=agent_id,
            otp_verification_id=otp_verification_id,
            description=description,
            created_at=now,
        )
        tx = LedgerTransaction(
            id=tx_id,
            wholesaler_id=store.wholesaler_id,
            store_id=store.id,
            sequence_no=sequence_no,
            type=tx_type,
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            payment_method=payment_method,
            order_id=order_id,
            agent_id=agent_id,
            otp_verification_id=otp_verification_id,
            idempotency_key=idempotency_key,
            description=description,
            created_by_telegram_user_id=actor_telegram_user_id,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
            created_at=now,
        )
        self.session.add(tx)
        store.current_debt = balance_after
        await self.session.flush()
        return tx

    @staticmethod
    def _hash_of(tx: LedgerTransaction) -> str:
        return compute_entry_hash(
            prev_hash=tx.prev_hash,
            transaction_id=tx.id,
            wholesaler_id=tx.wholesaler_id,
            store_id=tx.store_id,
            sequence_no=tx.sequence_no,
            tx_type=tx.type,
            amount=tx.amount,
            balance_before=tx.balance_before,
            balance_after=tx.balance_after,
            payment_method=tx.payment_method,
            order_id=tx.order_id,
            agent_id=tx.agent_id,
            otp_verification_id=tx.otp_verification_id,
            description=tx.description,
            created_at=tx.created_at,
        )

    async def _open_debt(
        self,
        store: Store,
        tx: LedgerTransaction,
        *,
        amount: Decimal,
        balance_before: Decimal,
        source: DebtSource,
        order_id: uuid.UUID | None,
        incurred_at: datetime,
        due_date: date,
    ) -> tuple[Debt, Decimal]:
        """Create a debt for a positive balance movement. If the store had an advance
        (negative balance), it is consumed first and recorded as an allocation."""
        offset = min(amount, max(-balance_before, ZERO))
        remaining = amount - offset
        closed = remaining == ZERO
        debt = Debt(
            id=uuid7(),
            wholesaler_id=store.wholesaler_id,
            store_id=store.id,
            order_id=order_id,
            source=source,
            origin_transaction_id=tx.id,
            initial_amount=amount,
            remaining_amount=remaining,
            is_closed=closed,
            closed_at=tx.created_at if closed else None,
            incurred_at=incurred_at,
            due_date=due_date,
        )
        self.session.add(debt)
        await self.session.flush()
        if offset > ZERO:
            self.session.add(
                DebtAllocation(id=uuid7(), transaction_id=tx.id, debt_id=debt.id, amount=offset)
            )
            await self.session.flush()
        return debt, offset

    async def _allocate_fifo(
        self,
        open_debts: Sequence[Debt],
        tx: LedgerTransaction,
        budget: Decimal,
        now: datetime,
    ) -> list[AllocationResult]:
        allocations: list[AllocationResult] = []
        for debt in open_debts:
            if budget <= ZERO:
                break
            take = min(debt.remaining_amount, budget)
            if take <= ZERO:
                continue
            debt.remaining_amount -= take
            budget -= take
            if debt.remaining_amount == ZERO:
                debt.is_closed = True
                debt.closed_at = now
            self.session.add(
                DebtAllocation(id=uuid7(), transaction_id=tx.id, debt_id=debt.id, amount=take)
            )
            allocations.append(
                AllocationResult(
                    debt_id=debt.id,
                    order_id=debt.order_id,
                    amount=take,
                    remaining_after=debt.remaining_amount,
                    closed=debt.is_closed,
                )
            )
        await self.session.flush()
        return allocations

    async def _assert_invariant(self, store: Store) -> None:
        open_total = await self.session.scalar(
            select(func.coalesce(func.sum(Debt.remaining_amount), 0)).where(
                Debt.store_id == store.id, Debt.is_closed.is_(False)
            )
        )
        expected = max(store.current_debt, ZERO)
        if to_money(open_total or ZERO) != expected:
            raise LedgerIntegrityError(
                "Open debts do not match the store balance",
                store_id=str(store.id),
                open_debts=str(open_total),
                current_debt=str(store.current_debt),
            )

    # --- idempotency / replay ---------------------------------------------------------

    async def _find_by_idempotency_key(
        self, wholesaler_id: uuid.UUID, key: str
    ) -> LedgerTransaction | None:
        tx: LedgerTransaction | None = await self.session.scalar(
            select(LedgerTransaction).where(
                LedgerTransaction.wholesaler_id == wholesaler_id,
                LedgerTransaction.idempotency_key == key,
            )
        )
        return tx

    async def _load_allocations(self, transaction_id: uuid.UUID) -> tuple[AllocationResult, ...]:
        rows = (
            await self.session.execute(
                select(DebtAllocation, Debt)
                .join(Debt, Debt.id == DebtAllocation.debt_id)
                .where(DebtAllocation.transaction_id == transaction_id)
                .order_by(Debt.incurred_at, Debt.id)
            )
        ).all()
        return tuple(
            AllocationResult(
                debt_id=debt.id,
                order_id=debt.order_id,
                amount=alloc.amount,
                remaining_after=debt.remaining_amount,
                closed=debt.is_closed,
            )
            for alloc, debt in rows
        )

    async def _payment_replay(self, tx: LedgerTransaction, store: Store) -> PaymentResult:
        allocations = await self._load_allocations(tx.id)
        allocated = sum((a.amount for a in allocations), ZERO)
        return PaymentResult(
            transaction=tx,
            allocations=allocations,
            store_balance=store.current_debt,
            advance_amount=-tx.amount - allocated,
            created=False,
        )

    async def _find_payment_replay(
        self,
        store: Store,
        amount: Decimal,
        idempotency_key: str | None,
        otp_verification_id: uuid.UUID | None,
    ) -> PaymentResult | None:
        existing: LedgerTransaction | None = None
        if idempotency_key is not None:
            existing = await self._find_by_idempotency_key(store.wholesaler_id, idempotency_key)
        if existing is None and otp_verification_id is not None:
            existing = await self.session.scalar(
                select(LedgerTransaction).where(
                    LedgerTransaction.otp_verification_id == otp_verification_id
                )
            )
        if existing is None:
            return None
        if (
            existing.store_id != store.id
            or existing.type != TransactionType.PAYMENT
            or existing.amount != -amount
        ):
            raise IdempotencyConflictError(
                "Idempotency key / OTP was already used for a different payment"
            )
        return await self._payment_replay(existing, store)

    async def _replay_charge(self, order: Order, store: Store) -> ChargeResult:
        tx = await self.session.scalar(
            select(LedgerTransaction).where(
                LedgerTransaction.order_id == order.id,
                LedgerTransaction.type == TransactionType.CHARGE,
            )
        )
        debt = (
            None
            if tx is None
            else await self.session.scalar(select(Debt).where(Debt.origin_transaction_id == tx.id))
        )
        if tx is None or debt is None:
            raise LedgerIntegrityError(
                "Delivered order has no charge in the ledger", order_id=str(order.id)
            )
        offset = sum(
            (a.amount for a in await self._load_allocations(tx.id) if a.debt_id == debt.id),
            ZERO,
        )
        return ChargeResult(
            transaction=tx,
            debt=debt,
            store_balance=store.current_debt,
            advance_applied=offset,
            created=False,
        )

    async def _lock_verified_otp(
        self,
        *,
        store: Store,
        otp_id: uuid.UUID,
        agent_id: uuid.UUID | None,
        amount: Decimal,
        method: PaymentMethod,
    ) -> PaymentOtpVerification:
        otp = await self.session.scalar(
            select(PaymentOtpVerification)
            .where(
                PaymentOtpVerification.id == otp_id,
                PaymentOtpVerification.wholesaler_id == store.wholesaler_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if otp is None:
            raise NotFoundError("Payment confirmation not found", otp_id=str(otp_id))
        if otp.status == OtpStatus.CONSUMED:
            raise OtpAlreadyUsedError("This confirmation code was already used")
        if otp.status != OtpStatus.VERIFIED:
            raise OtpRequiredError("Payment confirmation is not verified", status=otp.status.value)
        if otp.store_id != store.id or (agent_id is not None and otp.agent_id != agent_id):
            raise OtpMismatchError("Confirmation belongs to another store or agent")
        if otp.amount != amount or otp.payment_method != method:
            raise OtpMismatchError(
                "Payment differs from the confirmed amount",
                confirmed_amount=str(otp.amount),
                confirmed_method=otp.payment_method.value,
            )
        return otp
