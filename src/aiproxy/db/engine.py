"""SQLAlchemy async engine and session factory."""
from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def _apply_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    # WAL: concurrent reads alongside a single writer, durable across crashes.
    # synchronous=NORMAL: safe under WAL, materially faster than FULL.
    # busy_timeout: block briefly instead of raising "database is locked".
    # foreign_keys: SQLite needs this enabled per-connection.
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def create_engine_and_sessionmaker(
    database_url: str,
    *,
    echo: bool = False,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create an async engine and a session factory bound to it."""
    engine = create_async_engine(database_url, echo=echo, future=True)
    if engine.dialect.name == "sqlite":
        event.listen(engine.sync_engine, "connect", _apply_sqlite_pragmas)
    sessionmaker = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return engine, sessionmaker
