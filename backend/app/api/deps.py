"""FastAPI dependencies: DB session, the role-based security chain, services.

Security chain
--------------
1. `get_telegram_user`  — verifies the Telegram `initData` signature (no DB).
2. `resolve_*`          — maps the Telegram user id to exactly one role inside one
                          tenant (wholesaler) and checks it is active.
3. `require_*`          — endpoint guards. Each role has its own URL prefix
                          (`/store`, `/agent`, `/admin`), so a person who is, say,
                          both an agent and a store owner is never ambiguous.

A Telegram user can belong to several wholesalers (e.g. a shop buying from two
suppliers); the Mini App then sends `X-Wholesaler-Id` to choose the tenant.
The resolved `Principal` is also stored on `request.state.principal` and in a
context variable for logging/auditing.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.dispatcher import BotRuntime
from app.bot.notifications import BotNotifier
from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError, NotFoundError, PermissionDeniedError
from app.core.telegram_auth import TelegramUserDep, TelegramWebAppUser
from app.db.session import get_db_session, get_sessionmaker
from app.models import Agent, AgentStoreAssignment, Store, Wholesaler
from app.services.ledger_service import LedgerService
from app.services.notifications import (
    EventNotifier,
    LogOtpNotifier,
    NullEventNotifier,
    OtpNotifier,
)
from app.services.order_service import OrderService
from app.services.otp_service import OtpService
from app.services.pdf_service import ActSverkaPdfService

SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
WholesalerHintDep = Annotated[uuid.UUID | None, Header(alias="X-Wholesaler-Id")]


class Role(StrEnum):
    ADMIN = "wholesaler_admin"
    AGENT = "sales_agent"
    STORE = "store_owner"


@dataclass(frozen=True, slots=True)
class Principal:
    role: Role
    telegram_user_id: int
    wholesaler_id: uuid.UUID
    agent_id: uuid.UUID | None = None
    store_id: uuid.UUID | None = None

    @property
    def actor_id(self) -> uuid.UUID:
        """The id of the role entity (wholesaler, agent or store)."""
        return self.agent_id or self.store_id or self.wholesaler_id


current_principal: ContextVar[Principal | None] = ContextVar("current_principal", default=None)


def bind_principal(request: Request, principal: Principal) -> Principal:
    request.state.principal = principal
    current_principal.set(principal)
    return principal


def _pick_one(ids: list[uuid.UUID], what: str) -> uuid.UUID:
    if not ids:
        raise PermissionDeniedError(f"This Telegram account is not registered as {what}")
    if len(ids) > 1:
        raise AuthenticationError(
            "Account belongs to several wholesalers; send the X-Wholesaler-Id header"
        )
    return ids[0]


# --- resolvers (DB) --------------------------------------------------------------------


async def resolve_admin(
    session: AsyncSession, tg_user: TelegramWebAppUser, wholesaler_hint: uuid.UUID | None
) -> Principal | None:
    stmt = select(Wholesaler.id).where(
        Wholesaler.owner_telegram_user_id == tg_user.id, Wholesaler.is_active.is_(True)
    )
    if wholesaler_hint is not None:
        stmt = stmt.where(Wholesaler.id == wholesaler_hint)
    wholesaler_id = await session.scalar(stmt)
    if wholesaler_id is None:
        return None
    return Principal(Role.ADMIN, tg_user.id, wholesaler_id)


async def resolve_agent(
    session: AsyncSession, tg_user: TelegramWebAppUser, wholesaler_hint: uuid.UUID | None
) -> Principal | None:
    stmt = (
        select(Agent.id, Agent.wholesaler_id)
        .join(Wholesaler, Wholesaler.id == Agent.wholesaler_id)
        .where(
            Agent.telegram_user_id == tg_user.id,
            Agent.is_active.is_(True),
            Wholesaler.is_active.is_(True),
        )
    )
    if wholesaler_hint is not None:
        stmt = stmt.where(Agent.wholesaler_id == wholesaler_hint)
    rows = (await session.execute(stmt.limit(2))).all()
    if not rows:
        return None
    _pick_one([r.wholesaler_id for r in rows], "an agent")
    return Principal(Role.AGENT, tg_user.id, rows[0].wholesaler_id, agent_id=rows[0].id)


async def resolve_store(
    session: AsyncSession, tg_user: TelegramWebAppUser, wholesaler_hint: uuid.UUID | None
) -> Principal | None:
    stmt = (
        select(Store.id, Store.wholesaler_id)
        .join(Wholesaler, Wholesaler.id == Store.wholesaler_id)
        .where(
            Store.telegram_user_id == tg_user.id,
            Store.is_active.is_(True),
            Wholesaler.is_active.is_(True),
        )
    )
    if wholesaler_hint is not None:
        stmt = stmt.where(Store.wholesaler_id == wholesaler_hint)
    rows = (await session.execute(stmt.limit(2))).all()
    if not rows:
        return None
    _pick_one([r.wholesaler_id for r in rows], "a store")
    return Principal(Role.STORE, tg_user.id, rows[0].wholesaler_id, store_id=rows[0].id)


# --- guards ------------------------------------------------------------------------------


async def require_admin(
    request: Request, session: SessionDep, tg_user: TelegramUserDep, hint: WholesalerHintDep = None
) -> Principal:
    principal = await resolve_admin(session, tg_user, hint)
    if principal is None:
        raise PermissionDeniedError("Only the wholesaler admin can do this")
    return bind_principal(request, principal)


async def require_agent(
    request: Request, session: SessionDep, tg_user: TelegramUserDep, hint: WholesalerHintDep = None
) -> Principal:
    principal = await resolve_agent(session, tg_user, hint)
    if principal is None:
        raise PermissionDeniedError("Only a sales agent can do this")
    return bind_principal(request, principal)


async def require_store(
    request: Request, session: SessionDep, tg_user: TelegramUserDep, hint: WholesalerHintDep = None
) -> Principal:
    principal = await resolve_store(session, tg_user, hint)
    if principal is None:
        raise PermissionDeniedError("Only a store owner can do this")
    return bind_principal(request, principal)


async def get_any_principal(
    request: Request, session: SessionDep, tg_user: TelegramUserDep, hint: WholesalerHintDep = None
) -> Principal:
    """Role-agnostic endpoints (e.g. `/me`): admin > agent > store."""
    for resolver in (resolve_admin, resolve_agent, resolve_store):
        principal = await resolver(session, tg_user, hint)
        if principal is not None:
            return bind_principal(request, principal)
    raise PermissionDeniedError("This Telegram account has no access")


AdminDep = Annotated[Principal, Depends(require_admin)]
AgentDep = Annotated[Principal, Depends(require_agent)]
StoreDep = Annotated[Principal, Depends(require_store)]
PrincipalDep = Annotated[Principal, Depends(get_any_principal)]


async def ensure_agent_serves_store(
    session: AsyncSession, principal: Principal, store_id: uuid.UUID
) -> None:
    """Agents only act for stores assigned to them. Unassigned and foreign-tenant
    stores both answer 404 so store ids cannot be probed."""
    assigned = await session.scalar(
        select(AgentStoreAssignment.id).where(
            AgentStoreAssignment.agent_id == principal.agent_id,
            AgentStoreAssignment.store_id == store_id,
            AgentStoreAssignment.wholesaler_id == principal.wholesaler_id,
        )
    )
    if assigned is None:
        raise NotFoundError("Store not found", store_id=str(store_id))


# --- services --------------------------------------------------------------------------


def get_ledger_service(session: SessionDep, settings: SettingsDep) -> LedgerService:
    return LedgerService(session, settings=settings)


def get_order_service(session: SessionDep, settings: SettingsDep) -> OrderService:
    return OrderService(session, settings=settings)


def get_otp_service(session: SessionDep, settings: SettingsDep) -> OtpService:
    return OtpService(session, settings=settings)


def get_pdf_service(session: SessionDep, settings: SettingsDep) -> ActSverkaPdfService:
    return ActSverkaPdfService(session, settings=settings)


def get_bot_runtime(request: Request) -> BotRuntime | None:
    """Set by the FastAPI lifespan (`app.main`); absent when the lifespan did not run."""
    return getattr(request.app.state, "bot_runtime", None)


BotRuntimeDep = Annotated[BotRuntime | None, Depends(get_bot_runtime)]


def get_otp_notifier(settings: SettingsDep, runtime: BotRuntimeDep) -> OtpNotifier:
    if settings.otp_delivery == "log":
        return LogOtpNotifier()
    if runtime is not None:
        return runtime.notifier
    # No running bot: sending fails loudly (502) instead of silently losing the code.
    return BotNotifier(None, get_sessionmaker(), settings)


def get_event_notifier(runtime: BotRuntimeDep) -> EventNotifier:
    return runtime.notifier if runtime is not None else NullEventNotifier()


LedgerServiceDep = Annotated[LedgerService, Depends(get_ledger_service)]
OrderServiceDep = Annotated[OrderService, Depends(get_order_service)]
OtpServiceDep = Annotated[OtpService, Depends(get_otp_service)]
PdfServiceDep = Annotated[ActSverkaPdfService, Depends(get_pdf_service)]
OtpNotifierDep = Annotated[OtpNotifier, Depends(get_otp_notifier)]
EventNotifierDep = Annotated[EventNotifier, Depends(get_event_notifier)]
