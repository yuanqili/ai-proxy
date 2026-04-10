"""Integration tests for GET /dashboard/api/requests/{id}/replay."""
import time
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aiproxy.bus import StreamBus
from aiproxy.dashboard.routes import create_dashboard_router
from aiproxy.db.crud import requests as req_crud
from aiproxy.db.crud import chunks as chunks_crud
from aiproxy.db.engine import create_engine_and_sessionmaker
from aiproxy.db.models import Base
from aiproxy.providers.openai import OpenAIProvider
from aiproxy.registry import RequestRegistry


@pytest_asyncio.fixture
async def sessionmaker_fx():
    engine, sm = create_engine_and_sessionmaker("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield sm
    await engine.dispose()


@pytest.fixture
def app(sessionmaker_fx):
    bus = StreamBus()
    registry = RequestRegistry(bus)
    providers_map = {"openai": OpenAIProvider(base_url="http://x", api_key="x")}
    app = FastAPI()
    app.include_router(create_dashboard_router(
        bus=bus,
        registry=registry,
        sessionmaker=sessionmaker_fx,
        master_key="test-key",
        session_secret="test-secret-xxx",
        secure_cookies=False,
        providers_map=providers_map,
    ))
    return app


@pytest.fixture
def client(app):
    c = TestClient(app)
    r = c.post("/dashboard/login", json={"master_key": "test-key"})
    assert r.status_code == 200
    return c


@pytest_asyncio.fixture
async def streaming_request(sessionmaker_fx):
    """Seed a streaming OpenAI request with three chunks: role, content, content+usage."""
    async with sessionmaker_fx() as s:
        await req_crud.create_pending(
            s,
            req_id="rp1",
            api_key_id=None,
            provider="openai",
            endpoint="/openai/chat/completions",
            method="POST",
            model="gpt-4o-mini",
            is_streaming=True,
            client_ip=None,
            client_ua=None,
            req_headers={},
            req_query=None,
            req_body=b'{"model":"gpt-4o-mini","stream":true}',
            started_at=time.time(),
        )
        await req_crud.mark_finished(
            s,
            req_id="rp1",
            status="done",
            status_code=200,
            resp_headers={"content-type": "text/event-stream"},
            resp_body=b"",
            finished_at=time.time() + 2.0,
        )
        await chunks_crud.insert_batch(
            s,
            "rp1",
            [
                (0, 0, b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'),
                (1, 500_000_000, b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'),
                (2, 1_200_000_000, b'data: {"choices":[{"delta":{"content":" world"}}],"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n'),
                (3, 1_250_000_000, b'data: [DONE]\n\n'),
            ],
        )
        await s.commit()


def test_replay_requires_auth(app):
    c = TestClient(app)
    r = c.get("/dashboard/api/requests/rp1/replay")
    assert r.status_code == 401


def test_replay_not_found(client):
    r = client.get("/dashboard/api/requests/missing/replay")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_replay_returns_parsed_chunks(client, streaming_request):
    r = client.get("/dashboard/api/requests/rp1/replay")
    assert r.status_code == 200
    data = r.json()
    assert data["req_id"] == "rp1"
    assert data["is_streaming"] is True
    assert data["provider"] == "openai"
    chunks = data["chunks"]
    assert len(chunks) == 4
    # Chunk 0: role header, no text
    assert chunks[0]["text_delta"] == ""
    assert chunks[0]["size"] > 0
    assert chunks[0]["offset_ns"] == 0
    # Chunk 1: first content
    assert chunks[1]["text_delta"] == "Hello"
    assert chunks[1]["offset_ns"] == 500_000_000
    # Chunk 2: second content
    assert chunks[2]["text_delta"] == " world"
    # Chunk 3: [DONE] sentinel
    assert chunks[3]["text_delta"] == ""
    # first_content_offset_ns points at chunk 1 (the first non-empty text_delta)
    assert data["first_content_offset_ns"] == 500_000_000
    # total_duration_ns is the max offset_ns across chunks
    assert data["total_duration_ns"] == 1_250_000_000
    # raw_b64 is present for each chunk
    for c in chunks:
        assert "raw_b64" in c
        assert isinstance(c["raw_b64"], str)


@pytest.mark.asyncio
async def test_replay_non_streaming_returns_empty_chunks(client, sessionmaker_fx):
    async with sessionmaker_fx() as s:
        await req_crud.create_pending(
            s,
            req_id="rp2",
            api_key_id=None,
            provider="openai",
            endpoint="/openai/chat/completions",
            method="POST",
            model="gpt-4o-mini",
            is_streaming=False,
            client_ip=None,
            client_ua=None,
            req_headers={},
            req_query=None,
            req_body=b'{}',
            started_at=time.time(),
        )
        await s.commit()
    r = client.get("/dashboard/api/requests/rp2/replay")
    assert r.status_code == 200
    data = r.json()
    assert data["is_streaming"] is False
    assert data["chunks"] == []
    assert data["first_content_offset_ns"] is None
    assert data["total_duration_ns"] == 0
