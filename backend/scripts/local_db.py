"""Ensure a local PostgreSQL is running for development (no Docker needed).

    uv run python scripts/local_db.py [--data-dir .localdb]

Uses `pgserver` (embedded PostgreSQL binaries, dev dependency). Starts the server in the
background if needed, creates the `nasiya_dev` database and prints a DATABASE_URL line.
The server keeps running after this script exits; stop it with `--stop`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

DB_NAME = "nasiya_dev"


async def _ensure_database(uri: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(uri)
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", DB_NAME):
            await conn.execute(f'CREATE DATABASE "{DB_NAME}"')
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", default=str(Path(__file__).resolve().parents[1] / ".localdb"))
    parser.add_argument("--stop", action="store_true", help="stop the local server")
    args = parser.parse_args()

    try:
        import pgserver
    except ImportError:
        sys.exit("pgserver is not installed: run `uv sync` (it is a dev dependency)")

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    if args.stop:
        pgserver.get_server(str(data_dir), cleanup_mode="stop").cleanup()
        print("local PostgreSQL stopped")
        return

    server = pgserver.get_server(str(data_dir), cleanup_mode=None)
    uri = server.get_uri()  # postgresql://postgres:@127.0.0.1:<port>/postgres
    asyncio.run(_ensure_database(uri))
    base = uri.rsplit("/", 1)[0].replace(
        "postgresql://postgres:@", "postgresql+asyncpg://postgres@"
    )
    print(f"DATABASE_URL={base}/{DB_NAME}")


if __name__ == "__main__":
    main()
