"""Transaction boundary helper."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import SessionTransactionOrigin


@asynccontextmanager
async def atomic(session: AsyncSession) -> AsyncIterator[None]:
    """Run a block atomically.

    * No transaction, or only an *autobegun* one (e.g. the auth dependency ran a
      SELECT): the implicit transaction is closed and the block runs in its own
      BEGIN ... COMMIT. The block is durable once this context manager exits.
    * A transaction the caller opened explicitly (`session.begin()` / nested):
      the block runs in a SAVEPOINT; it is rolled back on error, but the final COMMIT
      belongs to the caller. This lets callers compose several service calls.
    """
    current = session.sync_session.get_transaction()
    if current is not None and current.origin is SessionTransactionOrigin.AUTOBEGIN:
        # Autobegun transactions are never a deliberate unit of work. With
        # expire_on_commit=False, committing (rather than rolling back) keeps every
        # loaded object usable.
        await session.commit()
        current = None

    if current is None:
        async with session.begin():
            yield
    else:
        async with session.begin_nested():
            yield
