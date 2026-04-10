"""End-to-end test: build full app, send a mocked request, verify DB row."""
import httpx
import pytest
import respx
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.app import build_app
from aiproxy.db.crud import api_keys as api_keys_crud
from aiproxy.db.models import Base, Request


@pytest.fixture
async def app_and_sm():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    async with sm() as s:
        await api_keys_crud.create(
            s, key="sk-aiprx-e2e", name="e2e", note=None,
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
        # build_app stored the http_client on app.state — close it
        await app.state.http_client.aclose()
        await engine.dispose()


@respx.mock
async def test_end_to_end_proxy_persists_request(app_and_sm) -> None:
    app, sm = app_and_sm
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "chatcmpl-x"})
    )

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(
            "/openai/chat/completions",
            headers={"authorization": "Bearer sk-aiprx-e2e"},
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert r.status_code == 200
    assert r.json()["id"] == "chatcmpl-x"

    async with sm() as s:
        result = await s.execute(select(Request))
        reqs = result.scalars().all()
    assert len(reqs) == 1
    assert reqs[0].provider == "openai"
    assert reqs[0].status == "done"
    assert reqs[0].status_code == 200
    assert reqs[0].model == "gpt-4o"
