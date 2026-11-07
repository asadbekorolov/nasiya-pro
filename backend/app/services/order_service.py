"""Orders and warehouse stock.

Lifecycle::

    draft --submit--> pending --approve--> approved --ship--> shipped --deliver--> delivered
      \\______________________\\________________\\______________/
                                cancel  -> cancelled

Stock model (per product)::

    stock_quantity     on hand
    reserved_quantity  held by approved/shipped orders
    available          = stock_quantity - reserved_quantity   (what can be ordered)

* approve  : available >= qty for every line, then reserved += qty (all-or-nothing)
* deliver  : stock -= qty, reserved -= qty, and the debt is billed through
             `LedgerService.deliver_order` IN THE SAME DB TRANSACTION
* cancel   : an approved/shipped order releases its reservation

Locks follow the global order in `services.locks` (store -> order -> products by id),
the same order LedgerService uses, so order and ledger operations never deadlock.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.clock import Clock, utc_now
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    CreditLimitExceededError,
    IdempotencyConflictError,
    InactiveEntityError,
    InsufficientStockError,
    InvalidOrderError,
    InvalidOrderStateError,
    LedgerIntegrityError,
    NotFoundError,
    ProductUnavailableError,
)
from app.core.money import ZERO, round_money
from app.core.uuid7 import uuid7
from app.db.uow import atomic
from app.models import (
    CANCELLABLE_ORDER_STATUSES,
    DELIVERABLE_ORDER_STATUSES,
    RESERVING_ORDER_STATUSES,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    Store,
)
from app.services.ledger_service import ChargeResult, LedgerService
from app.services.locks import lock_order, lock_products, lock_store

logger = logging.getLogger(__name__)

QTY_QUANTUM = Decimal("0.001")
MAX_ORDER_LINES = 200
MAX_LINE_QUANTITY = Decimal("999999999.999")


@dataclass(frozen=True, slots=True)
class OrderLine:
    product_id: uuid.UUID
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    order: Order
    charge: ChargeResult


@dataclass(frozen=True, slots=True)
class StockShortage:
    product_id: uuid.UUID
    product_name: str
    requested: Decimal
    available: Decimal


def _normalize_quantity(value: Decimal | int | str) -> Decimal:
    if isinstance(value, float):
        raise InvalidOrderError("Float quantities are not allowed; use Decimal or str")
    try:
        qty = Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise InvalidOrderError(f"Invalid quantity: {value!r}") from exc
    if not qty.is_finite() or qty != qty.quantize(QTY_QUANTUM):
        raise InvalidOrderError("Quantity must have at most 3 decimal places")
    if qty <= 0 or qty > MAX_LINE_QUANTITY:
        raise InvalidOrderError("Quantity must be greater than zero")
    return qty.quantize(QTY_QUANTUM)


class OrderService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        clock: Clock = utc_now,
        ledger: LedgerService | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._clock = clock
        self.ledger = ledger or LedgerService(session, settings=self.settings, clock=clock)

    # ======================================================================= create ==

    async def create_order(
        self,
        *,
        wholesaler_id: uuid.UUID,
        store_id: uuid.UUID,
        lines: Sequence[OrderLine],
        agent_id: uuid.UUID | None = None,
        note: str | None = None,
        submit: bool = True,
        idempotency_key: str | None = None,
        actor_telegram_user_id: int | None = None,
    ) -> Order:
        """Create an order with price snapshots. `submit=False` keeps it as a draft.
        Nothing is reserved yet: stock is held only when the wholesaler approves."""
        normalized = self._normalize_lines(lines)

        order_id: uuid.UUID | None = None
        created = False
        async with atomic(self.session):
            if idempotency_key is not None:
                existing = (
                    await self.session.execute(
                        select(Order.id, Order.store_id).where(
                            Order.wholesaler_id == wholesaler_id,
                            Order.idempotency_key == idempotency_key,
                        )
                    )
                ).first()
                if existing is not None:
                    if existing.store_id != store_id:
                        raise IdempotencyConflictError("Idempotency key was used for another store")
                    order_id = existing.id

            if order_id is None:
                created = True
                store = await self.session.scalar(
                    select(Store).where(Store.id == store_id, Store.wholesaler_id == wholesaler_id)
                )
                if store is None:
                    raise NotFoundError("Store not found", store_id=str(store_id))
                if not store.is_active:
                    raise InactiveEntityError("Store is deactivated")

                products = await self._load_orderable_products(
                    wholesaler_id, [line.product_id for line in normalized]
                )
                self._ensure_available(products, normalized)

                now = self._clock()
                order = Order(
                    id=uuid7(),
                    wholesaler_id=wholesaler_id,
                    store_id=store_id,
                    agent_id=agent_id,
                    status=OrderStatus.PENDING if submit else OrderStatus.DRAFT,
                    submitted_at=now if submit else None,
                    note=note,
                    idempotency_key=idempotency_key,
                    created_by_telegram_user_id=actor_telegram_user_id,
                    total_amount=ZERO,
                )
                self.session.add(order)
                await self.session.flush()

                total = ZERO
                for line in normalized:
                    product = products[line.product_id]
                    line_total = round_money(product.price * line.quantity)
                    total += line_total
                    self.session.add(
                        OrderItem(
                            id=uuid7(),
                            order_id=order.id,
                            product_id=product.id,
                            product_name=product.name,
                            unit=product.unit.value,
                            unit_price=product.price,
                            quantity=line.quantity,
                            line_total=line_total,
                        )
                    )
                order.total_amount = total
                await self.session.flush()
                order_id = order.id

        if created:
            logger.info("order created", extra={"order_id": str(order_id)})
        return await self.get_order(wholesaler_id=wholesaler_id, order_id=order_id)

    # ==================================================================== transitions ==

    async def submit_order(self, *, wholesaler_id: uuid.UUID, order_id: uuid.UUID) -> Order:
        async with atomic(self.session):
            order = await self._lock_order_with_store(wholesaler_id, order_id)
            if order.status == OrderStatus.PENDING:
                pass  # idempotent
            elif order.status != OrderStatus.DRAFT:
                raise self._state_error(order, "submitted")
            else:
                items = await self._items(order.id)
                products = await self._load_orderable_products(
                    wholesaler_id, [i.product_id for i in items]
                )
                self._ensure_available(
                    products, [OrderLine(i.product_id, i.quantity) for i in items]
                )
                order.status = OrderStatus.PENDING
                order.submitted_at = self._clock()
        return await self.get_order(wholesaler_id=wholesaler_id, order_id=order_id)

    async def approve_order(
        self,
        *,
        wholesaler_id: uuid.UUID,
        order_id: uuid.UUID,
        enforce_credit_limit: bool = True,
    ) -> Order:
        """Reserve stock for every line (all-or-nothing) and approve the order."""
        async with atomic(self.session):
            store, order = await self._lock_store_and_order(wholesaler_id, order_id)
            if order.status in RESERVING_ORDER_STATUSES:
                pass  # already approved: idempotent
            elif order.status != OrderStatus.PENDING:
                raise self._state_error(order, "approved")
            else:
                if not store.is_active:
                    raise InactiveEntityError("Store is deactivated")
                if enforce_credit_limit:
                    await self._ensure_credit_for(store, order)

                items = await self._items(order.id)
                products = await lock_products(
                    self.session, wholesaler_id, [i.product_id for i in items]
                )
                shortages = [
                    StockShortage(
                        product_id=i.product_id,
                        product_name=i.product_name,
                        requested=i.quantity,
                        available=products[i.product_id].available_quantity
                        if i.product_id in products
                        else Decimal("0.000"),
                    )
                    for i in items
                    if i.product_id not in products
                    or products[i.product_id].available_quantity < i.quantity
                ]
                if shortages:
                    raise InsufficientStockError(
                        "Not enough stock to approve the order",
                        shortages=[
                            {
                                "product_id": str(s.product_id),
                                "product_name": s.product_name,
                                "requested": str(s.requested),
                                "available": str(s.available),
                            }
                            for s in shortages
                        ],
                    )
                for item in items:
                    products[item.product_id].reserved_quantity += item.quantity
                order.status = OrderStatus.APPROVED
                order.approved_at = self._clock()
                await self.session.flush()
        return await self.get_order(wholesaler_id=wholesaler_id, order_id=order_id)

    async def ship_order(self, *, wholesaler_id: uuid.UUID, order_id: uuid.UUID) -> Order:
        async with atomic(self.session):
            order = await self._lock_order_with_store(wholesaler_id, order_id)
            if order.status == OrderStatus.SHIPPED:
                pass
            elif order.status != OrderStatus.APPROVED:
                raise self._state_error(order, "shipped")
            else:
                order.status = OrderStatus.SHIPPED
                order.shipped_at = self._clock()
        return await self.get_order(wholesaler_id=wholesaler_id, order_id=order_id)

    async def deliver_order(
        self,
        *,
        wholesaler_id: uuid.UUID,
        order_id: uuid.UUID,
        actor_telegram_user_id: int | None = None,
    ) -> DeliveryResult:
        """Write stock off and bill the store — one atomic DB transaction.
        Idempotent: delivering twice neither bills nor writes off stock twice."""
        async with atomic(self.session):
            _, order = await self._lock_store_and_order(wholesaler_id, order_id)

            if order.status == OrderStatus.DELIVERED:
                charge = await self.ledger.deliver_order(
                    wholesaler_id=wholesaler_id, order_id=order_id
                )
            elif order.status not in DELIVERABLE_ORDER_STATUSES:
                raise self._state_error(order, "delivered")
            else:
                items = await self._items(order.id)
                products = await lock_products(
                    self.session, wholesaler_id, [i.product_id for i in items]
                )
                for item in items:
                    product = products.get(item.product_id)
                    if (
                        product is None
                        or product.reserved_quantity < item.quantity
                        or product.stock_quantity < item.quantity
                    ):
                        raise LedgerIntegrityError(
                            "Stock reservation is missing for a delivered line",
                            product_id=str(item.product_id),
                        )
                    product.stock_quantity -= item.quantity
                    product.reserved_quantity -= item.quantity
                await self.session.flush()
                # Runs in a SAVEPOINT of this transaction, under the same locks.
                charge = await self.ledger.deliver_order(
                    wholesaler_id=wholesaler_id,
                    order_id=order_id,
                    actor_telegram_user_id=actor_telegram_user_id,
                )
        order = await self.get_order(wholesaler_id=wholesaler_id, order_id=order_id)
        return DeliveryResult(order=order, charge=charge)

    async def cancel_order(
        self,
        *,
        wholesaler_id: uuid.UUID,
        order_id: uuid.UUID,
        reason: str | None = None,
    ) -> Order:
        async with atomic(self.session):
            _, order = await self._lock_store_and_order(wholesaler_id, order_id)
            if order.status == OrderStatus.CANCELLED:
                pass
            elif order.status not in CANCELLABLE_ORDER_STATUSES:
                raise self._state_error(order, "cancelled")
            else:
                if order.status in RESERVING_ORDER_STATUSES:
                    items = await self._items(order.id)
                    products = await lock_products(
                        self.session, wholesaler_id, [i.product_id for i in items]
                    )
                    for item in items:
                        product = products[item.product_id]
                        product.reserved_quantity = max(
                            product.reserved_quantity - item.quantity, Decimal("0.000")
                        )
                order.status = OrderStatus.CANCELLED
                order.cancelled_at = self._clock()
                order.cancel_reason = reason
                await self.session.flush()
        return await self.get_order(wholesaler_id=wholesaler_id, order_id=order_id)

    # ========================================================================== reads ==

    async def get_order(
        self,
        *,
        wholesaler_id: uuid.UUID,
        order_id: uuid.UUID,
        store_id: uuid.UUID | None = None,
    ) -> Order:
        stmt = (
            select(Order)
            .where(Order.id == order_id, Order.wholesaler_id == wholesaler_id)
            .options(selectinload(Order.items))
            .execution_options(populate_existing=True)
        )
        if store_id is not None:
            stmt = stmt.where(Order.store_id == store_id)
        order = await self.session.scalar(stmt)
        if order is None:
            raise NotFoundError("Order not found", order_id=str(order_id))
        return order

    async def list_orders(
        self,
        *,
        wholesaler_id: uuid.UUID,
        store_ids: Sequence[uuid.UUID] | None = None,
        statuses: Sequence[OrderStatus] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Order]:
        stmt = (
            select(Order)
            .where(Order.wholesaler_id == wholesaler_id)
            .options(selectinload(Order.items))
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(min(max(limit, 1), 200))
            .offset(max(offset, 0))
        )
        if store_ids is not None:
            stmt = stmt.where(Order.store_id.in_(list(store_ids)))
        if statuses:
            stmt = stmt.where(Order.status.in_(list(statuses)))
        return list((await self.session.scalars(stmt)).all())

    async def list_catalog(
        self,
        *,
        wholesaler_id: uuid.UUID,
        search: str | None = None,
        in_stock_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Product]:
        stmt = (
            select(Product)
            .where(Product.wholesaler_id == wholesaler_id, Product.is_active.is_(True))
            .order_by(Product.name, Product.id)
            .limit(min(max(limit, 1), 200))
            .offset(max(offset, 0))
        )
        if search and search.strip():
            escaped = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            stmt = stmt.where(or_(Product.name.ilike(pattern), Product.sku.ilike(pattern)))
        if in_stock_only:
            stmt = stmt.where(Product.stock_quantity - Product.reserved_quantity > 0)
        return list((await self.session.scalars(stmt)).all())

    # ====================================================================== internals ==

    @staticmethod
    def _normalize_lines(lines: Sequence[OrderLine]) -> list[OrderLine]:
        if not lines:
            raise InvalidOrderError("Order must contain at least one line")
        if len(lines) > MAX_ORDER_LINES:
            raise InvalidOrderError(f"Order cannot have more than {MAX_ORDER_LINES} lines")
        seen: set[uuid.UUID] = set()
        result: list[OrderLine] = []
        for line in lines:
            if line.product_id in seen:
                raise InvalidOrderError(
                    "Each product may appear only once", product_id=str(line.product_id)
                )
            seen.add(line.product_id)
            result.append(OrderLine(line.product_id, _normalize_quantity(line.quantity)))
        return result

    async def _load_orderable_products(
        self, wholesaler_id: uuid.UUID, product_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, Product]:
        rows = (
            await self.session.scalars(
                select(Product)
                .where(Product.id.in_(list(product_ids)), Product.wholesaler_id == wholesaler_id)
                .execution_options(populate_existing=True)
            )
        ).all()
        products = {p.id: p for p in rows if p.is_active}
        missing = [str(pid) for pid in product_ids if pid not in products]
        if missing:
            raise ProductUnavailableError("Some products are not available", product_ids=missing)
        return products

    @staticmethod
    def _ensure_available(products: dict[uuid.UUID, Product], lines: Sequence[OrderLine]) -> None:
        """Early, non-locking check for a friendly error in the Mini App. The binding
        check happens under row locks in `approve_order`."""
        short = [
            {
                "product_id": str(line.product_id),
                "product_name": products[line.product_id].name,
                "requested": str(line.quantity),
                "available": str(products[line.product_id].available_quantity),
            }
            for line in lines
            if products[line.product_id].available_quantity < line.quantity
        ]
        if short:
            raise InsufficientStockError("Not enough stock", shortages=short)

    async def _items(self, order_id: uuid.UUID) -> list[OrderItem]:
        return list(
            (
                await self.session.scalars(
                    select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.id)
                )
            ).all()
        )

    async def _lock_store_and_order(
        self, wholesaler_id: uuid.UUID, order_id: uuid.UUID
    ) -> tuple[Store, Order]:
        store_id = await self.session.scalar(
            select(Order.store_id).where(Order.id == order_id, Order.wholesaler_id == wholesaler_id)
        )
        if store_id is None:
            raise NotFoundError("Order not found", order_id=str(order_id))
        store = await lock_store(self.session, wholesaler_id, store_id)
        order = await lock_order(self.session, wholesaler_id, order_id)
        return store, order

    async def _lock_order_with_store(self, wholesaler_id: uuid.UUID, order_id: uuid.UUID) -> Order:
        _, order = await self._lock_store_and_order(wholesaler_id, order_id)
        return order

    async def _ensure_credit_for(self, store: Store, order: Order) -> None:
        """Exposure = current debt + approved-but-undelivered orders + this order."""
        in_flight = await self.session.scalar(
            select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                Order.store_id == store.id,
                Order.status.in_(list(RESERVING_ORDER_STATUSES)),
                Order.id != order.id,
            )
        )
        exposure = max(store.current_debt, ZERO) + Decimal(in_flight or 0) + order.total_amount
        if exposure > store.credit_limit:
            raise CreditLimitExceededError(
                "Credit limit exceeded",
                credit_limit=str(store.credit_limit),
                current_debt=str(store.current_debt),
                pending_deliveries=str(in_flight),
                order_total=str(order.total_amount),
            )

    @staticmethod
    def _state_error(order: Order, target: str) -> InvalidOrderStateError:
        return InvalidOrderStateError(
            f"Order in status '{order.status.value}' cannot be {target}",
            status=order.status.value,
        )
