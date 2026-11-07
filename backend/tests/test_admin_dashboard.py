"""Wholesaler dashboard: stores & credit limits, stock levels, short aging buckets."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import httpx

from tests.conftest import World, next_tg_id
from tests.helpers import auth


async def _order(client: httpx.AsyncClient, world: World, quantity: str) -> dict:
    r = await client.post(
        "/store/orders",
        json={
            "lines": [{"product_id": str(world.product.id), "quantity": quantity}],
            "idempotency_key": f"tg-{uuid.uuid4().hex}",
        },
        headers=auth(world.store.telegram_user_id),
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_stores_list_and_credit_limit(client: httpx.AsyncClient, world: World) -> None:
    admin = auth(world.wholesaler.owner_telegram_user_id)
    await _order(client, world, "5")

    r = await client.get("/admin/stores", headers=admin)
    assert r.status_code == 200, r.text
    (row,) = r.json()
    assert row["id"] == str(world.store.id)
    assert row["credit_limit"] == "1000000.00"
    assert row["available_credit"] == "1000000.00"
    assert row["pending_orders"] == 1
    assert row["is_linked"] is True

    path = f"/admin/stores/{world.store.id}/credit-limit"
    r = await client.patch(path, json={"credit_limit": "250.00"}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["credit_limit"] == "250.00"
    assert r.json()["available_credit"] == "250.00"

    # The new limit is enforced on approval: 30 x 100.00 > 250.00.
    order = await _order(client, world, "30")
    r = await client.post(f"/admin/orders/{order['id']}/approve", json={}, headers=admin)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "credit_limit_exceeded"

    # Validation: negative and over-precise values are refused.
    for bad in ("-1", "10.001", "abc"):
        r = await client.patch(path, json={"credit_limit": bad}, headers=admin)
        assert r.status_code == 422, bad


async def test_credit_limit_is_admin_only_and_tenant_scoped(
    client: httpx.AsyncClient, world: World
) -> None:
    path = f"/admin/stores/{world.store.id}/credit-limit"
    body = {"credit_limit": "1.00"}
    for who in (world.store.telegram_user_id, world.agent.telegram_user_id, next_tg_id()):
        r = await client.patch(path, json=body, headers=auth(who))
        assert r.status_code == 403, who

    admin = auth(world.wholesaler.owner_telegram_user_id)
    r = await client.patch(f"/admin/stores/{uuid.uuid4()}/credit-limit", json=body, headers=admin)
    assert r.status_code == 404


async def test_stock_report_tracks_reservations_and_low_stock(
    client: httpx.AsyncClient, world: World
) -> None:
    admin = auth(world.wholesaler.owner_telegram_user_id)
    order = await _order(client, world, "990")
    r = await client.post(f"/admin/orders/{order['id']}/approve", json={}, headers=admin)
    assert r.status_code == 200, r.text

    r = await client.get("/admin/stock", headers=admin)
    assert r.status_code == 200, r.text
    report = r.json()
    (item,) = report["items"]
    assert item["stock_quantity"] == "1000.000"
    assert item["reserved_quantity"] == "990.000"
    assert item["available_quantity"] == "10.000"
    assert item["is_low"] is True  # 10 <= default threshold 20
    assert report["low_count"] == 1 and report["out_of_stock_count"] == 0
    assert report["stock_value"] == "100000.00"

    r = await client.get("/admin/stock", params={"threshold": "5", "low_only": True}, headers=admin)
    assert r.json()["items"] == [] and r.json()["low_count"] == 0

    # Cancelling releases the reservation.
    r = await client.post(f"/admin/orders/{order['id']}/cancel", json={}, headers=admin)
    assert r.json()["status"] == "cancelled"
    r = await client.get("/admin/stock", headers=admin)
    assert r.json()["items"][0]["available_quantity"] == "1000.000"


async def test_short_aging_scheme(client: httpx.AsyncClient, world: World) -> None:
    admin = auth(world.wholesaler.owner_telegram_user_id)
    today = date.today()
    for amount, overdue_days in (("100.00", -3), ("200.00", 10), ("300.00", 20), ("400.00", 45)):
        r = await client.post(
            f"/admin/stores/{world.store.id}/adjustments",
            json={
                "amount": amount,
                "reason": "Opening balance",
                "due_date": (today - timedelta(days=overdue_days)).isoformat(),
                "idempotency_key": f"adj-{uuid.uuid4().hex}",
            },
            headers=admin,
        )
        assert r.status_code == 201, r.text

    r = await client.get(
        "/admin/aging-report", params={"scheme": "short", "as_of": today.isoformat()}, headers=admin
    )
    assert r.status_code == 200, r.text
    (row,) = r.json()["rows"]
    assert {k: str(v) for k, v in row["buckets"].items()} == {
        "current": "100.00",
        "1_15": "200.00",
        "16_30": "300.00",
        "30_plus": "400.00",
    }
    assert r.json()["grand_total"] == "1000.00"

    r = await client.get("/admin/aging-report", params={"scheme": "weekly"}, headers=admin)
    assert r.status_code == 422
