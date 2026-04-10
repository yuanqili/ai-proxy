"""Integration tests for GET /dashboard/api/requests/export."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.bus import StreamBus
from aiproxy.dashboard.routes import create_dashboard_router
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


async def _authed(app: FastAPI) -> tuple[httpx.AsyncClient, dict[str, str]]:
    c = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await c.post("/dashboard/login", json={"master_key": "k"})
    return c, {"aiproxy_session": r.cookies["aiproxy_session"]}


async def _seed(sm, rows: list[tuple[str, str]]) -> None:
    """rows: list of (provider, status) — status in {'done', 'pending'}."""
    async with sm() as s:
        for i, (provider, status) in enumerate(rows):
            await req_crud.create_pending(
                s, req_id=f"r{i}", api_key_id=None, provider=provider,
                endpoint="/v1/chat/completions", method="POST", model="gpt-4o",
                is_streaming=False, client_ip=None, client_ua=None,
                req_headers={}, req_query=None, req_body=b"{}",
                started_at=float(i),
            )
            if status == "done":
                await req_crud.mark_finished(
                    s, req_id=f"r{i}", status="done", status_code=200,
                    resp_headers={}, resp_body=b'{"ok":1}',
                    finished_at=float(i) + 1,
                )
        await s.commit()


async def test_export_requires_auth(app_ctx) -> None:
    app, _ = app_ctx
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/dashboard/api/requests/export")
    assert r.status_code == 401


async def test_export_all_rows(app_ctx) -> None:
    app, sm = app_ctx
    await _seed(sm, [("openai", "done"), ("anthropic", "done"), ("openai", "pending")])
    c, cookies = await _authed(app)
    try:
        r = await c.get("/dashboard/api/requests/export", cookies=cookies)
    finally:
        await c.aclose()
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 3
    assert len(data["rows"]) == 3
    assert "exported_at" in data
    # Rows carry the full serialization shape (incl. req_body_b64).
    assert "req_body_b64" in data["rows"][0]


async def test_export_with_provider_filter(app_ctx) -> None:
    app, sm = app_ctx
    await _seed(sm, [("openai", "done"), ("anthropic", "done"), ("openai", "done")])
    c, cookies = await _authed(app)
    try:
        r = await c.get(
            "/dashboard/api/requests/export?provider=openai",
            cookies=cookies,
        )
    finally:
        await c.aclose()
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    for row in data["rows"]:
        assert row["provider"] == "openai"


async def test_export_with_status_filter(app_ctx) -> None:
    app, sm = app_ctx
    await _seed(sm, [("openai", "done"), ("openai", "pending"), ("openai", "done")])
    c, cookies = await _authed(app)
    try:
        r = await c.get(
            "/dashboard/api/requests/export?status=done",
            cookies=cookies,
        )
    finally:
        await c.aclose()
    data = r.json()
    assert data["total"] == 2
    assert all(row["status"] == "done" for row in data["rows"])
