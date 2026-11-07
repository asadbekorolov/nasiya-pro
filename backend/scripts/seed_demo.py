"""Create a demo tenant for local development of the Mini App.

    uv run python scripts/seed_demo.py

Telegram ids (use them with scripts/dev_init_data.py):
    1000001  wholesaler admin
    2000001  store owner ("Baraka market")
    3000001  sales agent
Re-running is a no-op when the demo wholesaler already exists. Never run in production.

    uv run python scripts/seed_demo.py --store-phone "+998 90 123 45 67"

puts YOUR phone on the demo store "Baraka market": share your contact with the bot
(/start -> "Telefon raqamni yuborish") and your Telegram account becomes its owner.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import build_engine, build_sessionmaker
from app.models import (
    Agent,
    AgentStoreAssignment,
    PaymentMethod,
    Product,
    ProductUnit,
    Store,
    Wholesaler,
)
from app.services.directory_service import normalize_phone
from app.services.ledger_service import LedgerService
from app.services.order_service import OrderLine, OrderService

ADMIN_TG, STORE_TG, AGENT_TG = 1000001, 2000001, 3000001
# The demo tenant is identified by its TIN, not by its owner: the owner can be handed
# over to a real Telegram account (scripts/seed_owner.py).
DEMO_TIN = "305112233"
EXTRA_STORES = [("Fayz savdo", "Samarqand, Siyob bozori, 7", "8000000.00")]

PRODUCTS = [
    ("UN-50", "Un oliy nav, 50 kg", ProductUnit.PIECE, "385000.00", "120"),
    ("SHK-50", "Shakar, 50 kg", ProductUnit.PIECE, "610000.00", "45"),
    ("YOG-5", "Kungaboqar yog'i, 5 l", ProductUnit.PIECE, "92000.00", "300"),
    ("GUR-25", "Guruch lazer, 25 kg", ProductUnit.PIECE, "410000.00", "60"),
    ("CHY-100", "Ko'k choy, 100 g (blok)", ProductUnit.BLOCK, "168000.00", "80"),
    ("MAK-5", "Makaron, 5 kg", ProductUnit.PIECE, "64000.00", "0"),
]


DEMO_STORE_NAME = "Baraka market"


async def assign_store_phone(session_factory, phone: str) -> None:  # type: ignore[no-untyped-def]
    """Put a real phone number on the demo store so its owner can claim it via the bot.
    Never detaches a store that a real Telegram account has already claimed."""
    normalized = normalize_phone(phone)
    if normalized is None:
        sys.exit(f"invalid phone number: {phone!r}")
    async with session_factory() as session, session.begin():
        store = await session.scalar(
            select(Store)
            .join(Wholesaler, Wholesaler.id == Store.wholesaler_id)
            .where(Wholesaler.tin == DEMO_TIN, Store.name == DEMO_STORE_NAME)
            .with_for_update()
        )
        if store is None:
            sys.exit("demo store not found")
        if store.telegram_user_id not in (None, STORE_TG):
            if normalize_phone(store.phone) == normalized:
                print(f"demo store already linked to Telegram id {store.telegram_user_id}")
            else:
                print(
                    "demo store is linked to another Telegram account "
                    f"({store.telegram_user_id}); phone left unchanged"
                )
            return
        store.phone = f"+{normalized}"
        store.telegram_user_id = None  # free it from the fake dev id
        print(f"demo store phone set to +{normalized}; share this contact with the bot")


async def main(store_phone: str | None = None) -> None:
    settings = get_settings()
    if settings.is_production:
        sys.exit("refusing to seed demo data in production")
    engine = build_engine(settings)
    session_factory = build_sessionmaker(engine)

    async with session_factory() as session, session.begin():
        exists = await session.scalar(select(Wholesaler.id).where(Wholesaler.tin == DEMO_TIN))
        if exists is None:
            w = Wholesaler(
                name="Samarqand Optom",
                legal_name='"Samarqand Optom Savdo" MChJ',
                tin=DEMO_TIN,
                address="Samarqand sh., Dagbitskaya ko'ch., 12",
                phone="+998 66 233 44 55",
                bank_name="Kapitalbank Samarqand filiali",
                bank_account="20208000400111222001",
                bank_mfo="01064",
                owner_telegram_user_id=ADMIN_TG,
            )
            session.add(w)
            await session.flush()
            store = Store(
                wholesaler_id=w.id,
                name="Baraka market",
                contact_name="Do'kon egasi",
                phone="+998 90 111 22 33",
                address="Samarqand, Registon ko'ch., 5",
                telegram_user_id=STORE_TG,
                credit_limit=Decimal("15000000.00"),
            )
            store2 = Store(
                wholesaler_id=w.id,
                name="Oila do'koni",
                address="Samarqand, Bog'ishamol, 21",
                credit_limit=Decimal("5000000.00"),
            )
            agent = Agent(
                wholesaler_id=w.id, display_name="Savdo agenti", telegram_user_id=AGENT_TG
            )
            extra = [
                Store(wholesaler_id=w.id, name=name, address=address, credit_limit=Decimal(limit))
                for name, address, limit in EXTRA_STORES
            ]
            session.add_all([store, store2, agent, *extra])
            for sku, name, unit, price, stock in PRODUCTS:
                session.add(
                    Product(
                        wholesaler_id=w.id,
                        sku=sku,
                        name=name,
                        unit=unit,
                        price=Decimal(price),
                        stock_quantity=Decimal(stock),
                    )
                )
            await session.flush()
            for s in (store, store2, *extra):
                session.add(
                    AgentStoreAssignment(wholesaler_id=w.id, agent_id=agent.id, store_id=s.id)
                )
            products = {
                p.sku: p.id
                for p in (
                    await session.scalars(select(Product).where(Product.wholesaler_id == w.id))
                )
            }
            ids = (w.id, store.id, store2.id, agent.id)

    if exists is not None:
        print("demo tenant already exists:", exists)
        if store_phone:
            await assign_store_phone(session_factory, store_phone)
        await engine.dispose()
        return

    wholesaler_id, store_id, store2_id, agent_id = ids
    async with session_factory() as session:
        orders = OrderService(session)
        ledger = LedgerService(session)
        for target, lines in (
            (
                store_id,
                [
                    OrderLine(products["UN-50"], Decimal("10")),
                    OrderLine(products["YOG-5"], Decimal("20")),
                ],
            ),
            (store_id, [OrderLine(products["SHK-50"], Decimal("4"))]),
            (store2_id, [OrderLine(products["GUR-25"], Decimal("3"))]),
        ):
            order = await orders.create_order(
                wholesaler_id=wholesaler_id, store_id=target, agent_id=agent_id, lines=lines
            )
            await orders.approve_order(wholesaler_id=wholesaler_id, order_id=order.id)
            await orders.deliver_order(wholesaler_id=wholesaler_id, order_id=order.id)
        await ledger.record_payment(
            wholesaler_id=wholesaler_id,
            store_id=store_id,
            amount="2500000.00",
            method=PaymentMethod.BANK_TRANSFER,
            description="Click orqali",
        )
        # A pending order waiting for approval.
        await orders.create_order(
            wholesaler_id=wholesaler_id,
            store_id=store_id,
            lines=[OrderLine(products["CHY-100"], Decimal("5"))],
        )
    print("demo tenant created:", wholesaler_id)
    if store_phone:
        await assign_store_phone(session_factory, store_phone)
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create the local demo tenant")
    parser.add_argument("--store-phone", help="your phone, to claim the demo store via the bot")
    args = parser.parse_args()
    asyncio.run(main(args.store_phone))
