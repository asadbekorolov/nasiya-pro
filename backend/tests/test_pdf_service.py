from __future__ import annotations

import io
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from pypdf import PdfReader

from app.models import PaymentMethod
from app.services.ledger_service import LedgerService
from app.services.order_service import OrderLine, OrderService
from app.services.pdf_service import (
    ActRow,
    ActSverkaData,
    ActSverkaPdfService,
    Party,
    render_act_sverka,
    resolve_fonts,
)
from tests.conftest import World
from tests.helpers import auth

D = Decimal


def _text(pdf: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _sample(rows: int = 3) -> ActSverkaData:
    balance = D("1000.00")
    items = []
    for i in range(rows):
        debit = D("250.00") if i % 2 == 0 else D("0")
        credit = D("0") if i % 2 == 0 else D("100.00")
        balance = balance + debit - credit
        items.append(
            ActRow(
                day=date(2026, 10, 1) + timedelta(days=i % 28),
                document=f"Operatsiya {i + 1}",
                debit=debit,
                credit=credit,
                balance=balance,
            )
        )
    return ActSverkaData(
        supplier=Party(
            role_label="Yetkazib beruvchi",
            name="Optom",
            legal_name='"Optom Savdo" MChJ',
            tin="301234567",
        ),
        buyer=Party(role_label="Xaridor", name="Магазин Ромашка"),
        currency="UZS",
        period_from=date(2026, 10, 1),
        period_to=date(2026, 10, 31),
        opening_balance=D("1000.00"),
        rows=tuple(items),
        total_debit=sum((r.debit for r in items), D("0")),
        total_credit=sum((r.credit for r in items), D("0")),
        closing_balance=balance,
        generated_at=datetime(2026, 11, 1, 9, 30, tzinfo=UTC),
        document_code="ABCD-1234-EF56-7890",
    )


def test_renders_a_real_pdf_with_unicode_text() -> None:
    fonts = resolve_fonts()
    if not fonts.unicode:
        pytest.skip("no Unicode TTF font installed on this machine")
    pdf = render_act_sverka(_sample())
    assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF")

    reader = PdfReader(io.BytesIO(pdf))
    assert reader.metadata is not None and reader.metadata.creator == "Nasiya Pro"
    text = _text(pdf)
    assert "SOLISHTIRISH DALOLATNOMASI" in text
    assert "Магазин Ромашка" in text  # Cyrillic survives the font subset
    assert '"Optom Savdo" MChJ' in text
    assert "301234567" in text
    assert "1 400.00" in text  # closing balance 1000 + 2*250 - 100
    assert "M.O'." in text  # stamp place
    assert "ABCD-1234-EF56-7890" in text


def test_long_statements_span_pages_with_repeated_header() -> None:
    pdf = render_act_sverka(_sample(rows=120))
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) >= 3
    for page in reader.pages:
        assert "Qoldiq" in (page.extract_text() or "")  # table header repeats


def test_empty_period_renders() -> None:
    pdf = render_act_sverka(replace(_sample(rows=0), closing_balance=D("1000.00")))
    assert "operatsiyalar yo'q" in _text(pdf)


async def test_build_from_ledger(session, world: World) -> None:
    orders = OrderService(session)
    order = await orders.create_order(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        lines=[OrderLine(world.product.id, D("30"))],
    )
    await orders.approve_order(wholesaler_id=world.wholesaler.id, order_id=order.id)
    await orders.deliver_order(wholesaler_id=world.wholesaler.id, order_id=order.id)
    await LedgerService(session).record_payment(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        amount="1200.00",
        method=PaymentMethod.CASH,
    )

    today = datetime.now(UTC).date()
    service = ActSverkaPdfService(session)
    data = await service.collect(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        date_from=today - timedelta(days=1),
        date_to=today + timedelta(days=1),
    )
    assert data.opening_balance == D("0.00")
    assert data.total_debit == D("3000.00")
    assert data.total_credit == D("1200.00")
    assert data.closing_balance == D("1800.00")
    assert data.rows[0].document == f"Tovar yetkazib berildi (buyurtma #{order.order_number})"
    assert data.rows[1].document == "To'lov qabul qilindi (naqd pul)"
    assert data.supplier.bank_mfo == "01158"

    doc = await service.build(
        wholesaler_id=world.wholesaler.id,
        store_id=world.store.id,
        date_from=today - timedelta(days=1),
        date_to=today + timedelta(days=1),
    )
    assert doc.content.startswith(b"%PDF-")
    assert doc.filename.endswith(".pdf") and doc.filename.startswith("akt-sverka_")
    if resolve_fonts().unicode:
        text = _text(doc.content)
        assert "1 800.00 UZS" in text
        assert f"#{order.order_number}" in text


async def test_statement_pdf_download_endpoints(
    client: httpx.AsyncClient, session, world: World, make_order
) -> None:
    order = await make_order("500.00")
    await LedgerService(session).deliver_order(wholesaler_id=world.wholesaler.id, order_id=order.id)

    r = await client.get("/store/statement.pdf", headers=auth(world.store.telegram_user_id))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith('attachment; filename="akt-sverka_')
    assert r.headers["cache-control"] == "private, no-store"
    assert int(r.headers["content-length"]) == len(r.content)
    assert r.content.startswith(b"%PDF-")
    assert len(PdfReader(io.BytesIO(r.content)).pages) >= 1

    admin = auth(world.wholesaler.owner_telegram_user_id)
    r = await client.get(f"/admin/stores/{world.store.id}/statement.pdf", headers=admin)
    assert r.status_code == 200 and r.content.startswith(b"%PDF-")

    r = await client.get(
        "/store/statement.pdf",
        params={"date_from": "2026-10-10", "date_to": "2026-10-01"},
        headers=auth(world.store.telegram_user_id),
    )
    assert r.status_code == 422
