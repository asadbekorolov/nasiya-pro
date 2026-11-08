"""Admin exports: Excel reports, the monthly Akt-sverka ZIP, and signed download links."""

from __future__ import annotations

import csv
import io
import uuid
import zipfile
from datetime import date, timedelta
from decimal import Decimal

import httpx
from openpyxl import Workbook, load_workbook
from pypdf import PdfReader
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Store
from app.services.export_service import UTF8_BOM, ExportService, parse_month
from app.services.ledger_service import LedgerService
from app.services.pdf_service import ActSverkaPdfService
from tests.conftest import World, end_read
from tests.helpers import auth

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
STORE_HEADER = [
    "Do'kon nomi",
    "Mas'ul shaxs",
    "Telefon raqam",
    "Manzil",
    "Kredit limiti",
    "Boshlang'ich qarz (so'm)",
    "Qarz muddati",
]


def admin_headers(world: World) -> dict[str, str]:
    return auth(world.wholesaler.owner_telegram_user_id)


def xlsx(header: list[str], *rows: list[object]) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(header)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def add_stores(client: httpx.AsyncClient, world: World) -> None:
    """Second and third store with opening debts (one of them overdue)."""
    overdue = (date.today() - timedelta(days=10)).strftime("%d.%m.%Y")
    r = await client.post(
        "/admin/import/stores",
        files={
            "file": (
                "s.xlsx",
                xlsx(
                    STORE_HEADER,
                    [
                        "Baraka market",
                        "Akmal",
                        "+998901234567",
                        "Samarqand",
                        5_000_000,
                        1_200_000,
                        overdue,
                    ],
                    ["Oila do'koni", None, None, None, 1_000_000, 0, None],
                ),
                XLSX,
            )
        },
        headers=admin_headers(world),
    )
    assert r.status_code == 200 and r.json()["created"] == 2, r.text


def rows_of(content: bytes) -> list[list[object]]:
    ws = load_workbook(io.BytesIO(content)).worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)]


# --- month parsing -------------------------------------------------------------------------


def test_parse_month() -> None:
    today = date(2026, 11, 8)
    assert parse_month("2026-10", today) == (date(2026, 10, 1), date(2026, 10, 31))
    assert parse_month("2024-02", today) == (date(2024, 2, 1), date(2024, 2, 29))
    assert parse_month("2026-11", today) == (date(2026, 11, 1), today)  # current month
    for bad in ("2026-12", "2026-13", "26-10", "2026/10", ""):
        try:
            parse_month(bad, today)
        except Exception as exc:
            assert getattr(exc, "code", "") == "invalid_amount", bad
        else:
            raise AssertionError(bad)


# --- excel ---------------------------------------------------------------------------------


async def test_stores_export(client: httpx.AsyncClient, world: World) -> None:
    admin = admin_headers(world)
    await add_stores(client, world)
    r = await client.post(
        f"/admin/stores/{world.store.id}/payments",
        json={"amount": "0.01", "method": "cash", "idempotency_key": f"p-{uuid.uuid4().hex}"},
        headers=admin,
    )
    assert r.status_code == 201, r.text

    r = await client.get("/admin/export/stores.xlsx", headers=admin)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == XLSX
    assert "dokonlar-qarzlar_" in r.headers["content-disposition"]
    rows = rows_of(r.content)
    assert rows[0] == [
        "Do'kon nomi",
        "Mas'ul shaxs (egasi)",
        "Telefon",
        "Manzil",
        "Kredit limiti",
        "Joriy qarz",
        "Muddati o'tgan qarz",
        "Bo'sh limit",
        "Oxirgi to'lov sanasi",
        "Holat",
    ]
    by_name = {
        row[0]: row for row in rows[1:] if row and row[0] and not str(row[0]).startswith("Jami")
    }
    baraka = by_name["Baraka market"]
    assert baraka[1:4] == ["Akmal", "+998901234567", "Samarqand"]
    assert Decimal(str(baraka[4])) == Decimal("5000000")
    assert Decimal(str(baraka[5])) == Decimal("1200000")
    assert Decimal(str(baraka[6])) == Decimal("1200000")  # overdue
    assert Decimal(str(baraka[7])) == Decimal("3800000")
    assert baraka[8] is None and baraka[9] == "faol"
    # The store that paid (advance of 0.01) shows the payment date.
    paid = by_name["Do'kon A"]
    assert Decimal(str(paid[5])) == Decimal("-0.01")
    assert paid[8] is not None and paid[8].date() >= date.today() - timedelta(days=1)
    total = rows[-1]
    assert total[0] == "Jami: 3 ta do'kon"
    assert Decimal(str(total[5])) == Decimal("1199999.99")


async def test_stock_export(client: httpx.AsyncClient, world: World) -> None:
    admin = admin_headers(world)
    r = await client.post(
        "/store/orders",
        json={
            "lines": [{"product_id": str(world.product.id), "quantity": "120.5"}],
            "idempotency_key": f"tg-{uuid.uuid4().hex}",
        },
        headers=auth(world.store.telegram_user_id),
    )
    order = r.json()
    assert (
        await client.post(f"/admin/orders/{order['id']}/approve", json={}, headers=admin)
    ).status_code == 200

    r = await client.get("/admin/export/stock.xlsx", headers=admin)
    assert r.status_code == 200, r.text
    rows = rows_of(r.content)
    assert rows[0] == [
        "Tovar nomi",
        "SKU / Artikul",
        "Birlik",
        "Optom narxi",
        "Omborda bor",
        "Band qilingan",
        "Erkin qoldiq",
        "Qoldiq qiymati",
    ]
    item = rows[1]
    assert item[0] == "Un 50kg" and item[1] == world.product.sku and item[2] == "dona"
    assert [Decimal(str(v)) for v in item[3:8]] == [
        Decimal("100"),
        Decimal("1000"),
        Decimal("120.5"),
        Decimal("879.5"),
        Decimal("100000"),
    ]
    assert rows[-1][0] == "Jami: 1 ta tovar"


# --- statements zip ------------------------------------------------------------------------


async def test_statements_zip(
    client: httpx.AsyncClient, world: World, session: AsyncSession
) -> None:
    admin = admin_headers(world)
    await add_stores(client, world)
    async with session.begin():
        await session.execute(
            update(Store)
            .where(Store.wholesaler_id == world.wholesaler.id, Store.name == "Oila do'koni")
            .values(is_active=False)
        )

    month = date.today().strftime("%Y-%m")
    r = await client.get("/admin/export/statements.zip", params={"month": month}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert f"statements_{month.replace('-', '_')}.zip" in r.headers["content-disposition"]
    assert r.headers["x-document-count"] == "2"
    assert int(r.headers["content-length"]) == len(r.content)

    archive = zipfile.ZipFile(io.BytesIO(r.content))
    assert archive.testzip() is None
    names = sorted(archive.namelist())
    assert names == sorted([f"Baraka-market_{month}.pdf", f"Do-kon-A_{month}.pdf", "royxat.csv"])

    baraka = PdfReader(io.BytesIO(archive.read(f"Baraka-market_{month}.pdf")))
    text = "".join(page.extract_text() for page in baraka.pages)
    assert "Baraka market" in text
    assert "1 200 000" in text or "1200000" in text

    index = archive.read("royxat.csv").decode("utf-8")
    assert index.startswith(UTF8_BOM)
    rows = list(csv.reader(io.StringIO(index.lstrip(UTF8_BOM))))
    assert rows[0] == ["Do'kon", "Fayl", "Hujjat kodi", "Davr"]
    assert {row[0] for row in rows[1:]} == {"Baraka market", "Do'kon A"}  # inactive one skipped

    # Validation
    for bad in ("2026-13", "oktyabr", (date.today() + timedelta(days=40)).strftime("%Y-%m")):
        r = await client.get("/admin/export/statements.zip", params={"month": bad}, headers=admin)
        assert r.status_code == 422, bad
    r = await client.get("/admin/export/statements.zip", headers=admin)
    assert r.status_code == 422  # month is required


async def test_zip_spool_is_closed_after_streaming(session: AsyncSession, world: World) -> None:
    ledger = LedgerService(session)
    service = ExportService(session, ledger, ActSverkaPdfService(session, ledger=ledger))
    archive = await service.statements_zip(
        wholesaler_id=world.wholesaler.id, month=date.today().strftime("%Y-%m")
    )
    assert archive.documents == 1 and not archive.file.closed
    data = b"".join([chunk async for chunk in archive.stream()])
    assert archive.file.closed
    assert len(data) == archive.size and zipfile.ZipFile(io.BytesIO(data)).testzip() is None
    await end_read(session)


# --- access & signed links -----------------------------------------------------------------


async def test_exports_are_admin_only(client: httpx.AsyncClient, world: World) -> None:
    for path in ("/admin/export/stores.xlsx", "/admin/export/stock.xlsx"):
        for who in (world.store.telegram_user_id, world.agent.telegram_user_id):
            assert (await client.get(path, headers=auth(who))).status_code == 403
        assert (await client.get(path)).status_code == 401


async def test_signed_export_links(client: httpx.AsyncClient, world: World) -> None:
    admin = admin_headers(world)
    r = await client.post("/admin/export/link", json={"kind": "stock"}, headers=admin)
    assert r.status_code == 200, r.text
    link = r.json()
    assert link["filename"].startswith("ombor-qoldiqlari_") and link["filename"].endswith(".xlsx")
    path = link["url"].removeprefix("/api/v1")
    r = await client.get(path)  # no Authorization header: the token is the grant
    assert r.status_code == 200 and r.headers["content-type"] == XLSX

    month = date.today().strftime("%Y-%m")
    r = await client.post(
        "/admin/export/link", json={"kind": "statements", "month": month}, headers=admin
    )
    assert r.json()["filename"] == f"statements_{month.replace('-', '_')}.zip"
    r = await client.get(r.json()["url"].removeprefix("/api/v1"))
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"

    # Tampered token, missing month, non-admin.
    r = await client.get(path[:-3] + ("abc" if not path.endswith("abc") else "xyz"))
    assert r.status_code == 401
    r = await client.post("/admin/export/link", json={"kind": "statements"}, headers=admin)
    assert r.status_code == 422
    r = await client.post(
        "/admin/export/link", json={"kind": "stock"}, headers=auth(world.store.telegram_user_id)
    )
    assert r.status_code == 403
