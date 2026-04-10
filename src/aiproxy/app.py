"""FastAPI app factory and lifespan."""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from aiproxy.dashboard.routes import router as dashboard_router
from aiproxy.proxy.openrouter import router as openrouter_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Long read timeout for reasoning models (o3/Claude thinking can take minutes).
    timeout = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=50)
    app.state.http_client = httpx.AsyncClient(
        timeout=timeout, limits=limits, http2=True
    )
    try:
        yield
    finally:
        await app.state.http_client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="AI Proxy", lifespan=lifespan)
    app.include_router(dashboard_router)
    app.include_router(openrouter_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
