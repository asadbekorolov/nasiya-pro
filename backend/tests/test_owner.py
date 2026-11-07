from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.models import Wholesaler
from app.services.owner_service import OwnerConflictError, assign_owner, first_name
from tests.conftest import World, end_read, next_tg_id
from tests.helpers import auth


@pytest.mark.parametrize(
    ("full", "expected"),
    [("Asadbek O'rolov", "Asadbek"), ("  Ali  ", "Ali"), ("", None), (None, None)],
)
def test_first_name(full: str | None, expected: str | None) -> None:
    assert first_name(full) == expected


async def test_assign_owner_moves_admin_access(
    client: httpx.AsyncClient, session, world: World
) -> None:
    new_owner = next_tg_id()
    await assign_owner(
        session,
        wholesaler_id=world.wholesaler.id,
        telegram_user_id=new_owner,
        owner_name=" Ali Vali ",
    )
    row = (
        await session.execute(
            select(Wholesaler.owner_telegram_user_id, Wholesaler.owner_name).where(
                Wholesaler.id == world.wholesaler.id
            )
        )
    ).one()
    await end_read(session)
    assert row == (new_owner, "Ali Vali")

    r = await client.get("/auth/me", headers=auth(new_owner))
    assert r.json()["role"] == "wholesaler_admin"
    # the previous owner lost admin access
    r = await client.get(
        "/admin/aging-report", headers=auth(world.wholesaler.owner_telegram_user_id)
    )
    assert r.status_code == 403


async def test_one_account_cannot_own_two_wholesalers(session, world: World) -> None:
    async with session.begin():
        other = Wholesaler(name="Boshqa", owner_telegram_user_id=next_tg_id())
        session.add(other)
        await session.flush()
        other_id = other.id
    with pytest.raises(OwnerConflictError):
        await assign_owner(
            session,
            wholesaler_id=other_id,
            telegram_user_id=world.wholesaler.owner_telegram_user_id,
        )


async def test_auth_me_role_hint(client: httpx.AsyncClient, world: World) -> None:
    admin = auth(world.wholesaler.owner_telegram_user_id)
    r = await client.get("/auth/me", params={"role": "wholesaler_admin"}, headers=admin)
    assert r.status_code == 200 and r.json()["role"] == "wholesaler_admin"
    # a role the person does not have is refused, never granted
    r = await client.get("/auth/me", params={"role": "store_owner"}, headers=admin)
    assert r.status_code == 403
    r = await client.get(
        "/auth/me", params={"role": "sales_agent"}, headers=auth(world.agent.telegram_user_id)
    )
    assert r.json()["agent_id"] == str(world.agent.id)
    r = await client.get("/auth/me", params={"role": "superuser"}, headers=admin)
    assert r.status_code == 422
