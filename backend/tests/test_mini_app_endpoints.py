"""Endpoints added for the Telegram Mini App frontend."""

from __future__ import annotations

import time

import httpx
import pytest

from app.core.exceptions import AuthenticationError
from app.core.signed_links import sign_link, verify_link
from app.models import PaymentMethod
from app.services.ledger_service import LedgerService
from tests.conftest import World
from tests.helpers import auth

SECRET = "s" * 40


def test_signed_link_roundtrip_and_tamper() -> None:
    token, exp = sign_link({"a": 1}, secret=SECRET, purpose="p", ttl_seconds=60)
    assert verify_link(token, secret=SECRET, purpose="p")["a"] == 1
    assert exp > time.time()
    with pytest.raises(AuthenticationError):
        verify_link(token, secret=SECRET, purpose="other")  # purpose-bound
    with pytest.raises(AuthenticationError):
        verify_link(token[:-2] + "xx", secret=SECRET, purpose="p")
    with pytest.raises(AuthenticationError, match="expired"):
        verify_link(token, secret=SECRET, purpose="p", now=time.time() + 120)
    with pytest.raises(AuthenticationError):
        verify_link("garbage", secret=SECRET, purpose="p")


async def test_store_transactions_paginate_newest_first(
    client: httpx.AsyncClient, session, world: World, make_order
) -> None:
    ledger = LedgerService(session)
    for total in ("100.00", "200.00", "300.00"):
        order = await make_order(total)
        await ledger.deliver_order(wholesaler_id=world.wholesaler.id, order_id=order.id)
    await ledger.record_payment(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        amount="50.00",
        method=PaymentMethod.CARD,
    )
    store = auth(world.store.telegram_user_id)

    r = await client.get("/store/transactions", params={"limit": 3}, headers=store)
    page = r.json()
    assert [t["sequence_no"] for t in page["items"]] == [4, 3, 2]
    assert page["items"][0]["type"] == "payment"
    assert page["items"][0]["payment_method"] == "card"
    assert page["next_before_sequence"] == 2

    r = await client.get(
        "/store/transactions", params={"limit": 3, "before_sequence": 2}, headers=store
    )
    page = r.json()
    assert [t["sequence_no"] for t in page["items"]] == [1]
    assert page["next_before_sequence"] is None


async def test_statement_link_downloads_without_auth(
    client: httpx.AsyncClient, world: World
) -> None:
    r = await client.post(
        "/store/statement-link",
        params={"date_from": "2026-01-01", "date_to": "2026-12-31"},
        headers=auth(world.store.telegram_user_id),
    )
    assert r.status_code == 200, r.text
    link = r.json()
    assert link["url"].startswith("/api/v1/files/statements/")
    assert link["filename"] == "akt-sverka_20260101-20261231.pdf"

    path = link["url"].removeprefix("/api/v1")
    r = await client.get(path)  # no Authorization header: Telegram fetches it itself
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF-")

    r = await client.get(path[:-3] + "abc")
    assert r.status_code == 401


async def test_agent_catalog_and_store_contact(client: httpx.AsyncClient, world: World) -> None:
    r = await client.get("/agent/catalog", headers=auth(world.agent.telegram_user_id))
    assert r.status_code == 200
    assert any(p["id"] == str(world.product.id) for p in r.json())
    r = await client.get("/agent/catalog", headers=auth(world.store.telegram_user_id))
    assert r.status_code == 403

    r = await client.get("/store/me", headers=auth(world.store.telegram_user_id))
    assert "contact_name" in r.json()


def test_log_otp_delivery_is_refused_outside_local() -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    base = {
        "telegram_bot_token": "1:x",
        "otp_secret": "s" * 40,
        "otp_delivery": "log",
    }
    assert Settings(environment="local", **base).otp_delivery == "log"  # type: ignore[arg-type]
    for env in ("staging", "production"):
        with pytest.raises(ValidationError, match="OTP_DELIVERY"):
            Settings(environment=env, **base)  # type: ignore[arg-type]
