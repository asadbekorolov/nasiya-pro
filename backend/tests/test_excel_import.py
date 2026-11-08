"""Excel onboarding: templates, parsing/validation, product upsert, stores with opening
debts through the ledger, dry runs, and all-or-nothing writes."""

from __future__ import annotations

import io
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from openpyxl import Workbook, load_workbook

from app.core.exceptions import InvalidAmountError
from app.services.excel_service import NBSP, STORE_COLUMNS, ImportFileError, _decimal, read_rows
from app.services.ledger_service import LedgerService
from tests.conftest import World
from tests.helpers import auth

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PRODUCT_HEADER = [
    "Nomi",
    "SKU / Artikul",
    "Narxi (so'm)",
    "Boshlang'ich qoldiq (dona/kg)",
    "O'lchov birligi",
]
STORE_HEADER = [c.title for c in STORE_COLUMNS]


def xlsx(header: list[Any], *rows: list[Any]) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(header)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def upload(
    client: httpx.AsyncClient,
    world: World,
    kind: str,
    data: bytes,
    *,
    dry_run: bool = False,
    filename: str = "import.xlsx",
) -> httpx.Response:
    return await client.post(
        f"/admin/import/{kind}",
        params={"dry_run": dry_run},
        files={"file": (filename, data, XLSX)},
        headers=auth(world.wholesaler.owner_telegram_user_id),
    )


def errors_by_row(body: dict) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for e in body["errors"]:
        out.setdefault(e["row"], []).append(e["message"])
    return out


# --- cell parsing ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (125000, "125000.00"),
        (12500.5, "12500.50"),
        ("1 250 000", "1250000.00"),
        (f"1{NBSP}250{NBSP}000 so'm", "1250000.00"),
        ("1'250'000", "1250000.00"),
        ("12500,50", "12500.50"),
        ("1,250,000.75", "1250000.75"),
        (None, None),
        ("", None),
    ],
)
def test_money_cells(raw: Any, expected: str | None) -> None:
    value = _decimal(raw, 2, "Narx")
    assert (str(value) if value is not None else None) == expected


@pytest.mark.parametrize("raw", [-1, "abc", "1.005", True, 10**14])
def test_bad_money_cells(raw: Any) -> None:
    with pytest.raises(ValueError):
        _decimal(raw, 2, "Narx")


def test_file_level_errors() -> None:
    with pytest.raises(ImportFileError, match="bo'sh"):
        read_rows(b"", "products")
    with pytest.raises(ImportFileError, match="xlsx"):
        read_rows(b"Nomi;Narxi\nUn;100\n", "products")
    with pytest.raises(ImportFileError, match="o'qib bo'lmadi"):
        read_rows(b"PK\x03\x04 not really a zip", "products")
    with pytest.raises(ImportFileError, match="Narxi") as exc:
        read_rows(xlsx(["Nomi", "Qoldiq"], ["Un", 1]), "products")
    assert exc.value.details["missing_columns"] == ["Narxi (so'm)"]


# --- templates ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["products", "stores"])
async def test_templates_round_trip(client: httpx.AsyncClient, world: World, kind: str) -> None:
    admin = auth(world.wholesaler.owner_telegram_user_id)
    r = await client.get(f"/admin/import/template/{kind}", headers=admin)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == XLSX
    assert "attachment" in r.headers["content-disposition"]

    # Same file without auth for Telegram's downloader.
    public = await client.get(f"/files/templates/{kind}.xlsx")
    assert public.status_code == 200
    assert public.content[:2] == b"PK"

    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames[1] == "Yo'riqnoma"
    header = [c.value for c in next(wb.worksheets[0].iter_rows(max_row=1))]
    assert header == (PRODUCT_HEADER if kind == "products" else STORE_HEADER)

    # The untouched template is itself a valid upload (dry run: nothing written).
    r = await upload(client, world, kind, r.content, dry_run=True)
    assert r.status_code == 200, r.text
    assert r.json()["errors"] == [] and r.json()["imported"] >= 2

    r = await client.get("/admin/import/template/orders", headers=admin)
    assert r.status_code == 422


# --- products ----------------------------------------------------------------------------


async def test_products_upsert_and_row_errors(client: httpx.AsyncClient, world: World) -> None:
    admin = auth(world.wholesaler.owner_telegram_user_id)
    data = xlsx(
        PRODUCT_HEADER,
        ["Un 50kg (yangi narx)", world.product.sku, "105 000", 1500, None],  # 2: update
        ["Shakar 1 kg", "SH-1", 13500, "800,5", "kg"],  # 3: create
        ["Ko'k choy", None, "168000", 40, "blok"],  # 4: create (no SKU)
        [None, "X-1", 100, 1, None],  # 5: no name
        ["Yog'", "Y-1", -5, 1, None],  # 6: negative price
        ["Guruch", "G-1", "arzon", 1, None],  # 7: not a number
        ["Shakar dublikat", "sh-1", 1, 1, None],  # 8: duplicate SKU in file
        ["Makaron", "M-1", 100, "1.0005", "qop"],  # 9: two bad cells
        [None, None, None, None, None],  # empty row: ignored
    )
    r = await upload(client, world, "products", data)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["total_rows"], body["created"], body["updated"], body["failed_rows"]) == (
        8,
        2,
        1,
        5,
    )
    errors = errors_by_row(body)
    assert set(errors) == {5, 6, 7, 8, 9}
    assert "bo'sh" in errors[5][0]
    assert "manfiy" in errors[6][0]
    assert "son bo'lishi kerak" in errors[7][0]
    assert "3-qator" in errors[8][0]
    assert len(errors[9]) == 2  # quantity precision AND unknown unit, both reported
    assert {e["column"] for e in body["errors"] if e["row"] == 9} == {
        "Boshlang'ich qoldiq (dona/kg)",
        "O'lchov birligi",
    }

    stock = (await client.get("/admin/stock", headers=admin)).json()["items"]
    by_name = {i["name"]: i for i in stock}
    assert by_name["Un 50kg (yangi narx)"]["price"] == "105000.00"
    assert by_name["Un 50kg (yangi narx)"]["stock_quantity"] == "1500.000"
    assert by_name["Shakar 1 kg"]["unit"] == "kg"
    assert by_name["Shakar 1 kg"]["stock_quantity"] == "800.500"
    assert by_name["Ko'k choy"]["unit"] == "block"
    assert len(stock) == 3

    # Re-upload: SKU rows update; the SKU-less row is matched by name, not duplicated.
    r = await upload(client, world, "products", data)
    assert (r.json()["created"], r.json()["updated"]) == (0, 3)
    assert len((await client.get("/admin/stock", headers=admin)).json()["items"]) == 3


async def test_products_stock_cannot_drop_below_reservations(
    client: httpx.AsyncClient, world: World
) -> None:
    admin = auth(world.wholesaler.owner_telegram_user_id)
    r = await client.post(
        "/store/orders",
        json={
            "lines": [{"product_id": str(world.product.id), "quantity": "300"}],
            "idempotency_key": f"tg-{uuid.uuid4().hex}",
        },
        headers=auth(world.store.telegram_user_id),
    )
    order = r.json()
    assert (
        await client.post(f"/admin/orders/{order['id']}/approve", json={}, headers=admin)
    ).status_code == 200

    r = await upload(
        client, world, "products", xlsx(PRODUCT_HEADER, ["Un", world.product.sku, 100, 250])
    )
    assert r.json()["updated"] == 0
    assert "band qilingan" in r.json()["errors"][0]["message"]
    r = await upload(
        client, world, "products", xlsx(PRODUCT_HEADER, ["Un", world.product.sku, 100, 300])
    )
    assert r.json()["updated"] == 1 and r.json()["errors"] == []


async def test_products_dry_run_writes_nothing(client: httpx.AsyncClient, world: World) -> None:
    admin = auth(world.wholesaler.owner_telegram_user_id)
    r = await upload(
        client, world, "products", xlsx(PRODUCT_HEADER, ["Yangi tovar", "N-1", 10, 5]), dry_run=True
    )
    assert r.json()["dry_run"] is True and r.json()["created"] == 1
    items = (await client.get("/admin/stock", headers=admin)).json()["items"]
    assert [i["name"] for i in items] == ["Un 50kg"]


# --- stores & opening debts ----------------------------------------------------------------


async def test_stores_with_opening_debts(client: httpx.AsyncClient, world: World) -> None:
    admin = auth(world.wholesaler.owner_telegram_user_id)
    overdue_due = (date.today() - timedelta(days=20)).strftime("%d.%m.%Y")
    data = xlsx(
        STORE_HEADER,
        [
            "Yangi market",
            "Akmal",
            "90 123 45 67",
            "Samarqand",
            "15 000 000",
            "2 500 000",
            overdue_due,
        ],
        ["Nur do'koni", None, "+998 91 234-56-78", None, 5000000, 0, None],
        ["Bo'sh limit", None, None, None, None, None, None],
        ["Do'kon A", None, None, None, 1, 1, None],  # 5: exists already
        ["Xato tel", None, "12345", None, 1, 1, None],  # 6: bad phone
        ["Xato sana", None, None, None, 1, 1, "32.13.2026"],  # 7: bad date
    )
    r = await upload(client, world, "stores", data)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["created"], body["failed_rows"]) == (3, 3)
    assert body["opening_debt_total"] == "2500000.00"
    errors = errors_by_row(body)
    assert "allaqachon mavjud" in errors[5][0]
    assert "telefon" in errors[6][0]
    assert "sana" in errors[7][0]

    stores = {s["name"]: s for s in (await client.get("/admin/stores", headers=admin)).json()}
    new = stores["Yangi market"]
    assert new["phone"] == "+998901234567"
    assert new["credit_limit"] == "15000000.00"
    assert new["current_debt"] == "2500000.00"
    assert new["overdue_amount"] == "2500000.00"
    assert stores["Nur do'koni"]["phone"] == "+998912345678"
    assert stores["Bo'sh limit"]["credit_limit"] == "0.00"
    assert stores["Bo'sh limit"]["current_debt"] == "0.00"

    # The debt is a real, hash-chained ledger adjustment with an aging bucket.
    statement = (await client.get(f"/admin/stores/{new['id']}/statement", headers=admin)).json()
    (tx,) = statement["transactions"]
    assert tx["type"] == "adjustment" and tx["amount"] == "2500000.00"
    assert tx["description"] == "Boshlang'ich qarz (Excel import)"
    assert (await client.get(f"/admin/stores/{new['id']}/ledger/verify", headers=admin)).json()[
        "ok"
    ]
    aging = (
        await client.get("/admin/aging-report", params={"scheme": "short"}, headers=admin)
    ).json()
    row = next(r for r in aging["rows"] if r["store_id"] == new["id"])
    assert row["buckets"]["16_30"] == "2500000.00"

    # Uploading the same file again never duplicates a store or its debt.
    r = await upload(client, world, "stores", data)
    assert r.json()["created"] == 0
    stores = (await client.get("/admin/stores", headers=admin)).json()
    assert len(stores) == 4
    assert next(s for s in stores if s["name"] == "Yangi market")["current_debt"] == "2500000.00"


async def test_stores_import_is_all_or_nothing(
    client: httpx.AsyncClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure while booking the 2nd opening debt rolls back the 1st store and debt."""
    admin = auth(world.wholesaler.owner_telegram_user_id)
    original = LedgerService.record_adjustment
    calls = 0

    async def flaky(self: LedgerService, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise InvalidAmountError("simulated ledger failure")
        return await original(self, **kwargs)

    monkeypatch.setattr(LedgerService, "record_adjustment", flaky)
    data = xlsx(
        STORE_HEADER,
        ["Birinchi", None, None, None, 0, 100, None],
        ["Ikkinchi", None, None, None, 0, 200, None],
    )
    r = await upload(client, world, "stores", data)
    assert r.status_code == 422
    assert calls == 2

    names = [s["name"] for s in (await client.get("/admin/stores", headers=admin)).json()]
    assert names == ["Do'kon A"]


async def test_import_access_and_file_checks(client: httpx.AsyncClient, world: World) -> None:
    data = xlsx(PRODUCT_HEADER, ["Un", "U-1", 1, 1])
    for who in (world.store.telegram_user_id, world.agent.telegram_user_id):
        r = await client.post(
            "/admin/import/products", files={"file": ("a.xlsx", data, XLSX)}, headers=auth(who)
        )
        assert r.status_code == 403

    r = await upload(client, world, "products", data, filename="eski.xls")
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_import_file"
    r = await upload(client, world, "stores", xlsx(["Ism"], ["X"]))
    assert r.status_code == 422 and "Do'kon nomi" in r.json()["error"]["message"]

    # The products file uploaded as stores (and back) is refused, not misread.
    r = await upload(client, world, "stores", data)
    assert r.status_code == 422 and "tovarlar namunasiga" in r.json()["error"]["message"]
    stores_file = xlsx(STORE_HEADER, ["Do'kon B", None, None, None, 1, 1, None])
    r = await upload(client, world, "products", stores_file)
    assert r.status_code == 422 and "do'konlar namunasiga" in r.json()["error"]["message"]
    r = await client.post(
        "/admin/import/products", headers=auth(world.wholesaler.owner_telegram_user_id)
    )
    assert r.status_code == 422  # no file at all

    # Money in a file is never parsed as float: 0.1 + 0.2 style values stay exact.
    r = await upload(client, world, "products", xlsx(PRODUCT_HEADER, ["Mayda", "MY-1", 0.3, 0.1]))
    assert r.json()["created"] == 1
    assert Decimal("0.30") == Decimal(
        next(
            i["price"]
            for i in (
                await client.get(
                    "/admin/stock", headers=auth(world.wholesaler.owner_telegram_user_id)
                )
            ).json()["items"]
            if i["name"] == "Mayda"
        )
    )
