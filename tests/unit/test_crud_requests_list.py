"""Tests for list_with_filters on aiproxy.db.crud.requests."""
import time

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.db.crud import requests as req_crud
from aiproxy.db.models import Base


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sm
    finally:
        await engine.dispose()


async def _seed(sm, req_id, provider, model, status="done", started_at=None):
    started_at = started_at or time.time()
    async with sm() as s:
        await req_crud.create_pending(
            s, req_id=req_id, api_key_id=None, provider=provider,
            endpoint="/chat/completions", method="POST", model=model,
            is_streaming=False, client_ip=None, client_ua=None,
            req_headers={}, req_query=None, req_body=None,
            started_at=started_at,
        )
        await req_crud.mark_finished(
            s, req_id=req_id, status=status, status_code=200,
            resp_headers={}, resp_body=b"", finished_at=time.time(),
        )
        await s.commit()


async def test_list_all_no_filter(session_factory) -> None:
    await _seed(session_factory, "r1", "openai", "gpt-4o")
    await _seed(session_factory, "r2", "anthropic", "claude-haiku")

    async with session_factory() as s:
        rows, total = await req_crud.list_with_filters(s, limit=50, offset=0)
    assert total == 2
    assert {r.req_id for r in rows} == {"r1", "r2"}


async def test_filter_by_provider(session_factory) -> None:
    await _seed(session_factory, "r1", "openai", "gpt-4o")
    await _seed(session_factory, "r2", "anthropic", "claude-haiku")
    await _seed(session_factory, "r3", "openai", "gpt-4o-mini")

    async with session_factory() as s:
        rows, total = await req_crud.list_with_filters(
            s, providers=["openai"], limit=50, offset=0
        )
    assert total == 2
    assert {r.req_id for r in rows} == {"r1", "r3"}


async def test_filter_by_model(session_factory) -> None:
    await _seed(session_factory, "r1", "openai", "gpt-4o")
    await _seed(session_factory, "r2", "openai", "gpt-4o-mini")

    async with session_factory() as s:
        rows, total = await req_crud.list_with_filters(
            s, models=["gpt-4o"], limit=50, offset=0
        )
    assert total == 1
    assert rows[0].req_id == "r1"


async def test_time_range(session_factory) -> None:
    now = time.time()
    await _seed(session_factory, "old", "openai", "gpt-4o", started_at=now - 3600)
    await _seed(session_factory, "new", "openai", "gpt-4o", started_at=now - 10)

    async with session_factory() as s:
        rows, total = await req_crud.list_with_filters(
            s, since=now - 60, limit=50, offset=0
        )
    assert total == 1
    assert rows[0].req_id == "new"


async def test_filter_by_req_ids_from_search(session_factory) -> None:
    """The FTS route returns req_ids; list_with_filters can filter by them."""
    await _seed(session_factory, "r1", "openai", "gpt-4o")
    await _seed(session_factory, "r2", "anthropic", "claude-haiku")
    await _seed(session_factory, "r3", "openai", "gpt-4o-mini")

    async with session_factory() as s:
        rows, total = await req_crud.list_with_filters(
            s, req_ids=["r1", "r3"], limit=50, offset=0
        )
    assert total == 2
    assert {r.req_id for r in rows} == {"r1", "r3"}


async def test_pagination(session_factory) -> None:
    for i in range(5):
        await _seed(session_factory, f"r{i}", "openai", "gpt-4o", started_at=time.time() + i)

    async with session_factory() as s:
        rows, total = await req_crud.list_with_filters(s, limit=2, offset=0)
    assert total == 5
    assert len(rows) == 2
    # Default sort is started_at DESC — newest first
    assert rows[0].req_id == "r4"
    assert rows[1].req_id == "r3"
