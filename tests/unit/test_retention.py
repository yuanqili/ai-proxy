"""Tests for aiproxy.db.retention."""
import time

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.db.crud import chunks as chunks_crud
from aiproxy.db.crud import requests as req_crud
from aiproxy.db.models import Base, Chunk, Request
from aiproxy.db.retention import prune_old_requests


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


async def _seed_request(sm, req_id: str, started_at: float, body: bytes | None = b"body", with_chunks: int = 0) -> None:
    async with sm() as s:
        await req_crud.create_pending(
            s, req_id=req_id, api_key_id=None, provider="openai",
            endpoint="/chat/completions", method="POST", model="gpt-4o",
            is_streaming=bool(with_chunks), client_ip=None, client_ua=None,
            req_headers={}, req_query=None, req_body=body,
            started_at=started_at,
        )
        if with_chunks:
            await chunks_crud.insert_batch(
                s, req_id, [(i, i * 1000, b"x") for i in range(with_chunks)]
            )
        await s.commit()


async def test_prune_keeps_recent_full_data(session_factory) -> None:
    now = time.time()
    # 5 requests, each with chunks and a body
    for i in range(5):
        await _seed_request(session_factory, f"r{i}", now - (5 - i), b"body", with_chunks=3)

    # Keep the 2 most recent full; prune older 3
    await prune_old_requests(session_factory, full_count=2)

    async with session_factory() as s:
        # All 5 requests rows still exist
        total = (await s.execute(select(func.count()).select_from(Request))).scalar()
        assert total == 5
        # Only 2 newest still have req_body
        with_body = (await s.execute(
            select(func.count()).select_from(Request).where(Request.req_body.is_not(None))
        )).scalar()
        assert with_body == 2
        # Only chunks for the 2 newest remain
        chunk_total = (await s.execute(select(func.count()).select_from(Chunk))).scalar()
        assert chunk_total == 2 * 3  # 2 requests * 3 chunks each


async def test_prune_noop_when_under_full_count(session_factory) -> None:
    now = time.time()
    for i in range(3):
        await _seed_request(session_factory, f"r{i}", now - (3 - i), with_chunks=2)

    await prune_old_requests(session_factory, full_count=5)

    async with session_factory() as s:
        with_body = (await s.execute(
            select(func.count()).select_from(Request).where(Request.req_body.is_not(None))
        )).scalar()
        assert with_body == 3
        chunk_total = (await s.execute(select(func.count()).select_from(Chunk))).scalar()
        assert chunk_total == 6
