"""Integration tests for /dashboard/api/requests/{req_id} detail endpoint."""
import base64
import time

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.bus import StreamBus
from aiproxy.dashboard.routes import create_dashboard_router
from aiproxy.db.crud import chunks as chunks_crud
from aiproxy.db.crud import requests as req_crud
from aiproxy.db.fts import install_fts_schema
from aiproxy.db.models import Base
from aiproxy.registry import RequestRegistry


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


async def test_detail_not_found(app_ctx) -> None:
    app, _ = app_ctx
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        login = await c.post("/dashboard/login", json={"master_key": "k"})
        c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
        r = await c.get("/dashboard/api/requests/missing")
    assert r.status_code == 404


async def test_detail_returns_full_row_and_chunks(app_ctx) -> None:
    app, sm = app_ctx
    async with sm() as s:
        await req_crud.create_pending(
            s, req_id="r1", api_key_id=None, provider="openai",
            endpoint="/chat/completions", method="POST", model="gpt-4o",
            is_streaming=True, client_ip="1.2.3.4", client_ua="pytest",
            req_headers={"x-foo": "bar"}, req_query=None,
            req_body=b'{"model":"gpt-4o"}',
            started_at=time.time(),
        )
        await chunks_crud.insert_batch(
            s, "r1", [(0, 0, b"hello"), (1, 10_000_000, b"world")]
        )
        await req_crud.mark_finished(
            s, req_id="r1", status="done", status_code=200,
            resp_headers={"x-bar": "baz"}, resp_body=b"helloworld",
            finished_at=time.time(),
        )
        await s.commit()

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        login = await c.post("/dashboard/login", json={"master_key": "k"})
        c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
        r = await c.get("/dashboard/api/requests/r1")
    assert r.status_code == 200
    data = r.json()
    assert data["request"]["req_id"] == "r1"
    assert data["request"]["model"] == "gpt-4o"
    assert data["request"]["is_streaming"] is True
    assert data["request"]["req_body_b64"]
    assert base64.b64decode(data["request"]["req_body_b64"]) == b'{"model":"gpt-4o"}'
    assert len(data["chunks"]) == 2
    assert data["chunks"][0]["seq"] == 0
    assert data["chunks"][0]["offset_ns"] == 0
    assert base64.b64decode(data["chunks"][0]["data_b64"]) == b"hello"
    assert data["chunks"][1]["offset_ns"] == 10_000_000
