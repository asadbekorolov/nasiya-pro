"""Outbound notifications (Telegram). Kept behind a Protocol so services and tests do
not depend on the Bot API."""

from __future__ import annotations

import html
import logging
from decimal import Decimal
from typing import Protocol

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError

from app.core.exceptions import NotificationDeliveryError

logger = logging.getLogger(__name__)


def format_money(value: Decimal, currency: str = "UZS") -> str:
    return f"{value:,.2f}".replace(",", " ") + f" {currency}"


class OtpNotifier(Protocol):
    async def send_payment_otp(
        self,
        *,
        chat_id: int,
        code: str,
        amount: Decimal,
        currency: str,
        agent_name: str,
        ttl_seconds: int,
    ) -> int | None:
        """Deliver the code; return the Telegram message id if available."""
        ...


class LogOtpNotifier:
    """Development only: prints the code to the server log instead of Telegram."""

    async def send_payment_otp(
        self,
        *,
        chat_id: int,
        code: str,
        amount: Decimal,
        currency: str,
        agent_name: str,
        ttl_seconds: int,
    ) -> int | None:
        logger.warning(
            "DEV OTP for chat %s: %s (%s, agent %s)",
            chat_id,
            code,
            format_money(amount, currency),
            agent_name,
        )
        return None


class TelegramOtpNotifier:
    def __init__(self, bot_token: str) -> None:
        self._token = bot_token

    async def send_payment_otp(
        self,
        *,
        chat_id: int,
        code: str,
        amount: Decimal,
        currency: str,
        agent_name: str,
        ttl_seconds: int,
    ) -> int | None:
        text = (
            "🔐 <b>To'lovni tasdiqlash</b>\n\n"
            f"Agent: <b>{html.escape(agent_name)}</b>\n"
            f"Summa: <b>{format_money(amount, currency)}</b>\n\n"
            f"Tasdiqlash kodi: <code>{code}</code>\n\n"
            f"Kodni faqat pulni topshirganingizdan keyin agentga ayting. "
            f"Kod {ttl_seconds // 60} daqiqa amal qiladi."
        )
        bot = Bot(self._token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        try:
            message = await bot.send_message(chat_id=chat_id, text=text, protect_content=True)
        except TelegramAPIError as exc:
            logger.warning("payment OTP delivery failed: %s", exc.__class__.__name__)
            raise NotificationDeliveryError("Could not deliver the code to the store") from exc
        finally:
            await bot.session.close()
        return message.message_id
