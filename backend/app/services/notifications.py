"""Notification ports. Services and API code depend on these Protocols, never on the
Bot API directly; the Telegram implementation lives in `app.bot.notifications`."""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Protocol

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
        """Deliver the code or raise NotificationDeliveryError; returns the message id."""
        ...


class EventNotifier(Protocol):
    """Best-effort push notifications, called after the DB transaction committed."""

    async def order_submitted(self, order_id: uuid.UUID) -> None: ...

    async def order_delivered(self, order_id: uuid.UUID) -> None: ...

    async def payment_received(self, transaction_id: uuid.UUID) -> None: ...


class LogOtpNotifier:
    """Development only (OTP_DELIVERY=log): prints the code to the server log."""

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


class NullEventNotifier:
    """Used when the bot runtime is not running (e.g. scripts, BOT_MODE=off without a
    configured bot): events are only logged."""

    async def order_submitted(self, order_id: uuid.UUID) -> None:
        logger.debug("event order_submitted %s (no bot)", order_id)

    async def order_delivered(self, order_id: uuid.UUID) -> None:
        logger.debug("event order_delivered %s (no bot)", order_id)

    async def payment_received(self, transaction_id: uuid.UUID) -> None:
        logger.debug("event payment_received %s (no bot)", transaction_id)
