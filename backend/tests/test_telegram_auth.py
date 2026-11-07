from __future__ import annotations

import time
from decimal import Decimal
from urllib.parse import parse_qsl, urlencode

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError
from app.core.telegram_auth import (
    compute_init_data_hash,
    parse_authorization_header,
    validate_telegram_init_data,
)
from app.models import Agent, Store, Wholesaler
from tests.conftest import World, next_tg_id
from tests.helpers import BOT_TOKEN, auth, make_init_data

# --- signature validation (unit) ---------------------------------------------------------


def _validate(init_data: str, **kw: object) -> object:
    return validate_telegram_init_data(
        init_data,
        bot_token=BOT_TOKEN,
        max_age_seconds=3600,
        **kw,  # type: ignore[arg-type]
    )


def test_valid_init_data_returns_user() -> None:
    user = _validate(make_init_data(42))
    assert user.id == 42  # type: ignore[attr-defined]
    assert user.language_code == "uz"  # type: ignore[attr-defined]


def test_hash_matches_independent_implementation() -> None:
    fields = dict(parse_qsl(make_init_data(7)))
    assert compute_init_data_hash(fields, BOT_TOKEN) == fields["hash"]


def test_wrong_bot_token_is_rejected() -> None:
    with pytest.raises(AuthenticationError, match="signature"):
        _validate(make_init_data(42, token="999:OTHER"))


def test_tampered_user_is_rejected() -> None:
    fields = dict(parse_qsl(make_init_data(42)))
    fields["user"] = fields["user"].replace("42", "43")
    with pytest.raises(AuthenticationError, match="signature"):
        _validate(urlencode(fields))


def test_unsigned_extra_field_is_rejected() -> None:
    with pytest.raises(AuthenticationError, match="signature"):
        _validate(make_init_data(42) + "&start_param=admin")


def test_duplicate_fields_are_rejected() -> None:
    data = make_init_data(42)
    with pytest.raises(AuthenticationError, match="duplicate"):
        _validate(data + "&auth_date=1")


def test_signed_extra_fields_are_accepted() -> None:
    user = _validate(make_init_data(42, extra={"start_param": "ref_1", "chat_type": "private"}))
    assert user.id == 42  # type: ignore[attr-defined]


def test_expired_and_future_init_data() -> None:
    with pytest.raises(AuthenticationError, match="expired"):
        _validate(make_init_data(42, auth_date=int(time.time()) - 7200))
    with pytest.raises(AuthenticationError, match="future"):
        _validate(make_init_data(42, auth_date=int(time.time()) + 600))


@pytest.mark.parametrize("raw", ["", "hash=abc", "user=%7B%7D&auth_date=1"])
def test_garbage_is_rejected(raw: str) -> None:
    with pytest.raises(AuthenticationError):
        _validate(raw)


@pytest.mark.parametrize("header", [None, "", "Bearer abc", "tma", "tma   "])
def test_authorization_header_scheme(header: str | None) -> None:
    with pytest.raises(AuthenticationError):
        parse_authorization_header(header)
    assert parse_authorization_header("TMA abc") == "abc"


# --- role resolution (HTTP security chain) ---------------------------------------------


async def test_each_role_is_resolved(client: httpx.AsyncClient, world: World) -> None:
    cases = {
        world.wholesaler.owner_telegram_user_id: ("wholesaler_admin", None, None),
        world.agent.telegram_user_id: ("sales_agent", str(world.agent.id), None),
        world.store.telegram_user_id: ("store_owner", None, str(world.store.id)),
    }
    for tg_id, (role, agent_id, store_id) in cases.items():
        r = await client.get("/auth/me", headers=auth(tg_id))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["role"] == role
        assert body["wholesaler_id"] == str(world.wholesaler.id)
        assert body["agent_id"] == agent_id
        assert body["store_id"] == store_id


async def test_role_guards(client: httpx.AsyncClient, world: World) -> None:
    store_h = auth(world.store.telegram_user_id)
    agent_h = auth(world.agent.telegram_user_id)
    admin_h = auth(world.wholesaler.owner_telegram_user_id)

    assert (await client.get("/store/me", headers=store_h)).status_code == 200
    assert (await client.get("/store/me", headers=agent_h)).status_code == 403
    assert (await client.get("/agent/stores", headers=store_h)).status_code == 403
    assert (await client.get("/agent/stores", headers=agent_h)).status_code == 200
    r = await client.get("/admin/aging-report", headers=agent_h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "permission_denied"
    assert (await client.get("/admin/aging-report", headers=admin_h)).status_code == 200


async def test_missing_or_forged_auth(client: httpx.AsyncClient, world: World) -> None:
    r = await client.get("/store/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "authentication_failed"

    forged = make_init_data(world.store.telegram_user_id, token="1:FORGED")
    r = await client.get("/store/me", headers={"Authorization": f"tma {forged}"})
    assert r.status_code == 401

    r = await client.get("/store/me", headers=auth(next_tg_id()))
    assert r.status_code == 403  # valid Telegram user, unknown to the system


async def test_bots_cannot_sign_in(client: httpx.AsyncClient, world: World) -> None:
    data = make_init_data(world.store.telegram_user_id, is_bot=True)
    r = await client.get("/store/me", headers={"Authorization": f"tma {data}"})
    assert r.status_code == 401


async def test_store_of_two_wholesalers_must_pick_tenant(
    client: httpx.AsyncClient, session: AsyncSession, world: World
) -> None:
    async with session.begin():
        other = Wholesaler(name="Boshqa Optom", owner_telegram_user_id=next_tg_id())
        session.add(other)
        await session.flush()
        session.add(
            Store(
                wholesaler_id=other.id,
                name="Do'kon A (2)",
                telegram_user_id=world.store.telegram_user_id,
            )
        )
        other_id = other.id

    tg = world.store.telegram_user_id
    r = await client.get("/store/me", headers=auth(tg))
    assert r.status_code == 401
    assert "X-Wholesaler-Id" in r.json()["error"]["message"]

    r = await client.get("/store/me", headers=auth(tg, world.wholesaler.id))
    assert r.status_code == 200 and r.json()["wholesaler_id"] == str(world.wholesaler.id)
    r = await client.get("/store/me", headers=auth(tg, other_id))
    assert r.status_code == 200 and r.json()["wholesaler_id"] == str(other_id)
    assert r.json()["current_debt"] == "0.00"


async def test_deactivated_agent_loses_access(
    client: httpx.AsyncClient, session: AsyncSession, world: World
) -> None:
    async with session.begin():
        agent = await session.get(Agent, world.agent.id)
        assert agent is not None
        agent.is_active = False
    r = await client.get("/agent/stores", headers=auth(world.agent.telegram_user_id))
    assert r.status_code == 403


async def test_agent_cannot_touch_unassigned_store(
    client: httpx.AsyncClient, session: AsyncSession, world: World
) -> None:
    async with session.begin():
        foreign = Store(
            wholesaler_id=world.wholesaler.id,
            name="Biriktirilmagan do'kon",
            telegram_user_id=next_tg_id(),
            credit_limit=Decimal("1000.00"),
        )
        session.add(foreign)
        await session.flush()
        foreign_id = foreign.id

    agent_h = auth(world.agent.telegram_user_id)
    r = await client.post(
        "/agent/collect-cash/request-otp",
        json={"store_id": str(foreign_id), "amount": "10.00"},
        headers=agent_h,
    )
    assert r.status_code == 404
    r = await client.get("/agent/stores", headers=agent_h)
    assert [s["id"] for s in r.json()] == [str(world.store.id)]
