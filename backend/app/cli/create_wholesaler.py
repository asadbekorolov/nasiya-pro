"""Create a wholesaler (tenant) and make a Telegram user its owner (Wholesaler Admin).

Production onboarding of a real company, with no demo data:

    python -m app.cli.create_wholesaler --name "Samarqand Optom" \\
        --owner-telegram-id 123456789 --owner-name "Ism Familiya" \\
        [--legal-name '"Samarqand Optom" MChJ'] [--tin 305112233] [--phone +998...] \\
        [--address "..."] [--bank-name ... --bank-account ... --bank-mfo ...] \\
        [--payment-term-days 14] [--timezone Asia/Tashkent]

Requisites (legal name, TIN, bank) are printed on the Akt-sverka. Products and stores
are then added from the Mini App (Import tab).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import build_engine, build_sessionmaker
from app.models import Wholesaler


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument("--name", required=True, help="trade name shown in the bot and Mini App")
    p.add_argument("--owner-telegram-id", type=int, required=True)
    p.add_argument("--owner-name", help="owner's full name (bot greeting)")
    p.add_argument("--legal-name")
    p.add_argument("--tin", help="STIR / INN")
    p.add_argument("--phone")
    p.add_argument("--address")
    p.add_argument("--bank-name")
    p.add_argument("--bank-account")
    p.add_argument("--bank-mfo")
    p.add_argument("--payment-term-days", type=int, default=None)
    p.add_argument("--timezone", default=None)
    args = p.parse_args(argv)
    if args.owner_telegram_id <= 0:
        p.error("--owner-telegram-id must be a positive Telegram user id")
    if args.timezone:
        try:
            ZoneInfo(args.timezone)
        except ZoneInfoNotFoundError:
            p.error(f"unknown timezone: {args.timezone}")
    if args.payment_term_days is not None and not 0 <= args.payment_term_days <= 365:
        p.error("--payment-term-days must be 0..365")
    return args


async def main(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = build_engine(settings)
    try:
        async with build_sessionmaker(engine)() as session, session.begin():
            existing = await session.scalar(
                select(Wholesaler).where(
                    Wholesaler.owner_telegram_user_id == args.owner_telegram_id
                )
            )
            if existing is not None:
                print(
                    f"Telegram user {args.owner_telegram_id} already owns "
                    f"'{existing.name}' ({existing.id}); nothing changed.",
                    file=sys.stderr,
                )
                return 1
            wholesaler = Wholesaler(
                name=args.name.strip(),
                owner_telegram_user_id=args.owner_telegram_id,
                owner_name=args.owner_name,
                legal_name=args.legal_name,
                tin=args.tin,
                phone=args.phone,
                address=args.address,
                bank_name=args.bank_name,
                bank_account=args.bank_account,
                bank_mfo=args.bank_mfo,
                currency=settings.default_currency,
                timezone=args.timezone or settings.default_timezone,
            )
            if args.payment_term_days is not None:
                wholesaler.default_payment_term_days = args.payment_term_days
            session.add(wholesaler)
            await session.flush()
            print(f"created wholesaler '{wholesaler.name}' ({wholesaler.id})")
            print(f"owner (Wholesaler Admin): Telegram id {args.owner_telegram_id}")
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args())))
