"""Print a correctly signed Telegram `initData` string for local development.

The API verifies every initData signature with TELEGRAM_BOT_TOKEN, so the Mini App
can only be tested outside Telegram with data signed by the same token. Usage:

    uv run python scripts/dev_init_data.py --tg-id 123456789 [--hours 24]

Put the output into frontend/.env.local as VITE_DEV_INIT_DATA=... (dev builds only).
Refuses to run when ENVIRONMENT=production.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.telegram_auth import compute_init_data_hash


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tg-id", type=int, required=True, help="Telegram user id to impersonate")
    parser.add_argument("--first-name", default="Dev")
    args = parser.parse_args()

    settings = get_settings()
    if settings.is_production:
        sys.exit("refusing to mint initData in production")

    fields = {
        "auth_date": str(int(time.time())),
        "query_id": "DEV",
        "user": json.dumps(
            {"id": args.tg_id, "first_name": args.first_name, "language_code": "uz"},
            separators=(",", ":"),
        ),
    }
    fields["hash"] = compute_init_data_hash(fields, settings.telegram_bot_token.get_secret_value())
    print(urlencode(fields))
    print(
        f"# valid for {settings.telegram_init_data_ttl_seconds // 3600} h "
        "(TELEGRAM_INIT_DATA_TTL_SECONDS)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
