"""FastAPI app factory + production lifespan.

Two entry points:
  - `build_app(...)` — synchronous factory for tests. Takes a pre-built
    sessionmaker so tests can inject in-memory SQLite. Stores the created
    http_client on `app.state` so the caller can close it in teardown.
  - `app` (module-level) — production instance. Uses `lifespan()` to
    async-init the DB from settings, then registers the router in-place.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from aiproxy.auth.proxy_auth import ApiKeyCache
from aiproxy.bus import StreamBus
from aiproxy.core.passthrough import PassthroughEngine
from aiproxy.db.engine import create_engine_and_sessionmaker
from aiproxy.db.models import Base
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
    """Synchronous factory for tests.

    The caller is responsible for closing `app.state.http_client` on teardown.
    """
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
    app.include_router(create_router(engine=engine, providers=providers, cache=cache))

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Production lifespan: init DB + http client, register router, dispose on exit."""
    sql_engine, sessionmaker = create_engine_and_sessionmaker(settings.database_url)
    async with sql_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

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

    # Register the router in-place. FastAPI allows include_router during lifespan
    # before the first request is served.
    app.include_router(create_router(engine=engine, providers=providers, cache=cache))
    app.state.http_client = http_client
    app.state.sessionmaker = sessionmaker
    app.state.bus = bus
    app.state.registry = registry

    try:
        yield
    finally:
        await http_client.aclose()
        await sql_engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="AI Proxy", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
