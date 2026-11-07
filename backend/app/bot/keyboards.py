from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MenuButtonWebApp,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)

from app.core.config import Settings
from app.services.directory_service import TelegramIdentity

SHARE_PHONE_TEXT = "📞 Telefon raqamni yuborish"
MENU_BUTTON_TEXT = "📱 Nasiya Pro"


# Mini App sections; the frontend opens the matching cabinet for these paths.
ADMIN_PATH, AGENT_PATH, STORE_PATH = "/admin", "/agent", "/store"


def app_url(settings: Settings, path: str = "") -> str | None:
    """WEBAPP_URL (+ section path), or None when it is not https: Telegram rejects
    web_app buttons with other URLs and the whole message would fail."""
    if not settings.webapp_buttons_enabled or not settings.webapp_url:
        return None
    return settings.webapp_url.rstrip("/") + path


def home_path(identity: TelegramIdentity) -> str:
    """The section a person lands in: admin > agent > store."""
    if identity.admin_of:
        return ADMIN_PATH
    if identity.agent_for:
        return AGENT_PATH
    if identity.stores:
        return STORE_PATH
    return ""


def menu_button(settings: Settings, path: str = "") -> MenuButtonWebApp | None:
    url = app_url(settings, path)
    if url is None:
        return None
    return MenuButtonWebApp(text=MENU_BUTTON_TEXT, web_app=WebAppInfo(url=url))


def open_app_button(settings: Settings, text: str, path: str = "") -> InlineKeyboardButton | None:
    url = app_url(settings, path)
    return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url)) if url else None


def cabinet_keyboard(settings: Settings, identity: TelegramIdentity) -> InlineKeyboardMarkup | None:
    path = home_path(identity)
    label = {
        ADMIN_PATH: "👑 Boshqaruv paneli",
        AGENT_PATH: "🧭 Agent kabineti",
    }.get(path, "📱 Nasiya Pro Kabineti")
    button = open_app_button(settings, label, path)
    return InlineKeyboardMarkup(inline_keyboard=[[button]]) if button else None


def single_app_button(settings: Settings, text: str, path: str = "") -> InlineKeyboardMarkup | None:
    button = open_app_button(settings, text, path)
    return InlineKeyboardMarkup(inline_keyboard=[[button]]) if button else None


def share_phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=SHARE_PHONE_TEXT, request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Telefon raqamingizni yuboring",
    )


REMOVE_KEYBOARD = ReplyKeyboardRemove()
