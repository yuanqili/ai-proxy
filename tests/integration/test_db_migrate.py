"""Tests for the lightweight additive column migration."""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from aiproxy.db.migrate import ensure_requests_columns


async def _columns(conn, table: str) -> set[str]:
    rows = (await conn.execute(text(f'PRAGMA table_info("{table}")'))).fetchall()
    return {row[1] for row in rows}


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    try:
        yield eng
    finally:
        await eng.dispose()


async def test_adds_missing_columns_to_legacy_table(engine) -> None:
    """Simulate a pre-labels/note DB and verify ensure_requests_columns
    backfills the new columns."""
    async with engine.begin() as conn:
        await conn.exec_driver_sql("""
            CREATE TABLE requests (
                req_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                method TEXT NOT NULL,
                req_headers TEXT NOT NULL,
                started_at REAL NOT NULL,
                status TEXT NOT NULL
            )
        """)
        before = await _columns(conn, "requests")
        assert "labels" not in before
        assert "note" not in before

        await ensure_requests_columns(conn)

        after = await _columns(conn, "requests")
        assert "labels" in after
        assert "note" in after


async def test_idempotent(engine) -> None:
    """Running the migration twice is a no-op on the second call."""
    async with engine.begin() as conn:
        await conn.exec_driver_sql("""
            CREATE TABLE requests (
                req_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                method TEXT NOT NULL,
                req_headers TEXT NOT NULL,
                started_at REAL NOT NULL,
                status TEXT NOT NULL
            )
        """)
        await ensure_requests_columns(conn)
        # Second run should not raise ("duplicate column name").
        await ensure_requests_columns(conn)
        cols = await _columns(conn, "requests")
        assert "labels" in cols
        assert "note" in cols
