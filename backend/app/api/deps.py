"""FastAPI dependencies: DB session, authenticated principal, services."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.core.security import validate_telegram_init_data
from app.db.session import get_db_session
from app.models import Agent, Wholesaler
from app.services.ledger_service import LedgerService
from app.services.notifications import OtpNotifier, TelegramOtpNotifier
from app.services.otp_service import OtpService

SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


class Role(StrEnum):
    OWNER = "owner"
    AGENT = "agent"


@dataclass(frozen=True, slots=True)
class Principal:
    role: Role
    telegram_user_id: int
    wholesaler_id: uuid.UUID
    agent_id: uuid.UUID | None = None


async def get_principal(
    session: SessionDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
    x_wholesaler_id: Annotated[uuid.UUID | None, Header()] = None,
) -> Principal:
    """Authenticate a Telegram Mini App request.

    Header: `Authorization: tma <Telegram.WebApp.initData>`. An agent who works for
    several wholesalers selects the tenant with `X-Wholesaler-Id`.
    """
    if not authorization or not authorization.lower().startswith("tma "):
        raise AuthenticationError("Expected 'Authorization: tma <initData>'")
    tg_user = validate_telegram_init_data(
        authorization[4:].strip(),
        bot_token=settings.telegram_bot_token.get_secret_value(),
        max_age_seconds=settings.telegram_init_data_ttl_seconds,
    )

    owner = await session.scalar(
        select(Wholesaler).where(
            Wholesaler.owner_telegram_user_id == tg_user.id, Wholesaler.is_active.is_(True)
        )
    )
    if owner is not None and (x_wholesaler_id is None or x_wholesaler_id == owner.id):
        return Principal(Role.OWNER, tg_user.id, owner.id)

    stmt = select(Agent).where(Agent.telegram_user_id == tg_user.id, Agent.is_active.is_(True))
    if x_wholesaler_id is not None:
        stmt = stmt.where(Agent.wholesaler_id == x_wholesaler_id)
    agents = (await session.scalars(stmt.limit(2))).all()
    if len(agents) == 1:
        return Principal(Role.AGENT, tg_user.id, agents[0].wholesaler_id, agents[0].id)
    if len(agents) > 1:
        raise AuthenticationError("Select a wholesaler with the X-Wholesaler-Id header")
    raise PermissionDeniedError("This Telegram account has no access")


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def require_owner(principal: PrincipalDep) -> Principal:
    if principal.role is not Role.OWNER:
        raise PermissionDeniedError("Only the wholesaler owner can do this")
    return principal


def require_agent(principal: PrincipalDep) -> Principal:
    if principal.role is not Role.AGENT or principal.agent_id is None:
        raise PermissionDeniedError("Only an agent can do this")
    return principal


OwnerDep = Annotated[Principal, Depends(require_owner)]
AgentDep = Annotated[Principal, Depends(require_agent)]


def get_ledger_service(session: SessionDep, settings: SettingsDep) -> LedgerService:
    return LedgerService(session, settings=settings)


def get_otp_service(session: SessionDep, settings: SettingsDep) -> OtpService:
    return OtpService(session, settings=settings)


def get_otp_notifier(settings: SettingsDep) -> OtpNotifier:
    return TelegramOtpNotifier(settings.telegram_bot_token.get_secret_value())


LedgerServiceDep = Annotated[LedgerService, Depends(get_ledger_service)]
OtpServiceDep = Annotated[OtpService, Depends(get_otp_service)]
OtpNotifierDep = Annotated[OtpNotifier, Depends(get_otp_notifier)]
