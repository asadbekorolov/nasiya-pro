"""Role discovery for the Mini App: which cabinet to open after launch."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.api.deps import (
    Principal,
    Role,
    SessionDep,
    WholesalerHintDep,
    bind_principal,
    resolve_admin,
    resolve_agent,
    resolve_store,
)
from app.core.exceptions import PermissionDeniedError
from app.core.telegram_auth import TelegramUserDep
from app.schemas.order import MeResponse

router = APIRouter(prefix="/auth", tags=["auth"])

RESOLVERS = {Role.ADMIN: resolve_admin, Role.AGENT: resolve_agent, Role.STORE: resolve_store}


@router.get("/me", response_model=MeResponse)
async def whoami(
    request: Request,
    session: SessionDep,
    tg_user: TelegramUserDep,
    hint: WholesalerHintDep = None,
    role: Role | None = Query(
        default=None,
        description="Ask for one specific role (the Mini App opened at /admin, /agent "
        "or /store). Without it: wholesaler_admin > sales_agent > store_owner.",
    ),
) -> MeResponse:
    order = [role] if role is not None else [Role.ADMIN, Role.AGENT, Role.STORE]
    principal: Principal | None = None
    for candidate in order:
        principal = await RESOLVERS[candidate](session, tg_user, hint)
        if principal is not None:
            break
    if principal is None:
        raise PermissionDeniedError(
            "This Telegram account has no access"
            if role is None
            else f"This Telegram account is not a {role.value}"
        )
    bind_principal(request, principal)
    return MeResponse(
        role=principal.role.value,
        wholesaler_id=principal.wholesaler_id,
        agent_id=principal.agent_id,
        store_id=principal.store_id,
    )
