"""Integration tests for GET /dashboard/api/stats/db."""
import time

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.bus import StreamBus
from aiproxy.dashboard.routes import create_dashboard_router
from aiproxy.db.crud import requests as req_crud
from aiproxy.db.models import Base
from aiproxy.registry import RequestRegistry


@pytest.fixture
async def app_ctx():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    bus = StreamBus()
    registry = RequestRegistry(bus)
    app = FastAPI()
    app.include_router(create_dashboard_router(
        bus=bus, registry=registry, sessionmaker=sm,
        master_key="k", session_secret="s",
        secure_cookies=False,
    ))
    try:
        yield app, sm
    finally:
        await engine.dispose()


async def _authed(app: FastAPI) -> tuple[httpx.AsyncClient, dict[str, str]]:
    c = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await c.post("/dashboard/login", json={"master_key": "k"})
    return c, {"aiproxy_session": r.cookies["aiproxy_session"]}


async def test_stats_db_requires_auth(app_ctx) -> None:
    app, _ = app_ctx
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/dashboard/api/stats/db")
    assert r.status_code == 401


async def test_stats_db_empty(app_ctx) -> None:
    app, _ = app_ctx
    c, cookies = await _authed(app)
    try:
        r = await c.get("/dashboard/api/stats/db", cookies=cookies)
    finally:
        await c.aclose()
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"db_size_bytes", "request_count", "chunk_count", "pricing_count"}
    assert data["request_count"] == 0
    assert data["chunk_count"] == 0
    assert isinstance(data["db_size_bytes"], int) and data["db_size_bytes"] >= 0


async def test_stats_db_reflects_seeded_rows(app_ctx) -> None:
    app, sm = app_ctx
    async with sm() as s:
        await req_crud.create_pending(
            s, req_id="r1", api_key_id=None, provider="openai",
            endpoint="/v1/chat/completions", method="POST", model="gpt-4o",
            is_streaming=False, client_ip=None, client_ua=None,
            req_headers={}, req_query=None, req_body=b"{}",
            started_at=time.time(),
        )
        await s.commit()

    c, cookies = await _authed(app)
    try:
        r = await c.get("/dashboard/api/stats/db", cookies=cookies)
    finally:
        await c.aclose()
    assert r.status_code == 200
    data = r.json()
    assert data["request_count"] == 1
