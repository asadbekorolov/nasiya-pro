"""End-to-end flow over HTTP through all three role routers."""

from __future__ import annotations

import uuid

import httpx

from tests.conftest import RecordingNotifier, World
from tests.helpers import auth


async def test_full_b2b_flow(
    client: httpx.AsyncClient, world: World, notifier: RecordingNotifier
) -> None:
    store = auth(world.store.telegram_user_id)
    agent = auth(world.agent.telegram_user_id)
    admin = auth(world.wholesaler.owner_telegram_user_id)

    # 1. Store browses the catalog and orders 30 x 100.00
    r = await client.get("/store/catalog", params={"q": "Un"}, headers=store)
    assert r.status_code == 200
    product = next(p for p in r.json() if p["id"] == str(world.product.id))
    assert product["available_quantity"] == "1000.000"
    r = await client.post(
        "/store/orders",
        json={
            "lines": [{"product_id": str(world.product.id), "quantity": "30"}],
            "idempotency_key": f"tg-{uuid.uuid4().hex}",
        },
        headers=store,
    )
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["status"] == "pending" and order["total_amount"] == "3000.00"

    # 2. Wholesaler approves -> stock reserved
    r = await client.post(f"/admin/orders/{order['id']}/approve", json={}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    r = await client.get("/store/catalog", headers=store)
    assert (
        next(p for p in r.json() if p["id"] == str(world.product.id))["available_quantity"]
        == "970.000"
    )

    # The store can no longer withdraw an approved order.
    r = await client.post(f"/store/orders/{order['id']}/cancel", json={}, headers=store)
    assert r.status_code == 403

    # 3. Agent delivers -> debt billed
    r = await client.post(f"/agent/orders/{order['id']}/deliver", headers=agent)
    assert r.status_code == 200, r.text
    assert r.json()["order"]["status"] == "delivered"
    assert r.json()["charge"]["store_balance"] == "3000.00"

    r = await client.get("/store/me", headers=store)
    me = r.json()
    assert me["current_debt"] == "3000.00"
    assert me["available_credit"] == "997000.00"
    assert me["last_payment"] is None

    # 4. Agent collects 1200 cash, store confirms with the OTP from Telegram
    r = await client.post(
        "/agent/collect-cash/request-otp",
        json={"store_id": str(world.store.id), "amount": "1200.00"},
        headers=agent,
    )
    assert r.status_code == 201, r.text
    assert "code" not in r.json()
    verification_id = r.json()["id"]
    assert notifier.sent[-1]["chat_id"] == world.store.telegram_user_id
    code = notifier.sent[-1]["code"]

    r = await client.post(
        "/agent/collect-cash/verify-otp",
        json={"verification_id": verification_id, "code": code},
        headers=agent,
    )
    assert r.status_code == 201, r.text
    assert r.json()["store_balance"] == "1800.00"
    # retry after a flaky network returns the same payment
    again = await client.post(
        "/agent/collect-cash/verify-otp",
        json={"verification_id": verification_id, "code": code},
        headers=agent,
    )
    assert again.status_code == 409  # code already consumed

    r = await client.get("/store/me", headers=store)
    assert r.json()["current_debt"] == "1800.00"
    assert r.json()["last_payment"]["amount"] == "1200.00"
    assert r.json()["last_payment"]["method"] == "cash"

    # 5. Wholesaler views receivables and verifies the ledger
    r = await client.get("/admin/aging-report", headers=admin)
    assert r.status_code == 200
    assert r.json()["grand_total"] == "1800.00"
    assert r.json()["rows"][0]["buckets"]["current"] == "1800.00"

    r = await client.get(f"/admin/stores/{world.store.id}/ledger/verify", headers=admin)
    assert r.json() == {
        "store_id": str(world.store.id),
        "ok": True,
        "transactions_checked": 2,
        "broken_at_sequence": None,
        "reason": None,
    }

    r = await client.get("/agent/stores", headers=agent)
    assert r.json()[0]["current_debt"] == "1800.00"


async def test_admin_payment_and_assignment(client: httpx.AsyncClient, world: World) -> None:
    admin = auth(world.wholesaler.owner_telegram_user_id)
    r = await client.post(
        f"/admin/stores/{world.store.id}/payments",
        json={"amount": "500.00", "method": "bank_transfer", "idempotency_key": "admin-pay-001"},
        headers=admin,
    )
    assert r.status_code == 201
    assert r.json()["advance_amount"] == "500.00"  # nothing owed yet -> advance
    assert r.json()["store_balance"] == "-500.00"

    path = f"/admin/agents/{world.agent.id}/stores/{world.store.id}"
    assert (await client.delete(path, headers=admin)).json()["assigned"] is False
    agent = auth(world.agent.telegram_user_id)
    assert (await client.get("/agent/stores", headers=agent)).json() == []
    assert (await client.put(path, headers=admin)).json()["assigned"] is True
    assert (await client.put(path, headers=admin)).status_code == 200  # idempotent
    assert len((await client.get("/agent/stores", headers=agent)).json()) == 1


async def test_store_sees_only_its_own_orders(
    client: httpx.AsyncClient, world: World, make_order
) -> None:
    order = await make_order("10.00")
    store = auth(world.store.telegram_user_id)
    r = await client.get("/store/orders", headers=store)
    assert [o["id"] for o in r.json()] == [str(order.id)]
    r = await client.get(f"/store/orders/{uuid.uuid4()}", headers=store)
    assert r.status_code == 404


async def test_validation_errors(client: httpx.AsyncClient, world: World) -> None:
    agent = auth(world.agent.telegram_user_id)
    r = await client.post(
        "/agent/collect-cash/verify-otp",
        json={"verification_id": str(uuid.uuid4()), "code": "abcd"},
        headers=agent,
    )
    assert r.status_code == 422
    r = await client.post(
        "/store/orders",
        json={"lines": [{"product_id": str(world.product.id), "quantity": "-1"}]},
        headers=auth(world.store.telegram_user_id),
    )
    assert r.status_code == 422
