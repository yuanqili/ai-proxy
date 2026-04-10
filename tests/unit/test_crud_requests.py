"""Tests for aiproxy.db.crud.requests."""
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


async def test_create_pending_and_mark_finished(session_factory) -> None:
    async with session_factory() as s:
        await req_crud.create_pending(
            s,
            req_id="abc123",
            api_key_id=None,
            provider="openai",
            endpoint="/chat/completions",
            method="POST",
            model="gpt-4o",
            is_streaming=True,
            client_ip="127.0.0.1",
            client_ua="pytest/1.0",
            req_headers={"content-type": "application/json"},
            req_query=None,
            req_body=b'{"model":"gpt-4o"}',
            started_at=time.time(),
        )
        await s.commit()

    async with session_factory() as s:
        row = await req_crud.get_by_id(s, "abc123")
        assert row is not None
        assert row.status == "pending"
        assert row.provider == "openai"
        assert row.is_streaming == 1
        assert row.req_body == b'{"model":"gpt-4o"}'
        assert row.req_body_size == len(b'{"model":"gpt-4o"}')

    async with session_factory() as s:
        await req_crud.mark_finished(
            s,
            req_id="abc123",
            status="done",
            status_code=200,
            resp_headers={"content-type": "text/event-stream"},
            resp_body=b"",
            finished_at=time.time(),
        )
        await s.commit()

    async with session_factory() as s:
        row = await req_crud.get_by_id(s, "abc123")
        assert row is not None
        assert row.status == "done"
        assert row.status_code == 200
        assert row.finished_at is not None


async def test_mark_error(session_factory) -> None:
    async with session_factory() as s:
        await req_crud.create_pending(
            s,
            req_id="err1",
            api_key_id=None,
            provider="anthropic",
            endpoint="/v1/messages",
            method="POST",
            model=None,
            is_streaming=False,
            client_ip=None,
            client_ua=None,
            req_headers={},
            req_query=None,
            req_body=None,
            started_at=time.time(),
        )
        await s.commit()
        await req_crud.mark_error(
            s,
            req_id="err1",
            error_class="upstream_connect",
            error_message="DNS failure",
            finished_at=time.time(),
        )
        await s.commit()

    async with session_factory() as s:
        row = await req_crud.get_by_id(s, "err1")
        assert row is not None
        assert row.status == "error"
        assert row.error_class == "upstream_connect"
