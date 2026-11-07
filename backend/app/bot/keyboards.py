from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)

from app.core.config import Settings
from app.services.directory_service import TelegramIdentity

SHARE_PHONE_TEXT = "📞 Telefon raqamni yuborish"


def open_app_button(settings: Settings, text: str) -> InlineKeyboardButton | None:
    """A web_app button, or None when WEBAPP_URL is not an https URL (Telegram rejects
    such buttons and the whole message would fail)."""
    if not settings.webapp_buttons_enabled or not settings.webapp_url:
        return None
    return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=settings.webapp_url))


def cabinet_keyboard(settings: Settings, identity: TelegramIdentity) -> InlineKeyboardMarkup | None:
    if identity.admin_of:
        label = "📊 Boshqaruv paneli"
    elif identity.agent_for and not identity.stores:
        label = "🧭 Agent kabineti"
    else:
        label = "📱 Nasiya Pro Kabineti"
    button = open_app_button(settings, label)
    return InlineKeyboardMarkup(inline_keyboard=[[button]]) if button else None


def single_app_button(settings: Settings, text: str) -> InlineKeyboardMarkup | None:
    button = open_app_button(settings, text)
    return InlineKeyboardMarkup(inline_keyboard=[[button]]) if button else None


def share_phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=SHARE_PHONE_TEXT, request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Telefon raqamingizni yuboring",
    )


REMOVE_KEYBOARD = ReplyKeyboardRemove()
