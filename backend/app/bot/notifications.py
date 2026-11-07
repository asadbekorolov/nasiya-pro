"""Outbound Telegram messages: payment OTP codes and push notifications.

Two classes of messages:
* critical (payment OTP) — failure raises `NotificationDeliveryError`, the API answers
  502 and the code is cancelled;
* informational (new order, delivered, payment receipt) — sent from FastAPI background
  tasks *after* the DB transaction committed; failures are logged, never raised, so a
  blocked bot or a Telegram outage can never break or roll back business operations.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from decimal import Decimal

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import messages
from app.bot.keyboards import single_app_button
from app.core.config import Settings
from app.core.exceptions import NotificationDeliveryError
from app.models import (
    Agent,
    AgentStoreAssignment,
    LedgerTransaction,
    Order,
    OrderItem,
    Store,
    TransactionType,
    Wholesaler,
)

logger = logging.getLogger(__name__)

MAX_RETRY_AFTER_SECONDS = 5


class BotNotifier:
    def __init__(
        self,
        bot: Bot | None,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self.bot = bot
        self.sessionmaker = sessionmaker
        self.settings = settings

    # --- transport -------------------------------------------------------------------

    async def send(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
        protect_content: bool = False,
        critical: bool = False,
    ) -> int | None:
        """Send one message; returns its message_id, or None if it could not be sent."""
        if self.bot is None:
            if critical:
                raise NotificationDeliveryError("Telegram bot is not configured")
            logger.debug("bot disabled; dropped message to %s", chat_id)
            return None

        for attempt in (1, 2):
            try:
                sent = await self.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    protect_content=protect_content,
                    disable_web_page_preview=True,
                )
                return sent.message_id
            except TelegramRetryAfter as exc:
                if attempt == 1 and exc.retry_after <= MAX_RETRY_AFTER_SECONDS:
                    await asyncio.sleep(exc.retry_after)
                    continue
                reason = f"flood control ({exc.retry_after}s)"
            except TelegramForbiddenError:
                reason = "user blocked the bot or never started it"
            except TelegramBadRequest as exc:
                reason = f"bad request: {exc.message}"
            except TelegramNetworkError as exc:
                reason = f"network error: {exc.message}"
            except TelegramAPIError as exc:
                reason = f"telegram error: {exc.message}"
            break

        logger.warning("telegram message to %s not delivered: %s", chat_id, reason)
        if critical:
            raise NotificationDeliveryError(
                "Could not deliver the message via Telegram", reason=reason
            )
        return None

    # --- payment OTP (critical) --------------------------------------------------------

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
        text = messages.payment_otp(
            agent_name=agent_name, amount=amount, code=code, ttl_seconds=ttl_seconds
        )
        # protect_content: the code cannot be forwarded or saved from the chat.
        return await self.send(chat_id, text, protect_content=True, critical=True)

    # --- push notifications (best effort) ----------------------------------------------

    async def order_submitted(self, order_id: uuid.UUID) -> None:
        """New order waiting for approval -> wholesaler admin + the store's agents
        (except whoever placed it)."""
        try:
            async with self.sessionmaker() as session:
                row = (
                    await session.execute(
                        select(Order, Store.name, Wholesaler.owner_telegram_user_id)
                        .join(Store, Store.id == Order.store_id)
                        .join(Wholesaler, Wholesaler.id == Order.wholesaler_id)
                        .where(Order.id == order_id)
                    )
                ).one_or_none()
                if row is None:
                    return
                order, store_name, owner_tg = row
                item_count = len(
                    (
                        await session.scalars(
                            select(OrderItem.id).where(OrderItem.order_id == order.id)
                        )
                    ).all()
                )
                agents = (
                    await session.execute(
                        select(Agent.telegram_user_id, Agent.display_name)
                        .join(AgentStoreAssignment, AgentStoreAssignment.agent_id == Agent.id)
                        .where(
                            AgentStoreAssignment.store_id == order.store_id,
                            Agent.is_active.is_(True),
                        )
                    )
                ).all()

            actor = order.created_by_telegram_user_id
            placed_by = next((name for tg, name in agents if tg == actor), None)
            text = messages.new_order(
                order_number=order.order_number,
                store_name=store_name,
                total=order.total_amount,
                items=item_count,
                placed_by=f"agent {placed_by}" if placed_by else "do'kon egasi",
                note=order.note,
            )
            markup = single_app_button(self.settings, "📋 Buyurtmani ko'rish")
            recipients = {owner_tg, *(tg for tg, _ in agents)} - {actor}
            await asyncio.gather(
                *(self.send(chat_id, text, reply_markup=markup) for chat_id in recipients)
            )
        except Exception:
            logger.exception("order_submitted notification failed for %s", order_id)

    async def order_delivered(self, order_id: uuid.UUID) -> None:
        """Delivered and billed -> the store owner, with the new balance."""
        try:
            async with self.sessionmaker() as session:
                row = (
                    await session.execute(
                        select(
                            Order.order_number,
                            Order.total_amount,
                            Store.telegram_user_id,
                            Store.current_debt,
                        )
                        .join(Store, Store.id == Order.store_id)
                        .where(Order.id == order_id)
                    )
                ).one_or_none()
            if row is None or row.telegram_user_id is None:
                return
            await self.send(
                row.telegram_user_id,
                messages.order_delivered(
                    order_number=row.order_number,
                    total=row.total_amount,
                    new_debt=row.current_debt,
                ),
                reply_markup=single_app_button(self.settings, "📱 Balansni ko'rish"),
            )
        except Exception:
            logger.exception("order_delivered notification failed for %s", order_id)

    async def payment_received(self, transaction_id: uuid.UUID) -> None:
        """Electronic receipt to the store owner after any recorded payment."""
        try:
            async with self.sessionmaker() as session:
                row = (
                    await session.execute(
                        select(
                            LedgerTransaction,
                            Store.telegram_user_id,
                            Store.current_debt,
                            Wholesaler.timezone,
                        )
                        .join(Store, Store.id == LedgerTransaction.store_id)
                        .join(Wholesaler, Wholesaler.id == LedgerTransaction.wholesaler_id)
                        .where(LedgerTransaction.id == transaction_id)
                    )
                ).one_or_none()
                if row is None:
                    return
                tx, store_tg, current_debt, tz = row
                agent_name = (
                    await session.scalar(select(Agent.display_name).where(Agent.id == tx.agent_id))
                    if tx.agent_id
                    else None
                )
            if store_tg is None or tx.type != TransactionType.PAYMENT:
                return
            await self.send(
                store_tg,
                messages.payment_received(
                    amount=-tx.amount,
                    current_debt=current_debt,
                    method=tx.payment_method,
                    receipt_no=tx.sequence_no,
                    receipt_hash=tx.entry_hash,
                    paid_at=messages.local_time(tx.created_at, tz),
                    agent_name=agent_name,
                ),
            )
        except Exception:
            logger.exception("payment_received notification failed for %s", transaction_id)
