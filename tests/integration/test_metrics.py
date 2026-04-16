"""Metrics endpoint integration test.

Verifies /metrics is reachable and includes the metric series we care about
after a successful proxied request.
"""
import httpx
import pytest
import respx
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.app import build_app
from aiproxy.db.crud import api_keys as api_keys_crud
from aiproxy.db.models import Base


@pytest.fixture
async def app_and_sm():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    async with sm() as s:
        await api_keys_crud.create(
            s, key="sk-aiprx-metrics", name="metrics-test", note=None,
            valid_from=None, valid_to=None,
        )
        await s.commit()

    app = build_app(
        sessionmaker=sm,
        openai_base_url="https://api.openai.com",
        openai_api_key="sk-test",
        anthropic_base_url="https://api.anthropic.com",
        anthropic_api_key="sk-ant-test",
        openrouter_base_url="https://openrouter.ai",
        openrouter_api_key="sk-or-test",
    )
    try:
        yield app, sm
    finally:
        await app.state.http_client.aclose()
        await engine.dispose()


async def test_metrics_endpoint_reachable_and_has_default_metric_names(app_and_sm) -> None:
    app, _ = app_and_sm
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text
    # Each metric family declares itself via a HELP line — that line is
    # present from first import even before any sample is recorded.
    for name in (
        "aiproxy_requests_total",
        "aiproxy_request_duration_seconds",
        "aiproxy_ttft_seconds",
        "aiproxy_tokens_total",
        "aiproxy_cost_usd_total",
        "aiproxy_active_requests",
        "aiproxy_errors_total",
    ):
        assert f"# HELP {name}" in body, f"missing {name} in /metrics output"


@respx.mock
async def test_metrics_emitted_after_successful_proxy_request(app_and_sm) -> None:
    app, _ = app_and_sm
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "chatcmpl-m",
                "usage": {"prompt_tokens": 7, "completion_tokens": 11, "total_tokens": 18},
            },
        )
    )

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(
            "/openai/chat/completions",
            headers={"authorization": "Bearer sk-aiprx-metrics"},
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200
        m = await c.get("/metrics")

    body = m.text
    # Counter has been incremented for this specific label combination.
    assert 'aiproxy_requests_total{model="gpt-4o",provider="openai",status="done",stream="false"}' in body
    # Tokens + duration should have non-zero series for gpt-4o.
    assert 'aiproxy_tokens_total{kind="input",model="gpt-4o",provider="openai"}' in body
    assert 'aiproxy_tokens_total{kind="output",model="gpt-4o",provider="openai"}' in body
    assert 'aiproxy_request_duration_seconds_count{model="gpt-4o",provider="openai"}' in body
