"""Integration tests for POST /dashboard/api/batch/vacuum."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.bus import StreamBus
from aiproxy.dashboard.routes import create_dashboard_router
from aiproxy.db.crud import requests as req_crud
from aiproxy.db.models import Base, Request
from aiproxy.registry import RequestRegistry


@pytest.fixture
async def app_ctx(tmp_path):
    # Use an on-disk DB so VACUUM has a real file to rewrite.
    db_path = tmp_path / "vacuum-test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
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


async def test_vacuum_requires_auth(app_ctx) -> None:
    app, _ = app_ctx
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/dashboard/api/batch/vacuum")
    assert r.status_code == 401


async def test_vacuum_empty_db_succeeds(app_ctx) -> None:
    app, _ = app_ctx
    c, cookies = await _authed(app)
    try:
        r = await c.post("/dashboard/api/batch/vacuum", cookies=cookies)
    finally:
        await c.aclose()
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "db_size_bytes_before" in data
    assert "db_size_bytes_after" in data
    assert data["db_size_bytes_before"] >= 0
    assert data["db_size_bytes_after"] >= 0


async def test_vacuum_after_delete_does_not_grow(app_ctx) -> None:
    """Fill, delete, vacuum → bytes_after should be <= bytes_before."""
    app, sm = app_ctx
    async with sm() as s:
        for i in range(20):
            await req_crud.create_pending(
                s, req_id=f"r{i}", api_key_id=None, provider="openai",
                endpoint="/v1/chat/completions", method="POST", model="gpt-4o",
                is_streaming=False, client_ip=None, client_ua=None,
                req_headers={"x": "y"}, req_query=None,
                req_body=b"x" * 4096, started_at=float(i),
            )
        await s.commit()
    async with sm() as s:
        await s.execute(delete(Request))
        await s.commit()

    c, cookies = await _authed(app)
    try:
        r = await c.post("/dashboard/api/batch/vacuum", cookies=cookies)
    finally:
        await c.aclose()
    assert r.status_code == 200
    data = r.json()
    assert data["db_size_bytes_after"] <= data["db_size_bytes_before"]
