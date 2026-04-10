# Phase 2 — Persistence + Basic Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add chunk-level persistence, SSE parsing + usage extraction + cost computation (with versioned pricing), a retention background task, and a basic live dashboard that tees upstream chunks to connected browsers in real time. After this phase, a user can send a streaming request through the proxy, watch tokens appear live in the dashboard, reload the page, and see the same request rendered from SQLite.

**Architecture:**
- **StreamBus** (in-memory pub/sub) sits between the proxy hot path and dashboard subscribers. Bounded per-subscriber queues drop events for slow subscribers to protect the proxy.
- **RequestRegistry** tracks currently in-flight requests so dashboards can see them before they land in SQLite.
- The `PassthroughEngine` from Phase 1 is extended to (a) publish each chunk to the StreamBus, (b) buffer `(offset_ns, chunk)` tuples in memory, and (c) in the already-shielded `_finalize()` path, batch-insert chunks into SQLite, parse the provider's SSE for `usage`, compute cost against the pricing table at write time, and snapshot the result into `requests.cost_usd` + `requests.pricing_id`.
- **Pricing** is append-only and versioned. Seed data is loaded on startup if the pricing table is empty.
- **Retention** is a background `asyncio.Task` that runs every 60 seconds and nulls out bodies + deletes chunks for rows older than the Nth most-recent request.
- **Dashboard** is a straight port of the v0 MVP's HTML page wired to the new data model. No login yet (Phase 3), no filter bar, no replay player (Phase 4).

**Tech Stack:** Phase 1 stack unchanged (FastAPI + httpx + SQLAlchemy async + aiosqlite + pytest + respx).

**Reference:** `docs/superpowers/specs/2026-04-10-ai-proxy-with-live-dashboard-design.md`
**Depends on:** Phase 1 (merged into `main`)

---

## Scope for Phase 2

**IN scope:**
- `StreamBus` (in-memory pub/sub with bounded queues) — reintroduced from v0 MVP
- `RequestRegistry` (in-memory active-request tracker) publishing lifecycle events to the bus
- Chunks table CRUD (batch insert + fetch by req_id)
- Provider `parse_usage()` method for OpenAI, Anthropic, OpenRouter (SSE chunk → Usage)
- Pricing table CRUD + seed loader
- Cost calculator using the versioned pricing table at write time
- Engine integration: tee chunks to bus + buffer with `offset_ns` + flush-path chunk insert + usage extraction + cost snapshotting
- Retention background task
- Dashboard HTTP routes: `/dashboard/` (HTML), `/dashboard/api/active` (snapshot)
- Dashboard WebSocket routes: `/ws/active` (live lifecycle events), `/ws/stream/{req_id}` (live chunks for one request)
- Dashboard HTML port from v0 MVP, adapted to the new data model

**OUT of scope (Phases 3+):**
- Dashboard master key login (Phase 3)
- Filter bar, full-text search, sortable columns (Phase 3)
- Detail tabs (Request / Response / Chunks / Replay) (Phase 3-4)
- Replay player with scrubber, speed controls, dim preview (Phase 4)
- Keys/Pricing/Settings management UI (Phase 3)
- Batch strip-binaries operation (Phase 3)
- Timeline tab (Phase 5)

**Verification criteria:**
1. Full pytest suite green
2. Live: start server, bootstrap a key, send a streaming request — the dashboard shows tokens appearing live, the request list updates without a page refresh.
3. After the request ends, reload the dashboard page and the same request appears in the history loaded from SQLite with correct `input_tokens`/`output_tokens`/`cost_usd`.
4. The `chunks` table has one row per upstream chunk with monotonically increasing `offset_ns`.

---

## File Structure

New files:
```
src/aiproxy/
├── bus.py                         [NEW — StreamBus]
├── registry.py                    [NEW — RequestRegistry]
├── pricing/
│   ├── __init__.py                [NEW]
│   ├── compute.py                 [NEW — cost calculation]
│   └── seed.py                    [NEW — pricing seed loader]
├── pricing_seed.json              [NEW — current known prices]
├── db/
│   ├── crud/
│   │   ├── chunks.py              [NEW]
│   │   └── pricing.py             [NEW]
│   └── retention.py               [NEW — background cleanup task]
├── providers/
│   ├── base.py                    [MODIFY — add parse_usage abstract + SSE helper]
│   ├── openai.py                  [MODIFY — parse_usage impl]
│   ├── anthropic.py               [MODIFY — parse_usage impl]
│   └── openrouter.py              [MODIFY — parse_usage impl (OpenAI-compat)]
├── core/
│   └── passthrough.py             [MODIFY — tee + chunk buffer + finalize integration]
├── dashboard/
│   ├── __init__.py                [NEW]
│   ├── routes.py                  [NEW — HTTP + WS endpoints]
│   └── static/
│       └── index.html             [NEW — ported from v0 MVP]
└── app.py                         [MODIFY — wire bus/registry/dashboard + retention task]
```

Modified tests:
```
tests/
├── unit/
│   ├── test_bus.py                [NEW]
│   ├── test_registry.py           [NEW]
│   ├── test_crud_chunks.py        [NEW]
│   ├── test_crud_pricing.py       [NEW]
│   ├── test_pricing_compute.py    [NEW]
│   ├── test_pricing_seed.py       [NEW]
│   ├── test_provider_openai.py    [MODIFY — add parse_usage cases]
│   ├── test_provider_anthropic.py [MODIFY — add parse_usage cases]
│   └── test_retention.py          [NEW]
└── integration/
    ├── test_proxy_engine.py       [MODIFY — verify chunks + usage + cost]
    ├── test_dashboard_api.py      [NEW — HTTP endpoints]
    └── test_dashboard_ws.py       [NEW — WebSocket endpoints]
```

---

## Task 1: StreamBus — in-memory pub/sub

**Files:**
- Create: `src/aiproxy/bus.py`
- Create: `tests/unit/test_bus.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_bus.py`:

```python
"""Tests for aiproxy.bus.StreamBus."""
import asyncio

import pytest

from aiproxy.bus import StreamBus


async def test_publish_to_subscriber() -> None:
    bus = StreamBus()
    q = bus.subscribe("ch1")
    bus.publish("ch1", {"type": "chunk", "n": 1})
    event = await asyncio.wait_for(q.get(), timeout=0.1)
    assert event == {"type": "chunk", "n": 1}


async def test_publish_to_multiple_subscribers() -> None:
    bus = StreamBus()
    q1 = bus.subscribe("ch1")
    q2 = bus.subscribe("ch1")
    bus.publish("ch1", {"n": 1})
    e1 = await asyncio.wait_for(q1.get(), timeout=0.1)
    e2 = await asyncio.wait_for(q2.get(), timeout=0.1)
    assert e1 == e2 == {"n": 1}


async def test_publish_without_subscribers_drops_silently() -> None:
    bus = StreamBus()
    # no subscribers — publish should not raise
    bus.publish("nobody", {"n": 1})


async def test_has_subscribers() -> None:
    bus = StreamBus()
    assert bus.has_subscribers("ch1") is False
    q = bus.subscribe("ch1")
    assert bus.has_subscribers("ch1") is True
    bus.unsubscribe("ch1", q)
    assert bus.has_subscribers("ch1") is False


async def test_unsubscribe_stops_delivery() -> None:
    bus = StreamBus()
    q = bus.subscribe("ch1")
    bus.unsubscribe("ch1", q)
    bus.publish("ch1", {"n": 1})
    # Queue should be empty
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.get(), timeout=0.05)


async def test_slow_subscriber_gets_dropped_not_blocked() -> None:
    """If a subscriber queue is full, publish should drop the event, not block."""
    bus = StreamBus(queue_maxsize=2)
    q = bus.subscribe("ch1")
    bus.publish("ch1", {"n": 1})
    bus.publish("ch1", {"n": 2})
    # Queue is now full. Third publish must not block or raise.
    bus.publish("ch1", {"n": 3})
    # First two events are still deliverable
    assert (await asyncio.wait_for(q.get(), timeout=0.05)) == {"n": 1}
    assert (await asyncio.wait_for(q.get(), timeout=0.05)) == {"n": 2}
    # The third was dropped
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.get(), timeout=0.05)
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `uv run pytest tests/unit/test_bus.py -v`
Expected: module not found.

- [ ] **Step 3: Implement `src/aiproxy/bus.py`**

```python
"""In-memory pub/sub bus for fanning out stream chunks to dashboard subscribers.

Design notes:
- Each channel is a string (usually a req_id, or the global '__active__' channel).
- Publish is non-blocking. If a subscriber's bounded queue is full, the event
  is dropped for that subscriber rather than stalling the proxy hot path.
- Single-process only. If we later need multi-worker, swap in Redis pub/sub.
"""
from __future__ import annotations

import asyncio
from typing import Any


class StreamBus:
    """Per-channel bounded pub/sub with tolerant drops on slow consumers."""

    def __init__(self, queue_maxsize: int = 1000) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = {}
        self._queue_maxsize = queue_maxsize

    def publish(self, channel: str, event: dict[str, Any]) -> None:
        """Non-blocking publish. Drops event for any full subscriber queue."""
        subs = self._subs.get(channel)
        if not subs:
            return
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Dashboard can't keep up; drop rather than block proxy.
                pass

    def has_subscribers(self, channel: str) -> bool:
        return bool(self._subs.get(channel))

    def subscribe(self, channel: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_maxsize)
        self._subs.setdefault(channel, []).append(q)
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(channel)
        if not subs:
            return
        try:
            subs.remove(q)
        except ValueError:
            pass
        if not subs:
            self._subs.pop(channel, None)
```

- [ ] **Step 4: Run tests — expect 6 passing**

Run: `uv run pytest tests/unit/test_bus.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/bus.py tests/unit/test_bus.py
git commit -m "feat(phase-2): add StreamBus in-memory pub/sub"
```

---

## Task 2: RequestRegistry — in-memory active request tracking

**Files:**
- Create: `src/aiproxy/registry.py`
- Create: `tests/unit/test_registry.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_registry.py`:

```python
"""Tests for aiproxy.registry.RequestRegistry."""
import asyncio

import pytest

from aiproxy.bus import StreamBus
from aiproxy.registry import ACTIVE_CHANNEL, RequestMeta, RequestRegistry


def test_start_adds_to_active_and_publishes() -> None:
    bus = StreamBus()
    reg = RequestRegistry(bus)
    q = bus.subscribe(ACTIVE_CHANNEL)

    meta = RequestMeta(
        req_id="abc", provider="openai", model="gpt-4o",
        method="POST", path="/chat/completions", client_ip="127.0.0.1",
    )
    reg.start(meta)

    assert len(reg.active()) == 1
    assert reg.active()[0]["req_id"] == "abc"
    ev = q.get_nowait()
    assert ev["type"] == "start"
    assert ev["req"]["req_id"] == "abc"


def test_update_mutates_and_publishes() -> None:
    bus = StreamBus()
    reg = RequestRegistry(bus)
    q = bus.subscribe(ACTIVE_CHANNEL)
    reg.start(RequestMeta(
        req_id="abc", provider="openai", model=None,
        method="POST", path="/p", client_ip=None,
    ))
    q.get_nowait()  # consume the start event

    reg.update("abc", status="streaming", status_code=200)
    ev = q.get_nowait()
    assert ev["type"] == "update"
    assert ev["req"]["status"] == "streaming"
    assert ev["req"]["status_code"] == 200


def test_finish_moves_to_history_and_publishes() -> None:
    bus = StreamBus()
    reg = RequestRegistry(bus, history_limit=3)
    q = bus.subscribe(ACTIVE_CHANNEL)
    reg.start(RequestMeta(
        req_id="abc", provider="openai", model=None,
        method="POST", path="/p", client_ip=None,
    ))
    q.get_nowait()

    reg.finish("abc", status="done", chunks=5, bytes_out=1024)
    assert reg.active() == []
    assert len(reg.history()) == 1
    assert reg.history()[0]["status"] == "done"
    assert reg.history()[0]["chunks"] == 5
    ev = q.get_nowait()
    assert ev["type"] == "finish"


def test_history_limit_enforced() -> None:
    bus = StreamBus()
    reg = RequestRegistry(bus, history_limit=2)
    for i in range(5):
        reg.start(RequestMeta(
            req_id=f"r{i}", provider="openai", model=None,
            method="POST", path="/p", client_ip=None,
        ))
        reg.finish(f"r{i}", status="done")
    assert len(reg.history()) == 2
    # Most recent first
    assert reg.history()[0]["req_id"] == "r4"
    assert reg.history()[1]["req_id"] == "r3"


def test_get_returns_active_or_history() -> None:
    bus = StreamBus()
    reg = RequestRegistry(bus)
    reg.start(RequestMeta(
        req_id="live", provider="openai", model=None,
        method="POST", path="/p", client_ip=None,
    ))
    reg.start(RequestMeta(
        req_id="old", provider="openai", model=None,
        method="POST", path="/p", client_ip=None,
    ))
    reg.finish("old", status="done")

    assert reg.get("live") is not None
    assert reg.get("live").status == "pending"
    assert reg.get("old") is not None
    assert reg.get("old").status == "done"
    assert reg.get("missing") is None
```

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement `src/aiproxy/registry.py`**

```python
"""In-memory registry of active + recently finished requests.

Used by the dashboard to get an instant list of what's happening RIGHT NOW
without hitting the DB. Publishes lifecycle events on ACTIVE_CHANNEL so
dashboards can live-update the list.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from aiproxy.bus import StreamBus

ACTIVE_CHANNEL = "__active__"


@dataclass
class RequestMeta:
    req_id: str
    provider: str
    model: str | None
    method: str
    path: str
    client_ip: str | None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    status: str = "pending"  # pending | streaming | done | error | canceled
    status_code: int | None = None
    error_class: str | None = None
    error_message: str | None = None
    chunks: int = 0
    bytes_out: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


class RequestRegistry:
    """Tracks in-flight and recently finished requests, publishes to a bus."""

    def __init__(self, bus: StreamBus, history_limit: int = 100) -> None:
        self._bus = bus
        self._active: dict[str, RequestMeta] = {}
        self._history: list[RequestMeta] = []
        self._history_limit = history_limit

    def start(self, meta: RequestMeta) -> None:
        self._active[meta.req_id] = meta
        self._bus.publish(ACTIVE_CHANNEL, {"type": "start", "req": meta.snapshot()})

    def update(self, req_id: str, **fields: Any) -> None:
        meta = self._active.get(req_id)
        if meta is None:
            return
        for k, v in fields.items():
            setattr(meta, k, v)
        self._bus.publish(ACTIVE_CHANNEL, {"type": "update", "req": meta.snapshot()})

    def finish(self, req_id: str, **fields: Any) -> None:
        meta = self._active.pop(req_id, None)
        if meta is None:
            return
        for k, v in fields.items():
            setattr(meta, k, v)
        meta.finished_at = time.time()
        self._history.insert(0, meta)  # most recent first
        if len(self._history) > self._history_limit:
            self._history.pop()
        self._bus.publish(ACTIVE_CHANNEL, {"type": "finish", "req": meta.snapshot()})

    def get(self, req_id: str) -> RequestMeta | None:
        return self._active.get(req_id) or next(
            (m for m in self._history if m.req_id == req_id), None
        )

    def active(self) -> list[dict[str, Any]]:
        return [m.snapshot() for m in self._active.values()]

    def history(self) -> list[dict[str, Any]]:
        return [m.snapshot() for m in self._history]
```

- [ ] **Step 4: Run tests — expect 5 passing**

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/registry.py tests/unit/test_registry.py
git commit -m "feat(phase-2): add RequestRegistry with bus lifecycle events"
```

---

## Task 3: Chunks CRUD

**Files:**
- Create: `src/aiproxy/db/crud/chunks.py`
- Create: `tests/unit/test_crud_chunks.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_crud_chunks.py`:

```python
"""Tests for aiproxy.db.crud.chunks."""
import time

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.db.crud import chunks as chunks_crud
from aiproxy.db.crud import requests as req_crud
from aiproxy.db.models import Base


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sm
    finally:
        await engine.dispose()


async def _seed_request(sm, req_id: str) -> None:
    async with sm() as s:
        await req_crud.create_pending(
            s, req_id=req_id, api_key_id=None, provider="openai",
            endpoint="/chat/completions", method="POST", model="gpt-4o",
            is_streaming=True, client_ip=None, client_ua=None,
            req_headers={}, req_query=None, req_body=None,
            started_at=time.time(),
        )
        await s.commit()


async def test_insert_batch_and_fetch(session_factory) -> None:
    await _seed_request(session_factory, "req1")

    batch = [
        (0, 0, b'chunk0'),
        (1, 12_345_678, b'chunk1'),
        (2, 25_000_000, b'chunk2'),
    ]
    async with session_factory() as s:
        await chunks_crud.insert_batch(s, "req1", batch)
        await s.commit()

    async with session_factory() as s:
        rows = await chunks_crud.list_for_request(s, "req1")
        assert len(rows) == 3
        assert rows[0].seq == 0
        assert rows[0].offset_ns == 0
        assert rows[0].data == b'chunk0'
        assert rows[0].size == 6
        assert rows[1].seq == 1
        assert rows[1].offset_ns == 12_345_678
        assert rows[2].data == b'chunk2'


async def test_list_empty_for_unknown_request(session_factory) -> None:
    async with session_factory() as s:
        rows = await chunks_crud.list_for_request(s, "nonexistent")
        assert rows == []


async def test_cascade_delete_when_request_deleted(session_factory) -> None:
    await _seed_request(session_factory, "reqDel")
    async with session_factory() as s:
        await chunks_crud.insert_batch(s, "reqDel", [(0, 0, b'x')])
        await s.commit()

    # Delete the parent request
    from sqlalchemy import delete
    from aiproxy.db.models import Request
    async with session_factory() as s:
        await s.execute(delete(Request).where(Request.req_id == "reqDel"))
        await s.commit()

    async with session_factory() as s:
        rows = await chunks_crud.list_for_request(s, "reqDel")
        assert rows == []


async def test_count_for_request(session_factory) -> None:
    await _seed_request(session_factory, "req2")
    async with session_factory() as s:
        await chunks_crud.insert_batch(s, "req2", [
            (0, 0, b'a'), (1, 100, b'b'), (2, 200, b'c')
        ])
        await s.commit()
        n = await chunks_crud.count_for_request(s, "req2")
        assert n == 3
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement `src/aiproxy/db/crud/chunks.py`**

```python
"""CRUD operations for the chunks table.

Chunks are batch-inserted at stream finalize time. They're never inserted
one-at-a-time to keep the proxy's hot path free of DB writes.
"""
from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiproxy.db.models import Chunk


async def insert_batch(
    session: AsyncSession,
    req_id: str,
    batch: list[tuple[int, int, bytes]],
) -> None:
    """Insert a batch of chunks. Each item is (seq, offset_ns, data)."""
    if not batch:
        return
    rows = [
        Chunk(req_id=req_id, seq=seq, offset_ns=offset_ns, size=len(data), data=data)
        for (seq, offset_ns, data) in batch
    ]
    session.add_all(rows)
    await session.flush()


async def list_for_request(session: AsyncSession, req_id: str) -> Sequence[Chunk]:
    result = await session.execute(
        select(Chunk).where(Chunk.req_id == req_id).order_by(Chunk.seq)
    )
    return result.scalars().all()


async def count_for_request(session: AsyncSession, req_id: str) -> int:
    result = await session.execute(
        select(func.count()).select_from(Chunk).where(Chunk.req_id == req_id)
    )
    return int(result.scalar() or 0)


async def delete_for_request(session: AsyncSession, req_id: str) -> None:
    from sqlalchemy import delete
    await session.execute(delete(Chunk).where(Chunk.req_id == req_id))
```

- [ ] **Step 4: Run tests — expect 4 passing**

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/db/crud/chunks.py tests/unit/test_crud_chunks.py
git commit -m "feat(phase-2): add chunks CRUD (batch insert + fetch)"
```

---

## Task 4: Provider `parse_usage()` — base + OpenAI + OpenRouter

**Files:**
- Modify: `src/aiproxy/providers/base.py`
- Modify: `src/aiproxy/providers/openai.py`
- Modify: `src/aiproxy/providers/openrouter.py`
- Modify: `tests/unit/test_provider_openai.py`
- Modify: `tests/unit/test_provider_openrouter.py`

- [ ] **Step 1: Extend the test file `tests/unit/test_provider_openai.py`**

Append these test functions (keep the existing ones):

```python
from aiproxy.providers.base import Usage


def test_parse_usage_non_streaming() -> None:
    """Non-streaming OpenAI response has usage in the top level."""
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    body = b'{"choices":[{"message":{"content":"hi"}}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}'
    u = p.parse_usage(is_streaming=False, resp_body=body, chunks=None)
    assert u is not None
    assert u.input_tokens == 10
    assert u.output_tokens == 5


def test_parse_usage_streaming_with_include_usage() -> None:
    """Streaming OpenAI responses include usage only when stream_options.include_usage=true.
    It appears in the last SSE data event before [DONE]."""
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    chunks = [
        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n',
        b'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":8}}\n\n',
        b'data: [DONE]\n\n',
    ]
    u = p.parse_usage(is_streaming=True, resp_body=None, chunks=chunks)
    assert u is not None
    assert u.input_tokens == 12
    assert u.output_tokens == 8


def test_parse_usage_streaming_without_usage_returns_none() -> None:
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    chunks = [
        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
        b'data: [DONE]\n\n',
    ]
    u = p.parse_usage(is_streaming=True, resp_body=None, chunks=chunks)
    assert u is None


def test_parse_usage_cached_and_reasoning_tokens() -> None:
    """OpenAI returns cached_tokens under prompt_tokens_details, reasoning under completion_tokens_details."""
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    body = (
        b'{"usage":{"prompt_tokens":100,"completion_tokens":50,'
        b'"prompt_tokens_details":{"cached_tokens":40},'
        b'"completion_tokens_details":{"reasoning_tokens":20}}}'
    )
    u = p.parse_usage(is_streaming=False, resp_body=body, chunks=None)
    assert u.input_tokens == 100
    assert u.output_tokens == 50
    assert u.cached_tokens == 40
    assert u.reasoning_tokens == 20
```

- [ ] **Step 2: Extend `tests/unit/test_provider_openrouter.py`**

Append:

```python
from aiproxy.providers.base import Usage


def test_parse_usage_matches_openai_format() -> None:
    """OpenRouter is OpenAI-compatible, so it reuses the OpenAI parser."""
    p = OpenRouterProvider(base_url="https://openrouter.ai", api_key="x")
    body = b'{"usage":{"prompt_tokens":7,"completion_tokens":3}}'
    u = p.parse_usage(is_streaming=False, resp_body=body, chunks=None)
    assert u.input_tokens == 7
    assert u.output_tokens == 3
```

- [ ] **Step 3: Run tests — expect FAIL (method not implemented)**

- [ ] **Step 4: Extend `src/aiproxy/providers/base.py`**

Replace the existing file content with (the existing code plus the new `parse_usage` method + SSE helper):

```python
"""Provider abstraction.

A Provider encapsulates everything that differs between upstream vendors:
URL prefix, auth header injection, and usage parsing.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None


def iter_sse_data_events(chunks: list[bytes]) -> list[dict]:
    """Parse a list of raw SSE chunks into a list of JSON data events.

    Handles OpenAI-style 'data: {...}\n\n' framing. Skips '[DONE]' and
    non-JSON lines. Concatenates chunks first because chunk boundaries
    may split an event mid-line.
    """
    blob = b"".join(chunks).decode("utf-8", errors="replace")
    events: list[dict] = []
    for line in blob.split("\n"):
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            events.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return events


class Provider(ABC):
    name: str                    # 'openai' | 'anthropic' | 'openrouter'
    base_url: str                # e.g. 'https://api.openai.com'
    upstream_path_prefix: str    # e.g. '/v1', '', '/api/v1'

    @abstractmethod
    def upstream_api_key(self) -> str: ...

    @abstractmethod
    def inject_auth(self, headers: dict[str, str]) -> dict[str, str]:
        """Mutate and return the headers dict with the upstream auth attached."""

    def map_path(self, client_path: str) -> str:
        """Client path (after provider prefix) → upstream path."""
        return f"{self.upstream_path_prefix}/{client_path.lstrip('/')}"

    def extract_model(self, body: bytes) -> str | None:
        """Parse JSON body to find 'model' for display."""
        if not body:
            return None
        try:
            obj = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if isinstance(obj, dict):
            model = obj.get("model")
            return model if isinstance(model, str) else None
        return None

    def is_streaming_request(self, body: bytes, headers: dict[str, str]) -> bool:
        if not body:
            return False
        try:
            obj = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False
        return isinstance(obj, dict) and bool(obj.get("stream"))

    def parse_usage(
        self,
        *,
        is_streaming: bool,
        resp_body: bytes | None,
        chunks: list[bytes] | None,
    ) -> Usage | None:
        """Extract token counts from the upstream response.

        For non-streaming: `resp_body` is the full response JSON.
        For streaming: `chunks` is the list of raw bytes from upstream.
        Provider-specific subclasses should override this.
        Default returns None (no parsing).
        """
        return None
```

- [ ] **Step 5: Rewrite `src/aiproxy/providers/openai.py`**

```python
"""OpenAI provider — Bearer token auth, /v1/ path prefix, OpenAI-style SSE."""
from __future__ import annotations

import json

from aiproxy.providers.base import Provider, Usage, iter_sse_data_events


def _parse_openai_usage(obj: dict) -> Usage | None:
    """Shared parser for OpenAI-compatible response shapes.

    Used by both OpenAIProvider and OpenRouterProvider.
    """
    usage = obj.get("usage")
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("prompt_tokens")
    output_tokens = usage.get("completion_tokens")
    cached = None
    reasoning = None
    ptd = usage.get("prompt_tokens_details")
    if isinstance(ptd, dict):
        cached = ptd.get("cached_tokens")
    ctd = usage.get("completion_tokens_details")
    if isinstance(ctd, dict):
        reasoning = ctd.get("reasoning_tokens")
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached,
        reasoning_tokens=reasoning,
    )


class OpenAIProvider(Provider):
    name = "openai"
    upstream_path_prefix = "/v1"

    def __init__(self, *, base_url: str, api_key: str) -> None:
        self.base_url = base_url
        self._api_key = api_key

    def upstream_api_key(self) -> str:
        return self._api_key

    def inject_auth(self, headers: dict[str, str]) -> dict[str, str]:
        headers["authorization"] = f"Bearer {self._api_key}"
        return headers

    def parse_usage(
        self,
        *,
        is_streaming: bool,
        resp_body: bytes | None,
        chunks: list[bytes] | None,
    ) -> Usage | None:
        if not is_streaming:
            if not resp_body:
                return None
            try:
                obj = json.loads(resp_body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return None
            return _parse_openai_usage(obj) if isinstance(obj, dict) else None

        # Streaming: parse SSE chunks, look for the last event containing `usage`.
        # This requires the client to have set stream_options.include_usage=true.
        if not chunks:
            return None
        events = iter_sse_data_events(chunks)
        for event in reversed(events):
            if isinstance(event, dict) and "usage" in event:
                return _parse_openai_usage(event)
        return None
```

- [ ] **Step 6: Rewrite `src/aiproxy/providers/openrouter.py`**

```python
"""OpenRouter provider — OpenAI-compatible Bearer auth, /api/v1/ prefix."""
from __future__ import annotations

from aiproxy.providers.base import Provider, Usage
from aiproxy.providers.openai import OpenAIProvider


class OpenRouterProvider(Provider):
    name = "openrouter"
    upstream_path_prefix = "/api/v1"

    def __init__(self, *, base_url: str, api_key: str) -> None:
        self.base_url = base_url
        self._api_key = api_key

    def upstream_api_key(self) -> str:
        return self._api_key

    def inject_auth(self, headers: dict[str, str]) -> dict[str, str]:
        headers["authorization"] = f"Bearer {self._api_key}"
        # Optional but helpful for OpenRouter's app rankings
        headers.setdefault("http-referer", "https://github.com/ai-proxy")
        return headers

    def parse_usage(
        self,
        *,
        is_streaming: bool,
        resp_body: bytes | None,
        chunks: list[bytes] | None,
    ) -> Usage | None:
        # OpenRouter is OpenAI-compatible — delegate.
        dummy = OpenAIProvider(base_url="", api_key="")
        return dummy.parse_usage(
            is_streaming=is_streaming,
            resp_body=resp_body,
            chunks=chunks,
        )
```

- [ ] **Step 7: Run tests — expect all passing (4 new OpenAI + 1 new OpenRouter + existing)**

- [ ] **Step 8: Commit**

```bash
git add src/aiproxy/providers/ tests/unit/test_provider_openai.py tests/unit/test_provider_openrouter.py
git commit -m "feat(phase-2): parse_usage for OpenAI + OpenRouter (shared OpenAI format)"
```

---

## Task 5: Anthropic `parse_usage()`

**Files:**
- Modify: `src/aiproxy/providers/anthropic.py`
- Modify: `tests/unit/test_provider_anthropic.py`

- [ ] **Step 1: Extend `tests/unit/test_provider_anthropic.py`**

Append:

```python
from aiproxy.providers.base import Usage


def test_parse_usage_non_streaming() -> None:
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="x")
    body = b'{"content":[{"type":"text","text":"hi"}],"usage":{"input_tokens":15,"output_tokens":7,"cache_read_input_tokens":3}}'
    u = p.parse_usage(is_streaming=False, resp_body=body, chunks=None)
    assert u is not None
    assert u.input_tokens == 15
    assert u.output_tokens == 7
    assert u.cached_tokens == 3


def test_parse_usage_streaming_message_delta() -> None:
    """Anthropic streaming sends usage in the message_delta event near the end.

    The `message_start` event carries an initial input_tokens count; the final
    `message_delta` event carries the output_tokens.
    """
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="x")
    chunks = [
        b'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":20,"output_tokens":1}}}\n\n',
        b'event: content_block_start\ndata: {"type":"content_block_start"}\n\n',
        b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}\n\n',
        b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":" world"}}\n\n',
        b'event: content_block_stop\ndata: {"type":"content_block_stop"}\n\n',
        b'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":12}}\n\n',
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    ]
    u = p.parse_usage(is_streaming=True, resp_body=None, chunks=chunks)
    assert u is not None
    assert u.input_tokens == 20
    assert u.output_tokens == 12


def test_parse_usage_streaming_without_usage_returns_none() -> None:
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="x")
    chunks = [b'event: content_block_delta\ndata: {"delta":{"text":"hi"}}\n\n']
    u = p.parse_usage(is_streaming=True, resp_body=None, chunks=chunks)
    assert u is None
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Rewrite `src/aiproxy/providers/anthropic.py`**

```python
"""Anthropic provider — x-api-key auth, no path prefix, custom SSE format."""
from __future__ import annotations

import json

from aiproxy.providers.base import Provider, Usage, iter_sse_data_events

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(Provider):
    name = "anthropic"
    upstream_path_prefix = ""  # clients already send /v1/messages etc.

    def __init__(self, *, base_url: str, api_key: str) -> None:
        self.base_url = base_url
        self._api_key = api_key

    def upstream_api_key(self) -> str:
        return self._api_key

    def inject_auth(self, headers: dict[str, str]) -> dict[str, str]:
        headers["x-api-key"] = self._api_key
        headers["anthropic-version"] = ANTHROPIC_VERSION
        headers.pop("authorization", None)
        return headers

    def parse_usage(
        self,
        *,
        is_streaming: bool,
        resp_body: bytes | None,
        chunks: list[bytes] | None,
    ) -> Usage | None:
        if not is_streaming:
            if not resp_body:
                return None
            try:
                obj = json.loads(resp_body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return None
            return self._usage_from_dict(obj.get("usage") if isinstance(obj, dict) else None)

        # Streaming: scan events for the message_start (initial input_tokens)
        # and the final message_delta (output_tokens).
        if not chunks:
            return None
        events = iter_sse_data_events(chunks)
        input_tokens: int | None = None
        output_tokens: int | None = None
        cached: int | None = None

        for event in events:
            t = event.get("type")
            if t == "message_start":
                msg = event.get("message", {})
                u = msg.get("usage")
                if isinstance(u, dict):
                    input_tokens = u.get("input_tokens", input_tokens)
                    cached = u.get("cache_read_input_tokens", cached)
            elif t == "message_delta":
                u = event.get("usage")
                if isinstance(u, dict):
                    output_tokens = u.get("output_tokens", output_tokens)

        if input_tokens is None and output_tokens is None:
            return None
        return Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached,
        )

    @staticmethod
    def _usage_from_dict(usage: dict | None) -> Usage | None:
        if not isinstance(usage, dict):
            return None
        return Usage(
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cached_tokens=usage.get("cache_read_input_tokens"),
        )
```

- [ ] **Step 4: Run tests — expect 3 new passing**

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/providers/anthropic.py tests/unit/test_provider_anthropic.py
git commit -m "feat(phase-2): parse_usage for Anthropic (message_delta SSE events)"
```

---

## Task 6: Pricing CRUD

**Files:**
- Create: `src/aiproxy/db/crud/pricing.py`
- Create: `tests/unit/test_crud_pricing.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_crud_pricing.py`:

```python
"""Tests for aiproxy.db.crud.pricing."""
import time

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.db.crud import pricing as pricing_crud
from aiproxy.db.models import Base


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sm
    finally:
        await engine.dispose()


async def test_upsert_creates_first_version(session_factory) -> None:
    async with session_factory() as s:
        row = await pricing_crud.upsert_current(
            s,
            provider="openai",
            model="gpt-4o",
            input_per_1m_usd=2.50,
            output_per_1m_usd=10.00,
            cached_per_1m_usd=None,
            note="initial",
        )
        await s.commit()
        assert row.id is not None
        assert row.effective_to is None


async def test_upsert_closes_previous_version(session_factory) -> None:
    async with session_factory() as s:
        v1 = await pricing_crud.upsert_current(
            s,
            provider="openai",
            model="gpt-4o",
            input_per_1m_usd=2.50,
            output_per_1m_usd=10.00,
            cached_per_1m_usd=None,
        )
        await s.commit()
        v1_id = v1.id
        v2 = await pricing_crud.upsert_current(
            s,
            provider="openai",
            model="gpt-4o",
            input_per_1m_usd=2.00,
            output_per_1m_usd=8.00,
            cached_per_1m_usd=None,
            note="price cut",
        )
        await s.commit()

    async with session_factory() as s:
        from sqlalchemy import select
        from aiproxy.db.models import Pricing
        rows = (await s.execute(
            select(Pricing).where(
                Pricing.provider == "openai", Pricing.model == "gpt-4o"
            ).order_by(Pricing.effective_from)
        )).scalars().all()
        assert len(rows) == 2
        assert rows[0].id == v1_id
        assert rows[0].effective_to is not None
        assert rows[1].effective_to is None


async def test_find_effective_returns_current(session_factory) -> None:
    async with session_factory() as s:
        v1 = await pricing_crud.upsert_current(
            s, provider="openai", model="gpt-4o",
            input_per_1m_usd=2.50, output_per_1m_usd=10.00,
            cached_per_1m_usd=None,
        )
        await s.commit()
        await pricing_crud.upsert_current(
            s, provider="openai", model="gpt-4o",
            input_per_1m_usd=2.00, output_per_1m_usd=8.00,
            cached_per_1m_usd=None,
        )
        await s.commit()

    async with session_factory() as s:
        effective = await pricing_crud.find_effective(
            s, provider="openai", model="gpt-4o", at=time.time(),
        )
        assert effective is not None
        assert effective.input_per_1m_usd == 2.00


async def test_find_effective_none_for_unknown_model(session_factory) -> None:
    async with session_factory() as s:
        result = await pricing_crud.find_effective(
            s, provider="openai", model="unknown", at=time.time(),
        )
        assert result is None
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Create `src/aiproxy/db/crud/pricing.py`**

```python
"""CRUD for the versioned pricing table."""
from __future__ import annotations

import time

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aiproxy.db.models import Pricing


async def upsert_current(
    session: AsyncSession,
    *,
    provider: str,
    model: str,
    input_per_1m_usd: float,
    output_per_1m_usd: float,
    cached_per_1m_usd: float | None,
    note: str | None = None,
) -> Pricing:
    """Add a new pricing version. Closes any currently-effective row for
    the same (provider, model) by setting its effective_to to now."""
    now = time.time()
    # Close any currently-effective row
    await session.execute(
        update(Pricing)
        .where(
            Pricing.provider == provider,
            Pricing.model == model,
            Pricing.effective_to.is_(None),
        )
        .values(effective_to=now)
    )
    row = Pricing(
        provider=provider,
        model=model,
        input_per_1m_usd=input_per_1m_usd,
        output_per_1m_usd=output_per_1m_usd,
        cached_per_1m_usd=cached_per_1m_usd,
        effective_from=now,
        effective_to=None,
        note=note,
        created_at=now,
    )
    session.add(row)
    await session.flush()
    return row


async def find_effective(
    session: AsyncSession,
    *,
    provider: str,
    model: str,
    at: float,
) -> Pricing | None:
    """Find the pricing row that was in effect at the given timestamp."""
    result = await session.execute(
        select(Pricing)
        .where(
            Pricing.provider == provider,
            Pricing.model == model,
            Pricing.effective_from <= at,
        )
        .where((Pricing.effective_to.is_(None)) | (Pricing.effective_to > at))
        .order_by(Pricing.effective_from.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
```

- [ ] **Step 4: Run tests — expect 4 passing**

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/db/crud/pricing.py tests/unit/test_crud_pricing.py
git commit -m "feat(phase-2): add versioned pricing CRUD"
```

---

## Task 7: Pricing seed + loader

**Files:**
- Create: `src/aiproxy/pricing_seed.json`
- Create: `src/aiproxy/pricing/__init__.py` (empty)
- Create: `src/aiproxy/pricing/seed.py`
- Create: `tests/unit/test_pricing_seed.py`

- [ ] **Step 1: Create `src/aiproxy/pricing_seed.json`**

Prices as of 2026-04-10, in USD per 1M tokens. Users can edit later.

```json
{
  "entries": [
    {
      "provider": "openai",
      "model": "gpt-4o",
      "input_per_1m_usd": 2.50,
      "output_per_1m_usd": 10.00,
      "cached_per_1m_usd": 1.25
    },
    {
      "provider": "openai",
      "model": "gpt-4o-mini",
      "input_per_1m_usd": 0.15,
      "output_per_1m_usd": 0.60,
      "cached_per_1m_usd": 0.075
    },
    {
      "provider": "openai",
      "model": "o3-mini",
      "input_per_1m_usd": 1.10,
      "output_per_1m_usd": 4.40,
      "cached_per_1m_usd": 0.55
    },
    {
      "provider": "anthropic",
      "model": "claude-sonnet-4-5-20250929",
      "input_per_1m_usd": 3.00,
      "output_per_1m_usd": 15.00,
      "cached_per_1m_usd": 0.30
    },
    {
      "provider": "anthropic",
      "model": "claude-haiku-4-5-20251001",
      "input_per_1m_usd": 0.80,
      "output_per_1m_usd": 4.00,
      "cached_per_1m_usd": 0.08
    },
    {
      "provider": "anthropic",
      "model": "claude-opus-4-6",
      "input_per_1m_usd": 15.00,
      "output_per_1m_usd": 75.00,
      "cached_per_1m_usd": 1.50
    }
  ]
}
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_pricing_seed.py`:

```python
"""Tests for the pricing seed loader."""
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.db.models import Base, Pricing
from aiproxy.pricing.seed import seed_pricing_if_empty


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sm
    finally:
        await engine.dispose()


async def test_seeds_when_empty(session_factory) -> None:
    await seed_pricing_if_empty(session_factory)
    async with session_factory() as s:
        count = (await s.execute(select(func.count()).select_from(Pricing))).scalar()
    assert count > 0


async def test_does_not_seed_when_non_empty(session_factory) -> None:
    # Pre-seed with one row
    from aiproxy.db.crud import pricing as pricing_crud
    async with session_factory() as s:
        await pricing_crud.upsert_current(
            s, provider="custom", model="my-model",
            input_per_1m_usd=1.0, output_per_1m_usd=2.0, cached_per_1m_usd=None,
        )
        await s.commit()

    await seed_pricing_if_empty(session_factory)

    async with session_factory() as s:
        count = (await s.execute(select(func.count()).select_from(Pricing))).scalar()
    # Only the custom row we inserted — seeder noticed the table is non-empty
    assert count == 1


async def test_idempotent_after_first_seed(session_factory) -> None:
    await seed_pricing_if_empty(session_factory)
    async with session_factory() as s:
        count1 = (await s.execute(select(func.count()).select_from(Pricing))).scalar()
    await seed_pricing_if_empty(session_factory)
    async with session_factory() as s:
        count2 = (await s.execute(select(func.count()).select_from(Pricing))).scalar()
    assert count1 == count2
```

- [ ] **Step 3: Run — expect FAIL**

- [ ] **Step 4: Create `src/aiproxy/pricing/__init__.py`** (empty)

Create `src/aiproxy/pricing/seed.py`:

```python
"""Seed the pricing table with known prices if it's empty."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aiproxy.db.crud import pricing as pricing_crud
from aiproxy.db.models import Pricing

_SEED_PATH = Path(__file__).parent.parent / "pricing_seed.json"


async def seed_pricing_if_empty(sessionmaker: async_sessionmaker) -> int:
    """Insert seed pricing rows if the table is empty. Returns rows inserted."""
    async with sessionmaker() as session:
        count = (
            await session.execute(select(func.count()).select_from(Pricing))
        ).scalar()
        if count and count > 0:
            return 0

        data = json.loads(_SEED_PATH.read_text())
        entries = data.get("entries", [])
        for entry in entries:
            await pricing_crud.upsert_current(
                session,
                provider=entry["provider"],
                model=entry["model"],
                input_per_1m_usd=entry["input_per_1m_usd"],
                output_per_1m_usd=entry["output_per_1m_usd"],
                cached_per_1m_usd=entry.get("cached_per_1m_usd"),
                note="seed",
            )
        await session.commit()
        return len(entries)
```

- [ ] **Step 5: Run tests — expect 3 passing**

- [ ] **Step 6: Commit**

```bash
git add src/aiproxy/pricing_seed.json src/aiproxy/pricing/ tests/unit/test_pricing_seed.py
git commit -m "feat(phase-2): add pricing seed data + loader"
```

---

## Task 8: Cost compute helper

**Files:**
- Create: `src/aiproxy/pricing/compute.py`
- Create: `tests/unit/test_pricing_compute.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_pricing_compute.py`:

```python
"""Tests for cost computation."""
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.db.crud import pricing as pricing_crud
from aiproxy.db.models import Base
from aiproxy.pricing.compute import compute_cost
from aiproxy.providers.base import Usage


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sm
    finally:
        await engine.dispose()


async def test_compute_cost_basic(session_factory) -> None:
    async with session_factory() as s:
        await pricing_crud.upsert_current(
            s, provider="openai", model="gpt-4o",
            input_per_1m_usd=2.50, output_per_1m_usd=10.00,
            cached_per_1m_usd=None,
        )
        await s.commit()
        result = await compute_cost(
            s,
            provider="openai",
            model="gpt-4o",
            usage=Usage(input_tokens=1000, output_tokens=500),
        )
    assert result is not None
    pricing_id, cost = result
    # 1000 / 1M * $2.50 + 500 / 1M * $10.00 = 0.0025 + 0.005 = 0.0075
    assert cost == pytest.approx(0.0075)
    assert pricing_id is not None


async def test_compute_cost_with_cached_tokens(session_factory) -> None:
    async with session_factory() as s:
        await pricing_crud.upsert_current(
            s, provider="openai", model="gpt-4o",
            input_per_1m_usd=2.50, output_per_1m_usd=10.00,
            cached_per_1m_usd=1.25,
        )
        await s.commit()
        result = await compute_cost(
            s,
            provider="openai",
            model="gpt-4o",
            usage=Usage(input_tokens=1000, output_tokens=500, cached_tokens=400),
        )
    pricing_id, cost = result
    # Non-cached input: 600 tokens * $2.50/1M = 0.0015
    # Cached input:     400 tokens * $1.25/1M = 0.0005
    # Output:           500 tokens * $10.00/1M = 0.005
    expected = 0.0015 + 0.0005 + 0.005
    assert cost == pytest.approx(expected)


async def test_compute_cost_unknown_model_returns_none(session_factory) -> None:
    async with session_factory() as s:
        result = await compute_cost(
            s,
            provider="openai",
            model="not-seeded",
            usage=Usage(input_tokens=100, output_tokens=50),
        )
    assert result is None


async def test_compute_cost_no_usage_returns_none(session_factory) -> None:
    async with session_factory() as s:
        await pricing_crud.upsert_current(
            s, provider="openai", model="gpt-4o",
            input_per_1m_usd=2.50, output_per_1m_usd=10.00,
            cached_per_1m_usd=None,
        )
        await s.commit()
        result = await compute_cost(
            s, provider="openai", model="gpt-4o", usage=None,
        )
    assert result is None
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement `src/aiproxy/pricing/compute.py`**

```python
"""Cost calculator: look up current pricing, compute cost from Usage."""
from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from aiproxy.db.crud import pricing as pricing_crud
from aiproxy.providers.base import Usage


async def compute_cost(
    session: AsyncSession,
    *,
    provider: str,
    model: str,
    usage: Usage | None,
) -> tuple[int, float] | None:
    """Return (pricing_id, cost_usd) or None if no pricing is found or no usage."""
    if usage is None:
        return None
    pricing = await pricing_crud.find_effective(
        session, provider=provider, model=model, at=time.time(),
    )
    if pricing is None:
        return None

    input_tokens = usage.input_tokens or 0
    output_tokens = usage.output_tokens or 0
    cached_tokens = usage.cached_tokens or 0

    # Non-cached portion of the input is billed at the standard input rate
    non_cached_input = max(0, input_tokens - cached_tokens)
    non_cached_cost = non_cached_input * pricing.input_per_1m_usd / 1_000_000
    output_cost = output_tokens * pricing.output_per_1m_usd / 1_000_000

    cached_cost = 0.0
    if cached_tokens and pricing.cached_per_1m_usd is not None:
        cached_cost = cached_tokens * pricing.cached_per_1m_usd / 1_000_000
    elif cached_tokens:
        # No discounted cached rate — treat as standard input rate
        cached_cost = cached_tokens * pricing.input_per_1m_usd / 1_000_000

    total = non_cached_cost + output_cost + cached_cost
    return (pricing.id, total)
```

- [ ] **Step 4: Run tests — expect 4 passing**

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/pricing/compute.py tests/unit/test_pricing_compute.py
git commit -m "feat(phase-2): add cost compute helper with cached-token handling"
```

---

## Task 9: Engine integration — tee + chunk persistence + cost

**Files:**
- Modify: `src/aiproxy/core/passthrough.py`
- Modify: `tests/integration/test_proxy_engine.py`

This is the largest task of Phase 2. The engine gains three new responsibilities:
1. Publish each chunk to the `StreamBus` (if anyone is subscribed)
2. Buffer chunks as `(seq, offset_ns, bytes)` tuples
3. On finalize: insert chunks in a batch, parse usage, compute cost, snapshot into the requests row

The engine now takes two new dependencies: a `StreamBus` and a `RequestRegistry`.

- [ ] **Step 1: Extend the existing integration test**

Open `tests/integration/test_proxy_engine.py` and update the `engine` fixture + add new tests. The full rewritten file:

```python
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
        assert row.status == "done"
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
        assert row.status == "error"
        assert row.error_class == "upstream_connect"
```

- [ ] **Step 2: Run — expect most tests failing (engine doesn't take bus/registry yet)**

- [ ] **Step 3: Rewrite `src/aiproxy/core/passthrough.py`**

```python
"""Shared passthrough engine used by the provider dispatcher router.

Phase 2 responsibilities (additions over Phase 1):
  - Publish each chunk to the StreamBus (dashboard live tee)
  - Buffer chunks as (seq, offset_ns, data) tuples in memory
  - On finalize: batch-insert chunks, parse provider-specific usage,
    compute cost via the versioned pricing table, snapshot into requests row
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from aiproxy.bus import StreamBus
from aiproxy.core.headers import clean_downstream_headers, clean_upstream_headers
from aiproxy.db.crud import chunks as chunks_crud
from aiproxy.db.crud import requests as req_crud
from aiproxy.db.models import Request
from aiproxy.pricing.compute import compute_cost
from aiproxy.providers.base import Provider
from aiproxy.registry import RequestMeta, RequestRegistry

from sqlalchemy import update


async def _finalize(
    *,
    sessionmaker: async_sessionmaker,
    registry: RequestRegistry,
    provider: Provider,
    req_id: str,
    final_status: str,
    status_code: int,
    resp_headers: dict[str, str],
    resp_body: bytes,
    error_class: str | None,
    error_message: str | None,
    chunk_buffer: list[tuple[int, int, bytes]],
    is_streaming: bool,
) -> None:
    """Persist terminal state + chunks + usage/cost for a request.

    Called from the stream generator's `finally` block under `asyncio.shield`.
    """
    now = time.time()
    async with sessionmaker() as session:
        if final_status == "error":
            await req_crud.mark_error(
                session,
                req_id=req_id,
                error_class=error_class or "unknown",
                error_message=error_message or "",
                finished_at=now,
            )
        else:
            await req_crud.mark_finished(
                session,
                req_id=req_id,
                status=final_status,
                status_code=status_code,
                resp_headers=resp_headers,
                resp_body=resp_body,
                finished_at=now,
            )

        # Batch-insert chunks (only for streaming requests)
        if is_streaming and chunk_buffer:
            await chunks_crud.insert_batch(session, req_id, chunk_buffer)

        # Parse usage + cost, snapshot into requests row
        usage = provider.parse_usage(
            is_streaming=is_streaming,
            resp_body=resp_body if not is_streaming else None,
            chunks=[data for (_, _, data) in chunk_buffer] if is_streaming else None,
        )
        input_tokens = usage.input_tokens if usage else None
        output_tokens = usage.output_tokens if usage else None
        cached_tokens = usage.cached_tokens if usage else None
        reasoning_tokens = usage.reasoning_tokens if usage else None

        pricing_id: int | None = None
        cost_usd: float | None = None
        if usage is not None and final_status != "error":
            # Get the model from the already-persisted request row
            result = await session.execute(
                __import__("sqlalchemy").select(Request).where(Request.req_id == req_id)
            )
            req_row = result.scalar_one_or_none()
            model = req_row.model if req_row else None
            if model:
                cost_result = await compute_cost(
                    session, provider=provider.name, model=model, usage=usage,
                )
                if cost_result:
                    pricing_id, cost_usd = cost_result

        await session.execute(
            update(Request)
            .where(Request.req_id == req_id)
            .values(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                reasoning_tokens=reasoning_tokens,
                cost_usd=cost_usd,
                pricing_id=pricing_id,
                chunk_count=len(chunk_buffer) if is_streaming else 0,
            )
        )
        await session.commit()

    # Update the in-memory registry
    registry.finish(
        req_id,
        status=final_status,
        status_code=status_code,
        error_class=error_class,
        error_message=error_message,
        chunks=len(chunk_buffer) if is_streaming else 0,
        bytes_out=sum(len(c) for (_, _, c) in chunk_buffer) if is_streaming else len(resp_body),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )


class PassthroughEngine:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        sessionmaker: async_sessionmaker,
        bus: StreamBus,
        registry: RequestRegistry,
    ) -> None:
        self._client = http_client
        self._sessionmaker = sessionmaker
        self._bus = bus
        self._registry = registry

    async def forward(
        self,
        *,
        provider: Provider,
        client_path: str,
        req_id: str,
        method: str,
        client_headers: dict[str, str],
        client_query: list[tuple[str, str]],
        client_body: bytes,
        client_ip: str | None,
        client_ua: str | None,
        api_key_id: int | None,
        started_at: float,
    ) -> tuple[int, dict[str, str], AsyncIterator[bytes]]:
        model = provider.extract_model(client_body)
        is_streaming = provider.is_streaming_request(client_body, client_headers)

        # Persist the pending DB record
        async with self._sessionmaker() as session:
            await req_crud.create_pending(
                session,
                req_id=req_id,
                api_key_id=api_key_id,
                provider=provider.name,
                endpoint="/" + client_path.lstrip("/"),
                method=method,
                model=model,
                is_streaming=is_streaming,
                client_ip=client_ip,
                client_ua=client_ua,
                req_headers=client_headers,
                req_query=client_query or None,
                req_body=client_body,
                started_at=started_at,
            )
            await session.commit()

        # Register in the in-memory tracker
        self._registry.start(RequestMeta(
            req_id=req_id,
            provider=provider.name,
            model=model,
            method=method,
            path="/" + client_path.lstrip("/"),
            client_ip=client_ip,
            started_at=started_at,
        ))

        upstream_url = f"{provider.base_url}{provider.map_path(client_path)}"
        upstream_headers = clean_upstream_headers(client_headers)
        upstream_headers = provider.inject_auth(upstream_headers)

        upstream_req = self._client.build_request(
            method=method,
            url=upstream_url,
            headers=upstream_headers,
            content=client_body if client_body else None,
            params=client_query,
        )

        try:
            upstream_resp = await self._client.send(upstream_req, stream=True)
        except httpx.ConnectError as e:
            async with self._sessionmaker() as session:
                await req_crud.mark_error(
                    session,
                    req_id=req_id,
                    error_class="upstream_connect",
                    error_message=str(e),
                    finished_at=time.time(),
                )
                await session.commit()
            self._registry.finish(
                req_id, status="error", error_class="upstream_connect", error_message=str(e)
            )
            raise
        except httpx.ReadTimeout as e:
            async with self._sessionmaker() as session:
                await req_crud.mark_error(
                    session,
                    req_id=req_id,
                    error_class="upstream_timeout",
                    error_message=str(e),
                    finished_at=time.time(),
                )
                await session.commit()
            self._registry.finish(
                req_id, status="error", error_class="upstream_timeout", error_message=str(e)
            )
            raise

        self._registry.update(req_id, status="streaming", status_code=upstream_resp.status_code)

        bus = self._bus
        sessionmaker = self._sessionmaker
        registry = self._registry

        async def stream_and_persist() -> AsyncIterator[bytes]:
            chunk_buffer: list[tuple[int, int, bytes]] = []
            first_ns: int | None = None
            final_status: str = "done"
            error_class: str | None = None
            error_message: str | None = None
            try:
                async for chunk in upstream_resp.aiter_raw():
                    ts_ns = time.monotonic_ns()
                    if first_ns is None:
                        first_ns = ts_ns
                    offset_ns = ts_ns - first_ns
                    seq = len(chunk_buffer)
                    chunk_buffer.append((seq, offset_ns, chunk))

                    # Publish to dashboard subscribers (short-circuit if no one listening)
                    if bus.has_subscribers(req_id):
                        bus.publish(req_id, {
                            "type": "chunk",
                            "req_id": req_id,
                            "seq": seq,
                            "offset_ns": offset_ns,
                            "size": len(chunk),
                        })

                    yield chunk
            except asyncio.CancelledError:
                final_status = "canceled"
                raise
            except httpx.ReadError as e:
                final_status = "error"
                error_class = "stream_interrupted"
                error_message = str(e)
                raise
            except Exception as e:
                final_status = "error"
                error_class = "stream_interrupted"
                error_message = str(e)
                raise
            finally:
                body_bytes = b"".join(data for (_, _, data) in chunk_buffer)
                try:
                    await asyncio.shield(_finalize(
                        sessionmaker=sessionmaker,
                        registry=registry,
                        provider=provider,
                        req_id=req_id,
                        final_status=final_status,
                        status_code=upstream_resp.status_code,
                        resp_headers=dict(upstream_resp.headers),
                        resp_body=body_bytes,
                        error_class=error_class,
                        error_message=error_message,
                        chunk_buffer=chunk_buffer,
                        is_streaming=is_streaming,
                    ))
                    # Publish a final 'done' event to the chunk channel
                    if bus.has_subscribers(req_id):
                        bus.publish(req_id, {
                            "type": "done" if final_status != "error" else "error",
                            "req_id": req_id,
                            "status": final_status,
                        })
                finally:
                    await upstream_resp.aclose()

        return (
            upstream_resp.status_code,
            clean_downstream_headers(dict(upstream_resp.headers)),
            stream_and_persist(),
        )
```

- [ ] **Step 4: Run tests — expect all 4 passing**

```bash
uv run pytest tests/integration/test_proxy_engine.py -v
```

- [ ] **Step 5: Run full suite**

```bash
uv run pytest tests/ -q
```

Several other tests will now fail because `PassthroughEngine` and the route `create_router` need bus/registry too. Fix those test fixtures before moving on — either pass a fresh StreamBus + RequestRegistry in each fixture, or add a helper. The fixture in `test_proxy_router.py` needs updating similarly.

In `tests/integration/test_proxy_router.py`, update the `app_with_seeded_key` fixture:

```python
# Add these imports
from aiproxy.bus import StreamBus
from aiproxy.registry import RequestRegistry

# In the fixture, after building the upstream_client:
bus = StreamBus()
registry = RequestRegistry(bus)
engine = PassthroughEngine(
    http_client=upstream_client,
    sessionmaker=db_sessionmaker,
    bus=bus,
    registry=registry,
)
```

In `tests/integration/test_end_to_end.py`, `build_app` is called — so `build_app` must create its own bus/registry internally. That's handled in Task 13. For Task 9, update the end-to-end test fixture inline similarly OR mark it xfail temporarily and fix in Task 13.

Simpler: for now, update `build_app` in Task 9 to also instantiate a fresh StreamBus + RequestRegistry internally, and stash them on `app.state`. Task 13 will formalize this.

Update `src/aiproxy/app.py` `build_app()` signature: keep existing params, but internally construct `StreamBus()` and `RequestRegistry(bus)`:

```python
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
    from aiproxy.bus import StreamBus
    from aiproxy.registry import RequestRegistry

    http_client = _make_http_client()
    bus = StreamBus()
    registry = RequestRegistry(bus)
    engine = PassthroughEngine(
        http_client=http_client,
        sessionmaker=sessionmaker,
        bus=bus,
        registry=registry,
    )
    # ... rest unchanged, but stash bus/registry on app.state for later:
    app.state.bus = bus
    app.state.registry = registry
    # ... unchanged ...
```

Do the same for `lifespan()`.

- [ ] **Step 6: Re-run full suite — expect all green**

`uv run pytest tests/ -q`

- [ ] **Step 7: Commit**

```bash
git add src/aiproxy/core/passthrough.py src/aiproxy/app.py tests/integration/test_proxy_engine.py tests/integration/test_proxy_router.py
git commit -m "feat(phase-2): engine tees chunks, persists them, parses usage, computes cost"
```

---

## Task 10: Retention background task

**Files:**
- Create: `src/aiproxy/db/retention.py`
- Create: `tests/unit/test_retention.py`

The retention task runs every 60 seconds, identifies the Nth most-recent request by `started_at`, and nulls out bodies + deletes chunks for rows older than that cutoff.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_retention.py`:

```python
"""Tests for aiproxy.db.retention."""
import time

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.db.crud import chunks as chunks_crud
from aiproxy.db.crud import requests as req_crud
from aiproxy.db.models import Base, Chunk, Request
from aiproxy.db.retention import prune_old_requests


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sm
    finally:
        await engine.dispose()


async def _seed_request(sm, req_id: str, started_at: float, body: bytes | None = b"body", with_chunks: int = 0) -> None:
    async with sm() as s:
        await req_crud.create_pending(
            s, req_id=req_id, api_key_id=None, provider="openai",
            endpoint="/chat/completions", method="POST", model="gpt-4o",
            is_streaming=bool(with_chunks), client_ip=None, client_ua=None,
            req_headers={}, req_query=None, req_body=body,
            started_at=started_at,
        )
        if with_chunks:
            await chunks_crud.insert_batch(
                s, req_id, [(i, i * 1000, b"x") for i in range(with_chunks)]
            )
        await s.commit()


async def test_prune_keeps_recent_full_data(session_factory) -> None:
    now = time.time()
    # 5 requests, each with chunks and a body
    for i in range(5):
        await _seed_request(session_factory, f"r{i}", now - (5 - i), b"body", with_chunks=3)

    # Keep the 2 most recent full; prune older 3
    await prune_old_requests(session_factory, full_count=2)

    async with session_factory() as s:
        # All 5 requests rows still exist
        total = (await s.execute(select(func.count()).select_from(Request))).scalar()
        assert total == 5
        # Only 2 newest still have req_body
        with_body = (await s.execute(
            select(func.count()).select_from(Request).where(Request.req_body.is_not(None))
        )).scalar()
        assert with_body == 2
        # Only chunks for the 2 newest remain
        chunk_total = (await s.execute(select(func.count()).select_from(Chunk))).scalar()
        assert chunk_total == 2 * 3  # 2 requests * 3 chunks each


async def test_prune_noop_when_under_full_count(session_factory) -> None:
    now = time.time()
    for i in range(3):
        await _seed_request(session_factory, f"r{i}", now - (3 - i), with_chunks=2)

    await prune_old_requests(session_factory, full_count=5)

    async with session_factory() as s:
        with_body = (await s.execute(
            select(func.count()).select_from(Request).where(Request.req_body.is_not(None))
        )).scalar()
        assert with_body == 3
        chunk_total = (await s.execute(select(func.count()).select_from(Chunk))).scalar()
        assert chunk_total == 6
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement `src/aiproxy/db/retention.py`**

```python
"""Background retention: prune old request bodies + chunks to keep DB small.

The design keeps metadata + cost + token counts for every request forever,
but strips out req_body, resp_body, req_headers, resp_headers, and deletes
the chunks table rows for requests older than the Nth most recent.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from aiproxy.db.models import Chunk, Request

logger = logging.getLogger(__name__)


async def prune_old_requests(
    sessionmaker: async_sessionmaker,
    *,
    full_count: int,
) -> int:
    """Null out bodies + delete chunks for rows older than the Nth newest.

    Returns the number of requests trimmed.
    """
    async with sessionmaker() as session:
        # Find the started_at of the Nth most recent request.
        result = await session.execute(
            select(Request.started_at)
            .order_by(Request.started_at.desc())
            .offset(full_count - 1)
            .limit(1)
        )
        cutoff = result.scalar()
        if cutoff is None:
            return 0  # fewer than full_count rows — nothing to trim

        # Count how many rows will be trimmed
        to_trim_result = await session.execute(
            select(Request.req_id)
            .where(Request.started_at < cutoff)
            .where(Request.req_body.is_not(None))
        )
        req_ids = [row[0] for row in to_trim_result]
        if not req_ids:
            return 0

        # Null out bodies and headers
        await session.execute(
            update(Request)
            .where(Request.req_id.in_(req_ids))
            .values(
                req_body=None,
                resp_body=None,
                req_headers="{}",
                resp_headers=None,
            )
        )

        # Delete all chunks belonging to those requests
        await session.execute(
            delete(Chunk).where(Chunk.req_id.in_(req_ids))
        )

        await session.commit()
        return len(req_ids)


async def retention_loop(
    sessionmaker: async_sessionmaker,
    *,
    get_full_count: callable,   # () -> int, e.g. reads from Settings or config table
    interval_seconds: float = 60.0,
) -> None:
    """Run prune_old_requests in a loop. Swallows exceptions so the task keeps running."""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            full_count = get_full_count()
            if full_count > 0:
                trimmed = await prune_old_requests(sessionmaker, full_count=full_count)
                if trimmed:
                    logger.info("retention: pruned %d old requests", trimmed)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("retention loop error (continuing)")
```

- [ ] **Step 4: Run tests — expect 2 passing**

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/db/retention.py tests/unit/test_retention.py
git commit -m "feat(phase-2): add retention background task for layered cleanup"
```

---

## Task 11: Dashboard — HTTP routes + static HTML

**Files:**
- Create: `src/aiproxy/dashboard/__init__.py` (empty)
- Create: `src/aiproxy/dashboard/routes.py`
- Create: `src/aiproxy/dashboard/static/index.html`
- Create: `tests/integration/test_dashboard_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_dashboard_api.py`:

```python
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
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Create `src/aiproxy/dashboard/__init__.py`** (empty)

Create `src/aiproxy/dashboard/routes.py`:

```python
"""Dashboard HTTP + WebSocket routes (Phase 2 — no login yet).

- GET  /dashboard/                 static HTML
- GET  /dashboard/api/active       snapshot of active + history (from in-memory registry)
- WS   /dashboard/ws/active        live lifecycle events (start/update/finish)
- WS   /dashboard/ws/stream/{rid}  live chunks for one request
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from aiproxy.bus import ACTIVE_CHANNEL, StreamBus
from aiproxy.registry import RequestRegistry

_STATIC_DIR = Path(__file__).parent / "static"


def create_dashboard_router(
    *,
    bus: StreamBus,
    registry: RequestRegistry,
) -> APIRouter:
    router = APIRouter(prefix="/dashboard")

    @router.get("/")
    async def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    @router.get("/api/active")
    async def list_requests() -> JSONResponse:
        return JSONResponse({
            "active": registry.active(),
            "history": registry.history(),
        })

    @router.websocket("/ws/active")
    async def ws_active(ws: WebSocket) -> None:
        await ws.accept()
        q = bus.subscribe(ACTIVE_CHANNEL)
        try:
            await ws.send_json({
                "type": "snapshot",
                "active": registry.active(),
                "history": registry.history(),
            })
            while True:
                event = await q.get()
                await ws.send_json(event)
        except WebSocketDisconnect:
            pass
        finally:
            bus.unsubscribe(ACTIVE_CHANNEL, q)

    @router.websocket("/ws/stream/{req_id}")
    async def ws_stream(ws: WebSocket, req_id: str) -> None:
        await ws.accept()
        q = bus.subscribe(req_id)
        try:
            meta = registry.get(req_id)
            if meta is None:
                await ws.send_json({"type": "not_found", "req_id": req_id})
                return
            await ws.send_json({"type": "meta", "req": meta.snapshot()})
            if meta.status in ("done", "error", "canceled"):
                await ws.send_json({"type": "already_done", "status": meta.status})
                return
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=60.0)
                except asyncio.TimeoutError:
                    await ws.send_json({"type": "heartbeat"})
                    continue
                await ws.send_json(event)
                if event.get("type") in ("done", "error"):
                    break
        except WebSocketDisconnect:
            pass
        finally:
            bus.unsubscribe(req_id, q)

    return router
```

Note: the `ACTIVE_CHANNEL` constant should be re-exported from `bus.py` or imported from `registry.py`. Since `ACTIVE_CHANNEL` is defined in `registry.py` (see Task 2), import it from there:

```python
# Remove:   from aiproxy.bus import ACTIVE_CHANNEL, StreamBus
# Replace with:
from aiproxy.bus import StreamBus
from aiproxy.registry import ACTIVE_CHANNEL, RequestRegistry
```

- [ ] **Step 4: Create `src/aiproxy/dashboard/static/index.html`**

A single-file HTML + vanilla JS dashboard ported from the v0 MVP, adapted to the new registry snapshot shape. (The v0 MVP HTML uses `.active`/`.history` structures, so minimal changes needed.)

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>AI Proxy Dashboard</title>
<style>
  :root {
    --bg: #0e1116;
    --panel: #161b22;
    --border: #2d333b;
    --text: #c9d1d9;
    --muted: #8b949e;
    --accent: #58a6ff;
    --ok: #3fb950;
    --warn: #d29922;
    --err: #f85149;
    --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    background: var(--bg); color: var(--text);
    font: 13px/1.5 -apple-system, Segoe UI, sans-serif;
    display: grid;
    grid-template-rows: auto 1fr;
  }
  header {
    padding: 10px 16px;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 12px;
  }
  header h1 { font-size: 14px; margin: 0; font-weight: 600; }
  .conn { font-size: 11px; padding: 2px 8px; border-radius: 10px; }
  .conn.ok { background: rgba(63,185,80,0.15); color: var(--ok); }
  .conn.bad { background: rgba(248,81,73,0.15); color: var(--err); }

  main {
    display: grid;
    grid-template-columns: 400px 1fr;
    min-height: 0;
  }
  aside {
    border-right: 1px solid var(--border);
    overflow-y: auto;
    background: var(--panel);
  }
  .req {
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
    cursor: pointer;
  }
  .req:hover { background: rgba(88,166,255,0.05); }
  .req.active { background: rgba(88,166,255,0.12); border-left: 2px solid var(--accent); padding-left: 12px; }
  .req-top {
    display: flex; justify-content: space-between; align-items: center;
    font-family: var(--mono); font-size: 11px;
  }
  .req-id { color: var(--accent); }
  .req-status { font-size: 10px; padding: 1px 6px; border-radius: 8px; text-transform: uppercase; }
  .req-status.pending, .req-status.streaming { background: rgba(210,153,34,0.2); color: var(--warn); }
  .req-status.done { background: rgba(63,185,80,0.2); color: var(--ok); }
  .req-status.error { background: rgba(248,81,73,0.2); color: var(--err); }
  .req-status.canceled { background: rgba(139,148,158,0.2); color: var(--muted); }
  .req-meta { margin-top: 4px; color: var(--muted); font-size: 11px; }
  .req-meta b { color: var(--text); font-weight: 500; }

  section.viewer {
    display: flex; flex-direction: column; min-height: 0; padding: 16px;
  }
  .viewer-header { padding-bottom: 10px; border-bottom: 1px solid var(--border); margin-bottom: 10px; }
  .viewer-header h2 { font-size: 13px; margin: 0; font-family: var(--mono); }
  .viewer-stats { margin-top: 6px; color: var(--muted); font-size: 11px; }
  .viewer-stats span { margin-right: 14px; }
  .viewer-stats b { color: var(--text); }
  .rendered {
    flex: 1; min-height: 0; overflow-y: auto; padding: 12px;
    background: #0a0d12; border: 1px solid var(--border); border-radius: 6px;
    font-family: var(--mono); font-size: 12px;
    white-space: pre-wrap; word-break: break-word;
  }
  .rendered.empty { color: var(--muted); font-style: italic; }
  .placeholder {
    display: flex; align-items: center; justify-content: center;
    flex: 1; color: var(--muted);
  }
</style>
</head>
<body>
<header>
  <h1>AI Proxy — Live Dashboard</h1>
  <span id="conn" class="conn bad">disconnected</span>
  <span style="color: var(--muted); font-size: 11px;">click any request to watch it live</span>
</header>

<main>
  <aside id="list"></aside>
  <section class="viewer" id="viewer">
    <div class="placeholder">select a request on the left</div>
  </section>
</main>

<script>
const listEl = document.getElementById('list');
const viewerEl = document.getElementById('viewer');
const connEl = document.getElementById('conn');

let requests = new Map();
let selectedId = null;
let streamSock = null;
let rendered = '';

function fmtTime(ts) { return ts ? new Date(ts * 1000).toLocaleTimeString() : ''; }
function fmtDuration(m) {
  const end = m.finished_at || Date.now() / 1000;
  const ms = (end - m.started_at) * 1000;
  return ms < 1000 ? ms.toFixed(0) + 'ms' : (ms / 1000).toFixed(2) + 's';
}
function fmtCost(c) { return c == null ? '' : ' · $' + c.toFixed(4); }

function renderList() {
  const sorted = [...requests.values()].sort((a, b) => b.started_at - a.started_at);
  listEl.innerHTML = '';
  for (const m of sorted) {
    const div = document.createElement('div');
    div.className = 'req' + (m.req_id === selectedId ? ' active' : '');
    div.onclick = () => selectRequest(m.req_id);
    div.innerHTML = `
      <div class="req-top">
        <span class="req-id">${m.req_id}</span>
        <span class="req-status ${m.status}">${m.status}</span>
      </div>
      <div class="req-meta">
        <b>${m.method}</b> ${m.path}
        ${m.model ? ' · <b>' + m.model + '</b>' : ''}
      </div>
      <div class="req-meta">
        ${fmtTime(m.started_at)} · ${fmtDuration(m)}
        ${m.chunks ? ' · ' + m.chunks + ' chunks' : ''}
        ${m.input_tokens != null ? ' · ' + m.input_tokens + '→' + m.output_tokens + ' tok' : ''}
        ${fmtCost(m.cost_usd)}
      </div>
    `;
    listEl.appendChild(div);
  }
}

function renderViewer() {
  if (!selectedId) {
    viewerEl.innerHTML = '<div class="placeholder">select a request on the left</div>';
    return;
  }
  const m = requests.get(selectedId);
  if (!m) return;
  viewerEl.innerHTML = `
    <div class="viewer-header">
      <h2>${m.req_id} · <span class="req-status ${m.status}">${m.status}</span></h2>
      <div class="viewer-stats">
        <span><b>${m.method}</b> ${m.path}</span>
        <span>model: <b>${m.model || '—'}</b></span>
        <span>status: <b>${m.status_code || '—'}</b></span>
        <span>tokens: <b>${m.input_tokens ?? '—'}→${m.output_tokens ?? '—'}</b></span>
        <span>cost: <b>${m.cost_usd != null ? '$' + m.cost_usd.toFixed(4) : '—'}</b></span>
        <span>duration: <b id="v-dur">${fmtDuration(m)}</b></span>
      </div>
    </div>
    <div id="body" class="rendered empty">waiting for events…</div>
  `;
  updateBody();
}

function updateBody() {
  const el = document.getElementById('body');
  if (!el) return;
  if (!rendered) {
    el.className = 'rendered empty';
    el.textContent = 'waiting for chunks…';
  } else {
    el.className = 'rendered';
    el.textContent = rendered;
    el.scrollTop = el.scrollHeight;
  }
}

function selectRequest(reqId) {
  if (streamSock) { streamSock.close(); streamSock = null; }
  selectedId = reqId;
  rendered = '';
  renderList();
  renderViewer();
  const url = `ws://${location.host}/dashboard/ws/stream/${reqId}`;
  streamSock = new WebSocket(url);
  streamSock.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (ev.type === 'chunk') {
      rendered += `[chunk #${ev.seq} · +${(ev.offset_ns / 1e6).toFixed(1)}ms · ${ev.size}B]\n`;
      updateBody();
    } else if (ev.type === 'done' || ev.type === 'error' || ev.type === 'already_done') {
      updateBody();
    }
  };
}

function connectActive() {
  const url = `ws://${location.host}/dashboard/ws/active`;
  const ws = new WebSocket(url);
  ws.onopen = () => { connEl.className = 'conn ok'; connEl.textContent = 'connected'; };
  ws.onclose = () => {
    connEl.className = 'conn bad'; connEl.textContent = 'disconnected';
    setTimeout(connectActive, 1000);
  };
  ws.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (ev.type === 'snapshot') {
      requests.clear();
      for (const r of ev.active) requests.set(r.req_id, r);
      for (const r of ev.history) requests.set(r.req_id, r);
    } else if (ev.type === 'start' || ev.type === 'update' || ev.type === 'finish') {
      requests.set(ev.req.req_id, ev.req);
    }
    renderList();
    if (ev.req && ev.req.req_id === selectedId) renderViewer();
  };
}

connectActive();
</script>
</body>
</html>
```

- [ ] **Step 5: Run tests — expect 2 passing**

- [ ] **Step 6: Commit**

```bash
git add src/aiproxy/dashboard/ tests/integration/test_dashboard_api.py
git commit -m "feat(phase-2): port basic dashboard HTTP routes + HTML"
```

---

## Task 12: Dashboard WebSocket tests

**Files:**
- Create: `tests/integration/test_dashboard_ws.py`

This task just adds tests for the WebSocket routes from Task 11; implementation is already there.

- [ ] **Step 1: Create `tests/integration/test_dashboard_ws.py`**

```python
"""Integration tests for the dashboard WebSocket endpoints."""
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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


def test_ws_active_snapshot_and_start(app) -> None:
    a, bus, registry = app
    client = TestClient(a)
    with client.websocket_connect("/dashboard/ws/active") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
        assert snap["active"] == []

        # Start a request on the server side
        registry.start(RequestMeta(
            req_id="live1", provider="openai", model="gpt-4o",
            method="POST", path="/chat/completions", client_ip=None,
        ))
        ev = ws.receive_json()
        assert ev["type"] == "start"
        assert ev["req"]["req_id"] == "live1"


def test_ws_stream_not_found(app) -> None:
    a, _, _ = app
    client = TestClient(a)
    with client.websocket_connect("/dashboard/ws/stream/nonexistent") as ws:
        ev = ws.receive_json()
        assert ev["type"] == "not_found"


def test_ws_stream_already_done(app) -> None:
    a, _, registry = app
    registry.start(RequestMeta(
        req_id="old", provider="openai", model=None,
        method="POST", path="/p", client_ip=None,
    ))
    registry.finish("old", status="done")
    client = TestClient(a)
    with client.websocket_connect("/dashboard/ws/stream/old") as ws:
        meta = ws.receive_json()
        assert meta["type"] == "meta"
        done = ws.receive_json()
        assert done["type"] == "already_done"
        assert done["status"] == "done"


def test_ws_stream_receives_chunks(app) -> None:
    a, bus, registry = app
    registry.start(RequestMeta(
        req_id="streaming", provider="openai", model="gpt-4o",
        method="POST", path="/chat/completions", client_ip=None,
    ))
    registry.update("streaming", status="streaming")

    client = TestClient(a)
    with client.websocket_connect("/dashboard/ws/stream/streaming") as ws:
        meta = ws.receive_json()
        assert meta["type"] == "meta"

        # Publish a chunk — the ws handler should forward it
        bus.publish("streaming", {
            "type": "chunk", "req_id": "streaming", "seq": 0,
            "offset_ns": 0, "size": 5,
        })
        ev = ws.receive_json()
        assert ev["type"] == "chunk"
        assert ev["seq"] == 0

        bus.publish("streaming", {"type": "done", "req_id": "streaming", "status": "done"})
        ev = ws.receive_json()
        assert ev["type"] == "done"
```

- [ ] **Step 2: Run tests — expect 4 passing**

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_dashboard_ws.py
git commit -m "test(phase-2): integration tests for dashboard WebSocket endpoints"
```

---

## Task 13: App assembly update — wire bus, registry, dashboard, retention

**Files:**
- Modify: `src/aiproxy/app.py`

The `build_app` factory was partially updated in Task 9 to create a StreamBus + RequestRegistry. Now we also add the dashboard router and wire the retention task into the lifespan.

- [ ] **Step 1: Rewrite `src/aiproxy/app.py`**

```python
"""FastAPI app factory + production lifespan.

Two entry points:
  - `build_app(...)` — synchronous factory for tests.
  - `app` (module-level) — production instance, uses `lifespan()`.

Phase 2 additions:
  - StreamBus + RequestRegistry created here (one per app instance)
  - Dashboard router mounted alongside the proxy router
  - Retention background task spawned in lifespan
  - Pricing seed on first startup
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from aiproxy.auth.proxy_auth import ApiKeyCache
from aiproxy.bus import StreamBus
from aiproxy.core.passthrough import PassthroughEngine
from aiproxy.dashboard.routes import create_dashboard_router
from aiproxy.db.engine import create_engine_and_sessionmaker
from aiproxy.db.models import Base
from aiproxy.db.retention import retention_loop
from aiproxy.pricing.seed import seed_pricing_if_empty
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
    """Synchronous factory for tests."""
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
    app.include_router(create_dashboard_router(bus=bus, registry=registry))

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Production lifespan: init DB + http client, register routes, spawn
    retention task, dispose on exit."""
    sql_engine, sessionmaker = create_engine_and_sessionmaker(settings.database_url)
    async with sql_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed pricing if the table is empty (first-run bootstrap)
    await seed_pricing_if_empty(sessionmaker)

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

    app.include_router(create_router(engine=engine, providers=providers, cache=cache))
    app.include_router(create_dashboard_router(bus=bus, registry=registry))
    app.state.http_client = http_client
    app.state.sessionmaker = sessionmaker
    app.state.bus = bus
    app.state.registry = registry

    # Background retention task — reads threshold from settings
    retention_task = asyncio.create_task(retention_loop(
        sessionmaker,
        get_full_count=lambda: 500,  # TODO(phase-3): read from config table
        interval_seconds=60.0,
    ))

    try:
        yield
    finally:
        retention_task.cancel()
        try:
            await retention_task
        except asyncio.CancelledError:
            pass
        await http_client.aclose()
        await sql_engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="AI Proxy", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Step 2: Run full suite**

```bash
uv run pytest tests/ -q
```

Expect: all tests from Phase 1 + Phase 2 green.

- [ ] **Step 3: Commit**

```bash
git add src/aiproxy/app.py
git commit -m "feat(phase-2): wire bus + registry + dashboard + retention into lifespan"
```

---

## Task 14: Phase 2 final verification

This is an end-to-end verification checkpoint — no new code, just manual validation.

- [ ] **Step 1: Run full suite + coverage**

```bash
uv run pytest tests/ -v
uv run pytest --cov=src/aiproxy --cov-report=term-missing
```

Expect: all tests green. Coverage for `bus/`, `registry/`, `db/crud/chunks`, `db/crud/pricing`, `pricing/`, `db/retention` ≥ 85%. `dashboard/routes.py` ≥ 70%.

- [ ] **Step 2: Live end-to-end smoke test**

```bash
# Make sure .env has real provider keys
rm -f aiproxy.db
nohup uv run python -m aiproxy > /tmp/aiproxy-phase2.log 2>&1 &
sleep 3
curl -s http://127.0.0.1:8000/healthz
```

In another terminal (or proceed sequentially):

```bash
PROXY_KEY=$(uv run python scripts/create_api_key.py --name phase2-verify | grep -oE 'sk-aiprx-\S+')
uv run python scripts/test_all.py --key "$PROXY_KEY"
uv run python scripts/test_all.py --key "$PROXY_KEY" --stream
```

Expect: all 6 calls succeed (3 providers x {stream, non-stream}).

- [ ] **Step 3: Verify DB state**

```bash
uv run python -c "
import asyncio
from sqlalchemy import select, func
from aiproxy.db.engine import create_engine_and_sessionmaker
from aiproxy.db.models import Request, Chunk, Pricing
from aiproxy.settings import settings

async def main():
    engine, sm = create_engine_and_sessionmaker(settings.database_url)
    async with sm() as s:
        reqs = (await s.execute(select(func.count()).select_from(Request))).scalar()
        chunks = (await s.execute(select(func.count()).select_from(Chunk))).scalar()
        pricing = (await s.execute(select(func.count()).select_from(Pricing))).scalar()
        print(f'requests: {reqs}, chunks: {chunks}, pricing rows: {pricing}')
        print()
        result = await s.execute(
            select(Request.provider, Request.model, Request.status, Request.input_tokens,
                   Request.output_tokens, Request.cost_usd, Request.chunk_count)
            .order_by(Request.started_at)
        )
        for row in result.all():
            print(row)
    await engine.dispose()

asyncio.run(main())
"
```

Expect:
- `requests: 6`
- `chunks: >0` (all streaming requests have chunks; exact count depends on provider chunking)
- `pricing: >=6` (seed rows)
- Each streaming row has non-null `input_tokens`/`output_tokens`/`cost_usd`/`chunk_count`

- [ ] **Step 4: Live dashboard check**

Open `http://127.0.0.1:8000/dashboard/` in a browser. You should see:
- `connected` status in the header
- The 6 requests from your smoke tests in the left list
- Clicking a streaming request shows `[chunk #N · +Nms · NB]` events accumulating (the basic Phase 2 renderer — Phase 4's replay player will make this pretty)

In another terminal, fire off one more streaming request and confirm it appears in the dashboard list *before* it finishes.

- [ ] **Step 5: Stop the server**

```bash
pkill -9 -f aiproxy
```

- [ ] **Step 6: Phase 2 checkpoint commit**

```bash
git commit --allow-empty -m "chore(phase-2): Phase 2 persistence + basic dashboard complete

Verified:
- Full test suite passing
- Live end-to-end: 6 provider calls, all chunks persisted with offset_ns,
  usage extracted, cost computed against pricing table
- Dashboard live tee working: chunks visible in browser as they arrive
- Reload persists: requests visible after browser refresh

Ready for Phase 3: rich dashboard UX (A-layout, filters, FTS, login,
key management, pricing management, settings)."
```

---

## Done checklist

- [x] StreamBus in-memory pub/sub with bounded queues and drop-on-full semantics
- [x] RequestRegistry with start/update/finish lifecycle events published to bus
- [x] Chunks CRUD with batch insert + fetch
- [x] Provider `parse_usage()` for OpenAI (+ reused by OpenRouter) and Anthropic
- [x] Versioned Pricing CRUD with `find_effective(at=...)` lookup
- [x] Pricing seed data for gpt-4o/4o-mini/o3-mini, claude-sonnet/haiku/opus
- [x] Cost compute helper with cached-token handling
- [x] Passthrough engine tees chunks to bus + buffers (seq, offset_ns, data) + finalizes with chunk insert + usage + cost
- [x] Retention background task with layered cleanup (keep recent N full, null out older)
- [x] Dashboard HTTP routes (`/dashboard/`, `/dashboard/api/active`)
- [x] Dashboard WebSocket routes (`/ws/active`, `/ws/stream/{req_id}`)
- [x] Dashboard HTML (basic, ported from v0)
- [x] `app.py` wires everything + spawns retention task in lifespan
- [x] Full test suite green + coverage targets met
- [x] Live end-to-end verified with all three providers (stream + non-stream)
- [x] Dashboard live tee verified in browser
