"""/start and phone-number registration."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import messages
from app.bot.keyboards import (
    REMOVE_KEYBOARD,
    cabinet_keyboard,
    share_phone_keyboard,
    single_app_button,
)
from app.core.config import Settings
from app.services.directory_service import DirectoryService

logger = logging.getLogger(__name__)


async def cmd_start(message: Message, session: AsyncSession, settings: Settings) -> None:
    if message.from_user is None:
        return
    identity = await DirectoryService(session).identify(message.from_user.id)
    if not identity.is_registered:
        await message.answer(messages.UNREGISTERED, reply_markup=share_phone_keyboard())
        return
    await message.answer(
        messages.welcome(identity, app_available=settings.webapp_buttons_enabled),
        reply_markup=cabinet_keyboard(settings, identity) or REMOVE_KEYBOARD,
    )


async def on_contact(message: Message, session: AsyncSession, settings: Settings) -> None:
    user, contact = message.from_user, message.contact
    if user is None or contact is None:
        return
    # Only a contact the user shared about *themselves* proves they own the number:
    # anyone can forward somebody else's contact card.
    if contact.user_id != user.id:
        await message.answer(messages.CONTACT_NOT_OWN, reply_markup=share_phone_keyboard())
        return

    result = await DirectoryService(session).link_stores_by_phone(
        telegram_user_id=user.id, phone=contact.phone_number
    )
    stores = result.linked + result.already_linked
    if stores:
        await message.answer(messages.contact_linked(stores), reply_markup=REMOVE_KEYBOARD)
        markup = single_app_button(settings, "📱 Nasiya Pro Kabineti")
        if markup:
            await message.answer("Kabinetni ochish:", reply_markup=markup)
        return
    if result.taken:
        await message.answer(messages.CONTACT_TAKEN, reply_markup=REMOVE_KEYBOARD)
        return
    await message.answer(messages.CONTACT_NO_MATCH, reply_markup=REMOVE_KEYBOARD)


async def fallback(message: Message) -> None:
    await message.answer("Menyuni ochish uchun /start ni bosing.")


def create_router() -> Router:
    """A fresh router per Dispatcher (an aiogram Router can only have one parent)."""
    router = Router(name="start")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.message.register(cmd_start, CommandStart())
    router.message.register(on_contact, F.contact)
    router.message.register(fallback)
    return router
