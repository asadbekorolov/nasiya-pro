"""Who is this Telegram user? — used by the bot (`/start`, contact sharing).

The Mini App API resolves exactly one role per request (`api/deps.py`); the bot instead
greets a person with *every* role they hold, e.g. someone who owns a shop and also
works as an agent for another wholesaler.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.uow import atomic
from app.models import Agent, Store, Wholesaler

logger = logging.getLogger(__name__)

UZ_COUNTRY_CODE = "998"


def normalize_phone(raw: str | None) -> str | None:
    """Digits only, in international form. Local Uzbek numbers (9 digits) get +998.

    "+998 (90) 111-22-33" -> "998901112233";  "90 111 22 33" -> "998901112233"
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 9:
        digits = UZ_COUNTRY_CODE + digits
    if not 10 <= len(digits) <= 15:
        return None
    return digits


@dataclass(frozen=True, slots=True)
class StoreRef:
    id: uuid.UUID
    name: str
    wholesaler_name: str


@dataclass(frozen=True, slots=True)
class TelegramIdentity:
    telegram_user_id: int
    admin_of: list[str] = field(default_factory=list)  # wholesaler names
    agent_for: list[str] = field(default_factory=list)  # wholesaler names
    stores: list[StoreRef] = field(default_factory=list)

    @property
    def is_registered(self) -> bool:
        return bool(self.admin_of or self.agent_for or self.stores)


@dataclass(frozen=True, slots=True)
class LinkResult:
    linked: list[StoreRef]
    already_linked: list[StoreRef]  # this user was linked before
    taken: int  # matching stores bound to a *different* Telegram account


class DirectoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def identify(self, telegram_user_id: int) -> TelegramIdentity:
        admin_of = (
            await self.session.scalars(
                select(Wholesaler.name).where(
                    Wholesaler.owner_telegram_user_id == telegram_user_id,
                    Wholesaler.is_active.is_(True),
                )
            )
        ).all()
        agent_for = (
            await self.session.scalars(
                select(Wholesaler.name)
                .join(Agent, Agent.wholesaler_id == Wholesaler.id)
                .where(
                    Agent.telegram_user_id == telegram_user_id,
                    Agent.is_active.is_(True),
                    Wholesaler.is_active.is_(True),
                )
                .order_by(Wholesaler.name)
            )
        ).all()
        stores = (
            await self.session.execute(
                select(Store.id, Store.name, Wholesaler.name)
                .join(Wholesaler, Wholesaler.id == Store.wholesaler_id)
                .where(
                    Store.telegram_user_id == telegram_user_id,
                    Store.is_active.is_(True),
                    Wholesaler.is_active.is_(True),
                )
                .order_by(Store.name)
            )
        ).all()
        return TelegramIdentity(
            telegram_user_id=telegram_user_id,
            admin_of=list(admin_of),
            agent_for=list(agent_for),
            stores=[StoreRef(sid, name, wname) for sid, name, wname in stores],
        )

    async def link_stores_by_phone(self, *, telegram_user_id: int, phone: str) -> LinkResult:
        """Bind every active store whose phone matches to this Telegram account.

        The caller MUST pass a phone Telegram verified for this very user (a contact the
        user shared about themselves). Stores already bound to another account are never
        re-bound — that requires the wholesaler.
        """
        normalized = normalize_phone(phone)
        if normalized is None:
            return LinkResult([], [], 0)

        # Cheap SQL pre-filter on the last 9 digits, exact comparison in Python
        # (phones are stored as typed: "+998 90 111-22-33", "901112233", ...).
        store_digits = func.regexp_replace(func.coalesce(Store.phone, ""), r"\D", "", "g")
        async with atomic(self.session):
            rows = (
                await self.session.execute(
                    select(
                        Store.id,
                        Store.name,
                        Store.phone,
                        Store.telegram_user_id,
                        Store.wholesaler_id,
                        Wholesaler.name,
                    )
                    .join(Wholesaler, Wholesaler.id == Store.wholesaler_id)
                    .where(
                        func.right(store_digits, 9) == normalized[-9:],
                        Store.is_active.is_(True),
                        Wholesaler.is_active.is_(True),
                    )
                    .order_by(Store.created_at)
                    .with_for_update(of=Store)
                )
            ).all()
            # A user may be the store of a wholesaler only once (unique per tenant).
            tenants_with_user = set(
                (
                    await self.session.scalars(
                        select(Store.wholesaler_id).where(
                            Store.telegram_user_id == telegram_user_id
                        )
                    )
                ).all()
            )

            linked: list[StoreRef] = []
            already: list[StoreRef] = []
            taken = 0
            for store_id, name, store_phone, current_tg, wholesaler_id, wholesaler_name in rows:
                if normalize_phone(store_phone) != normalized:
                    continue
                ref = StoreRef(store_id, name, wholesaler_name)
                if current_tg == telegram_user_id:
                    already.append(ref)
                elif current_tg is None and wholesaler_id not in tenants_with_user:
                    await self.session.execute(
                        update(Store)
                        .where(Store.id == store_id, Store.telegram_user_id.is_(None))
                        .values(telegram_user_id=telegram_user_id)
                    )
                    tenants_with_user.add(wholesaler_id)
                    linked.append(ref)
                else:
                    taken += 1
        if linked:
            logger.info(
                "telegram account linked to stores",
                extra={"telegram_user_id": telegram_user_id, "stores": [str(s.id) for s in linked]},
            )
        return LinkResult(linked, already, taken)
