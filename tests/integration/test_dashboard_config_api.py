"""Integration tests for /dashboard/api/config."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.bus import StreamBus
from aiproxy.dashboard.routes import create_dashboard_router
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
        yield app
    finally:
        await engine.dispose()


async def test_get_has_defaults(app_ctx) -> None:
    async with httpx.AsyncClient(transport=ASGITransport(app=app_ctx), base_url="http://test") as c:
        login = await c.post("/dashboard/login", json={"master_key": "k"})
        c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
        r = await c.get("/dashboard/api/config")
    cfg = r.json()["config"]
    assert cfg["retention.full_count"] == 500


async def test_set_and_get(app_ctx) -> None:
    async with httpx.AsyncClient(transport=ASGITransport(app=app_ctx), base_url="http://test") as c:
        login = await c.post("/dashboard/login", json={"master_key": "k"})
        c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
        await c.post("/dashboard/api/config", json={
            "key": "retention.full_count", "value": 200
        })
        r = await c.get("/dashboard/api/config")
    assert r.json()["config"]["retention.full_count"] == 200
