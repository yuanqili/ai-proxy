"""FTS5 virtual table for full-text search on requests.

SQLAlchemy doesn't directly model FTS5 virtual tables, so we create them
and their sync triggers with raw SQL. The shadow table is external-content
(content='' means it stores its own data) and we decode req_body / resp_body
bytes to text via triggers that use a scalar helper.

Since SQLite FTS5 triggers can't call Python, the triggers decode via the
raw BLOB data, which FTS5 will happily index as text if it's utf-8.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

_FTS_CREATE = """
CREATE VIRTUAL TABLE IF NOT EXISTS requests_fts USING fts5(
    req_id UNINDEXED,
    req_body_text,
    resp_body_text,
    model,
    endpoint,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

_TRIGGER_INSERT = """
CREATE TRIGGER IF NOT EXISTS requests_fts_insert AFTER INSERT ON requests
BEGIN
    INSERT INTO requests_fts (req_id, req_body_text, resp_body_text, model, endpoint)
    VALUES (
        NEW.req_id,
        COALESCE(CAST(NEW.req_body AS TEXT), ''),
        COALESCE(CAST(NEW.resp_body AS TEXT), ''),
        COALESCE(NEW.model, ''),
        NEW.endpoint
    );
END;
"""

_TRIGGER_UPDATE = """
CREATE TRIGGER IF NOT EXISTS requests_fts_update AFTER UPDATE ON requests
BEGIN
    DELETE FROM requests_fts WHERE req_id = OLD.req_id;
    INSERT INTO requests_fts (req_id, req_body_text, resp_body_text, model, endpoint)
    VALUES (
        NEW.req_id,
        COALESCE(CAST(NEW.req_body AS TEXT), ''),
        COALESCE(CAST(NEW.resp_body AS TEXT), ''),
        COALESCE(NEW.model, ''),
        NEW.endpoint
    );
END;
"""

_TRIGGER_DELETE = """
CREATE TRIGGER IF NOT EXISTS requests_fts_delete AFTER DELETE ON requests
BEGIN
    DELETE FROM requests_fts WHERE req_id = OLD.req_id;
END;
"""


async def install_fts_schema(conn: AsyncConnection) -> None:
    """Create the FTS5 virtual table and sync triggers. Idempotent."""
    for stmt in (_FTS_CREATE, _TRIGGER_INSERT, _TRIGGER_UPDATE, _TRIGGER_DELETE):
        await conn.execute(text(stmt))


async def search_requests(
    session: AsyncSession,
    *,
    query: str,
    limit: int = 50,
) -> list[str]:
    """Return a list of req_id values matching the FTS5 query, most-relevant first.

    The query string is passed through directly to FTS5 MATCH, so callers can use
    FTS5 query syntax (phrase queries in double quotes, AND/OR, prefix *, etc).
    For safety against empty strings, returns [] if query is empty.
    """
    if not query or not query.strip():
        return []
    q = query.strip()
    # Wrap in double-quotes for a phrase search unless the caller already
    # supplied FTS5 query syntax (starts with a quote, contains AND/OR/NOT).
    if not q.startswith('"') and " AND " not in q and " OR " not in q and " NOT " not in q:
        q = '"' + q.replace('"', '""') + '"'
    result = await session.execute(
        text(
            "SELECT req_id FROM requests_fts "
            "WHERE requests_fts MATCH :q "
            "ORDER BY rank "
            "LIMIT :lim"
        ),
        {"q": q, "lim": limit},
    )
    return [row[0] for row in result.all()]
