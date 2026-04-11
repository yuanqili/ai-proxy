"""Lightweight, idempotent column-level migration helper.

This project does not use Alembic — `Base.metadata.create_all` builds fresh
schemas, but it never backfills new columns onto an existing SQLite database.
For additive changes (new nullable columns) we run a cheap
`ALTER TABLE ... ADD COLUMN` on startup, guarded by a PRAGMA check so the
statement is only issued when the column is actually missing.

Use this for additive migrations only. Anything that would need a table
rebuild (dropping columns, changing types, adding non-null without default)
should be handled with a proper migration tool.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def _existing_columns(conn: AsyncConnection, table: str) -> set[str]:
    result = await conn.execute(text(f'PRAGMA table_info("{table}")'))
    return {row[1] for row in result.fetchall()}


async def ensure_requests_columns(conn: AsyncConnection) -> None:
    """Add any missing columns on the `requests` table.

    Safe to call on every startup — each ALTER is skipped if the column
    already exists. Columns added here must be nullable (or have a default)
    because SQLite's ALTER TABLE ADD COLUMN rejects NOT NULL without a
    default, and backfilling would need a table rewrite.
    """
    wanted = {
        "labels": "TEXT",
        "note": "TEXT",
    }
    existing = await _existing_columns(conn, "requests")
    for col, decl in wanted.items():
        if col not in existing:
            await conn.exec_driver_sql(
                f'ALTER TABLE "requests" ADD COLUMN "{col}" {decl}'
            )
