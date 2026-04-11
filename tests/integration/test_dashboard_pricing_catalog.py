"""Integration tests for /dashboard/api/pricing/catalog/{preview,apply}.

The LiteLLM catalog JSON is mocked via respx so the test suite stays fully
in-process and offline.
"""
import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.bus import StreamBus
from aiproxy.dashboard.routes import create_dashboard_router
from aiproxy.db.crud import pricing as pricing_crud
from aiproxy.db.models import Base
from aiproxy.pricing.catalog import (
    LITELLM_CATALOG_URL,
    apply_catalog,
    diff_against_db,
    parse_catalog,
)
from aiproxy.registry import RequestRegistry

# Minimal fake catalog: one new row, one unchanged row (once seeded),
# one price-changed row (once seeded), one unrelated bedrock entry that
# should be ignored.
FAKE_CATALOG = {
    "sample_spec": {"mode": "chat"},
    "claude-sonnet-4-6": {
        "litellm_provider": "anthropic",
        "mode": "chat",
        "input_cost_per_token": 3e-6,
        "output_cost_per_token": 15e-6,
        "cache_read_input_token_cost": 3e-7,
    },
    "gpt-4o": {
        "litellm_provider": "openai",
        "mode": "chat",
        "input_cost_per_token": 2.5e-6,
        "output_cost_per_token": 10e-6,
    },
    "gpt-4o-mini": {
        "litellm_provider": "openai",
        "mode": "chat",
        "input_cost_per_token": 2e-7,    # new price, different from seed 0.15
        "output_cost_per_token": 8e-7,   # new price, different from seed 0.60
    },
    "bedrock/anthropic.claude-v2": {
        "litellm_provider": "bedrock_converse",
        "mode": "chat",
        "input_cost_per_token": 1e-5,
        "output_cost_per_token": 3e-5,
    },
}


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


# ── Pure DB-level tests ────────────────────────────────────────────────────

async def test_diff_against_empty_db(app_ctx) -> None:
    _, sm = app_ctx
    entries = parse_catalog(FAKE_CATALOG)
    async with sm() as s:
        diff = await diff_against_db(s, entries)
    # All 3 non-bedrock entries should be added.
    added_models = {(e["provider"], e["model"]) for e in diff["would_add"]}
    assert added_models == {
        ("anthropic", "claude-sonnet-4-6"),
        ("openai", "gpt-4o"),
        ("openai", "gpt-4o-mini"),
    }
    assert diff["would_supersede"] == []
    assert diff["unchanged"] == 0


async def test_diff_detects_unchanged_vs_changed(app_ctx) -> None:
    _, sm = app_ctx
    # Pre-seed: gpt-4o matches catalog exactly, gpt-4o-mini at old price.
    async with sm() as s:
        await pricing_crud.upsert_current(
            s, provider="openai", model="gpt-4o",
            input_per_1m_usd=2.5, output_per_1m_usd=10.0, cached_per_1m_usd=None,
        )
        await pricing_crud.upsert_current(
            s, provider="openai", model="gpt-4o-mini",
            input_per_1m_usd=0.15, output_per_1m_usd=0.60, cached_per_1m_usd=0.075,
        )
        await s.commit()

    entries = parse_catalog(FAKE_CATALOG)
    async with sm() as s:
        diff = await diff_against_db(s, entries)

    # claude-sonnet-4-6 is still missing → added
    add_models = {(e["provider"], e["model"]) for e in diff["would_add"]}
    assert add_models == {("anthropic", "claude-sonnet-4-6")}
    # gpt-4o-mini has a price change → superseded
    sup_models = {(c["provider"], c["model"]) for c in diff["would_supersede"]}
    assert sup_models == {("openai", "gpt-4o-mini")}
    # gpt-4o matches exactly → unchanged
    assert diff["unchanged"] == 1


async def test_apply_writes_add_and_supersede(app_ctx) -> None:
    _, sm = app_ctx
    async with sm() as s:
        await pricing_crud.upsert_current(
            s, provider="openai", model="gpt-4o-mini",
            input_per_1m_usd=0.15, output_per_1m_usd=0.60, cached_per_1m_usd=0.075,
        )
        await s.commit()

    entries = parse_catalog(FAKE_CATALOG)
    async with sm() as s:
        summary = await apply_catalog(s, entries)
        await s.commit()

    assert summary["added"] == 2       # claude-sonnet-4-6, gpt-4o
    assert summary["superseded"] == 1  # gpt-4o-mini
    assert summary["unchanged"] == 0

    # Effective gpt-4o-mini row now has the catalog price, not the seed.
    async with sm() as s:
        import time
        row = await pricing_crud.find_effective(
            s, provider="openai", model="gpt-4o-mini", at=time.time(),
        )
    assert row.input_per_1m_usd == pytest.approx(0.2)
    assert row.output_per_1m_usd == pytest.approx(0.8)


async def test_apply_is_idempotent(app_ctx) -> None:
    """Running apply twice in a row should write nothing the second time."""
    _, sm = app_ctx
    entries = parse_catalog(FAKE_CATALOG)
    async with sm() as s:
        first = await apply_catalog(s, entries)
        await s.commit()
    async with sm() as s:
        second = await apply_catalog(s, entries)
        await s.commit()
    assert first["added"] >= 1
    assert second["added"] == 0
    assert second["superseded"] == 0
    assert second["unchanged"] == first["added"] + first["superseded"]


# ── HTTP endpoint tests (mocked upstream) ──────────────────────────────────

@respx.mock
async def test_preview_endpoint_returns_diff(app_ctx) -> None:
    app, _ = app_ctx
    respx.get(LITELLM_CATALOG_URL).mock(
        return_value=httpx.Response(200, json=FAKE_CATALOG)
    )
    c, cookies = await _authed(app)
    try:
        r = await c.get("/dashboard/api/pricing/catalog/preview", cookies=cookies)
    finally:
        await c.aclose()
    assert r.status_code == 200
    data = r.json()
    assert data["source"] == LITELLM_CATALOG_URL
    assert data["entries_total"] == 3   # excludes bedrock + sample_spec
    assert len(data["would_add"]) == 3  # all new on empty DB
    assert data["would_supersede"] == []
    assert data["unchanged"] == 0


@respx.mock
async def test_apply_endpoint_writes_rows(app_ctx) -> None:
    app, sm = app_ctx
    respx.get(LITELLM_CATALOG_URL).mock(
        return_value=httpx.Response(200, json=FAKE_CATALOG)
    )
    c, cookies = await _authed(app)
    try:
        r = await c.post("/dashboard/api/pricing/catalog/apply", cookies=cookies)
    finally:
        await c.aclose()
    assert r.status_code == 200
    data = r.json()
    assert data["added"] == 3
    assert data["superseded"] == 0

    # Verify claude-sonnet-4-6 now has pricing in the DB.
    async with sm() as s:
        import time
        row = await pricing_crud.find_effective(
            s, provider="anthropic", model="claude-sonnet-4-6", at=time.time(),
        )
    assert row is not None
    assert row.input_per_1m_usd == pytest.approx(3.0)
    assert row.output_per_1m_usd == pytest.approx(15.0)
    assert row.cached_per_1m_usd == pytest.approx(0.3)
    assert row.note == "litellm-catalog"


@respx.mock
async def test_preview_endpoint_upstream_failure_returns_502(app_ctx) -> None:
    app, _ = app_ctx
    respx.get(LITELLM_CATALOG_URL).mock(
        side_effect=httpx.ConnectError("DNS boom")
    )
    c, cookies = await _authed(app)
    try:
        r = await c.get("/dashboard/api/pricing/catalog/preview", cookies=cookies)
    finally:
        await c.aclose()
    assert r.status_code == 502


async def test_preview_endpoint_requires_auth(app_ctx) -> None:
    app, _ = app_ctx
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/dashboard/api/pricing/catalog/preview")
    assert r.status_code == 401


async def test_apply_endpoint_requires_auth(app_ctx) -> None:
    app, _ = app_ctx
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/dashboard/api/pricing/catalog/apply")
    assert r.status_code == 401
