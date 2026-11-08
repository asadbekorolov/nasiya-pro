"""Async engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings


def build_engine(settings: Settings) -> AsyncEngine:
    server_settings = {
        "application_name": "nasiya-pro-api",
        "statement_timeout": str(settings.db_statement_timeout_ms),
        "lock_timeout": str(settings.db_lock_timeout_ms),
    }
    if settings.db_session_timezone:
        server_settings["timezone"] = settings.db_session_timezone
    connect_args: dict[str, Any] = {"server_settings": server_settings}
    engine_kwargs: dict[str, Any] = {"echo": settings.db_echo, "pool_pre_ping": True}

    if settings.db_use_pgbouncer:
        # PgBouncer in transaction mode (Supabase pooler): no server-side prepared
        # statements and no client-side pooling on top of the bouncer.
        connect_args["statement_cache_size"] = 0
        connect_args["prepared_statement_cache_size"] = 0
        engine_kwargs["poolclass"] = NullPool
    else:
        engine_kwargs.update(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            pool_recycle=settings.db_pool_recycle,
        )

    engine = create_async_engine(settings.database_url, connect_args=connect_args, **engine_kwargs)

    if not settings.db_use_pgbouncer:
        # Connection poolers (Supabase Supavisor in session mode, PgBouncer) may drop
        # startup parameters, so the limits above would silently not apply. Re-apply them
        # with SET on every new physical connection: in session mode the connection is
        # ours for its whole life. (Transaction mode must not use session SETs; configure
        # those limits on the database role instead.)
        statements = [
            f"SET statement_timeout = {int(settings.db_statement_timeout_ms)}",
            f"SET lock_timeout = {int(settings.db_lock_timeout_ms)}",
        ]
        if settings.db_session_timezone:
            statements.append(f"SET TIME ZONE '{settings.db_session_timezone}'")

        @event.listens_for(engine.sync_engine, "connect")
        def _apply_session_limits(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                for statement in statements:
                    cursor.execute(statement)
            finally:
                cursor.close()

    return engine


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: ORM objects stay readable after commit (needed for
    # building API responses in async code without implicit lazy IO).
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=True)


@lru_cache
def get_engine() -> AsyncEngine:
    return build_engine(get_settings())


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return build_sessionmaker(get_engine())


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Services own transaction boundaries (see `db.uow.atomic`);
    anything left uncommitted is rolled back when the session closes."""
    async with get_sessionmaker()() as session:
        yield session


async def dispose_engine() -> None:
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
