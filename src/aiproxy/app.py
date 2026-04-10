"""FastAPI app factory + production lifespan.

Two entry points:
  - `build_app(...)` — synchronous factory for tests.
  - `app` (module-level) — production instance, uses `lifespan()`.

Phase 2 additions:
  - StreamBus + RequestRegistry created here (one per app instance)
  - Dashboard router mounted alongside the proxy router
  - Retention background task spawned in lifespan
  - Pricing seed on first startup
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from aiproxy.auth.proxy_auth import ApiKeyCache
from aiproxy.bus import StreamBus
from aiproxy.core.passthrough import PassthroughEngine
from aiproxy.dashboard.routes import create_dashboard_router
from aiproxy.db.crud import config as config_crud
from aiproxy.db.engine import create_engine_and_sessionmaker
from aiproxy.db.fts import install_fts_schema
from aiproxy.db.models import Base
from aiproxy.db.retention import retention_loop
from aiproxy.pricing.seed import seed_pricing_if_empty
from aiproxy.providers import build_registry
from aiproxy.registry import RequestRegistry
from aiproxy.routers.proxy import create_router
from aiproxy.settings import settings


def _make_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0),
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        http2=True,
    )


def build_app(
    *,
    sessionmaker: async_sessionmaker,
    openai_base_url: str,
    openai_api_key: str,
    anthropic_base_url: str,
    anthropic_api_key: str,
    openrouter_base_url: str,
    openrouter_api_key: str,
) -> FastAPI:
    """Synchronous factory for tests."""
    http_client = _make_http_client()
    bus = StreamBus()
    registry = RequestRegistry(bus)
    engine = PassthroughEngine(
        http_client=http_client,
        sessionmaker=sessionmaker,
        bus=bus,
        registry=registry,
    )
    providers = build_registry(
        openai_base_url=openai_base_url,
        openai_api_key=openai_api_key,
        anthropic_base_url=anthropic_base_url,
        anthropic_api_key=anthropic_api_key,
        openrouter_base_url=openrouter_base_url,
        openrouter_api_key=openrouter_api_key,
    )
    cache = ApiKeyCache(sessionmaker, ttl_seconds=60)

    app = FastAPI(title="AI Proxy")
    app.state.http_client = http_client
    app.state.sessionmaker = sessionmaker
    app.state.bus = bus
    app.state.registry = registry
    # Dashboard router must be registered BEFORE the proxy dispatcher.
    # The proxy dispatcher uses `/{provider}/{full_path:path}` which would
    # otherwise swallow `/dashboard/*` requests as "unknown provider: dashboard".
    app.include_router(create_dashboard_router(
        bus=bus,
        registry=registry,
        sessionmaker=sessionmaker,
        master_key=settings.proxy_master_key,
        session_secret=settings.session_secret,
        secure_cookies=settings.secure_cookies,
    ))
    app.include_router(create_router(engine=engine, providers=providers, cache=cache))

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Production lifespan: init DB + http client, register routes, spawn
    retention task, dispose on exit."""
    sql_engine, sessionmaker = create_engine_and_sessionmaker(settings.database_url)
    async with sql_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await install_fts_schema(conn)

    # Seed pricing if the table is empty (first-run bootstrap)
    await seed_pricing_if_empty(sessionmaker)

    http_client = _make_http_client()
    bus = StreamBus()
    registry = RequestRegistry(bus)
    engine = PassthroughEngine(
        http_client=http_client,
        sessionmaker=sessionmaker,
        bus=bus,
        registry=registry,
    )
    providers = build_registry(
        openai_base_url=settings.openai_base_url,
        openai_api_key=settings.openai_api_key,
        anthropic_base_url=settings.anthropic_base_url,
        anthropic_api_key=settings.anthropic_api_key,
        openrouter_base_url=settings.openrouter_base_url,
        openrouter_api_key=settings.openrouter_api_key,
    )
    cache = ApiKeyCache(sessionmaker, ttl_seconds=60)

    # Dashboard router must be registered BEFORE the proxy dispatcher (see build_app).
    app.include_router(create_dashboard_router(
        bus=bus,
        registry=registry,
        sessionmaker=sessionmaker,
        master_key=settings.proxy_master_key,
        session_secret=settings.session_secret,
        secure_cookies=settings.secure_cookies,
    ))
    app.include_router(create_router(engine=engine, providers=providers, cache=cache))
    app.state.http_client = http_client
    app.state.sessionmaker = sessionmaker
    app.state.bus = bus
    app.state.registry = registry

    # Background retention task — reads threshold from the config table
    async def _get_full_count() -> int:
        async with sessionmaker() as s:
            return int(await config_crud.get(s, "retention.full_count", default=500))

    retention_task = asyncio.create_task(retention_loop(
        sessionmaker,
        get_full_count=_get_full_count,
        interval_seconds=60.0,
    ))

    try:
        yield
    finally:
        retention_task.cancel()
        try:
            await retention_task
        except asyncio.CancelledError:
            pass
        await http_client.aclose()
        await sql_engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="AI Proxy", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
