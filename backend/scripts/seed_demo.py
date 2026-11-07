"""Create a demo tenant for local development of the Mini App.

    uv run python scripts/seed_demo.py

Telegram ids (use them with scripts/dev_init_data.py):
    1000001  wholesaler admin
    2000001  store owner ("Baraka market")
    3000001  sales agent
Re-running is a no-op when the demo wholesaler already exists. Never run in production.
"""

from __future__ import annotations

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
from app.services.ledger_service import LedgerService
from app.services.order_service import OrderLine, OrderService

ADMIN_TG, STORE_TG, AGENT_TG = 1000001, 2000001, 3000001

PRODUCTS = [
    ("UN-50", "Un oliy nav, 50 kg", ProductUnit.PIECE, "385000.00", "120"),
    ("SHK-50", "Shakar, 50 kg", ProductUnit.PIECE, "610000.00", "45"),
    ("YOG-5", "Kungaboqar yog'i, 5 l", ProductUnit.PIECE, "92000.00", "300"),
    ("GUR-25", "Guruch lazer, 25 kg", ProductUnit.PIECE, "410000.00", "60"),
    ("CHY-100", "Ko'k choy, 100 g (blok)", ProductUnit.BLOCK, "168000.00", "80"),
    ("MAK-5", "Makaron, 5 kg", ProductUnit.PIECE, "64000.00", "0"),
]


async def main() -> None:
    settings = get_settings()
    if settings.is_production:
        sys.exit("refusing to seed demo data in production")
    engine = build_engine(settings)
    session_factory = build_sessionmaker(engine)

    async with session_factory() as session, session.begin():
        exists = await session.scalar(
            select(Wholesaler.id).where(Wholesaler.owner_telegram_user_id == ADMIN_TG)
        )
        if exists is None:
            w = Wholesaler(
                name="Samarqand Optom",
                legal_name='"Samarqand Optom Savdo" MChJ',
                tin="305112233",
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
            session.add_all([store, store2, agent])
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
            for s in (store, store2):
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
    await engine.dispose()
    print("demo tenant created:", wholesaler_id)


if __name__ == "__main__":
    asyncio.run(main())
