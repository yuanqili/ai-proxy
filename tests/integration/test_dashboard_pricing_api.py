"""Integration tests for /dashboard/api/pricing endpoints."""
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


async def test_upsert_and_list(app_ctx) -> None:
    async with httpx.AsyncClient(transport=ASGITransport(app=app_ctx), base_url="http://test") as c:
        login = await c.post("/dashboard/login", json={"master_key": "k"})
        c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
        r = await c.post("/dashboard/api/pricing", json={
            "provider": "openai",
            "model": "gpt-5",
            "input_per_1m_usd": 1.0,
            "output_per_1m_usd": 2.0,
        })
        assert r.status_code == 200
        first = r.json()["pricing"]
        assert first["effective_to"] is None

        listing = await c.get("/dashboard/api/pricing")
    data = listing.json()
    assert len(data["pricing"]) == 1


async def test_upsert_versioning(app_ctx) -> None:
    async with httpx.AsyncClient(transport=ASGITransport(app=app_ctx), base_url="http://test") as c:
        login = await c.post("/dashboard/login", json={"master_key": "k"})
        c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
        await c.post("/dashboard/api/pricing", json={
            "provider": "openai", "model": "gpt-5",
            "input_per_1m_usd": 1.0, "output_per_1m_usd": 2.0,
        })
        await c.post("/dashboard/api/pricing", json={
            "provider": "openai", "model": "gpt-5",
            "input_per_1m_usd": 0.8, "output_per_1m_usd": 1.5,
        })

        listing = await c.get("/dashboard/api/pricing")
    rows = listing.json()["pricing"]
    assert len(rows) == 2
    active = [r for r in rows if r["effective_to"] is None]
    assert len(active) == 1
    assert active[0]["input_per_1m_usd"] == 0.8
