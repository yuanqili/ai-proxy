"""Integration tests for the passthrough engine using respx mocks."""
import json
import time
import uuid

import httpx
import pytest
import respx

from aiproxy.core.passthrough import PassthroughEngine
from aiproxy.db.crud import requests as req_crud
from aiproxy.providers.openai import OpenAIProvider


@pytest.fixture
async def engine(db_sessionmaker):
    client = httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=30, write=30, pool=5))
    eng = PassthroughEngine(http_client=client, sessionmaker=db_sessionmaker)
    try:
        yield eng, db_sessionmaker
    finally:
        await client.aclose()


@respx.mock
async def test_non_streaming_happy_path(engine) -> None:
    eng, sm = engine
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "hi"}}]},
            headers={"content-type": "application/json"},
        )
    )

    provider = OpenAIProvider(base_url="https://api.openai.com", api_key="sk-test")
    req_id = uuid.uuid4().hex[:12]
    body = json.dumps({"model": "gpt-4o", "messages": []}).encode()

    status_code, resp_headers, resp_body_stream = await eng.forward(
        provider=provider,
        client_path="chat/completions",
        req_id=req_id,
        method="POST",
        client_headers={"content-type": "application/json"},
        client_query=[],
        client_body=body,
        client_ip="127.0.0.1",
        client_ua="pytest",
        api_key_id=None,
        started_at=time.time(),
    )
    body_bytes = b""
    async for chunk in resp_body_stream:
        body_bytes += chunk

    assert status_code == 200
    data = json.loads(body_bytes)
    assert data["choices"][0]["message"]["content"] == "hi"

    async with sm() as s:
        row = await req_crud.get_by_id(s, req_id)
        assert row is not None
        assert row.status == "done"
        assert row.status_code == 200
        assert row.provider == "openai"
        assert row.model == "gpt-4o"
        assert row.req_body == body
        assert row.resp_body == body_bytes


@respx.mock
async def test_upstream_4xx_recorded_as_done(engine) -> None:
    eng, sm = engine
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            429, json={"error": {"message": "rate limited"}}, headers={"content-type": "application/json"}
        )
    )
    provider = OpenAIProvider(base_url="https://api.openai.com", api_key="sk-test")
    req_id = uuid.uuid4().hex[:12]

    status_code, _, stream = await eng.forward(
        provider=provider,
        client_path="chat/completions",
        req_id=req_id,
        method="POST",
        client_headers={},
        client_query=[],
        client_body=b'{"model":"gpt-4o","messages":[]}',
        client_ip=None,
        client_ua=None,
        api_key_id=None,
        started_at=time.time(),
    )
    async for _ in stream:
        pass

    assert status_code == 429
    async with sm() as s:
        row = await req_crud.get_by_id(s, req_id)
        assert row is not None
        assert row.status == "done"  # NOT 'error' — 4xx is a business signal
        assert row.status_code == 429


@respx.mock
async def test_upstream_connect_error(engine) -> None:
    eng, sm = engine
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("DNS failure")
    )
    provider = OpenAIProvider(base_url="https://api.openai.com", api_key="sk-test")
    req_id = uuid.uuid4().hex[:12]

    with pytest.raises(httpx.ConnectError):
        await eng.forward(
            provider=provider,
            client_path="chat/completions",
            req_id=req_id,
            method="POST",
            client_headers={},
            client_query=[],
            client_body=b'{"model":"gpt-4o"}',
            client_ip=None,
            client_ua=None,
            api_key_id=None,
            started_at=time.time(),
        )

    async with sm() as s:
        row = await req_crud.get_by_id(s, req_id)
        assert row is not None
        assert row.status == "error"
        assert row.error_class == "upstream_connect"
