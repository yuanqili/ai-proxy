"""Integration tests for the label filter on GET /dashboard/api/requests."""
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


async def _seed(sm, rows: list[tuple[str, str | None, str | None]]) -> None:
    """rows: list of (req_id, labels, note)."""
    async with sm() as s:
        for i, (req_id, labels, note) in enumerate(rows):
            await req_crud.create_pending(
                s, req_id=req_id, api_key_id=None, provider="openai",
                endpoint="/v1/chat/completions", method="POST", model="gpt-4o",
                is_streaming=False, client_ip=None, client_ua=None,
                req_headers={}, req_query=None, req_body=b"{}",
                started_at=float(i), labels=labels, note=note,
            )
        await s.commit()


async def test_list_returns_labels_and_note(app_ctx) -> None:
    app, sm = app_ctx
    await _seed(sm, [
        ("r1", "prod,v1", "hello"),
        ("r2", None, None),
    ])
    c, cookies = await _authed(app)
    try:
        r = await c.get("/dashboard/api/requests", cookies=cookies)
    finally:
        await c.aclose()
    data = r.json()
    by_id = {row["req_id"]: row for row in data["rows"]}
    assert by_id["r1"]["labels"] == "prod,v1"
    assert by_id["r1"]["note"] == "hello"
    assert by_id["r2"]["labels"] is None
    assert by_id["r2"]["note"] is None


async def test_filter_by_single_label_matches_any_position(app_ctx) -> None:
    app, sm = app_ctx
    await _seed(sm, [
        ("only", "prod", None),         # sole label
        ("first", "prod,v1", None),     # first position
        ("last", "v1,prod", None),      # last position
        ("middle", "a,prod,b", None),   # middle position
        ("other", "staging,v2", None),  # no match
        ("null", None, None),           # no labels
    ])
    c, cookies = await _authed(app)
    try:
        r = await c.get("/dashboard/api/requests?label=prod", cookies=cookies)
    finally:
        await c.aclose()
    data = r.json()
    ids = {row["req_id"] for row in data["rows"]}
    assert ids == {"only", "first", "last", "middle"}


async def test_filter_by_multiple_labels_is_intersection(app_ctx) -> None:
    """Passing label=a&label=b must require BOTH labels on the row."""
    app, sm = app_ctx
    await _seed(sm, [
        ("both", "prod,v1", None),
        ("only-prod", "prod,staging", None),
        ("only-v1", "dev,v1", None),
        ("neither", "dev,staging", None),
    ])
    c, cookies = await _authed(app)
    try:
        r = await c.get("/dashboard/api/requests?label=prod&label=v1", cookies=cookies)
    finally:
        await c.aclose()
    data = r.json()
    ids = {row["req_id"] for row in data["rows"]}
    assert ids == {"both"}


async def test_filter_escapes_like_wildcards(app_ctx) -> None:
    """A label like '%dangerous' must not behave as a LIKE wildcard."""
    app, sm = app_ctx
    await _seed(sm, [
        ("safe", "prod,v1", None),
        ("wildcardy", "%dangerous", None),
    ])
    c, cookies = await _authed(app)
    try:
        r = await c.get("/dashboard/api/requests?label=%25", cookies=cookies)
    finally:
        await c.aclose()
    # Plain '%' as a label should match nothing, not everything.
    data = r.json()
    ids = {row["req_id"] for row in data["rows"]}
    assert "safe" not in ids  # would match everything if we didn't escape
