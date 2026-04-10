"""Tests for the proxy dispatcher router via FastAPI TestClient."""
import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport

from aiproxy.auth.proxy_auth import ApiKeyCache
from aiproxy.bus import StreamBus
from aiproxy.core.passthrough import PassthroughEngine
from aiproxy.db.crud import api_keys as api_keys_crud
from aiproxy.providers import build_registry
from aiproxy.registry import RequestRegistry
from aiproxy.routers.proxy import create_router


@pytest.fixture
async def app_with_seeded_key(db_sessionmaker):
    async with db_sessionmaker() as s:
        await api_keys_crud.create(
            s, key="sk-aiprx-valid", name="test", note=None,
            valid_from=None, valid_to=None,
        )
        await s.commit()

    upstream_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    bus = StreamBus()
    registry = RequestRegistry(bus)
    engine = PassthroughEngine(
        http_client=upstream_client,
        sessionmaker=db_sessionmaker,
        bus=bus,
        registry=registry,
    )
    providers = build_registry(
        openai_base_url="https://api.openai.com",
        openai_api_key="sk-test",
        anthropic_base_url="https://api.anthropic.com",
        anthropic_api_key="sk-ant-test",
        openrouter_base_url="https://openrouter.ai",
        openrouter_api_key="sk-or-test",
    )
    cache = ApiKeyCache(db_sessionmaker, ttl_seconds=60)

    app = FastAPI()
    app.include_router(create_router(engine=engine, providers=providers, cache=cache))

    try:
        yield app
    finally:
        await upstream_client.aclose()


async def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@respx.mock
async def test_unknown_provider_returns_404(app_with_seeded_key) -> None:
    async with await _client(app_with_seeded_key) as c:
        r = await c.post(
            "/unknown-provider/foo",
            headers={"authorization": "Bearer sk-aiprx-valid"},
            json={},
        )
    assert r.status_code == 404


@respx.mock
async def test_missing_auth_returns_401(app_with_seeded_key) -> None:
    async with await _client(app_with_seeded_key) as c:
        r = await c.post("/openai/chat/completions", json={"model": "gpt-4o"})
    assert r.status_code == 401


@respx.mock
async def test_happy_path_dispatches_to_openai(app_with_seeded_key) -> None:
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    async with await _client(app_with_seeded_key) as c:
        r = await c.post(
            "/openai/chat/completions",
            headers={"authorization": "Bearer sk-aiprx-valid"},
            json={"model": "gpt-4o", "messages": []},
        )
    assert r.status_code == 200
    assert r.json()["ok"] is True
