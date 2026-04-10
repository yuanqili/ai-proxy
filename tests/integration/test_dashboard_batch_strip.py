"""Integration tests for /dashboard/api/batch/strip-binaries."""
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


async def test_strip_replaces_base64_image(app_ctx) -> None:
    app, sm = app_ctx
    fake_body = b'{"messages":[{"content":[{"type":"image_url","image_url":{"url":"data:image/png;base64,AAAABBBBCCCCDDDDEEEE"}}]}]}'
    async with sm() as s:
        await req_crud.create_pending(
            s, req_id="r1", api_key_id=None, provider="openai",
            endpoint="/chat/completions", method="POST", model="gpt-4o",
            is_streaming=False, client_ip=None, client_ua=None,
            req_headers={}, req_query=None, req_body=fake_body,
            started_at=time.time(),
        )
        await s.commit()

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        login = await c.post("/dashboard/login", json={"master_key": "k"})
        c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
        r = await c.post("/dashboard/api/batch/strip-binaries", json={"req_ids": ["r1"]})
    assert r.status_code == 200
    assert r.json()["stripped"] == 1

    # Verify the body no longer contains the base64 payload
    from aiproxy.db.crud import requests as req_crud_mod
    async with sm() as s:
        row = await req_crud_mod.get_by_id(s, "r1")
    assert b"AAAABBBBCCCCDDDDEEEE" not in (row.req_body or b"")
    assert b"stripped" in (row.req_body or b"")
    assert row.binaries_stripped == 1


async def test_strip_requires_filter(app_ctx) -> None:
    app, _ = app_ctx
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        login = await c.post("/dashboard/login", json={"master_key": "k"})
        c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
        r = await c.post("/dashboard/api/batch/strip-binaries", json={})
    assert r.status_code == 400
