"""Test helpers. `make_init_data` signs initData exactly as Telegram does, written
independently of `app.core.telegram_auth` so the tests check the implementation
against the Bot API spec rather than against itself."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

BOT_TOKEN = "123456:TEST-TOKEN"


def make_init_data(
    user_id: int,
    *,
    token: str = BOT_TOKEN,
    auth_date: int | None = None,
    extra: dict[str, str] | None = None,
    is_bot: bool = False,
) -> str:
    user: dict[str, object] = {"id": user_id, "first_name": "Test", "language_code": "uz"}
    if is_bot:
        user["is_bot"] = True
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAH-test",
        "user": json.dumps(user, separators=(",", ":")),
        **(extra or {}),
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def auth(tg_id: int, wholesaler_id: object | None = None) -> dict[str, str]:
    headers = {"Authorization": f"tma {make_init_data(tg_id)}"}
    if wholesaler_id is not None:
        headers["X-Wholesaler-Id"] = str(wholesaler_id)
    return headers
