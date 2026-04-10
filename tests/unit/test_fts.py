"""Tests for FTS5 shadow table on requests."""
import time

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.db.crud import requests as req_crud
from aiproxy.db.fts import install_fts_schema, search_requests
from aiproxy.db.models import Base


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Install FTS table + triggers via raw SQL because SQLAlchemy doesn't
    # model FTS5 virtual tables directly.
    async with engine.begin() as conn:
        await install_fts_schema(conn)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sm
    finally:
        await engine.dispose()


async def _seed(sm, req_id: str, body: bytes, model: str = "gpt-4o") -> None:
    async with sm() as s:
        await req_crud.create_pending(
            s, req_id=req_id, api_key_id=None, provider="openai",
            endpoint="/chat/completions", method="POST", model=model,
            is_streaming=False, client_ip=None, client_ua=None,
            req_headers={}, req_query=None, req_body=body,
            started_at=time.time(),
        )
        await s.commit()


async def test_search_finds_request_by_body_text(session_factory) -> None:
    await _seed(session_factory, "r1", b'{"model":"gpt-4o","messages":[{"role":"user","content":"refactor this Python code"}]}')
    await _seed(session_factory, "r2", b'{"model":"gpt-4o","messages":[{"role":"user","content":"write a poem about spring"}]}')

    async with session_factory() as s:
        hits = await search_requests(s, query="refactor", limit=10)
    assert "r1" in hits
    assert "r2" not in hits


async def test_search_finds_by_model(session_factory) -> None:
    await _seed(session_factory, "r1", b'{"model":"gpt-4o"}', model="gpt-4o")
    await _seed(session_factory, "r2", b'{"model":"claude-haiku"}', model="claude-haiku")

    async with session_factory() as s:
        hits = await search_requests(s, query="claude", limit=10)
    assert "r2" in hits
    assert "r1" not in hits


async def test_search_no_matches_returns_empty(session_factory) -> None:
    await _seed(session_factory, "r1", b'{"model":"gpt-4o"}')

    async with session_factory() as s:
        hits = await search_requests(s, query="nonexistentxyz", limit=10)
    assert hits == []


async def test_fts_updated_via_trigger_on_insert(session_factory) -> None:
    """Inserting a request should automatically populate the FTS table."""
    await _seed(session_factory, "r1", b'{"messages":[{"content":"special-marker"}]}')

    async with session_factory() as s:
        result = await s.execute(
            text('SELECT COUNT(*) FROM requests_fts WHERE requests_fts MATCH \'"special-marker"\'')
        )
        count = result.scalar()
    assert count == 1
