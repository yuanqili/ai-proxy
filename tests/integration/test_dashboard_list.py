"""Integration tests for /dashboard/api/requests — list + filters + search."""
import time

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from aiproxy.bus import StreamBus
from aiproxy.dashboard.routes import create_dashboard_router
from aiproxy.db.crud import requests as req_crud
from aiproxy.db.fts import install_fts_schema
from aiproxy.db.models import Base
from aiproxy.registry import RequestRegistry

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
async def app_ctx():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await install_fts_schema(conn)
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


async def _seed(sm, req_id, provider, model, body_text=""):
    async with sm() as s:
        await req_crud.create_pending(
            s, req_id=req_id, api_key_id=None, provider=provider,
            endpoint="/chat/completions", method="POST", model=model,
            is_streaming=False, client_ip=None, client_ua=None,
            req_headers={}, req_query=None,
            req_body=body_text.encode() if body_text else None,
            started_at=time.time(),
        )
        await req_crud.mark_finished(
            s, req_id=req_id, status="done", status_code=200,
            resp_headers={}, resp_body=b"", finished_at=time.time(),
        )
        await s.commit()


async def _authed_client(app):
    """Return a new AsyncClient (not yet entered); caller must use async with."""
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_list_empty(app_ctx) -> None:
    app, _ = app_ctx
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        login = await c.post("/dashboard/login", json={"master_key": "k"})
        c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
        r = await c.get("/dashboard/api/requests")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 0
    assert data["rows"] == []


async def test_list_all_rows(app_ctx) -> None:
    app, sm = app_ctx
    await _seed(sm, "r1", "openai", "gpt-4o")
    await _seed(sm, "r2", "anthropic", "claude-haiku")

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        login = await c.post("/dashboard/login", json={"master_key": "k"})
        c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
        r = await c.get("/dashboard/api/requests")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2


async def test_list_filter_by_provider(app_ctx) -> None:
    app, sm = app_ctx
    await _seed(sm, "r1", "openai", "gpt-4o")
    await _seed(sm, "r2", "anthropic", "claude-haiku")

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        login = await c.post("/dashboard/login", json={"master_key": "k"})
        c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
        r = await c.get("/dashboard/api/requests?provider=anthropic")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["rows"][0]["req_id"] == "r2"


async def test_list_fts_search(app_ctx) -> None:
    app, sm = app_ctx
    await _seed(sm, "r1", "openai", "gpt-4o", body_text='{"messages":[{"content":"refactor this"}]}')
    await _seed(sm, "r2", "openai", "gpt-4o", body_text='{"messages":[{"content":"write a poem"}]}')

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        login = await c.post("/dashboard/login", json={"master_key": "k"})
        c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
        r = await c.get("/dashboard/api/requests?q=refactor")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["rows"][0]["req_id"] == "r1"


async def test_list_pagination(app_ctx) -> None:
    app, sm = app_ctx
    for i in range(5):
        await _seed(sm, f"r{i}", "openai", "gpt-4o")

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        login = await c.post("/dashboard/login", json={"master_key": "k"})
        c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
        r = await c.get("/dashboard/api/requests?limit=2&offset=0")
    data = r.json()
    assert data["total"] == 5
    assert len(data["rows"]) == 2
