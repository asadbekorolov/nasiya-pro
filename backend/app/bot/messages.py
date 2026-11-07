"""Every text the bot sends, as pure functions (HTML parse mode).

All user-controlled values (store/agent names, descriptions) go through `esc()`, so a
store called "<b>X</b>" cannot inject markup into someone else's chat.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from html import escape
from zoneinfo import ZoneInfo

from app.core.money import format_amount
from app.models import PaymentMethod
from app.services.directory_service import StoreRef, TelegramIdentity
from app.services.owner_service import first_name

PAYMENT_METHOD_LABEL = {
    PaymentMethod.CASH: "naqd pul",
    PaymentMethod.CARD: "plastik karta",
    PaymentMethod.BANK_TRANSFER: "bank o'tkazmasi",
    PaymentMethod.OTHER: "boshqa",
}


def esc(value: object) -> str:
    return escape(str(value), quote=False)


def som(value: Decimal) -> str:
    """1500000.00 -> "1 500 000 so'm";  1200.50 -> "1 200.50 so'm"."""
    text = format_amount(value)
    if text.endswith(".00"):
        text = text[:-3]
    return f"{text} so'm"


def local_time(value: datetime, tz: str) -> str:
    return value.astimezone(ZoneInfo(tz)).strftime("%d.%m.%Y %H:%M")


# --- /start ----------------------------------------------------------------------------


def admin_greeting(name: str | None) -> str:
    who = f"Boshqaruvchi {esc(name)}" if name else "Boshqaruvchi"
    return (
        f"👑 Xush kelibsiz, {who}! Nasiya Pro boshqaruv panelini ochish uchun quyidagi "
        "tugmani bosing."
    )


def welcome(
    identity: TelegramIdentity, *, app_available: bool, telegram_first_name: str | None = None
) -> str:
    parts: list[str] = []
    if identity.admin_of:
        parts.append(admin_greeting(first_name(identity.owner_name) or telegram_first_name))
    if identity.stores:
        parts.append(
            "Xush kelibsiz! Shaxsiy qarz balansingiz va tovar katalogini ko'rish uchun "
            "pastdagi tugmani bosing."
        )
        parts.append(_store_list(identity.stores))
    if identity.agent_for:
        parts.append(
            "<b>Xush kelibsiz, Savdo Agenti!</b>\n"
            "Marshrut, naqd pul qabul qilish va joyida buyurtma — Agent kabinetida.\n"
            f"Ulgurji sotuvchi: {esc(', '.join(identity.agent_for))}"
        )
    if not app_available:
        parts.append("<i>Mini App manzili hali sozlanmagan — administratorga murojaat qiling.</i>")
    return "\n\n".join(parts)


def _store_list(stores: Sequence[StoreRef]) -> str:
    lines = [f"• <b>{esc(s.name)}</b> — {esc(s.wholesaler_name)}" for s in stores]
    return "\n".join(lines)


UNREGISTERED = (
    "Assalomu alaykum! <b>Nasiya Pro</b> — ulgurji sotuvchingiz bilan buyurtma va "
    "hisob-kitoblar uchun xizmat.\n\n"
    "Siz hali tizimga ulanmagansiz. Do'koningizni ulash uchun pastdagi tugma orqali "
    "<b>telefon raqamingizni</b> yuboring — u ulgurji sotuvchingizdagi raqam bilan "
    "solishtiriladi."
)

CONTACT_NOT_OWN = (
    "Iltimos, boshqa odamning kontaktini emas, pastdagi tugma orqali "
    "<b>o'zingizning</b> raqamingizni yuboring."
)

CONTACT_NO_MATCH = (
    "Bu raqam bilan ro'yxatdan o'tgan do'kon topilmadi.\n\n"
    "Ulgurji sotuvchingizdan do'koningiz kartasiga aynan shu raqamni yozib qo'yishini "
    "so'rang, so'ng /start ni qayta bosing."
)

CONTACT_TAKEN = (
    "Bu raqamdagi do'kon boshqa Telegram akkauntga ulangan. Xavfsizlik uchun uni "
    "faqat ulgurji sotuvchingiz qayta ulashi mumkin."
)


def contact_linked(stores: Sequence[StoreRef]) -> str:
    return (
        "✅ <b>Telefon raqamingiz tasdiqlandi.</b>\n"
        "Quyidagi do'kon(lar) Telegram akkauntingizga ulandi:\n"
        f"{_store_list(stores)}\n\n"
        "Endi qarz balansingiz va katalogni Nasiya Pro kabinetida ko'rishingiz mumkin."
    )


# --- payment OTP -------------------------------------------------------------------------


def payment_otp(*, agent_name: str, amount: Decimal, code: str, ttl_seconds: int) -> str:
    minutes = max(1, round(ttl_seconds / 60))
    return (
        "⚠️ <b>Naqd pul qabul qilish so'rovi</b>\n\n"
        f"Savdo agenti: <b>{esc(agent_name)}</b>\n"
        f"Qabul qilinayotgan summa: <b>{som(amount)}</b>\n\n"
        "Sizning tasdiqlash kodingiz:\n"
        f"🔢 <code>{esc(code)}</code> <i>(nusxalash uchun bosing)</i>\n\n"
        f"⏳ <i>Kod {minutes} daqiqa davomida amal qiladi. Agar agentga pul bermayotgan "
        "bo'lsangiz, kodni unga aytmang!</i>"
    )


# --- push notifications ----------------------------------------------------------------


def new_order(
    *,
    order_number: int,
    store_name: str,
    total: Decimal,
    items: int,
    placed_by: str,
    note: str | None,
) -> str:
    text = (
        f"🛒 <b>Yangi buyurtma #{order_number}</b>\n\n"
        f"Do'kon: <b>{esc(store_name)}</b>\n"
        f"Summa: <b>{som(total)}</b>\n"
        f"Mahsulotlar: {items} xil\n"
        f"Buyurtma berdi: {esc(placed_by)}"
    )
    if note:
        text += f"\nIzoh: <i>{esc(note)}</i>"
    return text + "\n\nTasdiqlash uchun kabinetni oching."


def order_delivered(*, order_number: int, total: Decimal, new_debt: Decimal) -> str:
    balance = (
        f"Yangi qarz balansingiz: <b>{som(new_debt)}</b>"
        if new_debt >= 0
        else f"Oldindan to'lovingiz qoldig'i: <b>{som(-new_debt)}</b>"
    )
    return (
        f"📦 <b>Buyurtmangiz #{order_number} yetkazildi</b>\n\n"
        f"Buyurtma summasi: {som(total)}\n"
        f"{balance}"
    )


def payment_received(
    *,
    amount: Decimal,
    current_debt: Decimal,
    method: PaymentMethod | None,
    receipt_no: int,
    receipt_hash: str,
    paid_at: str,
    agent_name: str | None,
) -> str:
    balance = (
        f"Joriy qarz qoldig'i: <b>{som(current_debt)}</b>"
        if current_debt >= 0
        else f"Oldindan to'lov (avans): <b>{som(-current_debt)}</b>"
    )
    lines = [
        f"✅ <b>To'lov qabul qilindi: {som(amount)}</b>",
        balance,
        "",
        f"Kvitansiya: № {receipt_no} · <code>{esc(receipt_hash[:8].upper())}</code>",
        f"Sana: {esc(paid_at)}",
        f"To'lov turi: {PAYMENT_METHOD_LABEL.get(method or PaymentMethod.OTHER, 'boshqa')}",
    ]
    if agent_name:
        lines.append(f"Qabul qildi: {esc(agent_name)}")
    return "\n".join(lines)
