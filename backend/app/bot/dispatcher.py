"""Bot + Dispatcher construction and lifecycle, driven by FastAPI's lifespan.

Modes (BOT_MODE):
* off      — no updates are received. If the token is valid the Bot object still
             exists so OTP codes and notifications can be *sent* (e.g. extra API
             workers while one dedicated process polls).
* polling  — development / single process: long polling in a background task.
* webhook  — production: Telegram POSTs updates to /api/v1/telegram/webhook,
             authenticated by the X-Telegram-Bot-Api-Secret-Token header.
Run polling in exactly one process: two pollers on one token conflict.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, MenuButtonWebApp, Update, WebAppInfo
from aiogram.utils.token import TokenValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.middlewares import DependenciesMiddleware
from app.bot.notifications import BotNotifier
from app.bot.routers import build_root_router
from app.core.config import Settings

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/telegram/webhook"


def create_bot(settings: Settings) -> Bot | None:
    try:
        return Bot(
            token=settings.telegram_bot_token.get_secret_value(),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    except TokenValidationError:
        logger.warning("TELEGRAM_BOT_TOKEN is not a valid bot token; the bot is disabled")
        return None


def create_dispatcher(
    sessionmaker: async_sessionmaker[AsyncSession], settings: Settings
) -> Dispatcher:
    dp = Dispatcher()
    middleware = DependenciesMiddleware(sessionmaker, settings)
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
    dp.include_router(build_root_router())
    return dp


class BotRuntime:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        bot: Bot | None = None,
    ) -> None:
        self.settings = settings
        self.bot = bot if bot is not None else create_bot(settings)
        self.dispatcher = create_dispatcher(sessionmaker, settings)
        self.notifier = BotNotifier(self.bot, sessionmaker, settings)
        self._polling_task: asyncio.Task[None] | None = None

    @property
    def receives_updates(self) -> bool:
        return self.bot is not None and self.settings.bot_mode != "off"

    async def start(self) -> None:
        if self.bot is None or self.settings.bot_mode == "off":
            logger.info("bot updates disabled (BOT_MODE=%s)", self.settings.bot_mode)
            return
        await self._configure_profile()
        if self.settings.bot_mode == "polling":
            await self.bot.delete_webhook(drop_pending_updates=False)
            self._polling_task = asyncio.create_task(self._poll(), name="telegram-polling")
            logger.info("telegram bot polling started")
        else:
            url = f"{self.settings.bot_webhook_base_url}{self.settings.api_v1_prefix}{WEBHOOK_PATH}"
            secret = self.settings.bot_webhook_secret
            await self.bot.set_webhook(
                url=url,
                secret_token=secret.get_secret_value() if secret else None,
                allowed_updates=self.dispatcher.resolve_used_update_types(),
                drop_pending_updates=False,
            )
            logger.info("telegram webhook set to %s", url)

    async def _poll(self) -> None:
        assert self.bot is not None
        try:
            await self.dispatcher.start_polling(
                self.bot,
                handle_signals=False,  # uvicorn owns the signals
                close_bot_session=False,  # closed in stop()
                allowed_updates=self.dispatcher.resolve_used_update_types(),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("telegram polling crashed")

    async def _configure_profile(self) -> None:
        """Bot commands and the chat menu button. Cosmetic: never fatal."""
        assert self.bot is not None
        try:
            await self.bot.set_my_commands(
                [BotCommand(command="start", description="Asosiy menyu")]
            )
            if self.settings.webapp_buttons_enabled and self.settings.webapp_url:
                await self.bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp(
                        text="Kabinet", web_app=WebAppInfo(url=self.settings.webapp_url)
                    )
                )
        except TelegramAPIError as exc:
            logger.warning("could not configure bot profile: %s", exc)

    async def feed_webhook_update(self, payload: dict[str, Any]) -> None:
        assert self.bot is not None
        update = Update.model_validate(payload, context={"bot": self.bot})
        await self.dispatcher.feed_update(self.bot, update)

    async def stop(self) -> None:
        if self._polling_task is not None:
            with contextlib.suppress(RuntimeError):  # polling may already have stopped
                await self.dispatcher.stop_polling()
            try:
                await asyncio.wait_for(self._polling_task, timeout=10)
            except (TimeoutError, asyncio.CancelledError):
                self._polling_task.cancel()
            self._polling_task = None
            logger.info("telegram bot polling stopped")
        if self.bot is not None:
            await self.bot.session.close()
