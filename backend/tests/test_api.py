from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_otp_notifier
from app.db.session import get_db_session
from app.main import create_app
from tests.conftest import World
from tests.test_core import make_init_data


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_payment_otp(self, **kwargs) -> int:  # type: ignore[no-untyped-def]
        self.sent.append(kwargs)
        return 777


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


@pytest.fixture
async def client(
    sessionmaker: async_sessionmaker[AsyncSession], notifier: RecordingNotifier
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def _session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as s:
            yield s

    app.dependency_overrides[get_db_session] = _session
    app.dependency_overrides[get_otp_notifier] = lambda: notifier
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
        yield c


def auth(tg_id: int) -> dict[str, str]:
    return {"Authorization": f"tma {make_init_data(tg_id)}"}


async def test_requires_telegram_auth(client: httpx.AsyncClient, world: World) -> None:
    r = await client.get(f"/stores/{world.store.id}/balance")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "authentication_failed"


async def test_deliver_then_agent_collects_cash_with_otp(
    client: httpx.AsyncClient, world: World, make_order, notifier: RecordingNotifier
) -> None:
    owner = auth(world.wholesaler.owner_telegram_user_id)
    agent = auth(world.agent.telegram_user_id)
    order = await make_order("250000.00")

    r = await client.post(f"/orders/{order.id}/deliver", json={}, headers=agent)
    assert r.status_code == 200, r.text
    assert r.json()["store_balance"] == "250000.00"

    # Agent cannot post a payment directly.
    r = await client.post(
        f"/stores/{world.store.id}/payments",
        json={"amount": "100000.00", "method": "cash"},
        headers=agent,
    )
    assert r.status_code == 403

    r = await client.post(
        "/payments/otp",
        json={"store_id": str(world.store.id), "amount": "100000.00"},
        headers=agent,
    )
    assert r.status_code == 201, r.text
    otp_id = r.json()["id"]
    assert "code" not in r.json() and "code_hash" not in r.json()
    assert notifier.sent[0]["chat_id"] == world.store.telegram_user_id
    code = notifier.sent[0]["code"]

    r = await client.post(f"/payments/otp/{otp_id}/confirm", json={"code": code}, headers=agent)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["store_balance"] == "150000.00"
    assert body["transaction"]["type"] == "payment"

    r = await client.get(f"/stores/{world.store.id}/balance", headers=owner)
    assert Decimal(r.json()["current_debt"]) == Decimal("150000.00")

    r = await client.get(f"/stores/{world.store.id}/ledger/verify", headers=owner)
    assert r.json()["ok"] is True

    r = await client.get("/reports/aging", headers=owner)
    assert r.status_code == 200
    assert r.json()["grand_total"] == "150000.00"


async def test_owner_payment_and_wrong_otp_error_shape(
    client: httpx.AsyncClient, world: World, make_order
) -> None:
    owner = auth(world.wholesaler.owner_telegram_user_id)
    agent = auth(world.agent.telegram_user_id)
    order = await make_order("1000.00")
    await client.post(f"/orders/{order.id}/deliver", json={}, headers=owner)

    r = await client.post(
        f"/stores/{world.store.id}/payments",
        json={"amount": "1000.00", "method": "bank_transfer", "idempotency_key": "owner-pay-01"},
        headers=owner,
    )
    assert r.status_code == 201
    assert r.json()["allocations"][0]["closed"] is True

    r = await client.post(
        "/payments/otp",
        json={"store_id": str(world.store.id), "amount": "5.00"},
        headers=agent,
    )
    otp_id = r.json()["id"]
    r = await client.post(f"/payments/otp/{otp_id}/confirm", json={"code": "abcd"}, headers=agent)
    assert r.status_code == 422  # schema validation
