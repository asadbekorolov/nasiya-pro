"""Telegram webhook (BOT_MODE=webhook)."""

from __future__ import annotations

import hmac
import logging
from typing import Annotated

from fastapi import APIRouter, Header, Request

from app.api.deps import BotRuntimeDep, SettingsDep
from app.bot.dispatcher import WEBHOOK_PATH
from app.core.exceptions import AuthenticationError, NotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["telegram"])


@router.post(WEBHOOK_PATH, include_in_schema=False)
async def telegram_webhook(
    request: Request,
    settings: SettingsDep,
    runtime: BotRuntimeDep,
    secret_token: Annotated[str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")] = None,
) -> dict[str, bool]:
    if runtime is None or settings.bot_mode != "webhook" or not runtime.receives_updates:
        raise NotFoundError("Not found")
    expected = settings.bot_webhook_secret.get_secret_value() if settings.bot_webhook_secret else ""
    if not expected or not hmac.compare_digest(secret_token or "", expected):
        raise AuthenticationError("Invalid webhook secret")
    payload = await request.json()
    try:
        await runtime.feed_webhook_update(payload)
    except Exception:
        # Answer 200 anyway: a non-2xx makes Telegram redeliver the same update forever.
        logger.exception("failed to process telegram update %s", payload.get("update_id"))
    return {"ok": True}
