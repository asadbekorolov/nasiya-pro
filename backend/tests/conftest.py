"""Integration tests run against a real PostgreSQL (locks, CHECKs and triggers are the
point). Set TEST_DATABASE_URL, e.g.:

    TEST_DATABASE_URL=postgresql+asyncpg://nasiya:nasiya@localhost:5432/nasiya_test

Each test creates its own tenant (wholesaler), so tests never see each other's rows
and the append-only ledger never needs to be truncated.
"""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get(
        "TEST_DATABASE_URL", "postgresql+asyncpg://nasiya:nasiya@localhost:5432/nasiya_test"
    ),
)
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TEST-TOKEN")
os.environ.setdefault("OTP_SECRET", "test-otp-secret-0123456789-abcdefghijklmnop")

import secrets
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.money import round_money
from app.db.base import Base, model_to_dict
from app.db.session import build_engine, build_sessionmaker
from app.models import (
    Agent,
    AgentStoreAssignment,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    Store,
    Wholesaler,
)


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = build_engine(get_settings())
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="session")
def sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return build_sessionmaker(engine)


@pytest.fixture
async def session(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as s:
        yield s


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now = self.now + timedelta(**kwargs)


@dataclass(frozen=True)
class World:
    """Plain snapshots (not ORM instances): a rollback inside a service expires ORM
    objects in the session, and touching them afterwards would trigger lazy IO."""

    wholesaler: SimpleNamespace
    store: SimpleNamespace
    agent: SimpleNamespace
    product: SimpleNamespace


def _snapshot(obj: object) -> SimpleNamespace:
    return SimpleNamespace(**model_to_dict(obj))


def next_tg_id() -> int:
    """Random Telegram user id; unique enough across test runs on a reused DB."""
    return secrets.randbelow(10**12) + 1


@pytest.fixture
async def world(session: AsyncSession) -> World:
    async with session.begin():
        w = Wholesaler(
            name="Test Optom",
            legal_name='"Test Optom Savdo" MChJ',
            tin="301234567",
            address="Toshkent sh., Chilonzor t., 1-uy",
            phone="+998901234567",
            bank_name="Kapitalbank",
            bank_account="20208000900123456001",
            bank_mfo="01158",
            owner_telegram_user_id=next_tg_id(),
            timezone="Asia/Tashkent",
        )
        session.add(w)
        await session.flush()
        store = Store(
            wholesaler_id=w.id,
            name="Do'kon A",
            telegram_user_id=next_tg_id(),
            credit_limit=Decimal("1000000.00"),
        )
        agent = Agent(wholesaler_id=w.id, display_name="Agent 1", telegram_user_id=next_tg_id())
        product = Product(
            wholesaler_id=w.id,
            sku=f"SKU-{uuid.uuid4().hex[:8]}",
            name="Un 50kg",
            price=Decimal("100.00"),
            stock_quantity=Decimal("1000.000"),
        )
        session.add_all([store, agent, product])
        await session.flush()
        session.add(AgentStoreAssignment(wholesaler_id=w.id, agent_id=agent.id, store_id=store.id))
        await session.flush()
        world = World(_snapshot(w), _snapshot(store), _snapshot(agent), _snapshot(product))
    return world


@pytest.fixture
def make_order(session: AsyncSession, world: World) -> Callable[..., Awaitable[Order]]:
    async def _make(
        total: Decimal | str,
        *,
        status: OrderStatus = OrderStatus.APPROVED,
        store_id: uuid.UUID | None = None,
    ) -> Order:
        """Ledger-level fixture: inserts an order directly in `status` (bypassing
        OrderService, so no stock is reserved)."""
        total = Decimal(total)
        now = datetime.now(UTC)
        submitted = status not in (OrderStatus.DRAFT, OrderStatus.CANCELLED)
        approved = status in (OrderStatus.APPROVED, OrderStatus.SHIPPED, OrderStatus.DELIVERED)
        async with session.begin():
            order = Order(
                wholesaler_id=world.wholesaler.id,
                store_id=store_id or world.store.id,
                agent_id=world.agent.id,
                status=status,
                total_amount=total,
                submitted_at=now if submitted else None,
                approved_at=now if approved else None,
                cancelled_at=now if status == OrderStatus.CANCELLED else None,
            )
            session.add(order)
            await session.flush()
            qty = Decimal("1.000")
            session.add(
                OrderItem(
                    order_id=order.id,
                    product_id=world.product.id,
                    product_name=world.product.name,
                    unit=world.product.unit.value,
                    unit_price=total,
                    quantity=qty,
                    line_total=round_money(total * qty),
                )
            )
            await session.flush()
            snapshot = SimpleNamespace(id=order.id, order_number=order.order_number)
        return snapshot  # type: ignore[return-value]

    return _make


async def end_read(session: AsyncSession) -> None:
    """Finish a read in a test: detach loaded objects (so they keep their values) and
    close the implicit transaction, so the next service call starts a fresh one."""
    session.expunge_all()
    await session.rollback()


# --- HTTP client -------------------------------------------------------------------------


class RecordingNotifier:
    """Replaces Telegram delivery of payment OTP codes."""

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send_payment_otp(self, **kwargs: object) -> int:
        self.sent.append(kwargs)
        return 777


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


@pytest.fixture
async def client(
    sessionmaker: async_sessionmaker[AsyncSession], notifier: RecordingNotifier
) -> AsyncIterator[httpx.AsyncClient]:
    from app.api.deps import get_otp_notifier
    from app.db.session import get_db_session
    from app.main import create_app

    app = create_app()

    async def _session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as s:
            yield s

    app.dependency_overrides[get_db_session] = _session
    app.dependency_overrides[get_otp_notifier] = lambda: notifier
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
        yield c
