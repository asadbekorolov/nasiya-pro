"""Role discovery for the Mini App: which screen set to open after launch."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import PrincipalDep
from app.schemas.order import MeResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=MeResponse)
async def whoami(principal: PrincipalDep) -> MeResponse:
    return MeResponse(
        role=principal.role.value,
        wholesaler_id=principal.wholesaler_id,
        agent_id=principal.agent_id,
        store_id=principal.store_id,
    )
