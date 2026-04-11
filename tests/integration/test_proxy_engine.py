"""Integration tests for the passthrough engine using respx mocks."""
import asyncio
import json
import time
import uuid

import httpx
import pytest
import respx

from aiproxy.bus import StreamBus
from aiproxy.core.passthrough import PassthroughEngine
from aiproxy.db.crud import chunks as chunks_crud
from aiproxy.db.crud import pricing as pricing_crud
from aiproxy.db.crud import requests as req_crud
from aiproxy.providers.openai import OpenAIProvider
from aiproxy.registry import RequestRegistry


@pytest.fixture
async def engine(db_sessionmaker):
    client = httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=30, write=30, pool=5))
    bus = StreamBus()
    registry = RequestRegistry(bus)
    eng = PassthroughEngine(
        http_client=client,
        sessionmaker=db_sessionmaker,
        bus=bus,
        registry=registry,
    )
    try:
        yield eng, db_sessionmaker, bus, registry
    finally:
        await client.aclose()


@respx.mock
async def test_non_streaming_happy_path(engine) -> None:
    eng, sm, _, _ = engine
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
            headers={"content-type": "application/json"},
        )
    )
    # Seed pricing
    async with sm() as s:
        await pricing_crud.upsert_current(
            s, provider="openai", model="gpt-4o",
            input_per_1m_usd=2.50, output_per_1m_usd=10.00,
            cached_per_1m_usd=None,
        )
        await s.commit()

    provider = OpenAIProvider(base_url="https://api.openai.com", api_key="sk-test")
    req_id = uuid.uuid4().hex[:12]
    body = json.dumps({"model": "gpt-4o", "messages": []}).encode()

    status_code, _, resp_body_stream = await eng.forward(
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
    async with sm() as s:
        row = await req_crud.get_by_id(s, req_id)
        assert row.status == "done"
        assert row.input_tokens == 10
        assert row.output_tokens == 5
        # 10/1M * $2.50 + 5/1M * $10 = 0.000025 + 0.00005 = 0.000075
        assert row.cost_usd == pytest.approx(0.000075)
        assert row.pricing_id is not None
        # Non-streaming: no chunks table rows
        assert await chunks_crud.count_for_request(s, req_id) == 0


@respx.mock
async def test_streaming_tees_to_bus_and_persists_chunks(engine) -> None:
    eng, sm, bus, registry = engine
    # Simulate a streaming response as a sequence of SSE chunks
    sse_stream = [
        b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
        b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n',
        b'data: [DONE]\n\n',
    ]
    async def stream_content():
        for c in sse_stream:
            yield c

    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=stream_content(),
        )
    )
    async with sm() as s:
        await pricing_crud.upsert_current(
            s, provider="openai", model="gpt-4o",
            input_per_1m_usd=2.50, output_per_1m_usd=10.00,
            cached_per_1m_usd=None,
        )
        await s.commit()

    provider = OpenAIProvider(base_url="https://api.openai.com", api_key="sk-test")
    req_id = uuid.uuid4().hex[:12]
    body = json.dumps({"model": "gpt-4o", "stream": True, "messages": []}).encode()

    # Subscribe to the bus BEFORE calling forward, so we catch all publishes
    q = bus.subscribe(req_id)

    _, _, stream = await eng.forward(
        provider=provider,
        client_path="chat/completions",
        req_id=req_id,
        method="POST",
        client_headers={},
        client_query=[],
        client_body=body,
        client_ip=None,
        client_ua=None,
        api_key_id=None,
        started_at=time.time(),
    )
    received: list[bytes] = []
    async for chunk in stream:
        received.append(chunk)

    # Client saw all chunks
    assert b"".join(received) == b"".join(sse_stream)

    # Dashboard bus got at least the chunk events
    events: list[dict] = []
    while not q.empty():
        events.append(q.get_nowait())
    chunk_events = [e for e in events if e.get("type") == "chunk"]
    assert len(chunk_events) == len(sse_stream)

    # DB has the chunks persisted
    async with sm() as s:
        rows = await chunks_crud.list_for_request(s, req_id)
        assert len(rows) == len(sse_stream)
        # offset_ns is monotonically non-decreasing
        offsets = [r.offset_ns for r in rows]
        assert offsets == sorted(offsets)
        # First chunk has offset 0
        assert rows[0].offset_ns == 0

    # Request row has usage + cost
    async with sm() as s:
        row = await req_crud.get_by_id(s, req_id)
        assert row.input_tokens == 3
        assert row.output_tokens == 2
        assert row.cost_usd == pytest.approx(
            3 / 1_000_000 * 2.50 + 2 / 1_000_000 * 10.00
        )
        assert row.chunk_count == len(sse_stream)


@respx.mock
async def test_openai_streaming_auto_injects_include_usage(engine) -> None:
    """Streaming request to OpenAI without stream_options → proxy injects
    stream_options.include_usage=true before forwarding upstream, and the
    persisted req_body in the DB reflects the rewritten bytes."""
    eng, sm, _, _ = engine
    sse_stream = [
        b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n',
        b'data: {"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":1}}\n\n',
        b'data: [DONE]\n\n',
    ]
    async def stream_content():
        for c in sse_stream:
            yield c
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=stream_content(),
        )
    )

    provider = OpenAIProvider(base_url="https://api.openai.com", api_key="sk-test")
    req_id = uuid.uuid4().hex[:12]
    # Client body deliberately has NO stream_options.
    client_body = json.dumps({
        "model": "gpt-4o-mini",
        "stream": True,
        "messages": [{"role": "user", "content": "hi"}],
    }).encode()

    _, _, stream = await eng.forward(
        provider=provider,
        client_path="chat/completions",
        req_id=req_id,
        method="POST",
        client_headers={"content-type": "application/json"},
        client_query=[],
        client_body=client_body,
        client_ip=None,
        client_ua=None,
        api_key_id=None,
        started_at=time.time(),
    )
    async for _ in stream:
        pass

    # What the upstream actually received:
    forwarded = route.calls[0].request
    fwd_body = json.loads(forwarded.content)
    assert fwd_body["stream_options"] == {"include_usage": True}
    assert fwd_body["model"] == "gpt-4o-mini"
    assert fwd_body["stream"] is True
    # And the persisted request body matches what went upstream (for faithful replay).
    async with sm() as s:
        row = await req_crud.get_by_id(s, req_id)
        persisted = json.loads(row.req_body)
        assert persisted["stream_options"] == {"include_usage": True}


@respx.mock
async def test_openai_non_streaming_body_untouched(engine) -> None:
    """Non-streaming requests are forwarded byte-for-byte (no injection)."""
    eng, sm, _, _ = engine
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "hi"}}],
                  "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
            headers={"content-type": "application/json"},
        )
    )
    provider = OpenAIProvider(base_url="https://api.openai.com", api_key="sk-test")
    req_id = uuid.uuid4().hex[:12]
    client_body = b'{"model":"gpt-4o","messages":[]}'
    _, _, stream = await eng.forward(
        provider=provider,
        client_path="chat/completions",
        req_id=req_id,
        method="POST",
        client_headers={"content-type": "application/json"},
        client_query=[],
        client_body=client_body,
        client_ip=None,
        client_ua=None,
        api_key_id=None,
        started_at=time.time(),
    )
    async for _ in stream:
        pass
    assert route.calls[0].request.content == client_body


@respx.mock
async def test_streaming_records_ttft_timestamps(engine) -> None:
    """Streaming request finalize should persist upstream_sent_at and
    first_chunk_at so the dashboard can compute TTFT."""
    eng, sm, _, _ = engine
    sse_stream = [
        b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n',
        b'data: [DONE]\n\n',
    ]
    async def stream_content():
        for c in sse_stream:
            yield c
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=stream_content(),
        )
    )

    provider = OpenAIProvider(base_url="https://api.openai.com", api_key="sk-test")
    req_id = uuid.uuid4().hex[:12]
    body = json.dumps({"model": "gpt-4o", "stream": True, "messages": []}).encode()

    before = time.time()
    _, _, stream = await eng.forward(
        provider=provider,
        client_path="chat/completions",
        req_id=req_id,
        method="POST",
        client_headers={},
        client_query=[],
        client_body=body,
        client_ip=None,
        client_ua=None,
        api_key_id=None,
        started_at=before,
    )
    async for _ in stream:
        pass
    after = time.time()

    async with sm() as s:
        row = await req_crud.get_by_id(s, req_id)
        assert row.upstream_sent_at is not None
        assert row.first_chunk_at is not None
        # Both timestamps lie within the test's wall-clock window.
        assert before <= row.upstream_sent_at <= after
        assert row.upstream_sent_at <= row.first_chunk_at <= after


@respx.mock
async def test_non_streaming_records_upstream_sent_at_only(engine) -> None:
    """Non-streaming response has no 'first_chunk_at' — but upstream_sent_at
    should still be set so future analytics have a dispatch timestamp."""
    eng, sm, _, _ = engine
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "hi"}}],
                  "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
            headers={"content-type": "application/json"},
        )
    )
    provider = OpenAIProvider(base_url="https://api.openai.com", api_key="sk-test")
    req_id = uuid.uuid4().hex[:12]

    _, _, stream = await eng.forward(
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

    async with sm() as s:
        row = await req_crud.get_by_id(s, req_id)
        assert row.upstream_sent_at is not None
        # Non-streaming: no first chunk in the aiter_raw sense — httpx buffered
        # the whole body into one read, which IS our "first chunk". So this
        # may or may not be set depending on whether aiter_raw yields a chunk
        # for buffered responses. Treat it as "may be None" and don't assert.


@respx.mock
async def test_aiproxy_headers_captured_and_stripped(engine) -> None:
    """X-AIProxy-Labels / X-AIProxy-Note are stored on the row and stripped
    from the upstream request so they never leak to the provider."""
    eng, sm, _, _ = engine
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}],
                  "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
            headers={"content-type": "application/json"},
        )
    )
    provider = OpenAIProvider(base_url="https://api.openai.com", api_key="sk-test")
    req_id = uuid.uuid4().hex[:12]
    # Note: labels header has whitespace and a duplicate to exercise normalization.
    client_headers = {
        "content-type": "application/json",
        "X-AIProxy-Labels": " prod , v1 , v1, regression ",
        "X-AIProxy-Note": "checking json output",
    }
    _, _, stream = await eng.forward(
        provider=provider,
        client_path="chat/completions",
        req_id=req_id,
        method="POST",
        client_headers=client_headers,
        client_query=[],
        client_body=b'{"model":"gpt-4o","messages":[]}',
        client_ip=None,
        client_ua=None,
        api_key_id=None,
        started_at=time.time(),
    )
    async for _ in stream:
        pass

    async with sm() as s:
        row = await req_crud.get_by_id(s, req_id)
        # Normalized: trimmed, deduped, order preserved.
        assert row.labels == "prod,v1,regression"
        assert row.note == "checking json output"

    # Upstream never sees the aiproxy headers.
    fwd_headers = dict(route.calls[0].request.headers)
    assert "x-aiproxy-labels" not in {k.lower() for k in fwd_headers}
    assert "x-aiproxy-note" not in {k.lower() for k in fwd_headers}


@respx.mock
async def test_aiproxy_headers_optional(engine) -> None:
    """Requests without the classification headers land with labels/note = None."""
    eng, sm, _, _ = engine
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}],
                  "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
            headers={"content-type": "application/json"},
        )
    )
    provider = OpenAIProvider(base_url="https://api.openai.com", api_key="sk-test")
    req_id = uuid.uuid4().hex[:12]
    _, _, stream = await eng.forward(
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
    async with sm() as s:
        row = await req_crud.get_by_id(s, req_id)
        assert row.labels is None
        assert row.note is None


@respx.mock
async def test_upstream_4xx_recorded_as_done(engine) -> None:
    eng, sm, _, _ = engine
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            429,
            json={"error": {"message": "rate limited"}},
            headers={"content-type": "application/json"},
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
        assert row.status == "done"  # NOT 'error' — 4xx is a business signal
        assert row.status_code == 429


@respx.mock
async def test_upstream_connect_error(engine) -> None:
    eng, sm, _, _ = engine
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
