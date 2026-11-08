"""Excel (.xlsx) onboarding: bulk import of the product catalog and of stores with their
opening debts, plus the blank templates wholesalers fill in.

Rules:
* Every row is validated first; a bad row never stops the good ones. Errors come back
  with the exact Excel row number, column and reason.
* All good rows are written in ONE database transaction (all-or-nothing): if anything
  unexpected fails, nothing from the file is kept.
* `dry_run=True` runs the same validation (including checks against the database) and
  writes nothing, so the UI can show a preview before the real import.
* Opening debts go through `LedgerService.record_adjustment` — the same append-only,
  hash-chained ledger path as any other correction — never by editing balances.
* Imports of one tenant are serialised with a transaction-scoped advisory lock, so two
  uploads of the same file cannot create the same store (and its debt) twice.
"""

from __future__ import annotations

import io
import logging
import re
import uuid
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.exceptions import DomainError
from app.db.uow import atomic
from app.models import Product, Store
from app.models.enums import ProductUnit
from app.services.directory_service import normalize_phone
from app.services.ledger_service import LedgerService

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_DATA_ROWS = 5000
OPENING_DEBT_REASON = "Boshlang'ich qarz (Excel import)"

ImportKind = Literal["products", "stores"]


class ImportFileError(DomainError):
    """The file itself cannot be used (not .xlsx, too big, no header, ...)."""

    code = "invalid_import_file"
    status_code = 422


# --- columns -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Column:
    key: str
    title: str  # exactly as written in the template
    required: bool = False
    aliases: tuple[str, ...] = ()
    width: int = 20
    hint: str = ""


PRODUCT_COLUMNS: tuple[Column, ...] = (
    Column(
        "name",
        "Nomi",
        True,
        ("tovar nomi", "tovar", "nomlanishi", "name"),
        36,
        "Majburiy. Tovar nomi, 255 belgigacha.",
    ),
    Column(
        "sku",
        "SKU / Artikul",
        False,
        ("sku", "artikul", "kod", "shtrix kod"),
        18,
        "Ixtiyoriy, lekin tavsiya etiladi: shu kod bo'yicha mavjud tovar yangilanadi. "
        "Bo'sh bo'lsa, tovar nomi bo'yicha qidiriladi.",
    ),
    Column(
        "price",
        "Narxi (so'm)",
        True,
        ("narxi", "narx", "price"),
        16,
        "Majburiy. Sotuv narxi, masalan 125000 yoki 12500,50.",
    ),
    Column(
        "stock",
        "Boshlang'ich qoldiq (dona/kg)",
        False,
        ("boshlang'ich qoldiq", "qoldiq", "ombordagi qoldiq", "soni", "stock"),
        22,
        "Ombordagi miqdor (3 xonagacha kasr). Bo'sh = 0. Mavjud tovarda qoldiq shu "
        "songa almashtiriladi.",
    ),
    Column(
        "unit",
        "O'lchov birligi",
        False,
        ("birlik", "o'lchov", "unit"),
        16,
        "Ixtiyoriy: dona, kg, l, m, quti, pachka, blok. Bo'sh = dona.",
    ),
)

STORE_COLUMNS: tuple[Column, ...] = (
    Column(
        "name",
        "Do'kon nomi",
        True,
        ("do'kon", "store"),
        30,
        "Majburiy. Bir xil nomli do'kon ikki marta yaratilmaydi.",
    ),
    Column(
        "contact_name",
        "Mas'ul shaxs",
        False,
        ("mas'ul", "egasi", "ism", "fio"),
        24,
        "Ixtiyoriy. Do'kon egasi yoki mas'ul xodim.",
    ),
    Column(
        "phone",
        "Telefon raqam",
        False,
        ("telefon", "tel", "phone"),
        18,
        "Ixtiyoriy, lekin tavsiya etiladi: do'kon egasi botda shu raqamni yuborsa, "
        "kabinetiga avtomatik ulanadi. Masalan +998 90 123 45 67.",
    ),
    Column("address", "Manzil", False, ("manzil", "address"), 34, "Ixtiyoriy."),
    Column(
        "credit_limit",
        "Kredit limiti",
        False,
        ("limit", "kredit limiti (so'm)"),
        16,
        "Nasiyaga beriladigan eng ko'p summa (so'm). Bo'sh = 0.",
    ),
    Column(
        "opening_debt",
        "Boshlang'ich qarz (so'm)",
        False,
        ("boshlang'ich qarz", "qarz", "joriy qarz", "debt"),
        20,
        "Do'konning hozirgi qarzi. 0 dan katta bo'lsa, hisobga dastlabki qarz sifatida "
        "yoziladi (akt-sverkada ko'rinadi).",
    ),
    Column(
        "due_date",
        "Qarz muddati",
        False,
        ("to'lov muddati", "muddati", "due date"),
        16,
        "Ixtiyoriy: qarzni to'lash sanasi (kk.oo.yyyy). Bo'sh bo'lsa, do'konning "
        "to'lov muddati bo'yicha hisoblanadi.",
    ),
)

COLUMNS: dict[ImportKind, tuple[Column, ...]] = {
    "products": PRODUCT_COLUMNS,
    "stores": STORE_COLUMNS,
}

UNIT_WORDS: dict[str, ProductUnit] = {
    "dona": ProductUnit.PIECE,
    "pcs": ProductUnit.PIECE,
    "ta": ProductUnit.PIECE,
    "sht": ProductUnit.PIECE,
    "шт": ProductUnit.PIECE,
    "kg": ProductUnit.KILOGRAM,
    "кг": ProductUnit.KILOGRAM,
    "l": ProductUnit.LITER,
    "litr": ProductUnit.LITER,
    "л": ProductUnit.LITER,
    "m": ProductUnit.METER,
    "metr": ProductUnit.METER,
    "quti": ProductUnit.BOX,
    "box": ProductUnit.BOX,
    "karobka": ProductUnit.BOX,
    "pachka": ProductUnit.PACK,
    "pack": ProductUnit.PACK,
    "blok": ProductUnit.BLOCK,
    "block": ProductUnit.BLOCK,
}


# Headers that only ever appear in one template: guards against uploading the products
# file as stores (which would turn every product into a store) and vice versa.
_MARKERS: dict[ImportKind, set[str]] = {
    "products": {"narxi", "narx", "sku / artikul", "artikul", "o'lchov birligi"},
    "stores": {"do'kon nomi", "boshlang'ich qarz", "kredit limiti", "mas'ul shaxs"},
}


# --- results -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RowError:
    row: int  # Excel row number, as the user sees it
    column: str | None
    message: str


@dataclass(slots=True)
class ImportResult:
    kind: ImportKind
    dry_run: bool
    total_rows: int = 0
    created: int = 0
    updated: int = 0
    opening_debt_total: Decimal = Decimal("0.00")
    errors: list[RowError] = field(default_factory=list)

    @property
    def imported(self) -> int:
        return self.created + self.updated

    @property
    def failed_rows(self) -> int:
        return len({e.row for e in self.errors})


# --- cell parsing ------------------------------------------------------------------------


class CellError(ValueError):
    pass


# Uzbek Latin is typed with many apostrophe look-alikes: fold them all to a plain quote.
_APOSTROPHES = str.maketrans(
    dict.fromkeys(map(chr, (0x02BB, 0x02BC, 0x2018, 0x2019, 0x60, 0xB4)), "'")
)
NBSP = chr(0xA0)


def _norm_header(value: Any) -> str:
    raw = str(value or "").translate(_APOSTROPHES).lower()
    raw = re.sub(r"\(.*?\)", " ", raw)  # "Narxi (so'm)" -> "narxi"
    return re.sub(r"\s+", " ", raw).strip(" :*")


def _text(value: Any, max_len: int) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise CellError("matn kutilgan")
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # SKU 1234 typed as a number
    out = re.sub(r"\s+", " ", str(value)).strip()
    if not out:
        return None
    if len(out) > max_len:
        raise CellError(f"{max_len} belgidan uzun")
    return out


def _decimal(value: Any, places: int, what: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise CellError(f"{what} son bo'lishi kerak")
    if isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        # Excel stores numbers as doubles: repr gives the shortest exact-looking form.
        number = Decimal(repr(value))
    else:
        raw = str(value).translate(_APOSTROPHES).lower().replace(NBSP, "").replace(" ", "")
        raw = re.sub(r"(so'm|сум|sum|uzs)$", "", raw).replace("'", "")  # 1'250'000
        if not raw:
            return None
        if "," in raw and "." in raw:
            raw = raw.replace(",", "")  # 1,250,000.50
        elif raw.count(",") == 1:
            raw = raw.replace(",", ".")  # 12500,50
        elif raw.count(",") > 1:
            raw = raw.replace(",", "")  # 1,250,000
        try:
            number = Decimal(raw)
        except InvalidOperation:
            raise CellError(f"{what} son bo'lishi kerak: {value!r}") from None
    if not number.is_finite():
        raise CellError(f"{what} son bo'lishi kerak")
    if number < 0:
        raise CellError(f"{what} manfiy bo'lishi mumkin emas")
    quantum = Decimal(1).scaleb(-places)
    if number != number.quantize(quantum):
        raise CellError(f"{what}: verguldan keyin ko'pi bilan {places} xona")
    number = number.quantize(quantum)
    if number >= Decimal(10) ** (15 - places):
        raise CellError(f"{what} juda katta")
    return number


def _money(value: Any, what: str) -> Decimal | None:
    return _decimal(value, 2, what)


def _quantity(value: Any, what: str) -> Decimal | None:
    return _decimal(value, 3, what)


def _date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise CellError("sana noto'g'ri, masalan 31.12.2026")


def _unit(value: Any) -> ProductUnit | None:
    word = _text(value, 20)
    if word is None:
        return None
    unit = UNIT_WORDS.get(word.lower().rstrip("."))
    if unit is None:
        raise CellError("noma'lum birlik (dona, kg, l, m, quti, pachka, blok)")
    return unit


# --- reading the workbook ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SheetRow:
    number: int
    values: dict[str, Any]


def read_rows(data: bytes, kind: ImportKind) -> tuple[list[SheetRow], int]:
    """Rows of the first sheet keyed by column key. Returns (rows, data_row_count)."""
    if not data:
        raise ImportFileError("Fayl bo'sh")
    if len(data) > MAX_FILE_BYTES:
        raise ImportFileError(f"Fayl juda katta (ko'pi bilan {MAX_FILE_BYTES // (1024 * 1024)} MB)")
    if not data.startswith(b"PK"):
        raise ImportFileError(
            "Fayl .xlsx formatida emas. Excelda «Saqlash» -> «Excel kitobi (.xlsx)» ni tanlang"
        )
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (InvalidFileException, zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
        raise ImportFileError(
            "Excel faylini o'qib bo'lmadi (fayl buzilgan bo'lishi mumkin)"
        ) from exc

    try:
        sheet = workbook.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        header = next(rows, None)
        columns = COLUMNS[kind]
        index: dict[str, int] = {}
        for position, cell in enumerate(header or ()):
            name = _norm_header(cell)
            if not name:
                continue
            for col in columns:
                if col.key not in index and name in (_norm_header(col.title), *col.aliases):
                    index[col.key] = position
                    break
        headers = {_norm_header(cell) for cell in header or ()}
        if headers & _MARKERS["stores" if kind == "products" else "products"]:
            other = "do'konlar" if kind == "products" else "tovarlar"
            raise ImportFileError(
                f"Bu fayl {other} namunasiga o'xshaydi. Uni «{other.capitalize()}» importi "
                "orqali yuklang."
            )
        missing = [c.title for c in columns if c.required and c.key not in index]
        if missing:
            raise ImportFileError(
                "1-qatorda kerakli ustunlar topilmadi: "
                + ", ".join(f"«{m}»" for m in missing)
                + ". Namuna faylidan foydalaning.",
                missing_columns=missing,
            )

        out: list[SheetRow] = []
        count = 0
        for number, values in enumerate(rows, start=2):
            if values is None or all(v is None or str(v).strip() == "" for v in values):
                continue
            count += 1
            if count > MAX_DATA_ROWS:
                raise ImportFileError(f"Juda ko'p qator: bir faylda ko'pi bilan {MAX_DATA_ROWS} ta")
            out.append(
                SheetRow(
                    number=number,
                    values={
                        key: values[pos] if pos < len(values) else None
                        for key, pos in index.items()
                    },
                )
            )
        return out, count
    finally:
        workbook.close()


# --- parsed rows ---------------------------------------------------------------------------


@dataclass(slots=True)
class ProductRow:
    row: int
    name: str
    sku: str | None
    price: Decimal
    stock: Decimal
    unit: ProductUnit | None


@dataclass(slots=True)
class StoreRow:
    row: int
    name: str
    contact_name: str | None
    phone: str | None
    address: str | None
    credit_limit: Decimal
    opening_debt: Decimal
    due_date: date | None


def _cells(
    row: SheetRow,
    kind: ImportKind,
    parsers: dict[str, Callable[[Any], Any]],
    errors: list[RowError],
) -> dict[str, Any] | None:
    titles = {c.key: c.title for c in COLUMNS[kind]}
    out: dict[str, Any] = {}
    ok = True
    for key, parse in parsers.items():
        try:
            out[key] = parse(row.values.get(key))
        except CellError as exc:
            errors.append(RowError(row.number, titles[key], str(exc)))
            ok = False
    return out if ok else None


def parse_products(rows: Sequence[SheetRow], errors: list[RowError]) -> list[ProductRow]:
    parsed: list[ProductRow] = []
    seen: dict[str, int] = {}
    for r in rows:
        cells = _cells(
            r,
            "products",
            {
                "name": lambda v: _text(v, 255),
                "sku": lambda v: _text(v, 64),
                "price": lambda v: _money(v, "Narx"),
                "stock": lambda v: _quantity(v, "Qoldiq"),
                "unit": _unit,
            },
            errors,
        )
        if cells is None:
            continue
        if not cells["name"]:
            errors.append(RowError(r.number, "Nomi", "tovar nomi bo'sh"))
            continue
        if cells["price"] is None:
            errors.append(RowError(r.number, "Narxi (so'm)", "narx ko'rsatilmagan"))
            continue
        key = (
            f"sku:{cells['sku'].casefold()}" if cells["sku"] else f"name:{cells['name'].casefold()}"
        )
        if key in seen:
            column = "SKU / Artikul" if cells["sku"] else "Nomi"
            errors.append(
                RowError(r.number, column, f"fayl ichida takrorlangan ({seen[key]}-qator bilan)")
            )
            continue
        seen[key] = r.number
        parsed.append(
            ProductRow(
                row=r.number,
                name=cells["name"],
                sku=cells["sku"],
                price=cells["price"],
                stock=cells["stock"] or Decimal("0.000"),
                unit=cells["unit"],
            )
        )
    return parsed


def parse_stores(rows: Sequence[SheetRow], errors: list[RowError]) -> list[StoreRow]:
    parsed: list[StoreRow] = []
    seen: dict[str, int] = {}
    for r in rows:
        cells = _cells(
            r,
            "stores",
            {
                "name": lambda v: _text(v, 200),
                "contact_name": lambda v: _text(v, 200),
                "phone": lambda v: _text(v, 40),
                "address": lambda v: _text(v, 1000),
                "credit_limit": lambda v: _money(v, "Kredit limiti"),
                "opening_debt": lambda v: _money(v, "Qarz"),
                "due_date": _date,
            },
            errors,
        )
        if cells is None:
            continue
        if not cells["name"]:
            errors.append(RowError(r.number, "Do'kon nomi", "do'kon nomi bo'sh"))
            continue
        phone = None
        if cells["phone"]:
            phone = normalize_phone(cells["phone"])
            if phone is None:
                errors.append(RowError(r.number, "Telefon raqam", "telefon raqam noto'g'ri"))
                continue
        key = cells["name"].casefold()
        if key in seen:
            errors.append(
                RowError(
                    r.number, "Do'kon nomi", f"fayl ichida takrorlangan ({seen[key]}-qator bilan)"
                )
            )
            continue
        seen[key] = r.number
        parsed.append(
            StoreRow(
                row=r.number,
                name=cells["name"],
                contact_name=cells["contact_name"],
                phone=f"+{phone}" if phone else None,
                address=cells["address"],
                credit_limit=cells["credit_limit"] or Decimal("0.00"),
                opening_debt=cells["opening_debt"] or Decimal("0.00"),
                due_date=cells["due_date"],
            )
        )
    return parsed


# --- service -----------------------------------------------------------------------------


class ExcelImportService:
    def __init__(self, session: AsyncSession, ledger: LedgerService) -> None:
        self.session = session
        self.ledger = ledger

    async def _lock_tenant_imports(self, wholesaler_id: uuid.UUID) -> None:
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"nasiya-import:{wholesaler_id}"},
        )

    # ------------------------------------------------------------------ products --

    async def import_products(
        self, *, wholesaler_id: uuid.UUID, data: bytes, dry_run: bool = False
    ) -> ImportResult:
        sheet_rows, total = read_rows(data, "products")
        result = ImportResult(kind="products", dry_run=dry_run, total_rows=total)
        rows = parse_products(sheet_rows, result.errors)

        async with atomic(self.session):
            await self._lock_tenant_imports(wholesaler_id)
            by_sku, by_name = await self._existing_products(wholesaler_id, rows)

            for r in rows:
                product = by_sku.get(r.sku.casefold()) if r.sku else by_name.get(r.name.casefold())
                if product is None:
                    if not dry_run:
                        self.session.add(
                            Product(
                                wholesaler_id=wholesaler_id,
                                sku=r.sku,
                                name=r.name,
                                price=r.price,
                                stock_quantity=r.stock,
                                unit=r.unit or ProductUnit.PIECE,
                            )
                        )
                    result.created += 1
                    continue
                if r.stock < product.reserved_quantity:
                    result.errors.append(
                        RowError(
                            r.row,
                            "Boshlang'ich qoldiq (dona/kg)",
                            f"qoldiq tasdiqlangan buyurtmalarga band qilingan miqdordan "
                            f"({product.reserved_quantity.normalize():f}) kam bo'lishi mumkin emas",
                        )
                    )
                    continue
                if not dry_run:
                    product.name = r.name
                    product.price = r.price
                    product.stock_quantity = r.stock
                    if r.unit is not None:
                        product.unit = r.unit
                result.updated += 1
            await self.session.flush()

        result.errors.sort(key=lambda e: e.row)
        logger.info(
            "products import",
            extra={
                "wholesaler_id": str(wholesaler_id),
                "dry_run": dry_run,
                "rows_created": result.created,
                "rows_updated": result.updated,
                "row_errors": len(result.errors),
            },
        )
        return result

    async def _existing_products(
        self, wholesaler_id: uuid.UUID, rows: Sequence[ProductRow]
    ) -> tuple[dict[str, Product], dict[str, Product]]:
        """Matching products, row-locked in id order (the global lock order)."""
        skus = {r.sku.casefold() for r in rows if r.sku}
        names = {r.name.casefold() for r in rows if not r.sku}
        if not skus and not names:
            return {}, {}
        conditions: list[ColumnElement[bool]] = []
        if skus:
            conditions.append(func.lower(Product.sku).in_(skus))
        if names:
            conditions.append(Product.sku.is_(None) & func.lower(Product.name).in_(names))
        products = (
            await self.session.scalars(
                select(Product)
                .where(Product.wholesaler_id == wholesaler_id, or_(*conditions))
                .order_by(Product.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
        by_sku = {p.sku.casefold(): p for p in products if p.sku}
        by_name: dict[str, Product] = {}
        for p in products:
            if p.sku is None:
                by_name.setdefault(p.name.casefold(), p)
        return by_sku, by_name

    # -------------------------------------------------------------------- stores --

    async def import_stores(
        self,
        *,
        wholesaler_id: uuid.UUID,
        data: bytes,
        dry_run: bool = False,
        actor_telegram_user_id: int | None = None,
    ) -> ImportResult:
        sheet_rows, total = read_rows(data, "stores")
        result = ImportResult(kind="stores", dry_run=dry_run, total_rows=total)
        rows = parse_stores(sheet_rows, result.errors)

        async with atomic(self.session):
            await self._lock_tenant_imports(wholesaler_id)
            existing = (
                set(
                    (
                        await self.session.scalars(
                            select(func.lower(Store.name)).where(
                                Store.wholesaler_id == wholesaler_id,
                                func.lower(Store.name).in_({r.name.casefold() for r in rows}),
                            )
                        )
                    ).all()
                )
                if rows
                else set()
            )

            created: list[tuple[StoreRow, Store]] = []
            for r in rows:
                if r.name.casefold() in existing:
                    result.errors.append(
                        RowError(r.row, "Do'kon nomi", "bunday nomli do'kon allaqachon mavjud")
                    )
                    continue
                result.created += 1
                result.opening_debt_total += r.opening_debt
                if dry_run:
                    continue
                store = Store(
                    wholesaler_id=wholesaler_id,
                    name=r.name,
                    contact_name=r.contact_name,
                    phone=r.phone,
                    address=r.address,
                    credit_limit=r.credit_limit,
                )
                self.session.add(store)
                created.append((r, store))
            await self.session.flush()

            # Opening balances: one ledger adjustment per store, in the same transaction.
            for r, store in created:
                if r.opening_debt > 0:
                    await self.ledger.record_adjustment(
                        wholesaler_id=wholesaler_id,
                        store_id=store.id,
                        amount=r.opening_debt,
                        reason=OPENING_DEBT_REASON,
                        due_date=r.due_date,
                        idempotency_key=f"import-opening-{store.id}",
                        actor_telegram_user_id=actor_telegram_user_id,
                    )

        result.errors.sort(key=lambda e: e.row)
        logger.info(
            "stores import",
            extra={
                "wholesaler_id": str(wholesaler_id),
                "dry_run": dry_run,
                "rows_created": result.created,
                "opening_debt": str(result.opening_debt_total),
                "row_errors": len(result.errors),
            },
        )
        return result


# --- templates ---------------------------------------------------------------------------

_EXAMPLES: dict[ImportKind, list[list[Any]]] = {
    "products": [
        ["Un, oliy nav, 50 kg", "UN-50", 385000, 120, "dona"],
        ["Shakar, 1 kg", "SH-1", 13500, 800, "kg"],
        ["Ko'k choy, 100 g", "CH-100", 168000, 40, "blok"],
    ],
    "stores": [
        [
            "Baraka market",
            "Akmal Karimov",
            "+998 90 123 45 67",
            "Samarqand, Registon ko'chasi, 12",
            15000000,
            5630000,
            None,
        ],
        [
            "Oila do'koni",
            "Dilnoza Rahimova",
            "+998 91 234 56 78",
            "Samarqand, Bog'ishamol, 21",
            5000000,
            0,
            None,
        ],
    ],
}

_SHEET_TITLE: dict[ImportKind, str] = {"products": "Tovarlar", "stores": "Do'konlar"}


def build_template(kind: ImportKind) -> bytes:
    """A ready-to-fill workbook: data sheet with headers and sample rows + a guide."""
    columns = COLUMNS[kind]
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = _SHEET_TITLE[kind]

    header_fill = PatternFill("solid", fgColor="2481CC")
    required_fill = PatternFill("solid", fgColor="1A5F99")
    for i, col in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=i, value=col.title)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = required_fill if col.required else header_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = col.width
    ws.row_dimensions[1].height = 32
    ws.freeze_panes = "A2"

    for r, values in enumerate(_EXAMPLES[kind], start=2):
        for c, value in enumerate(values, start=1):
            ws.cell(row=r, column=c, value=value)

    keys = [c.key for c in columns]
    last_row = MAX_DATA_ROWS + 1
    for key in ("price", "credit_limit", "opening_debt"):
        if key in keys:
            letter = get_column_letter(keys.index(key) + 1)
            for row in ws.iter_rows(
                min_row=2,
                max_row=len(_EXAMPLES[kind]) + 1,
                min_col=keys.index(key) + 1,
                max_col=keys.index(key) + 1,
            ):
                for money_cell in row:
                    money_cell.number_format = "#,##0"
            dv = DataValidation(
                type="decimal",
                operator="greaterThanOrEqual",
                formula1="0",
                allow_blank=True,
                showErrorMessage=True,
                error="Faqat 0 yoki musbat son kiriting",
            )
            dv.add(f"{letter}2:{letter}{last_row}")
            ws.add_data_validation(dv)
    if "unit" in keys:
        letter = get_column_letter(keys.index("unit") + 1)
        dv = DataValidation(
            type="list", formula1='"dona,kg,l,m,quti,pachka,blok"', allow_blank=True
        )
        dv.add(f"{letter}2:{letter}{last_row}")
        ws.add_data_validation(dv)
    if "due_date" in keys:
        col_no = keys.index("due_date") + 1
        for row in ws.iter_rows(min_row=2, max_row=200, min_col=col_no, max_col=col_no):
            for date_cell in row:
                date_cell.number_format = "DD.MM.YYYY"

    guide = wb.create_sheet("Yo'riqnoma")
    guide.column_dimensions["A"].width = 30
    guide.column_dimensions["B"].width = 90
    guide["A1"] = "Nasiya Pro — " + (
        "tovarlar importi" if kind == "products" else "do'konlar va qarzlar importi"
    )
    guide["A1"].font = Font(bold=True, size=14)
    guide["A2"] = (
        "Birinchi varaqni to'ldiring: 1-qatordagi sarlavhalarni o'zgartirmang, namuna "
        "qatorlarini o'z ma'lumotlaringiz bilan almashtiring. Faylni .xlsx holida saqlab, "
        "Nasiya Pro boshqaruv panelidagi «Import» bo'limiga yuklang."
    )
    guide["A2"].alignment = Alignment(wrap_text=True)
    guide.merge_cells("A2:B2")
    guide.row_dimensions[2].height = 45
    guide["A4"], guide["B4"] = "Ustun", "Qanday to'ldiriladi"
    guide["A4"].font = guide["B4"].font = Font(bold=True)
    for i, col in enumerate(columns, start=5):
        guide.cell(row=i, column=1, value=col.title + (" *" if col.required else ""))
        hint = guide.cell(row=i, column=2, value=col.hint)
        hint.alignment = Alignment(wrap_text=True, vertical="top")
    footer = len(columns) + 6
    guide.cell(row=footer, column=1, value="* — majburiy ustun.")
    guide.cell(
        row=footer + 1,
        column=1,
        value=(
            f"Bir faylda {MAX_DATA_ROWS} tagacha qator. Xato qatorlar kiritilmaydi — panel "
            "ularning raqami va sababini ko'rsatadi. To'g'rilab, faylni qayta yuklashingiz "
            "mumkin: tovarlar SKU bo'yicha yangilanadi, mavjud do'konlar (va ularning "
            "qarzi) qayta yaratilmaydi."
        ),
    )

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def template_filename(kind: ImportKind) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return f"nasiya-pro-{'tovarlar' if kind == 'products' else 'dokonlar'}-namuna-{stamp}.xlsx"
