"""Admin exports: Excel reports (stores & receivables, warehouse stock) and a monthly
batch of official Akt-sverka PDFs packed into one ZIP.

Memory: the workbooks are written in openpyxl's write-only (streaming) mode. The ZIP
is assembled in a `SpooledTemporaryFile` — kept in RAM while small, spilled to disk
once it passes `ZIP_SPOOL_MAX_BYTES` — and each PDF is dropped right after it has been
compressed into the archive, so peak memory is one PDF plus the spool threshold, not
the whole month. CPU-bound steps (PDF rendering inside `ActSverkaPdfService`, deflate,
workbook serialisation) run in worker threads so the event loop keeps serving requests.
"""

from __future__ import annotations

import asyncio
import calendar
import csv
import io
import logging
import re
import tempfile
import uuid
import zipfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import IO, Any
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import Cell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet._write_only import WriteOnlyWorksheet
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DomainError, InvalidAmountError
from app.core.money import ZERO
from app.models import LedgerTransaction, Product, Store, TransactionType, Wholesaler
from app.services.ledger_service import LedgerService
from app.services.pdf_service import ActSverkaPdfService

logger = logging.getLogger(__name__)

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ZIP_SPOOL_MAX_BYTES = 16 * 1024 * 1024
STREAM_CHUNK_BYTES = 64 * 1024
MAX_STATEMENTS_PER_ZIP = 1000
UTF8_BOM = chr(0xFEFF)
MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")

UNIT_LABEL = {
    "pcs": "dona",
    "kg": "kg",
    "l": "l",
    "m": "m",
    "box": "quti",
    "pack": "pachka",
    "block": "blok",
}


class ExportTooLargeError(DomainError):
    code = "export_too_large"
    status_code = 422


@dataclass(frozen=True, slots=True)
class ExportFile:
    filename: str
    content: bytes
    media_type: str = XLSX_MEDIA_TYPE


@dataclass(slots=True)
class ZipExport:
    """A finished archive in a spooled temp file. `stream()` yields it in chunks and
    closes the file when done (or when the client disconnects)."""

    filename: str
    file: IO[bytes]
    size: int
    documents: int

    async def stream(self) -> AsyncIterator[bytes]:
        try:
            self.file.seek(0)
            while chunk := await asyncio.to_thread(self.file.read, STREAM_CHUNK_BYTES):
                yield chunk
        finally:
            self.file.close()

    def close(self) -> None:
        self.file.close()


def parse_month(value: str, today: date) -> tuple[date, date]:
    """'2026-10' -> (2026-10-01, 2026-10-31); the current month ends today."""
    match = MONTH_RE.fullmatch(value.strip())
    if not match:
        raise InvalidAmountError("month must look like YYYY-MM", month=value)
    year, month = int(match.group(1)), int(match.group(2))
    start = date(year, month, 1)
    if start > today:
        raise InvalidAmountError("month is in the future", month=value)
    end = date(year, month, calendar.monthrange(year, month)[1])
    return start, min(end, today)


# --- xlsx helpers ------------------------------------------------------------------------

_HEADER_FONT = Font(bold=True, color="FFFFFF")
_HEADER_FILL = PatternFill("solid", fgColor="2481CC")
_TOTAL_FONT = Font(bold=True)
MONEY_FORMAT = "#,##0.00"
QTY_FORMAT = "#,##0.###"
DATE_FORMAT = "DD.MM.YYYY"


def _header(ws: WriteOnlyWorksheet, titles: list[str]) -> list[Cell]:
    cells = []
    for title in titles:
        cell = WriteOnlyCell(ws, value=title)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        cells.append(cell)
    return cells


def _cell(ws: WriteOnlyWorksheet, value: Any, fmt: str | None = None, bold: bool = False) -> Cell:
    cell = WriteOnlyCell(ws, value=value)
    if fmt:
        cell.number_format = fmt
    if bold:
        cell.font = _TOTAL_FONT
    return cell


def _save(wb: Workbook) -> bytes:
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _setup_sheet(wb: Workbook, title: str, widths: list[int]) -> WriteOnlyWorksheet:
    ws = wb.create_sheet(title)
    assert isinstance(ws, WriteOnlyWorksheet)
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width  # type: ignore[attr-defined]
    ws.freeze_panes = "A2"
    return ws


def _slug(text: str) -> str:
    # Keep Latin, digits and Cyrillic (U+0400..U+04FF) in file names; everything else -> "-".
    cleaned = re.sub(f"[^0-9A-Za-z{chr(0x400)}-{chr(0x4FF)}]+", "-", text).strip("-")
    return cleaned[:60] or "dokon"


# --- service -----------------------------------------------------------------------------


class ExportService:
    def __init__(
        self, session: AsyncSession, ledger: LedgerService, pdf: ActSverkaPdfService
    ) -> None:
        self.session = session
        self.ledger = ledger
        self.pdf = pdf

    async def _tz(self, wholesaler_id: uuid.UUID) -> ZoneInfo:
        name = await self.session.scalar(
            select(Wholesaler.timezone).where(Wholesaler.id == wholesaler_id)
        )
        return ZoneInfo(name or "UTC")

    # ------------------------------------------------------------------ stores --

    async def stores_xlsx(self, *, wholesaler_id: uuid.UUID) -> ExportFile:
        """Stores & receivables: owner, phone, limit, current and overdue debt, last
        payment. One row per store (inactive ones included and marked)."""
        stores = list(
            (
                await self.session.scalars(
                    select(Store)
                    .where(Store.wholesaler_id == wholesaler_id)
                    .order_by(Store.is_active.desc(), Store.name)
                )
            ).all()
        )
        today = await self.ledger.local_today(wholesaler_id)
        tz = await self._tz(wholesaler_id)
        overdue = await self.ledger.get_overdue_amounts(
            wholesaler_id=wholesaler_id, store_ids=[s.id for s in stores], as_of=today
        )
        last_paid: dict[uuid.UUID, datetime] = {}
        if stores:
            rows = await self.session.execute(
                select(LedgerTransaction.store_id, func.max(LedgerTransaction.created_at))
                .where(
                    LedgerTransaction.wholesaler_id == wholesaler_id,
                    LedgerTransaction.type == TransactionType.PAYMENT,
                )
                .group_by(LedgerTransaction.store_id)
            )
            last_paid = {sid: at for sid, at in rows.tuples().all()}

        def build() -> bytes:
            wb = Workbook(write_only=True)
            ws = _setup_sheet(wb, "Do'konlar", [30, 24, 18, 34, 16, 16, 18, 16, 16, 12])
            ws.append(
                _header(
                    ws,
                    [
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
                    ],
                )
            )
            total_limit = total_debt = total_overdue = ZERO
            for s in stores:
                debt = s.current_debt
                late = overdue.get(s.id, ZERO)
                paid = last_paid.get(s.id)
                ws.append(
                    [
                        s.name,
                        s.contact_name,
                        s.phone,
                        s.address,
                        _cell(ws, s.credit_limit, MONEY_FORMAT),
                        _cell(ws, debt, MONEY_FORMAT),
                        _cell(ws, late, MONEY_FORMAT),
                        _cell(ws, max(s.credit_limit - max(debt, ZERO), ZERO), MONEY_FORMAT),
                        _cell(ws, paid.astimezone(tz).date() if paid else None, DATE_FORMAT),
                        "faol" if s.is_active else "nofaol",
                    ]
                )
                total_limit += s.credit_limit
                total_debt += debt
                total_overdue += late
            ws.append([])
            ws.append(
                [
                    _cell(ws, f"Jami: {len(stores)} ta do'kon", bold=True),
                    None,
                    None,
                    None,
                    _cell(ws, total_limit, MONEY_FORMAT, True),
                    _cell(ws, total_debt, MONEY_FORMAT, True),
                    _cell(ws, total_overdue, MONEY_FORMAT, True),
                ]
            )
            return _save(wb)

        content = await asyncio.to_thread(build)
        return ExportFile(filename=f"dokonlar-qarzlar_{today:%Y-%m-%d}.xlsx", content=content)

    # ------------------------------------------------------------------- stock --

    async def stock_xlsx(self, *, wholesaler_id: uuid.UUID) -> ExportFile:
        """Active catalog: wholesale price, on hand, reserved by approved orders, free."""
        products = list(
            (
                await self.session.scalars(
                    select(Product)
                    .where(Product.wholesaler_id == wholesaler_id, Product.is_active.is_(True))
                    .order_by(Product.name)
                )
            ).all()
        )
        today = await self.ledger.local_today(wholesaler_id)

        def build() -> bytes:
            wb = Workbook(write_only=True)
            ws = _setup_sheet(wb, "Ombor", [36, 16, 10, 16, 14, 14, 14, 18])
            ws.append(
                _header(
                    ws,
                    [
                        "Tovar nomi",
                        "SKU / Artikul",
                        "Birlik",
                        "Optom narxi",
                        "Omborda bor",
                        "Band qilingan",
                        "Erkin qoldiq",
                        "Qoldiq qiymati",
                    ],
                )
            )
            total_value = ZERO
            for p in products:
                value = (p.stock_quantity * p.price).quantize(Decimal("0.01"))
                total_value += value
                ws.append(
                    [
                        p.name,
                        p.sku,
                        UNIT_LABEL.get(p.unit.value, p.unit.value),
                        _cell(ws, p.price, MONEY_FORMAT),
                        _cell(ws, p.stock_quantity, QTY_FORMAT),
                        _cell(ws, p.reserved_quantity, QTY_FORMAT),
                        _cell(ws, p.available_quantity, QTY_FORMAT),
                        _cell(ws, value, MONEY_FORMAT),
                    ]
                )
            ws.append([])
            ws.append(
                [
                    _cell(ws, f"Jami: {len(products)} ta tovar", bold=True),
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    _cell(ws, total_value, MONEY_FORMAT, True),
                ]
            )
            return _save(wb)

        content = await asyncio.to_thread(build)
        return ExportFile(filename=f"ombor-qoldiqlari_{today:%Y-%m-%d}.xlsx", content=content)

    # -------------------------------------------------------------- statements --

    async def statements_zip(self, *, wholesaler_id: uuid.UUID, month: str) -> ZipExport:
        """Akt-sverka PDF for every active store for one calendar month, in one ZIP,
        plus `royxat.csv` (index: store, file, document code)."""
        today = await self.ledger.local_today(wholesaler_id)
        date_from, date_to = parse_month(month, today)
        stores = list(
            (
                await self.session.execute(
                    select(Store.id, Store.name)
                    .where(Store.wholesaler_id == wholesaler_id, Store.is_active.is_(True))
                    .order_by(Store.name)
                )
            )
            .tuples()
            .all()
        )
        if len(stores) > MAX_STATEMENTS_PER_ZIP:
            raise ExportTooLargeError(
                f"Too many stores for one archive (max {MAX_STATEMENTS_PER_ZIP})",
                stores=len(stores),
            )

        spool: IO[bytes] = tempfile.SpooledTemporaryFile(max_size=ZIP_SPOOL_MAX_BYTES)  # noqa: SIM115
        try:
            index = io.StringIO()
            writer = csv.writer(index)
            writer.writerow(["Do'kon", "Fayl", "Hujjat kodi", "Davr"])
            used: set[str] = set()
            with zipfile.ZipFile(
                spool, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as zf:
                for store_id, store_name in stores:
                    document = await self.pdf.build(
                        wholesaler_id=wholesaler_id,
                        store_id=store_id,
                        date_from=date_from,
                        date_to=date_to,
                    )
                    name = f"{_slug(store_name)}_{date_from:%Y-%m}.pdf"
                    if name in used:
                        name = f"{_slug(store_name)}_{str(store_id)[-6:]}_{date_from:%Y-%m}.pdf"
                    used.add(name)
                    await asyncio.to_thread(zf.writestr, name, document.content)
                    writer.writerow(
                        [
                            store_name,
                            name,
                            document.document_code,
                            f"{date_from:%d.%m.%Y}-{date_to:%d.%m.%Y}",
                        ]
                    )
                    del document  # keep at most one rendered PDF in memory
                # UTF-8 BOM so Excel opens the Cyrillic/Uzbek names correctly.
                zf.writestr("royxat.csv", UTF8_BOM + index.getvalue())
            size = spool.tell()
        except BaseException:
            spool.close()
            raise

        logger.info(
            "statements zip built",
            extra={
                "wholesaler_id": str(wholesaler_id),
                "month": month,
                "documents": len(stores),
                "bytes": size,
            },
        )
        return ZipExport(
            filename=f"statements_{date_from:%Y_%m}.zip",
            file=spool,
            size=size,
            documents=len(stores),
        )
