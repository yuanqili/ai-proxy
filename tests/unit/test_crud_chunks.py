"""Tests for aiproxy.db.crud.chunks."""
import time

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.db.crud import chunks as chunks_crud
from aiproxy.db.crud import requests as req_crud
from aiproxy.db.models import Base


@pytest.fixture
async def session_factory():
    from sqlalchemy import event, text

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)

    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sm
    finally:
        await engine.dispose()


async def _seed_request(sm, req_id: str) -> None:
    async with sm() as s:
        await req_crud.create_pending(
            s, req_id=req_id, api_key_id=None, provider="openai",
            endpoint="/chat/completions", method="POST", model="gpt-4o",
            is_streaming=True, client_ip=None, client_ua=None,
            req_headers={}, req_query=None, req_body=None,
            started_at=time.time(),
        )
        await s.commit()


async def test_insert_batch_and_fetch(session_factory) -> None:
    await _seed_request(session_factory, "req1")

    batch = [
        (0, 0, b'chunk0'),
        (1, 12_345_678, b'chunk1'),
        (2, 25_000_000, b'chunk2'),
    ]
    async with session_factory() as s:
        await chunks_crud.insert_batch(s, "req1", batch)
        await s.commit()

    async with session_factory() as s:
        rows = await chunks_crud.list_for_request(s, "req1")
        assert len(rows) == 3
        assert rows[0].seq == 0
        assert rows[0].offset_ns == 0
        assert rows[0].data == b'chunk0'
        assert rows[0].size == 6
        assert rows[1].seq == 1
        assert rows[1].offset_ns == 12_345_678
        assert rows[2].data == b'chunk2'


async def test_list_empty_for_unknown_request(session_factory) -> None:
    async with session_factory() as s:
        rows = await chunks_crud.list_for_request(s, "nonexistent")
        assert rows == []


async def test_cascade_delete_when_request_deleted(session_factory) -> None:
    await _seed_request(session_factory, "reqDel")
    async with session_factory() as s:
        await chunks_crud.insert_batch(s, "reqDel", [(0, 0, b'x')])
        await s.commit()

    # Delete the parent request
    from sqlalchemy import delete
    from aiproxy.db.models import Request
    async with session_factory() as s:
        await s.execute(delete(Request).where(Request.req_id == "reqDel"))
        await s.commit()

    async with session_factory() as s:
        rows = await chunks_crud.list_for_request(s, "reqDel")
        assert rows == []


async def test_count_for_request(session_factory) -> None:
    await _seed_request(session_factory, "req2")
    async with session_factory() as s:
        await chunks_crud.insert_batch(s, "req2", [
            (0, 0, b'a'), (1, 100, b'b'), (2, 200, b'c')
        ])
        await s.commit()
        n = await chunks_crud.count_for_request(s, "req2")
        assert n == 3
