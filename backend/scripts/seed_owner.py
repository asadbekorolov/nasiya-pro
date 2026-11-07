"""Make a real Telegram account the owner (Wholesaler Admin) of the primary wholesaler.

    uv run python scripts/seed_owner.py --telegram-id 123456789 --name "Ism Familiya"

* creates the demo tenant first if it does not exist yet (scripts/seed_demo.py)
* makes sure the demo stores ("Baraka market", "Oila do'koni", "Fayz savdo"), catalog
  and agents belong to that wholesaler, every store assigned to every demo agent
* sets wholesalers.owner_telegram_user_id (+ owner_name for the bot greeting)
Idempotent. The id/name are personal data: keep them in backend/.env
(FOUNDER_TELEGRAM_ID / FOUNDER_NAME), not in the repository.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import seed_demo
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import get_settings
from app.core.exceptions import DomainError
from app.core.uuid7 import uuid7
from app.db.session import build_engine, build_sessionmaker
from app.models import Agent, AgentStoreAssignment, Product, Store, Wholesaler
from app.services.owner_service import assign_owner


async def main(telegram_id: int, name: str | None) -> None:
    settings = get_settings()
    if settings.is_production:
        sys.exit("refusing to modify demo data in production")

    await seed_demo.main()  # no-op when the demo tenant exists
    engine = build_engine(settings)
    session_factory = build_sessionmaker(engine)
    try:
        async with session_factory() as session:
            wholesaler = await session.scalar(
                select(Wholesaler).where(Wholesaler.tin == seed_demo.DEMO_TIN)
            )
            if wholesaler is None:
                sys.exit("demo wholesaler not found")
            wid = wholesaler.id
            await session.rollback()

            async with session.begin():
                existing = set(
                    (
                        await session.scalars(select(Store.name).where(Store.wholesaler_id == wid))
                    ).all()
                )
                for store_name, address, limit in seed_demo.EXTRA_STORES:
                    if store_name not in existing:
                        session.add(
                            Store(
                                wholesaler_id=wid,
                                name=store_name,
                                address=address,
                                credit_limit=Decimal(limit),
                            )
                        )
                await session.flush()
                store_ids = (
                    await session.scalars(select(Store.id).where(Store.wholesaler_id == wid))
                ).all()
                agent_ids = (
                    await session.scalars(select(Agent.id).where(Agent.wholesaler_id == wid))
                ).all()
                for agent_id in agent_ids:
                    for store_id in store_ids:
                        await session.execute(
                            insert(AgentStoreAssignment)
                            .values(
                                id=uuid7(), wholesaler_id=wid, agent_id=agent_id, store_id=store_id
                            )
                            .on_conflict_do_nothing(index_elements=["agent_id", "store_id"])
                        )

            try:
                owner = await assign_owner(
                    session, wholesaler_id=wid, telegram_user_id=telegram_id, owner_name=name
                )
            except DomainError as exc:
                sys.exit(f"cannot assign owner: {exc.message} {exc.details}")

            stores = (
                await session.scalars(
                    select(Store.name).where(Store.wholesaler_id == wid).order_by(Store.name)
                )
            ).all()
            products = await session.scalar(
                select(Product.id).where(Product.wholesaler_id == wid).limit(1)
            )
            print(f"owner of {owner.name!r}: telegram id {telegram_id} ({owner.owner_name or '-'})")
            catalog = "ok" if products else "empty"
            print(f"stores: {', '.join(stores)}; agents: {len(agent_ids)}; catalog: {catalog}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Assign the primary wholesaler's owner")
    parser.add_argument("--telegram-id", type=int, required=True)
    parser.add_argument("--name", help="owner's full name, e.g. for the bot greeting")
    args = parser.parse_args()
    asyncio.run(main(args.telegram_id, args.name))
