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

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from aiproxy.core.traits import detect_request_traits
from aiproxy.db.models import Request


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
        "request_has_image": "INTEGER",
        "request_has_file": "INTEGER",
        "response_is_json": "INTEGER",
    }
    existing = await _existing_columns(conn, "requests")
    for col, decl in wanted.items():
        if col not in existing:
            await conn.exec_driver_sql(
                f'ALTER TABLE "requests" ADD COLUMN "{col}" {decl}'
            )


async def backfill_request_traits(
    session: AsyncSession,
    *,
    batch_size: int = 500,
    rescan_all: bool = False,
) -> int:
    """Compute trait flags for rows that need it.

    By default only rows with at least one NULL trait flag are processed —
    safe and cheap to call on every startup. When ``rescan_all`` is true
    every row is reprocessed; use this when the detector logic itself has
    changed (signalled via ``DETECTOR_VERSION`` in core/traits.py).

    Returns the number of rows updated.
    """
    updated = 0
    last_id: str | None = None
    while True:
        stmt = select(Request.req_id, Request.req_body)
        if not rescan_all:
            stmt = stmt.where(
                Request.request_has_image.is_(None)
                | Request.request_has_file.is_(None)
                | Request.response_is_json.is_(None)
            )
        if last_id is not None:
            stmt = stmt.where(Request.req_id > last_id)
        stmt = stmt.order_by(Request.req_id).limit(batch_size)
        rows = (await session.execute(stmt)).all()
        if not rows:
            break
        for req_id, body in rows:
            traits = detect_request_traits(body)
            await session.execute(
                update(Request)
                .where(Request.req_id == req_id)
                .values(
                    request_has_image=int(traits["request_has_image"]),
                    request_has_file=int(traits["request_has_file"]),
                    response_is_json=int(traits["response_is_json"]),
                )
            )
            updated += 1
        await session.commit()
        last_id = rows[-1][0]
        if len(rows) < batch_size:
            break
    return updated
