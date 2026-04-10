"""Integration tests for dashboard login + auth dependency."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from aiproxy.bus import StreamBus
from aiproxy.dashboard.routes import create_dashboard_router
from aiproxy.registry import RequestRegistry


@pytest.fixture
def app():
    bus = StreamBus()
    registry = RequestRegistry(bus)
    app = FastAPI()
    app.include_router(create_dashboard_router(
        bus=bus,
        registry=registry,
        sessionmaker=None,  # login endpoint doesn't need DB
        master_key="test-master-key",
        session_secret="test-secret-0123456789",
        secure_cookies=False,
    ))
    return app


async def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_login_with_correct_master_key_returns_cookie(app) -> None:
    async with await _client(app) as c:
        r = await c.post("/dashboard/login", json={"master_key": "test-master-key"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert "aiproxy_session" in r.cookies


async def test_login_with_wrong_master_key_returns_401(app) -> None:
    async with await _client(app) as c:
        r = await c.post("/dashboard/login", json={"master_key": "wrong"})
    assert r.status_code == 401


async def test_protected_route_without_cookie_returns_401(app) -> None:
    async with await _client(app) as c:
        r = await c.get("/dashboard/api/active")
    assert r.status_code == 401


async def test_protected_route_with_valid_cookie_succeeds(app) -> None:
    async with await _client(app) as c:
        login = await c.post("/dashboard/login", json={"master_key": "test-master-key"})
        assert login.status_code == 200
        cookie = login.cookies.get("aiproxy_session")
        assert cookie

        r = await c.get(
            "/dashboard/api/active",
            cookies={"aiproxy_session": cookie},
        )
    assert r.status_code == 200


async def test_logout_clears_cookie(app) -> None:
    async with await _client(app) as c:
        login = await c.post("/dashboard/login", json={"master_key": "test-master-key"})
        cookie = login.cookies.get("aiproxy_session")

        r = await c.post(
            "/dashboard/logout",
            cookies={"aiproxy_session": cookie},
        )
    assert r.status_code == 200
    # Cookie should be unset — the response sets an empty aiproxy_session
    # with max_age=0
    assert "aiproxy_session" in r.headers.get("set-cookie", "")
