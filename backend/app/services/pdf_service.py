"""Official reconciliation act ("Akt-sverka" / o'zaro hisob-kitoblarni solishtirish
dalolatnomasi) as PDF, generated fully in memory (io.BytesIO).

Split in two layers:
* `ActSverkaPdfService.build()`  — async; loads parties and the period statement from
  `LedgerService.get_statement` and turns them into a plain `ActSverkaData` snapshot.
* `render_act_sverka()`          — pure, synchronous ReportLab rendering of that
  snapshot. It runs in a worker thread so the event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from functools import lru_cache
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock, utc_now
from app.core.config import Settings, get_settings
from app.core.exceptions import InvalidAmountError, NotFoundError
from app.core.money import ZERO, format_amount
from app.models import LedgerTransaction, Order, PaymentMethod, Store, TransactionType, Wholesaler
from app.services.ledger_service import LedgerService

logger = logging.getLogger(__name__)

MAX_PERIOD_DAYS = 3 * 366

PAYMENT_METHOD_LABELS: dict[PaymentMethod, str] = {
    PaymentMethod.CASH: "naqd pul",
    PaymentMethod.CARD: "plastik karta",
    PaymentMethod.BANK_TRANSFER: "bank o'tkazmasi",
    PaymentMethod.OTHER: "boshqa",
}


# --- snapshot passed to the renderer ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class Party:
    role_label: str
    name: str
    legal_name: str | None = None
    tin: str | None = None
    address: str | None = None
    phone: str | None = None
    bank_name: str | None = None
    bank_account: str | None = None
    bank_mfo: str | None = None

    @property
    def display_name(self) -> str:
        return self.legal_name or self.name


@dataclass(frozen=True, slots=True)
class ActRow:
    day: date
    document: str
    debit: Decimal  # store's debt increased (goods delivered)
    credit: Decimal  # store's debt decreased (money received)
    balance: Decimal


@dataclass(frozen=True, slots=True)
class ActSverkaData:
    supplier: Party
    buyer: Party
    currency: str
    period_from: date
    period_to: date
    opening_balance: Decimal
    rows: tuple[ActRow, ...]
    total_debit: Decimal
    total_credit: Decimal
    closing_balance: Decimal
    generated_at: datetime
    document_code: str


@dataclass(frozen=True, slots=True)
class PdfDocument:
    filename: str
    content: bytes
    document_code: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


# --- fonts ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FontPair:
    regular: str
    bold: str
    unicode: bool


_FONT_CANDIDATES: tuple[tuple[str, str], ...] = (
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    ("/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ),
)


@lru_cache(maxsize=8)
def resolve_fonts(regular_path: str | None = None, bold_path: str | None = None) -> FontPair:
    """Register a Unicode TTF family once per process (ReportLab's registry is global).
    Falls back to Helvetica, which cannot render Cyrillic — logged as a warning."""
    candidates: list[tuple[str, str]] = []
    if regular_path:
        candidates.append((regular_path, bold_path or regular_path))
    candidates.extend(_FONT_CANDIDATES)

    for regular, bold in candidates:
        if Path(regular).is_file() and Path(bold).is_file():
            suffix = hashlib.sha1(f"{regular}|{bold}".encode()).hexdigest()[:8]  # noqa: S324
            reg_name, bold_name = f"NasiyaSans-{suffix}", f"NasiyaSans-Bold-{suffix}"
            pdfmetrics.registerFont(TTFont(reg_name, regular))
            pdfmetrics.registerFont(TTFont(bold_name, bold))
            pdfmetrics.registerFontFamily(reg_name, normal=reg_name, bold=bold_name)
            return FontPair(reg_name, bold_name, unicode=True)

    if regular_path:
        logger.warning("configured PDF font not found: %s", regular_path)
    logger.warning("no Unicode TTF font found; Cyrillic text will not render in PDFs")
    return FontPair("Helvetica", "Helvetica-Bold", unicode=False)


# --- rendering -------------------------------------------------------------------------------


def _fmt_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def _money(value: Decimal) -> str:
    return format_amount(value)


def _text(value: str | None) -> str:
    return escape(value) if value else "&mdash;"


def render_act_sverka(data: ActSverkaData, fonts: FontPair | None = None) -> bytes:
    fonts = fonts or resolve_fonts()
    buffer = io.BytesIO()
    margin = 15 * mm
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
        title=f"Akt-sverka {data.buyer.display_name}",
        author=data.supplier.display_name,
        subject=f"{_fmt_date(data.period_from)} - {_fmt_date(data.period_to)}",
        creator="Nasiya Pro",
    )
    width = A4[0] - 2 * margin

    base = ParagraphStyle("base", fontName=fonts.regular, fontSize=8.5, leading=11)
    small = ParagraphStyle("small", parent=base, fontSize=7.5, leading=9.5)
    title = ParagraphStyle(
        "title", parent=base, fontName=fonts.bold, fontSize=12.5, leading=16, alignment=TA_CENTER
    )
    subtitle = ParagraphStyle("subtitle", parent=base, alignment=TA_CENTER)
    cell = ParagraphStyle("cell", parent=base, fontSize=8, leading=10)
    cell_right = ParagraphStyle("cell_right", parent=cell, alignment=TA_RIGHT)
    cell_bold = ParagraphStyle("cell_bold", parent=cell, fontName=fonts.bold)
    cell_bold_right = ParagraphStyle("cell_bold_right", parent=cell_bold, alignment=TA_RIGHT)
    head = ParagraphStyle(
        "head", parent=cell, fontName=fonts.bold, alignment=TA_CENTER, textColor=colors.white
    )
    sign = ParagraphStyle("sign", parent=base, alignment=TA_LEFT, leading=15)

    story: list[object] = [
        Paragraph("O'ZARO HISOB-KITOBLARNI SOLISHTIRISH DALOLATNOMASI", title),
        Paragraph("(Akt-sverka)", subtitle),
        Spacer(1, 2 * mm),
        Paragraph(
            f"{_fmt_date(data.period_from)} &ndash; {_fmt_date(data.period_to)} davr uchun "
            f"<b>{escape(data.supplier.display_name)}</b> va "
            f"<b>{escape(data.buyer.display_name)}</b> o'rtasida",
            subtitle,
        ),
        Paragraph(f"Valyuta: {escape(data.currency)}", subtitle),
        Spacer(1, 5 * mm),
    ]

    # --- requisites -------------------------------------------------------------------
    def requisites(party: Party) -> list[Paragraph]:
        lines = [
            f"<b>{escape(party.role_label)}</b>",
            f"<b>{escape(party.display_name)}</b>",
            f"STIR (INN): {_text(party.tin)}",
            f"Manzil: {_text(party.address)}",
            f"Telefon: {_text(party.phone)}",
            f"Bank: {_text(party.bank_name)}",
            f"H/r: {_text(party.bank_account)} &nbsp; MFO: {_text(party.bank_mfo)}",
        ]
        return [Paragraph(line, small) for line in lines]

    req_table = Table(
        [[requisites(data.supplier), requisites(data.buyer)]],
        colWidths=[width / 2, width / 2],
    )
    req_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (0, 0), 0.5, colors.grey),
                ("BOX", (1, 0), (1, 0), 0.5, colors.grey),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story += [req_table, Spacer(1, 5 * mm)]

    # --- movements -------------------------------------------------------------------
    header = [
        Paragraph(h, head)
        for h in (
            "№" if fonts.unicode else "#",
            "Sana",
            "Hujjat / operatsiya",
            "Debet",
            "Kredit",
            "Qoldiq",
        )
    ]
    rows: list[list[Paragraph]] = [
        header,
        [
            Paragraph("", cell),
            Paragraph(_fmt_date(data.period_from), cell),
            Paragraph("Davr boshidagi qoldiq", cell_bold),
            Paragraph("", cell),
            Paragraph("", cell),
            Paragraph(_money(data.opening_balance), cell_bold_right),
        ],
    ]
    for index, row in enumerate(data.rows, start=1):
        rows.append(
            [
                Paragraph(str(index), cell),
                Paragraph(_fmt_date(row.day), cell),
                Paragraph(escape(row.document), cell),
                Paragraph(_money(row.debit) if row.debit else "", cell_right),
                Paragraph(_money(row.credit) if row.credit else "", cell_right),
                Paragraph(_money(row.balance), cell_right),
            ]
        )
    if not data.rows:
        rows.append(
            [
                Paragraph("", cell),
                Paragraph("", cell),
                Paragraph("Davr ichida operatsiyalar yo'q", cell),
            ]
            + [Paragraph("", cell)] * 3
        )
    rows += [
        [
            Paragraph("", cell),
            Paragraph("", cell),
            Paragraph("Davr aylanmasi", cell_bold),
            Paragraph(_money(data.total_debit), cell_bold_right),
            Paragraph(_money(data.total_credit), cell_bold_right),
            Paragraph("", cell),
        ],
        [
            Paragraph("", cell),
            Paragraph(_fmt_date(data.period_to), cell),
            Paragraph("Davr oxiridagi qoldiq", cell_bold),
            Paragraph("", cell),
            Paragraph("", cell),
            Paragraph(_money(data.closing_balance), cell_bold_right),
        ],
    ]
    col_widths = [w * mm for w in (9, 21, 72, 26, 26, 26)]
    scale = width / sum(col_widths)
    movements = Table(rows, colWidths=[w * scale for w in col_widths], repeatRows=1)
    summary_start = len(rows) - 2
    movements.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3A5F")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9AA5B1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#EEF2F6")),
                ("BACKGROUND", (0, summary_start), (-1, -1), colors.HexColor("#EEF2F6")),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ]
        )
    )
    story += [movements, Spacer(1, 5 * mm)]

    # --- conclusion ------------------------------------------------------------------
    supplier, buyer = escape(data.supplier.display_name), escape(data.buyer.display_name)
    closing = data.closing_balance
    amount = format_amount(abs(closing), data.currency)
    if closing > ZERO:
        conclusion = (
            f"{_fmt_date(data.period_to)} holatiga <b>{buyer}</b> ning <b>{supplier}</b> "
            f"oldidagi qarzi <b>{amount}</b> ni tashkil etadi."
        )
    elif closing < ZERO:
        conclusion = (
            f"{_fmt_date(data.period_to)} holatiga <b>{supplier}</b> ning <b>{buyer}</b> "
            f"oldidagi qarzi (oldindan to'lov) <b>{amount}</b> ni tashkil etadi."
        )
    else:
        conclusion = (
            f"{_fmt_date(data.period_to)} holatiga tomonlar o'rtasida qarzdorlik mavjud emas."
        )
    story += [
        Paragraph(conclusion, base),
        Spacer(1, 2 * mm),
        Paragraph(
            "Ushbu dalolatnoma ikki nusxada tuzildi. Imzolangan kundan boshlab 10 kun ichida "
            "e'tiroz bildirilmasa, ma'lumotlar tasdiqlangan hisoblanadi.",
            small,
        ),
        Spacer(1, 8 * mm),
    ]

    # --- signatures & stamps -----------------------------------------------------------
    def signature(party: Party) -> list[Paragraph]:
        return [
            Paragraph(f"<b>{escape(party.role_label)}:</b>", sign),
            Paragraph(f"{escape(party.display_name)}", sign),
            Paragraph("Rahbar: ____________________ / ______________ /", sign),
            Paragraph("Bosh hisobchi: ______________ / ______________ /", sign),
            Paragraph("Sana: «____» ______________ 20___ y.", sign),
            Spacer(1, 3 * mm),
            Paragraph("M.O'. (muhr o'rni)", small),
        ]

    sign_table = Table(
        [[signature(data.supplier), signature(data.buyer)]], colWidths=[width / 2, width / 2]
    )
    sign_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(KeepTogether([sign_table]))

    footer_left = (
        f"Nasiya Pro · Hujjat kodi: {data.document_code} · "
        f"Yaratildi: {data.generated_at.strftime('%d.%m.%Y %H:%M')}"
    )

    def draw_footer(canvas: Canvas, doc_: SimpleDocTemplate) -> None:
        canvas.saveState()
        canvas.setFont(fonts.regular, 7)
        canvas.setFillColor(colors.grey)
        canvas.drawString(margin, 9 * mm, footer_left)
        canvas.drawRightString(A4[0] - margin, 9 * mm, f"Sahifa {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return buffer.getvalue()


# --- service ---------------------------------------------------------------------------------


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return slug[:60] or "store"


def _document_label(tx: LedgerTransaction, order_numbers: dict[uuid.UUID, int]) -> str:
    if tx.type == TransactionType.CHARGE:
        number = order_numbers.get(tx.order_id) if tx.order_id else None
        return (
            f"Tovar yetkazib berildi (buyurtma #{number})" if number else "Tovar yetkazib berildi"
        )
    if tx.type == TransactionType.PAYMENT:
        method = PAYMENT_METHOD_LABELS.get(tx.payment_method or PaymentMethod.OTHER, "boshqa")
        label = f"To'lov qabul qilindi ({method})"
        return f"{label} — {tx.description}" if tx.description else label
    return f"Tuzatish: {tx.description}" if tx.description else "Tuzatish"


class ActSverkaPdfService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        ledger: LedgerService | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.ledger = ledger or LedgerService(session, settings=self.settings, clock=clock)
        self._clock = clock

    async def build(
        self,
        *,
        wholesaler_id: uuid.UUID,
        store_id: uuid.UUID,
        date_from: date,
        date_to: date,
    ) -> PdfDocument:
        if date_to < date_from:
            raise InvalidAmountError("date_to must not be before date_from")
        if (date_to - date_from).days > MAX_PERIOD_DAYS:
            raise InvalidAmountError("The period may not exceed 3 years")

        data = await self.collect(
            wholesaler_id=wholesaler_id, store_id=store_id, date_from=date_from, date_to=date_to
        )
        fonts = resolve_fonts(self.settings.pdf_font_regular_path, self.settings.pdf_font_bold_path)
        content = await asyncio.to_thread(render_act_sverka, data, fonts)
        filename = f"akt-sverka_{_slug(data.buyer.name)}_{date_from:%Y%m%d}-{date_to:%Y%m%d}.pdf"
        return PdfDocument(filename=filename, content=content, document_code=data.document_code)

    async def collect(
        self,
        *,
        wholesaler_id: uuid.UUID,
        store_id: uuid.UUID,
        date_from: date,
        date_to: date,
    ) -> ActSverkaData:
        wholesaler = await self.session.scalar(
            select(Wholesaler).where(Wholesaler.id == wholesaler_id)
        )
        store = await self.session.scalar(
            select(Store).where(Store.id == store_id, Store.wholesaler_id == wholesaler_id)
        )
        if wholesaler is None or store is None:
            raise NotFoundError("Store not found", store_id=str(store_id))

        tz = ZoneInfo(wholesaler.timezone)
        statement = await self.ledger.get_statement(
            wholesaler_id=wholesaler_id,
            store_id=store_id,
            period_from=datetime.combine(date_from, time.min, tzinfo=tz),
            period_to=datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz),
        )

        order_ids = {t.order_id for t in statement.transactions if t.order_id}
        order_numbers: dict[uuid.UUID, int] = {}
        if order_ids:
            result = await self.session.execute(
                select(Order.id, Order.order_number).where(Order.id.in_(order_ids))
            )
            order_numbers = {oid: number for oid, number in result.all()}

        rows = tuple(
            ActRow(
                day=t.created_at.astimezone(tz).date(),
                document=_document_label(t, order_numbers),
                debit=t.amount if t.amount > ZERO else ZERO,
                credit=-t.amount if t.amount < ZERO else ZERO,
                balance=t.balance_after,
            )
            for t in statement.transactions
        )
        total_debit = sum((r.debit for r in rows), ZERO)
        total_credit = sum((r.credit for r in rows), ZERO)

        last_hash = statement.transactions[-1].entry_hash if statement.transactions else ""
        code_source = (
            f"{store.id}|{date_from}|{date_to}|{statement.opening_balance}|"
            f"{statement.closing_balance}|{last_hash}"
        )
        document_code = hashlib.sha256(code_source.encode()).hexdigest()[:16].upper()

        return ActSverkaData(
            supplier=Party(
                role_label="Yetkazib beruvchi",
                name=wholesaler.name,
                legal_name=wholesaler.legal_name,
                tin=wholesaler.tin,
                address=wholesaler.address,
                phone=wholesaler.phone,
                bank_name=wholesaler.bank_name,
                bank_account=wholesaler.bank_account,
                bank_mfo=wholesaler.bank_mfo,
            ),
            buyer=Party(
                role_label="Xaridor",
                name=store.name,
                legal_name=store.legal_name,
                tin=store.tin,
                address=store.address,
                phone=store.phone,
                bank_name=store.bank_name,
                bank_account=store.bank_account,
                bank_mfo=store.bank_mfo,
            ),
            currency=wholesaler.currency,
            period_from=date_from,
            period_to=date_to,
            opening_balance=statement.opening_balance,
            rows=rows,
            total_debit=total_debit,
            total_credit=total_credit,
            closing_balance=statement.closing_balance,
            generated_at=self._clock().astimezone(tz),
            document_code="-".join(document_code[i : i + 4] for i in range(0, 16, 4)),
        )
