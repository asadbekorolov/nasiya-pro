"""Row locks shared by every service that changes money or stock.

GLOBAL LOCK ORDER (never acquire in a different order, or two services can deadlock):

    stores  ->  orders  ->  products (ascending id)  ->  payment OTP  ->  debts

All lock queries use `populate_existing` so the identity map cannot return values
read before the lock was taken.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models import Order, Product, Store


async def lock_store(session: AsyncSession, wholesaler_id: uuid.UUID, store_id: uuid.UUID) -> Store:
    store = await session.scalar(
        select(Store)
        .where(Store.id == store_id, Store.wholesaler_id == wholesaler_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if store is None:
        raise NotFoundError("Store not found", store_id=str(store_id))
    return store


async def lock_order(session: AsyncSession, wholesaler_id: uuid.UUID, order_id: uuid.UUID) -> Order:
    order = await session.scalar(
        select(Order)
        .where(Order.id == order_id, Order.wholesaler_id == wholesaler_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if order is None:
        raise NotFoundError("Order not found", order_id=str(order_id))
    return order


async def lock_products(
    session: AsyncSession, wholesaler_id: uuid.UUID, product_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, Product]:
    ids = sorted(set(product_ids))
    if not ids:
        return {}
    rows = (
        await session.scalars(
            select(Product)
            .where(Product.id.in_(ids), Product.wholesaler_id == wholesaler_id)
            .order_by(Product.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    return {p.id: p for p in rows}
