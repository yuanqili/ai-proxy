"""Integration tests for /dashboard/api/keys endpoints."""
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


async def test_list_empty(app_ctx) -> None:
    async with httpx.AsyncClient(transport=ASGITransport(app=app_ctx), base_url="http://test") as c:
        login = await c.post("/dashboard/login", json={"master_key": "k"})
        c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
        r = await c.get("/dashboard/api/keys")
    assert r.status_code == 200
    assert r.json() == {"keys": []}


async def test_create_key(app_ctx) -> None:
    async with httpx.AsyncClient(transport=ASGITransport(app=app_ctx), base_url="http://test") as c:
        login = await c.post("/dashboard/login", json={"master_key": "k"})
        c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
        r = await c.post("/dashboard/api/keys", json={"name": "prod", "note": "backend"})
    assert r.status_code == 200
    key = r.json()["key"]
    assert key["name"] == "prod"
    assert key["note"] == "backend"
    assert key["is_active"] is True
    assert key["key"].startswith("sk-aiprx-")


async def test_list_after_create(app_ctx) -> None:
    async with httpx.AsyncClient(transport=ASGITransport(app=app_ctx), base_url="http://test") as c:
        login = await c.post("/dashboard/login", json={"master_key": "k"})
        c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
        await c.post("/dashboard/api/keys", json={"name": "a"})
        await c.post("/dashboard/api/keys", json={"name": "b"})
        r = await c.get("/dashboard/api/keys")
    data = r.json()
    assert len(data["keys"]) == 2
    names = {k["name"] for k in data["keys"]}
    assert names == {"a", "b"}


async def test_patch_deactivate(app_ctx) -> None:
    async with httpx.AsyncClient(transport=ASGITransport(app=app_ctx), base_url="http://test") as c:
        login = await c.post("/dashboard/login", json={"master_key": "k"})
        c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
        created = await c.post("/dashboard/api/keys", json={"name": "x"})
        key_id = created.json()["key"]["id"]
        r = await c.patch(f"/dashboard/api/keys/{key_id}", json={"is_active": False})
    assert r.status_code == 200
    assert r.json()["key"]["is_active"] is False


async def test_patch_rename(app_ctx) -> None:
    async with httpx.AsyncClient(transport=ASGITransport(app=app_ctx), base_url="http://test") as c:
        login = await c.post("/dashboard/login", json={"master_key": "k"})
        c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
        created = await c.post("/dashboard/api/keys", json={"name": "x"})
        key_id = created.json()["key"]["id"]
        r = await c.patch(f"/dashboard/api/keys/{key_id}", json={"name": "renamed"})
    assert r.json()["key"]["name"] == "renamed"
