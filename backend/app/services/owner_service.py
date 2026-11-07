"""Wholesaler ownership: which Telegram account administers a tenant."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DomainError, NotFoundError
from app.db.uow import atomic
from app.models import Wholesaler

logger = logging.getLogger(__name__)


class OwnerConflictError(DomainError):
    code = "owner_conflict"
    status_code = 409


def first_name(full_name: str | None) -> str | None:
    """ "Asadbek O'rolov" -> "Asadbek" (used to greet the owner)."""
    if not full_name or not full_name.strip():
        return None
    return full_name.strip().split()[0]


async def assign_owner(
    session: AsyncSession,
    *,
    wholesaler_id: uuid.UUID,
    telegram_user_id: int,
    owner_name: str | None = None,
) -> Wholesaler:
    """Make `telegram_user_id` the owner (Wholesaler Admin) of a wholesaler.

    A Telegram account owns at most one wholesaler (unique constraint), so this refuses
    to silently take ownership away from another tenant.
    """
    if telegram_user_id <= 0:
        raise OwnerConflictError("Invalid Telegram user id")
    async with atomic(session):
        wholesaler = await session.scalar(
            select(Wholesaler)
            .where(Wholesaler.id == wholesaler_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if wholesaler is None:
            raise NotFoundError("Wholesaler not found", wholesaler_id=str(wholesaler_id))
        other = await session.scalar(
            select(Wholesaler.name).where(
                Wholesaler.owner_telegram_user_id == telegram_user_id,
                Wholesaler.id != wholesaler_id,
            )
        )
        if other is not None:
            raise OwnerConflictError(
                "This Telegram account already owns another wholesaler", wholesaler=other
            )
        previous = wholesaler.owner_telegram_user_id
        wholesaler.owner_telegram_user_id = telegram_user_id
        if owner_name is not None:
            wholesaler.owner_name = owner_name.strip() or None
        await session.flush()
    if previous != telegram_user_id:
        logger.info(
            "wholesaler owner changed",
            extra={"wholesaler_id": str(wholesaler_id), "previous": previous},
        )
    return wholesaler
