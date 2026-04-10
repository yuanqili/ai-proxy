"""Tests for dashboard HTTP endpoints."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from aiproxy.bus import StreamBus
from aiproxy.dashboard.routes import create_dashboard_router
from aiproxy.registry import ACTIVE_CHANNEL, RequestMeta, RequestRegistry


@pytest.fixture
def app():
    bus = StreamBus()
    registry = RequestRegistry(bus)
    app = FastAPI()
    app.include_router(create_dashboard_router(bus=bus, registry=registry))
    return app, bus, registry


async def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_index_html_served(app) -> None:
    a, _, _ = app
    async with await _client(a) as c:
        r = await c.get("/dashboard/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "AI Proxy" in r.text


async def test_api_active_returns_snapshot(app) -> None:
    a, _, registry = app
    registry.start(RequestMeta(
        req_id="r1", provider="openai", model="gpt-4o",
        method="POST", path="/chat/completions", client_ip=None,
    ))
    async with await _client(a) as c:
        r = await c.get("/dashboard/api/active")
    assert r.status_code == 200
    data = r.json()
    assert len(data["active"]) == 1
    assert data["active"][0]["req_id"] == "r1"
    assert data["history"] == []
