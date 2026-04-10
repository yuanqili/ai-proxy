"""Integration tests for GET /dashboard/api/timeline."""
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


async def _seed(sm, rows: list[tuple[str, str, str, float]]) -> None:
    """rows: list of (req_id, provider, model, started_at)."""
    async with sm() as s:
        for req_id, provider, model, started_at in rows:
            await req_crud.create_pending(
                s, req_id=req_id, api_key_id=None, provider=provider,
                endpoint="/v1/chat/completions", method="POST", model=model,
                is_streaming=False, client_ip=None, client_ua=None,
                req_headers={}, req_query=None, req_body=b"{}",
                started_at=started_at,
            )
            await req_crud.mark_finished(
                s, req_id=req_id, status="done", status_code=200,
                resp_headers={}, resp_body=b'{"ok":1}',
                finished_at=started_at + 0.5,
            )
        await s.commit()


async def test_timeline_requires_auth(app_ctx) -> None:
    app, _ = app_ctx
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/dashboard/api/timeline")
    assert r.status_code == 401


async def test_timeline_groups_by_model(app_ctx) -> None:
    app, sm = app_ctx
    await _seed(sm, [
        ("r1", "openai", "gpt-4o", 100.0),
        ("r2", "openai", "gpt-4o", 110.0),
        ("r3", "anthropic", "claude-haiku-4-5", 105.0),
    ])
    c, cookies = await _authed(app)
    try:
        r = await c.get(
            "/dashboard/api/timeline?since=0&until=1000",
            cookies=cookies,
        )
    finally:
        await c.aclose()
    assert r.status_code == 200
    data = r.json()
    assert data["group_by"] == "model"
    assert len(data["lanes"]) == 2
    by_key = {lane["key"]: lane for lane in data["lanes"]}
    assert len(by_key["gpt-4o"]["requests"]) == 2
    assert len(by_key["claude-haiku-4-5"]["requests"]) == 1
    req0 = by_key["gpt-4o"]["requests"][0]
    for k in ("req_id", "started_at", "finished_at", "status"):
        assert k in req0


async def test_timeline_window_excludes_outliers(app_ctx) -> None:
    app, sm = app_ctx
    await _seed(sm, [
        ("old", "openai", "gpt-4o", 10.0),
        ("in1", "openai", "gpt-4o", 100.0),
        ("in2", "openai", "gpt-4o", 150.0),
        ("fut", "openai", "gpt-4o", 500.0),
    ])
    c, cookies = await _authed(app)
    try:
        r = await c.get(
            "/dashboard/api/timeline?since=50&until=200",
            cookies=cookies,
        )
    finally:
        await c.aclose()
    assert r.status_code == 200
    data = r.json()
    all_ids = {req["req_id"] for lane in data["lanes"] for req in lane["requests"]}
    assert all_ids == {"in1", "in2"}


async def test_timeline_group_by_provider(app_ctx) -> None:
    app, sm = app_ctx
    await _seed(sm, [
        ("r1", "openai", "gpt-4o", 100.0),
        ("r2", "anthropic", "claude-haiku-4-5", 110.0),
        ("r3", "anthropic", "claude-opus-4-6", 120.0),
    ])
    c, cookies = await _authed(app)
    try:
        r = await c.get(
            "/dashboard/api/timeline?since=0&until=1000&group_by=provider",
            cookies=cookies,
        )
    finally:
        await c.aclose()
    assert r.status_code == 200
    data = r.json()
    assert data["group_by"] == "provider"
    by_key = {lane["key"]: len(lane["requests"]) for lane in data["lanes"]}
    assert by_key == {"openai": 1, "anthropic": 2}


async def test_timeline_invalid_group_by(app_ctx) -> None:
    app, _ = app_ctx
    c, cookies = await _authed(app)
    try:
        r = await c.get(
            "/dashboard/api/timeline?group_by=bogus",
            cookies=cookies,
        )
    finally:
        await c.aclose()
    assert r.status_code == 400
