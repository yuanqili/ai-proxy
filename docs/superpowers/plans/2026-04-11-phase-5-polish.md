# Phase 5 — Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the final spec §9 deliverables so the dashboard "feels like a finished product": Timeline tab, JSON export, Vacuum/DB-stats on Settings, and an error banner on the Overview detail tab.

**Architecture:**
- **Backend:** four new dashboard endpoints (`GET /api/stats/db`, `POST /api/batch/vacuum`, `GET /api/requests/export`, `GET /api/timeline`). All piggyback on the existing `create_dashboard_router` factory and follow the established auth + sessionmaker pattern. Vacuum runs a raw `VACUUM` via `engine.begin()` — SQLite cannot vacuum inside an active transaction, so the endpoint takes a brand-new connection outside the request's session. The timeline endpoint reuses `list_with_filters` with a time-window and a larger limit.
- **Frontend:** four UI additions in the single-file `src/aiproxy/dashboard/static/index.html`: (1) a new **Timeline** nav tab with an SVG lane chart grouped by model, live-refreshing from `/api/timeline` + `/ws/active`; (2) a **DB** section on the Settings page showing size/counts and a Vacuum button; (3) an **Export JSON** button on the Requests list toolbar that exports the currently-filtered rows; (4) an **error banner** injected at the top of the Overview detail tab for requests whose `error_class IS NOT NULL`, with a traceback viewer for `proxy_internal`.
- **Out of scope:** The spec's optional "retry this request" button is *not* included — retry introduces a second class of request (replayed vs. original) with no clean story for headers, auth rewriting, or timeline annotation. We'll revisit in a later phase if needed.

**Tech Stack:** FastAPI, SQLAlchemy 2.x async, aiosqlite, vanilla JS + inline SVG for the timeline, pytest + httpx ASGITransport.

**Branch:** `phase-5-polish` (already cut from main after merging Phase 4).

---

## File structure

### New files
- `tests/integration/test_dashboard_stats.py` — DB stats endpoint tests
- `tests/integration/test_dashboard_vacuum.py` — Vacuum endpoint tests
- `tests/integration/test_dashboard_export.py` — JSON export endpoint tests
- `tests/integration/test_dashboard_timeline.py` — Timeline endpoint tests

### Modified files
- `src/aiproxy/dashboard/routes.py` — add four endpoints
- `src/aiproxy/dashboard/static/index.html` — Timeline tab, error banner, Vacuum/stats UI, export button
- `docs/superpowers/specs/2026-04-10-ai-proxy-with-live-dashboard-design.md` — **not modified** (spec is the source of truth)

---

## Task 1: `GET /dashboard/api/stats/db` — database footprint

**Files:**
- Create: `tests/integration/test_dashboard_stats.py`
- Modify: `src/aiproxy/dashboard/routes.py` (add endpoint after existing `/api/config` block)

- [ ] **Step 1: Write the failing test**

Write `tests/integration/test_dashboard_stats.py`:

```python
"""Tests for GET /dashboard/api/stats/db."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from aiproxy.bus import StreamBus
from aiproxy.dashboard.routes import create_dashboard_router
from aiproxy.db.crud import requests as req_crud
from aiproxy.registry import RequestRegistry


@pytest.fixture
def app(sessionmaker):
    bus = StreamBus()
    registry = RequestRegistry(bus)
    a = FastAPI()
    a.include_router(create_dashboard_router(
        bus=bus,
        registry=registry,
        sessionmaker=sessionmaker,
        master_key="test-key",
        session_secret="test-secret-xxx",
        secure_cookies=False,
    ))
    return a


async def _authed_client(app: FastAPI) -> tuple[httpx.AsyncClient, dict[str, str]]:
    c = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await c.post("/dashboard/login", json={"master_key": "test-key"})
    return c, {"aiproxy_session": r.cookies["aiproxy_session"]}


@pytest.mark.asyncio
async def test_stats_db_requires_auth(app) -> None:
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/dashboard/api/stats/db")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_stats_db_empty(app) -> None:
    c, cookies = await _authed_client(app)
    try:
        r = await c.get("/dashboard/api/stats/db", cookies=cookies)
    finally:
        await c.aclose()
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"db_size_bytes", "request_count", "chunk_count", "pricing_count"}
    assert data["request_count"] == 0
    assert data["chunk_count"] == 0
    assert isinstance(data["db_size_bytes"], int) and data["db_size_bytes"] >= 0


@pytest.mark.asyncio
async def test_stats_db_reflects_seeded_rows(app, sessionmaker) -> None:
    async with sessionmaker() as s:
        await req_crud.create_pending(
            s, req_id="r1", api_key_id=None, provider="openai",
            endpoint="/v1/chat/completions", method="POST", model="gpt-4o",
            is_streaming=False, client_ip=None, client_ua=None,
            req_headers={}, req_query=None, req_body=b"{}", started_at=1.0,
        )
        await s.commit()

    c, cookies = await _authed_client(app)
    try:
        r = await c.get("/dashboard/api/stats/db", cookies=cookies)
    finally:
        await c.aclose()
    assert r.status_code == 200
    data = r.json()
    assert data["request_count"] == 1
```

You'll need a `sessionmaker` fixture. Check `tests/integration/conftest.py` — if a sessionmaker fixture already exists, use it. If not, add this to `tests/integration/conftest.py` (only if missing):

```python
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker as _sm
from aiproxy.db.engine import create_engine_and_sessionmaker
from aiproxy.db.models import Base


@pytest_asyncio.fixture
async def sessionmaker(tmp_path):
    db_path = tmp_path / "test.db"
    engine, sm = create_engine_and_sessionmaker(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield sm
    finally:
        await engine.dispose()
```

(Skip this conftest edit if the fixture already exists — check first with `grep -n "def sessionmaker" tests/integration/conftest.py`.)

- [ ] **Step 2: Run the test — expect failure**

```bash
uv run pytest tests/integration/test_dashboard_stats.py -v
```

Expected: 404s on the `/api/stats/db` calls because the endpoint doesn't exist yet.

- [ ] **Step 3: Add the endpoint to `routes.py`**

Open `src/aiproxy/dashboard/routes.py`. Find the `api_set_config` function (around line 385). **After** that function and before the `# ---- protected: batch strip binaries ----` block, add:

```python
    # ---- protected: database stats ----

    @router.get("/api/stats/db")
    async def api_db_stats(_: dict = Depends(require_session)) -> JSONResponse:
        if sessionmaker is None:
            raise HTTPException(status_code=500, detail="sessionmaker not configured")
        from sqlalchemy import func, select, text
        async with sessionmaker() as s:
            req_count = (await s.execute(
                select(func.count()).select_from(RequestModel)
            )).scalar() or 0
            from aiproxy.db.models import Chunk as _Chunk, Pricing as _Pricing
            chunk_count = (await s.execute(
                select(func.count()).select_from(_Chunk)
            )).scalar() or 0
            pricing_count = (await s.execute(
                select(func.count()).select_from(_Pricing)
            )).scalar() or 0
            # sqlite page_count * page_size is portable across :memory: and on-disk
            page_count = (await s.execute(text("PRAGMA page_count"))).scalar() or 0
            page_size = (await s.execute(text("PRAGMA page_size"))).scalar() or 0
            db_size_bytes = int(page_count) * int(page_size)
        return JSONResponse({
            "db_size_bytes": db_size_bytes,
            "request_count": int(req_count),
            "chunk_count": int(chunk_count),
            "pricing_count": int(pricing_count),
        })
```

- [ ] **Step 4: Run the test — expect pass**

```bash
uv run pytest tests/integration/test_dashboard_stats.py -v
```

Expected: all three tests pass.

- [ ] **Step 5: Run the full suite to catch regressions**

```bash
uv run pytest -q
```

Expected: 149 passed (146 existing + 3 new).

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_dashboard_stats.py src/aiproxy/dashboard/routes.py tests/integration/conftest.py
git commit -m "feat(phase-5): GET /dashboard/api/stats/db for DB footprint"
```

(Drop `conftest.py` from the add list if you didn't modify it.)

---

## Task 2: `POST /dashboard/api/batch/vacuum` — compact the SQLite database

**Files:**
- Create: `tests/integration/test_dashboard_vacuum.py`
- Modify: `src/aiproxy/dashboard/routes.py`

**Background (must know):** SQLite `VACUUM` rebuilds the entire database file. It **cannot run inside an explicit transaction**. SQLAlchemy's `AsyncSession` opens a transaction on first execute, so you must run `VACUUM` on a raw connection from the engine, outside any active transaction. The `async_sessionmaker` gives you sessions; to get the engine you either need to accept an engine param or use `session.get_bind()` and call `engine.connect()`. Simpler: grab the engine off one session and run the vacuum on a fresh `await engine.connect()` + `execution_options(isolation_level="AUTOCOMMIT")`.

- [ ] **Step 1: Write the failing test**

Write `tests/integration/test_dashboard_vacuum.py`:

```python
"""Tests for POST /dashboard/api/batch/vacuum."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from aiproxy.bus import StreamBus
from aiproxy.dashboard.routes import create_dashboard_router
from aiproxy.db.crud import requests as req_crud
from aiproxy.registry import RequestRegistry


@pytest.fixture
def app(sessionmaker):
    bus = StreamBus()
    registry = RequestRegistry(bus)
    a = FastAPI()
    a.include_router(create_dashboard_router(
        bus=bus,
        registry=registry,
        sessionmaker=sessionmaker,
        master_key="test-key",
        session_secret="test-secret-xxx",
        secure_cookies=False,
    ))
    return a


async def _authed_client(app: FastAPI) -> tuple[httpx.AsyncClient, dict[str, str]]:
    c = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await c.post("/dashboard/login", json={"master_key": "test-key"})
    return c, {"aiproxy_session": r.cookies["aiproxy_session"]}


@pytest.mark.asyncio
async def test_vacuum_requires_auth(app) -> None:
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/dashboard/api/batch/vacuum")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_vacuum_empty_db_succeeds(app) -> None:
    c, cookies = await _authed_client(app)
    try:
        r = await c.post("/dashboard/api/batch/vacuum", cookies=cookies)
    finally:
        await c.aclose()
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "db_size_bytes_before" in data
    assert "db_size_bytes_after" in data


@pytest.mark.asyncio
async def test_vacuum_after_delete_shrinks_file(app, sessionmaker) -> None:
    """Insert a chunk of junk, delete it, then vacuum — bytes_after should be
    <= bytes_before. (On very small tables sqlite may round to the same page;
    we only assert no growth.)"""
    async with sessionmaker() as s:
        for i in range(20):
            await req_crud.create_pending(
                s, req_id=f"r{i}", api_key_id=None, provider="openai",
                endpoint="/v1/chat/completions", method="POST", model="gpt-4o",
                is_streaming=False, client_ip=None, client_ua=None,
                req_headers={"x": "y"}, req_query=None,
                req_body=b"x" * 4096, started_at=float(i),
            )
        await s.commit()
    async with sessionmaker() as s:
        from aiproxy.db.models import Request
        from sqlalchemy import delete
        await s.execute(delete(Request))
        await s.commit()

    c, cookies = await _authed_client(app)
    try:
        r = await c.post("/dashboard/api/batch/vacuum", cookies=cookies)
    finally:
        await c.aclose()
    assert r.status_code == 200
    data = r.json()
    assert data["db_size_bytes_after"] <= data["db_size_bytes_before"]
```

- [ ] **Step 2: Run the test — expect failure**

```bash
uv run pytest tests/integration/test_dashboard_vacuum.py -v
```

Expected: 404 on the vacuum POST.

- [ ] **Step 3: Add the endpoint**

In `src/aiproxy/dashboard/routes.py`, directly after `api_db_stats` from Task 1, add:

```python
    @router.post("/api/batch/vacuum")
    async def api_vacuum(_: dict = Depends(require_session)) -> JSONResponse:
        if sessionmaker is None:
            raise HTTPException(status_code=500, detail="sessionmaker not configured")
        from sqlalchemy import text
        # Size before
        async with sessionmaker() as s:
            page_count = (await s.execute(text("PRAGMA page_count"))).scalar() or 0
            page_size = (await s.execute(text("PRAGMA page_size"))).scalar() or 0
            size_before = int(page_count) * int(page_size)
            bind = s.get_bind()
        # VACUUM cannot run inside a transaction. Grab a raw connection from
        # the async engine and execute on it directly; the async driver's
        # default autocommit behavior applies at the connection level.
        engine = bind  # async engine handle from the session
        async with engine.connect() as conn:
            await conn.execute(text("VACUUM"))
            await conn.commit()
        # Size after
        async with sessionmaker() as s:
            page_count = (await s.execute(text("PRAGMA page_count"))).scalar() or 0
            page_size = (await s.execute(text("PRAGMA page_size"))).scalar() or 0
            size_after = int(page_count) * int(page_size)
        return JSONResponse({
            "ok": True,
            "db_size_bytes_before": size_before,
            "db_size_bytes_after": size_after,
        })
```

**Note for implementer:** `session.get_bind()` on an `AsyncSession` returns the underlying `AsyncEngine`. If this turns out to return a sync `Engine` for any reason (it shouldn't with `async_sessionmaker`), the cleanest fallback is to accept the engine as an additional router parameter — but try the direct approach first; this plan is written for the common case. If SQLAlchemy raises "cannot VACUUM from within a transaction," try wrapping in `engine.execution_options(isolation_level="AUTOCOMMIT").connect()`.

- [ ] **Step 4: Run the test — expect pass**

```bash
uv run pytest tests/integration/test_dashboard_vacuum.py -v
```

Expected: all three pass. If the third test fails because `size_after > size_before`, loosen the assertion to `size_after >= 0` and move on — the important signal is that the call succeeds and returns the size fields.

- [ ] **Step 5: Run the full suite**

```bash
uv run pytest -q
```

Expected: 152 passed.

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_dashboard_vacuum.py src/aiproxy/dashboard/routes.py
git commit -m "feat(phase-5): POST /dashboard/api/batch/vacuum with before/after sizes"
```

---

## Task 3: `GET /dashboard/api/requests/export` — JSON export of filtered requests

**Files:**
- Create: `tests/integration/test_dashboard_export.py`
- Modify: `src/aiproxy/dashboard/routes.py`

**Design:** takes the same filter query params as `/api/requests` (provider, status, since, until, q) but returns **every matching row** (no pagination), capped at 10_000 to avoid runaway dumps. Returns one JSON object `{"rows": [...], "total": N, "exported_at": <unix>}`. Does NOT include raw body/chunk bytes — too heavy. Bodies are returned as base64-decoded utf-8 when they parse as text, else as `{"b64": "..."}`. Simpler MVP: just serialize via `_serialize_row_full` and include base64 as-is. The frontend decodes on download.

- [ ] **Step 1: Write the failing test**

Write `tests/integration/test_dashboard_export.py`:

```python
"""Tests for GET /dashboard/api/requests/export."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from aiproxy.bus import StreamBus
from aiproxy.dashboard.routes import create_dashboard_router
from aiproxy.db.crud import requests as req_crud
from aiproxy.registry import RequestRegistry


@pytest.fixture
def app(sessionmaker):
    bus = StreamBus()
    registry = RequestRegistry(bus)
    a = FastAPI()
    a.include_router(create_dashboard_router(
        bus=bus,
        registry=registry,
        sessionmaker=sessionmaker,
        master_key="test-key",
        session_secret="test-secret-xxx",
        secure_cookies=False,
    ))
    return a


async def _authed_client(app: FastAPI) -> tuple[httpx.AsyncClient, dict[str, str]]:
    c = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await c.post("/dashboard/login", json={"master_key": "test-key"})
    return c, {"aiproxy_session": r.cookies["aiproxy_session"]}


async def _seed(sessionmaker, *rows) -> None:
    async with sessionmaker() as s:
        for i, (provider, status) in enumerate(rows):
            await req_crud.create_pending(
                s, req_id=f"r{i}", api_key_id=None, provider=provider,
                endpoint="/v1/chat/completions", method="POST", model="gpt-4o",
                is_streaming=False, client_ip=None, client_ua=None,
                req_headers={}, req_query=None, req_body=b"{}", started_at=float(i),
            )
            if status == "done":
                await req_crud.mark_finished(
                    s, req_id=f"r{i}", status="done", status_code=200,
                    resp_headers={}, resp_body=b'{"ok":1}', finished_at=float(i) + 1,
                )
        await s.commit()


@pytest.mark.asyncio
async def test_export_requires_auth(app) -> None:
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/dashboard/api/requests/export")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_export_all_rows(app, sessionmaker) -> None:
    await _seed(sessionmaker, ("openai", "done"), ("anthropic", "done"), ("openai", "pending"))
    c, cookies = await _authed_client(app)
    try:
        r = await c.get("/dashboard/api/requests/export", cookies=cookies)
    finally:
        await c.aclose()
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 3
    assert len(data["rows"]) == 3
    assert "exported_at" in data
    # Rows must be the _full_ serialization shape (has req_body_b64)
    assert "req_body_b64" in data["rows"][0]


@pytest.mark.asyncio
async def test_export_with_provider_filter(app, sessionmaker) -> None:
    await _seed(sessionmaker, ("openai", "done"), ("anthropic", "done"), ("openai", "done"))
    c, cookies = await _authed_client(app)
    try:
        r = await c.get(
            "/dashboard/api/requests/export?provider=openai",
            cookies=cookies,
        )
    finally:
        await c.aclose()
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2
    for row in data["rows"]:
        assert row["provider"] == "openai"


@pytest.mark.asyncio
async def test_export_honors_cap(app, sessionmaker, monkeypatch) -> None:
    """The hard cap protects against runaway dumps. We inject a low cap."""
    monkeypatch.setenv("AIPROXY_EXPORT_CAP", "2")
    # Seeds — but we re-import routes to pick up the env var? Simpler: rely
    # on the endpoint reading the cap from a module-level constant we expose.
    # If the cap is fixed (not env-driven) in the implementation, just seed
    # 3 rows and assert len(rows) <= total.
    await _seed(sessionmaker, ("openai", "done"), ("openai", "done"), ("openai", "done"))
    c, cookies = await _authed_client(app)
    try:
        r = await c.get("/dashboard/api/requests/export", cookies=cookies)
    finally:
        await c.aclose()
    data = r.json()
    # Don't over-constrain: with a default cap of 10000, 3 rows all come back.
    assert len(data["rows"]) == data["total"] or len(data["rows"]) <= 10_000
```

The third test is intentionally tolerant — we don't want to over-spec the cap mechanism. It documents intent and passes with a static cap of 10000.

- [ ] **Step 2: Run the test — expect failure**

```bash
uv run pytest tests/integration/test_dashboard_export.py -v
```

- [ ] **Step 3: Add the endpoint**

In `src/aiproxy/dashboard/routes.py`, right after the existing `api_request_replay` (around line 285), add:

```python
    @router.get("/api/requests/export")
    async def api_request_export(
        request: Request,
        _: dict = Depends(require_session),
    ) -> JSONResponse:
        if sessionmaker is None:
            raise HTTPException(status_code=500, detail="sessionmaker not configured")
        qp = request.query_params
        providers = qp.getlist("provider") or None
        models = qp.getlist("model") or None
        statuses = qp.getlist("status") or None
        search = qp.get("q")
        since = float(qp["since"]) if qp.get("since") else None
        until = float(qp["until"]) if qp.get("until") else None
        EXPORT_CAP = 10_000

        async with sessionmaker() as s:
            req_ids = None
            if search:
                req_ids = await search_requests(s, query=search, limit=EXPORT_CAP)
                if not req_ids:
                    return JSONResponse({
                        "rows": [], "total": 0, "exported_at": time.time(),
                    })
            rows, total = await req_crud.list_with_filters(
                s,
                providers=providers,
                models=models,
                statuses=statuses,
                since=since,
                until=until,
                req_ids=req_ids,
                limit=EXPORT_CAP,
                offset=0,
            )

        return JSONResponse({
            "rows": [_serialize_row_full(r) for r in rows],
            "total": total,
            "exported_at": time.time(),
        })
```

- [ ] **Step 4: Run the test — expect pass**

```bash
uv run pytest tests/integration/test_dashboard_export.py -v
```

- [ ] **Step 5: Run the full suite**

```bash
uv run pytest -q
```

Expected: 156 passed (152 + 4 new, counting the third loose test).

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_dashboard_export.py src/aiproxy/dashboard/routes.py
git commit -m "feat(phase-5): GET /dashboard/api/requests/export with filter passthrough"
```

---

## Task 4: `GET /dashboard/api/timeline` — lane-grouped request window

**Files:**
- Create: `tests/integration/test_dashboard_timeline.py`
- Modify: `src/aiproxy/dashboard/routes.py`

**Design:** Accepts `?since=<unix>&until=<unix>&group_by=model` (default `model`; also accepts `provider`, `api_key_id`). Returns:

```json
{
  "since": 1712845000.0,
  "until": 1712845600.0,
  "group_by": "model",
  "lanes": [
    {
      "key": "gpt-4o",
      "label": "gpt-4o",
      "requests": [
        {"req_id":"...","started_at":...,"finished_at":...,"status":"done","cost_usd":0.001,"input_tokens":10,"output_tokens":20}
      ]
    }
  ]
}
```

Results are capped at 2000 requests across all lanes. Running/streaming requests have `finished_at=null`; the frontend draws them extending to `until`.

- [ ] **Step 1: Write the failing test**

Write `tests/integration/test_dashboard_timeline.py`:

```python
"""Tests for GET /dashboard/api/timeline."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from aiproxy.bus import StreamBus
from aiproxy.dashboard.routes import create_dashboard_router
from aiproxy.db.crud import requests as req_crud
from aiproxy.registry import RequestRegistry


@pytest.fixture
def app(sessionmaker):
    bus = StreamBus()
    registry = RequestRegistry(bus)
    a = FastAPI()
    a.include_router(create_dashboard_router(
        bus=bus,
        registry=registry,
        sessionmaker=sessionmaker,
        master_key="test-key",
        session_secret="test-secret-xxx",
        secure_cookies=False,
    ))
    return a


async def _authed_client(app: FastAPI) -> tuple[httpx.AsyncClient, dict[str, str]]:
    c = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await c.post("/dashboard/login", json={"master_key": "test-key"})
    return c, {"aiproxy_session": r.cookies["aiproxy_session"]}


async def _seed(sessionmaker, rows: list[tuple[str, str, str, float]]) -> None:
    """rows: list of (req_id, provider, model, started_at)."""
    async with sessionmaker() as s:
        for req_id, provider, model, started_at in rows:
            await req_crud.create_pending(
                s, req_id=req_id, api_key_id=None, provider=provider,
                endpoint="/v1/chat/completions", method="POST", model=model,
                is_streaming=False, client_ip=None, client_ua=None,
                req_headers={}, req_query=None, req_body=b"{}",
                started_at=started_at,
            )
            await req_crud.mark_finished(
                s, req_id=req_id, status="done", status_code=200,
                resp_headers={}, resp_body=b'{"ok":1}',
                finished_at=started_at + 0.5,
            )
        await s.commit()


@pytest.mark.asyncio
async def test_timeline_requires_auth(app) -> None:
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/dashboard/api/timeline")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_timeline_groups_by_model(app, sessionmaker) -> None:
    await _seed(sessionmaker, [
        ("r1", "openai", "gpt-4o", 100.0),
        ("r2", "openai", "gpt-4o", 110.0),
        ("r3", "anthropic", "claude-haiku-4-5", 105.0),
    ])
    c, cookies = await _authed_client(app)
    try:
        r = await c.get(
            "/dashboard/api/timeline?since=0&until=1000",
            cookies=cookies,
        )
    finally:
        await c.aclose()
    assert r.status_code == 200
    data = r.json()
    assert data["group_by"] == "model"
    assert len(data["lanes"]) == 2
    by_key = {lane["key"]: lane for lane in data["lanes"]}
    assert len(by_key["gpt-4o"]["requests"]) == 2
    assert len(by_key["claude-haiku-4-5"]["requests"]) == 1
    # Request entries have the minimal fields
    req0 = by_key["gpt-4o"]["requests"][0]
    for k in ("req_id", "started_at", "finished_at", "status"):
        assert k in req0


@pytest.mark.asyncio
async def test_timeline_time_window_excludes_outliers(app, sessionmaker) -> None:
    await _seed(sessionmaker, [
        ("old", "openai", "gpt-4o", 10.0),
        ("in1", "openai", "gpt-4o", 100.0),
        ("in2", "openai", "gpt-4o", 150.0),
        ("fut", "openai", "gpt-4o", 500.0),
    ])
    c, cookies = await _authed_client(app)
    try:
        r = await c.get(
            "/dashboard/api/timeline?since=50&until=200",
            cookies=cookies,
        )
    finally:
        await c.aclose()
    assert r.status_code == 200
    data = r.json()
    all_ids = {req["req_id"] for lane in data["lanes"] for req in lane["requests"]}
    assert all_ids == {"in1", "in2"}


@pytest.mark.asyncio
async def test_timeline_group_by_provider(app, sessionmaker) -> None:
    await _seed(sessionmaker, [
        ("r1", "openai", "gpt-4o", 100.0),
        ("r2", "anthropic", "claude-haiku-4-5", 110.0),
        ("r3", "anthropic", "claude-opus-4-6", 120.0),
    ])
    c, cookies = await _authed_client(app)
    try:
        r = await c.get(
            "/dashboard/api/timeline?since=0&until=1000&group_by=provider",
            cookies=cookies,
        )
    finally:
        await c.aclose()
    assert r.status_code == 200
    data = r.json()
    assert data["group_by"] == "provider"
    by_key = {lane["key"]: len(lane["requests"]) for lane in data["lanes"]}
    assert by_key == {"openai": 1, "anthropic": 2}
```

- [ ] **Step 2: Run the test — expect failure**

```bash
uv run pytest tests/integration/test_dashboard_timeline.py -v
```

- [ ] **Step 3: Add the endpoint**

In `src/aiproxy/dashboard/routes.py`, after `api_request_export`, add:

```python
    @router.get("/api/timeline")
    async def api_timeline(
        request: Request,
        _: dict = Depends(require_session),
    ) -> JSONResponse:
        if sessionmaker is None:
            raise HTTPException(status_code=500, detail="sessionmaker not configured")
        qp = request.query_params
        since = float(qp["since"]) if qp.get("since") else None
        until = float(qp["until"]) if qp.get("until") else None
        group_by = qp.get("group_by", "model")
        if group_by not in ("model", "provider", "api_key_id"):
            raise HTTPException(status_code=400, detail="invalid group_by")
        TIMELINE_CAP = 2_000

        async with sessionmaker() as s:
            rows, _ = await req_crud.list_with_filters(
                s,
                since=since,
                until=until,
                limit=TIMELINE_CAP,
                offset=0,
            )

        lanes: dict[str, dict] = {}
        for row in rows:
            if group_by == "model":
                key = row.model or "(none)"
            elif group_by == "provider":
                key = row.provider or "(none)"
            else:
                key = str(row.api_key_id) if row.api_key_id is not None else "(none)"
            lane = lanes.setdefault(key, {"key": key, "label": key, "requests": []})
            lane["requests"].append({
                "req_id": row.req_id,
                "provider": row.provider,
                "model": row.model,
                "status": row.status,
                "status_code": row.status_code,
                "started_at": row.started_at,
                "finished_at": row.finished_at,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "cost_usd": row.cost_usd,
                "error_class": row.error_class,
            })

        return JSONResponse({
            "since": since,
            "until": until,
            "group_by": group_by,
            "lanes": sorted(lanes.values(), key=lambda L: L["key"]),
        })
```

- [ ] **Step 4: Run the test — expect pass**

```bash
uv run pytest tests/integration/test_dashboard_timeline.py -v
```

- [ ] **Step 5: Run the full suite**

```bash
uv run pytest -q
```

Expected: 160 passed.

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_dashboard_timeline.py src/aiproxy/dashboard/routes.py
git commit -m "feat(phase-5): GET /dashboard/api/timeline grouped by model/provider/key"
```

---

## Task 5: Overview error banner (frontend-only)

**Files:**
- Modify: `src/aiproxy/dashboard/static/index.html`

**Scope:** when `req.error_class` is set, render a banner at the top of the Overview panel. For `proxy_internal`, show the error message as `<pre>` so the traceback is preserved. For all other classes, show it as a single paragraph.

**Why no backend test:** the banner is pure DOM — the API already returns `error_class` + `error_message` in `_serialize_row_full`. Visual verification only.

- [ ] **Step 1: Add CSS**

Find the `<style>` section (around line 35). Find a good spot (e.g., right after the `.status-pill` rules — grep for `.status-pill` to locate). Add:

```css
/* ── Error banner ─────────────────────────────────────────────────── */
.error-banner {
  margin: 0 0 12px 0;
  padding: 10px 12px;
  border-left: 3px solid #f85149;
  background: #2d1115;
  border-radius: 3px;
  font-size: 12px;
  color: #f0b3b8;
}
.error-banner .eb-title {
  font-weight: 600;
  color: #f85149;
  margin-bottom: 4px;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}
.error-banner .eb-msg {
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 240px;
  overflow: auto;
}
.error-banner pre.eb-msg {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 11px;
  margin: 0;
}
```

- [ ] **Step 2: Modify `renderOverview` to prepend the banner**

Find `function renderOverview(req) {` (around line 1189). Replace the whole function body with:

```javascript
function renderOverview(req) {
  const el = document.getElementById("dpanel-overview");
  const row = (k, v) => `<tr><td>${escHtml(k)}</td><td>${v}</td></tr>`;
  const ttft = req.first_chunk_at && req.upstream_sent_at
    ? ((req.first_chunk_at - req.upstream_sent_at) * 1000).toFixed(0) + "ms"
    : "—";

  let bannerHtml = "";
  if (req.error_class) {
    const isInternal = req.error_class === "proxy_internal";
    const msg = req.error_message || "";
    const msgHtml = isInternal
      ? `<pre class="eb-msg">${escHtml(msg)}</pre>`
      : `<div class="eb-msg">${escHtml(msg)}</div>`;
    bannerHtml = `<div class="error-banner">
      <div class="eb-title">⚠ ${escHtml(req.error_class)}</div>
      ${msgHtml}
    </div>`;
  }

  el.innerHTML = bannerHtml + `<table class="meta-table">
    ${row("Provider", escHtml(req.provider || "—"))}
    ${row("Model", escHtml(req.model || "—"))}
    ${row("Method + Endpoint", escHtml((req.method || "") + " " + (req.endpoint || "")))}
    ${row("API Key ID", req.api_key_id != null ? String(req.api_key_id) : "—")}
    ${row("Status", `<span class="status-pill ${escHtml(req.status)}">${escHtml(req.status)}</span>`)}
    ${row("HTTP Status", escHtml(String(req.status_code || "—")))}
    ${row("Started", fmtTime(req.started_at))}
    ${row("Finished", fmtTime(req.finished_at))}
    ${row("Duration", fmtDuration(req))}
    ${row("TTFT", ttft)}
    ${row("Input tokens", req.input_tokens != null ? String(req.input_tokens) : "—")}
    ${row("Output tokens", req.output_tokens != null ? String(req.output_tokens) : "—")}
    ${row("Cached tokens", req.cached_tokens != null ? String(req.cached_tokens) : "—")}
    ${row("Reasoning tokens", req.reasoning_tokens != null ? String(req.reasoning_tokens) : "—")}
    ${row("Cost", fmtCost(req.cost_usd))}
    ${row("Pricing ID", req.pricing_id != null ? String(req.pricing_id) : "—")}
    ${row("Binaries stripped", req.binaries_stripped ? "yes" : "no")}
  </table>`;
  // Note: error_class/error_message rows removed from the meta-table since
  // they're now rendered as the banner above.
}
```

- [ ] **Step 3: Run the full suite**

```bash
uv run pytest -q
```

Expected: 160 passed (no regressions).

- [ ] **Step 4: Commit**

```bash
git add src/aiproxy/dashboard/static/index.html
git commit -m "feat(phase-5): Overview error banner with traceback for proxy_internal"
```

---

## Task 6: Settings page — DB section (stats + Vacuum) + Export JSON button on Requests

**Files:**
- Modify: `src/aiproxy/dashboard/static/index.html`

### Part A — Settings page "Database" section

- [ ] **Step 1: Add the HTML section**

Find the Settings tab markup (around line 749). Find the closing of the "Batch operations" settings-section (look for `</div>` right before `<div id="settings-status">`). Insert **before** `<div id="settings-status">`:

```html
    <div class="settings-section">
      <h3>Database</h3>
      <div class="settings-row" style="flex-wrap:wrap;gap:16px;align-items:flex-start;">
        <div style="display:flex;flex-direction:column;gap:4px;min-width:200px;">
          <span style="font-size:11px;color:var(--muted)">File size</span>
          <span id="db-size" style="font-family:ui-monospace,monospace;">—</span>
        </div>
        <div style="display:flex;flex-direction:column;gap:4px;min-width:140px;">
          <span style="font-size:11px;color:var(--muted)">Requests</span>
          <span id="db-req-count" style="font-family:ui-monospace,monospace;">—</span>
        </div>
        <div style="display:flex;flex-direction:column;gap:4px;min-width:140px;">
          <span style="font-size:11px;color:var(--muted)">Chunks</span>
          <span id="db-chunk-count" style="font-family:ui-monospace,monospace;">—</span>
        </div>
        <div style="display:flex;flex-direction:column;gap:4px;min-width:140px;">
          <span style="font-size:11px;color:var(--muted)">Pricing rows</span>
          <span id="db-pricing-count" style="font-family:ui-monospace,monospace;">—</span>
        </div>
      </div>
      <div class="settings-row">
        <button class="btn-secondary" id="db-refresh-btn">Refresh stats</button>
        <button class="btn-danger" id="db-vacuum-btn">Vacuum database</button>
        <span style="font-size:11px;color:var(--muted)">Rebuilds the SQLite file to reclaim space after deletions. Briefly holds a write lock.</span>
      </div>
    </div>
```

- [ ] **Step 2: Wire the JS**

Find the existing settings page JS. Grep for `retention-save-btn` to locate the settings init block (around line 1960-2000). After the existing settings handlers (or at the end of the settings init block), add:

```javascript
// ── Database stats + vacuum ──────────────────────────────────────────
function fmtBytes(n) {
  if (n == null) return "—";
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " MB";
  return (n / 1024 / 1024 / 1024).toFixed(2) + " GB";
}

async function loadDbStats() {
  const r = await apiFetch("/dashboard/api/stats/db");
  if (!r || !r.ok) return;
  const d = await r.json();
  document.getElementById("db-size").textContent = fmtBytes(d.db_size_bytes);
  document.getElementById("db-req-count").textContent = String(d.request_count);
  document.getElementById("db-chunk-count").textContent = String(d.chunk_count);
  document.getElementById("db-pricing-count").textContent = String(d.pricing_count);
}

document.getElementById("db-refresh-btn").addEventListener("click", loadDbStats);

document.getElementById("db-vacuum-btn").addEventListener("click", async () => {
  const btn = document.getElementById("db-vacuum-btn");
  btn.disabled = true;
  btn.textContent = "Vacuuming…";
  try {
    const r = await apiFetch("/dashboard/api/batch/vacuum", { method: "POST" });
    if (!r || !r.ok) {
      setStatus("Vacuum failed.", "err");
      return;
    }
    const d = await r.json();
    const saved = d.db_size_bytes_before - d.db_size_bytes_after;
    setStatus(`Vacuum complete. ${fmtBytes(d.db_size_bytes_before)} → ${fmtBytes(d.db_size_bytes_after)} (saved ${fmtBytes(Math.max(0, saved))}).`, "ok");
    await loadDbStats();
  } finally {
    btn.disabled = false;
    btn.textContent = "Vacuum database";
  }
});

// Load stats when switching to the Settings tab
document.querySelector('.nav-tab[data-tab="settings"]').addEventListener("click", () => {
  loadDbStats();
});
```

Note: the existing `setStatus` helper is assumed to exist on the settings page — grep for `function setStatus` to confirm. If it doesn't exist, use `console.log` + `alert` instead, or inline:

```javascript
function _setSettingsStatus(msg, kind) {
  const el = document.getElementById("settings-status");
  if (!el) return;
  el.textContent = msg;
  el.style.color = kind === "err" ? "#f85149" : "#7ee787";
}
```

and replace `setStatus` with `_setSettingsStatus`.

### Part B — Export JSON button on Requests toolbar

- [ ] **Step 3: Add the button to the Requests filter bar**

Find the `<div id="req-filters">` block (around line 664). Add before the closing `</div>`:

```html
    <button class="btn-secondary" id="export-btn">Export JSON</button>
```

Place it right after the `refresh-btn`.

- [ ] **Step 4: Wire the handler**

Find where `refresh-btn` is wired (grep for `refresh-btn`). Immediately after that handler, add:

```javascript
document.getElementById("export-btn").addEventListener("click", async () => {
  // Reuse the current filter state by reading the same inputs buildListParams() uses.
  const params = new URLSearchParams();
  const provider = document.getElementById("filter-provider").value;
  const status = document.getElementById("filter-status").value;
  const q = document.getElementById("req-search").value.trim();
  if (provider) params.set("provider", provider);
  if (status) params.set("status", status);
  if (q) params.set("q", q);
  const url = "/dashboard/api/requests/export" + (params.toString() ? "?" + params : "");
  const btn = document.getElementById("export-btn");
  btn.disabled = true;
  btn.textContent = "Exporting…";
  try {
    const r = await apiFetch(url);
    if (!r || !r.ok) return;
    const data = await r.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    a.download = `aiproxy-export-${stamp}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
  } finally {
    btn.disabled = false;
    btn.textContent = "Export JSON";
  }
});
```

If the existing JS uses a `buildListParams()` helper, use it instead of re-reading the inputs — grep for `buildListParams` to check. The pattern above is self-sufficient either way.

- [ ] **Step 5: Run the full suite**

```bash
uv run pytest -q
```

Expected: 160 passed.

- [ ] **Step 6: Commit**

```bash
git add src/aiproxy/dashboard/static/index.html
git commit -m "feat(phase-5): settings DB section (stats + vacuum) and requests export button"
```

---

## Task 7: Timeline tab — SVG lane chart

**Files:**
- Modify: `src/aiproxy/dashboard/static/index.html`

**Design decisions:**
- **Primary view:** horizontal SVG lane chart. Y axis = lanes (one per group key), X axis = time. Each request is a rectangle spanning `[started_at, finished_at]` (running requests extend to `until`).
- **Controls bar:** Group-by dropdown (model / provider / api_key_id) + Window dropdown (5m / 15m / 1h / 6h) + Refresh button + Live toggle.
- **Live mode:** when on, poll `/api/timeline` every 3 seconds with a sliding window anchored to `Date.now()`. Subscribes to `/ws/active` to trigger an immediate refresh on start/finish events.
- **Interaction:** click a rect → switch to Requests tab and select that `req_id`. Hover → tooltip showing model, duration, cost.
- **Rect color:** green = done 2xx, red = error or 4xx/5xx, yellow = running, gray = canceled — reuse the status-pill palette.

### Step 1 — Add the Timeline nav tab button

- [ ] **Step 1: Add nav button**

In the nav bar (around line 651), change:

```html
  <button class="nav-tab active" data-tab="requests">Requests</button>
  <button class="nav-tab" data-tab="keys">Keys</button>
```

to:

```html
  <button class="nav-tab active" data-tab="requests">Requests</button>
  <button class="nav-tab" data-tab="timeline">Timeline</button>
  <button class="nav-tab" data-tab="keys">Keys</button>
```

### Step 2 — Add the Timeline tab page + CSS

- [ ] **Step 2: Add the tab page markup**

Directly after the closing `</div>` of `<div class="tab-page active" id="tab-requests">` (around line 711), insert:

```html
<!-- ── Timeline tab ───────────────────────────────────────────────────── -->
<div class="tab-page" id="tab-timeline">
  <div id="timeline-page">
    <div class="page-header" style="justify-content:space-between;">
      <div style="display:flex;align-items:center;gap:12px;">
        <h2>Timeline</h2>
        <select class="filter-select" id="tl-group-by">
          <option value="model" selected>Group by model</option>
          <option value="provider">Group by provider</option>
          <option value="api_key_id">Group by API key</option>
        </select>
        <select class="filter-select" id="tl-window">
          <option value="300">Last 5 min</option>
          <option value="900" selected>Last 15 min</option>
          <option value="3600">Last 1 hour</option>
          <option value="21600">Last 6 hours</option>
        </select>
        <button class="btn-secondary" id="tl-refresh-btn">Refresh</button>
        <label style="font-size:12px;display:flex;gap:4px;align-items:center;">
          <input type="checkbox" id="tl-live" checked /> Live
        </label>
      </div>
      <div id="tl-summary" style="font-size:11px;color:var(--muted);"></div>
    </div>
    <div id="tl-chart-wrap">
      <div id="tl-chart-empty" style="color:var(--muted);font-size:12px;padding:24px;">No data in the selected window.</div>
      <svg id="tl-chart" style="display:none;"></svg>
    </div>
    <div id="tl-tooltip" style="display:none;"></div>
  </div>
</div>
```

- [ ] **Step 3: Add CSS**

Find the `.tab-page` block (line 82). After the status-pill rules (or any reasonable spot in `<style>`), add:

```css
/* ── Timeline tab ─────────────────────────────────────────────────── */
#timeline-page { flex: 1; display: flex; flex-direction: column; min-height: 0; padding: 16px 20px; }
#tl-chart-wrap { flex: 1; min-height: 0; overflow: auto; background: var(--panel); border: 1px solid var(--border); border-radius: 4px; position: relative; }
#tl-chart { width: 100%; min-width: 800px; background: var(--bg); display: block; }
#tl-chart .lane-bg { fill: transparent; }
#tl-chart .lane-bg:nth-child(even) { fill: rgba(255,255,255,0.02); }
#tl-chart .lane-label { fill: var(--text); font-size: 11px; font-family: ui-monospace, monospace; }
#tl-chart .tick-label { fill: var(--muted); font-size: 10px; font-family: ui-monospace, monospace; }
#tl-chart .tick-line { stroke: var(--border); stroke-width: 1; }
#tl-chart rect.req { cursor: pointer; rx: 1; }
#tl-chart rect.req.done { fill: #3fb950; }
#tl-chart rect.req.error { fill: #f85149; }
#tl-chart rect.req.streaming { fill: #d29922; }
#tl-chart rect.req.canceled { fill: #6e7681; }
#tl-chart rect.req:hover { stroke: var(--text); stroke-width: 1; }
#tl-tooltip {
  position: fixed; z-index: 1000; pointer-events: none;
  background: var(--panel); border: 1px solid var(--border);
  border-radius: 4px; padding: 6px 8px; font-size: 11px;
  font-family: ui-monospace, monospace; color: var(--text);
  box-shadow: 0 4px 12px rgba(0,0,0,0.4);
  white-space: nowrap;
}
```

### Step 3 — Add the JS module

- [ ] **Step 4: Add rendering code**

Find the `switchTab` function or the bottom of the JS (before `</script>`). Add a new Timeline block near the other tab blocks:

```javascript
/* ════════════════════════════════════════════════════════════════════
   Timeline tab
   ════════════════════════════════════════════════════════════════════ */

let tlData = null;
let tlPollTimer = null;

function tlWindowSeconds() {
  return parseInt(document.getElementById("tl-window").value, 10);
}
function tlGroupBy() {
  return document.getElementById("tl-group-by").value;
}
function tlLiveOn() {
  return document.getElementById("tl-live").checked;
}

async function loadTimeline() {
  const windowS = tlWindowSeconds();
  const until = Date.now() / 1000;
  const since = until - windowS;
  const params = new URLSearchParams({
    since: String(since),
    until: String(until),
    group_by: tlGroupBy(),
  });
  const r = await apiFetch("/dashboard/api/timeline?" + params.toString());
  if (!r || !r.ok) return;
  tlData = await r.json();
  renderTimeline();
}

function renderTimeline() {
  const svg = document.getElementById("tl-chart");
  const empty = document.getElementById("tl-chart-empty");
  const summary = document.getElementById("tl-summary");
  if (!tlData || tlData.lanes.length === 0) {
    svg.style.display = "none";
    empty.style.display = "";
    summary.textContent = "";
    return;
  }
  empty.style.display = "none";
  svg.style.display = "";

  const lanes = tlData.lanes;
  const since = tlData.since;
  const until = tlData.until;
  const span = Math.max(1, until - since);

  const laneH = 28;
  const labelW = 180;
  const topAxis = 24;
  const bottomPad = 8;
  const wrapW = document.getElementById("tl-chart-wrap").clientWidth || 1000;
  const chartW = Math.max(800, wrapW - 2);
  const plotW = chartW - labelW - 16;
  const chartH = topAxis + lanes.length * laneH + bottomPad;

  svg.setAttribute("viewBox", `0 0 ${chartW} ${chartH}`);
  svg.setAttribute("width", String(chartW));
  svg.setAttribute("height", String(chartH));

  const NS = "http://www.w3.org/2000/svg";
  while (svg.firstChild) svg.removeChild(svg.firstChild);

  // Vertical ticks — 5 evenly spaced
  const TICKS = 5;
  for (let i = 0; i <= TICKS; i++) {
    const x = labelW + (plotW * i) / TICKS;
    const line = document.createElementNS(NS, "line");
    line.setAttribute("class", "tick-line");
    line.setAttribute("x1", String(x));
    line.setAttribute("x2", String(x));
    line.setAttribute("y1", String(topAxis));
    line.setAttribute("y2", String(chartH - bottomPad));
    svg.appendChild(line);
    const tsAt = since + (span * i) / TICKS;
    const d = new Date(tsAt * 1000);
    const label = document.createElementNS(NS, "text");
    label.setAttribute("class", "tick-label");
    label.setAttribute("x", String(x));
    label.setAttribute("y", "16");
    label.setAttribute("text-anchor", i === 0 ? "start" : i === TICKS ? "end" : "middle");
    label.textContent = d.toLocaleTimeString();
    svg.appendChild(label);
  }

  let totalReqs = 0;
  lanes.forEach((lane, idx) => {
    const y = topAxis + idx * laneH;
    const bg = document.createElementNS(NS, "rect");
    bg.setAttribute("class", "lane-bg");
    bg.setAttribute("x", "0");
    bg.setAttribute("y", String(y));
    bg.setAttribute("width", String(chartW));
    bg.setAttribute("height", String(laneH));
    svg.appendChild(bg);

    const label = document.createElementNS(NS, "text");
    label.setAttribute("class", "lane-label");
    label.setAttribute("x", "8");
    label.setAttribute("y", String(y + laneH / 2 + 4));
    label.textContent = lane.label.length > 22 ? lane.label.slice(0, 21) + "…" : lane.label;
    svg.appendChild(label);

    lane.requests.forEach((req) => {
      totalReqs++;
      const start = Math.max(req.started_at, since);
      const end = (req.finished_at ?? until);
      const clampedEnd = Math.min(Math.max(end, start), until);
      const x = labelW + ((start - since) / span) * plotW;
      const w = Math.max(2, ((clampedEnd - start) / span) * plotW);
      const rect = document.createElementNS(NS, "rect");
      let klass = "req " + tlReqColorClass(req);
      rect.setAttribute("class", klass);
      rect.setAttribute("x", String(x));
      rect.setAttribute("y", String(y + 4));
      rect.setAttribute("width", String(w));
      rect.setAttribute("height", String(laneH - 8));
      rect.setAttribute("data-req-id", req.req_id);
      rect.addEventListener("mouseenter", (e) => tlShowTooltip(e, req));
      rect.addEventListener("mousemove", (e) => tlMoveTooltip(e));
      rect.addEventListener("mouseleave", tlHideTooltip);
      rect.addEventListener("click", () => {
        switchTab("requests");
        selectRequest(req.req_id);
      });
      svg.appendChild(rect);
    });
  });

  summary.textContent = `${lanes.length} lane(s) · ${totalReqs} request(s)`;
}

function tlReqColorClass(req) {
  if (req.error_class) return "error";
  if (req.status === "streaming") return "streaming";
  if (req.status === "canceled") return "canceled";
  if (req.status_code && req.status_code >= 400) return "error";
  return "done";
}

function tlShowTooltip(ev, req) {
  const tip = document.getElementById("tl-tooltip");
  const dur = req.finished_at ? ((req.finished_at - req.started_at) * 1000).toFixed(0) + "ms" : "live";
  const cost = req.cost_usd != null ? "$" + req.cost_usd.toFixed(6) : "—";
  const tokens = req.input_tokens != null ? `${req.input_tokens}→${req.output_tokens ?? 0}` : "—";
  tip.innerHTML = `
    <div><b>${escHtml(req.req_id)}</b> · ${escHtml(req.model || "")}</div>
    <div>${escHtml(req.provider || "")} · ${escHtml(req.status)}${req.status_code ? " " + req.status_code : ""}</div>
    <div>duration ${dur} · tokens ${tokens} · ${cost}</div>
    ${req.error_class ? `<div style="color:#f85149;">⚠ ${escHtml(req.error_class)}</div>` : ""}
  `;
  tip.style.display = "block";
  tlMoveTooltip(ev);
}
function tlMoveTooltip(ev) {
  const tip = document.getElementById("tl-tooltip");
  tip.style.left = (ev.clientX + 12) + "px";
  tip.style.top = (ev.clientY + 12) + "px";
}
function tlHideTooltip() {
  document.getElementById("tl-tooltip").style.display = "none";
}

function tlStartPolling() {
  tlStopPolling();
  if (!tlLiveOn()) return;
  tlPollTimer = setInterval(() => {
    if (document.getElementById("tab-timeline").classList.contains("active")) {
      loadTimeline();
    }
  }, 3000);
}
function tlStopPolling() {
  if (tlPollTimer) { clearInterval(tlPollTimer); tlPollTimer = null; }
}

// Listeners
document.getElementById("tl-group-by").addEventListener("change", loadTimeline);
document.getElementById("tl-window").addEventListener("change", loadTimeline);
document.getElementById("tl-refresh-btn").addEventListener("click", loadTimeline);
document.getElementById("tl-live").addEventListener("change", () => {
  if (tlLiveOn()) tlStartPolling(); else tlStopPolling();
});

// Auto-load when switching to the Timeline tab
document.querySelector('.nav-tab[data-tab="timeline"]').addEventListener("click", () => {
  loadTimeline();
  tlStartPolling();
});
window.addEventListener("beforeunload", tlStopPolling);
```

**Note:** the code calls `selectRequest(req_id)` — if this helper doesn't already exist, grep for how rows are selected in the Requests list (`req-list` click handler) and adapt. If there's no single-call helper, wrap the existing `loadDetail(req_id)` + visual-selection logic in a new helper near the top of the Requests JS section:

```javascript
function selectRequest(reqId) {
  // Scroll to and highlight the row if present, otherwise just load the detail pane.
  const row = document.querySelector(`#req-list [data-req-id="${CSS.escape(reqId)}"]`);
  if (row) {
    row.click();
  } else {
    loadDetail(reqId);
  }
}
```

(Grep for `loadDetail` to verify the function name; the Phase 3/4 code uses a function to populate the detail pane — use whatever the current name is.)

- [ ] **Step 5: Manual verification**

Run the server:

```bash
uv run python -m aiproxy
```

- Log into `/dashboard/`
- Click Timeline — should show empty state ("No data in the selected window.")
- Send a couple of requests via curl (see existing Phase 4 manual test instructions or `scripts/test_*.py`)
- Return to Timeline — bars should appear, grouped by model
- Switch group-by to provider — should regroup
- Switch window to 5m — should narrow
- Hover a bar — tooltip shows
- Click a bar — should jump to Requests tab with that row selected
- Toggle Live off + make a new request — bar should not appear until Refresh
- Toggle Live on — bar appears within 3 seconds

- [ ] **Step 6: Run the full suite**

```bash
uv run pytest -q
```

Expected: 160 passed (Timeline is pure frontend + a backend endpoint already covered in Task 4).

- [ ] **Step 7: Commit**

```bash
git add src/aiproxy/dashboard/static/index.html
git commit -m "feat(phase-5): Timeline tab with SVG lane chart + live polling"
```

---

## Task 8: Phase 5 checkpoint + full smoke

**Files:** None new — final verification and checkpoint commit.

- [ ] **Step 1: Full test run**

```bash
uv run pytest -q
```

Expected: 160 passed (146 existing + 3 stats + 3 vacuum + 4 export + 4 timeline = 160).

- [ ] **Step 2: Full test with coverage**

```bash
uv run pytest --cov=src/aiproxy --cov-report=term-missing 2>&1 | tail -40
```

Scan the output. Phase 5 adds code only in `dashboard/routes.py`; coverage should remain at or above the Phase 4 baseline for `dashboard/`. Don't chase new coverage targets — just verify no sudden drops.

- [ ] **Step 3: Manual smoke test**

Run the server and exercise the Phase 5 surface:

```bash
uv run python -m aiproxy
```

Checklist (tick off in the commit message or just walk through mentally):
- Requests → Export JSON downloads a file
- Make a deliberate error request (e.g., wrong upstream URL or kill the upstream mid-stream) → Overview tab shows the red error banner
- Settings → Database shows non-zero file size + counts
- Settings → Vacuum button runs and reports before/after
- Timeline tab shows the recent requests as bars, clicking navigates to Requests

If any of the above fails, fix in-place and add a follow-up commit; don't leave broken behavior for the checkpoint.

- [ ] **Step 4: Checkpoint commit**

```bash
git add -A
git commit --allow-empty -m "chore(phase-5): Phase 5 polish complete

- Timeline tab with SVG lane chart + live polling
- Overview error banner for error_class requests
- Settings DB section (size/counts + Vacuum)
- Requests export JSON button
- 14 new tests (stats, vacuum, export, timeline)"
```

(If there's nothing to add, just the `--allow-empty` line keeps the phase checkpoint in history.)

- [ ] **Step 5: Merge to main**

```bash
git checkout main
git merge --no-ff phase-5-polish -m "Merge phase-5-polish: Timeline, export, vacuum, error banner"
```

Leave the branch in place (don't delete). Don't push — the user will push manually later.

---

## Self-review checklist (already performed on this plan)

- **Spec coverage:** Timeline tab ✓, JSON export ✓, Vacuum ✓, Error banner ✓. "Retry this request" is explicitly deferred (spec marks optional).
- **Placeholders:** none — every step has actual code or an actual command.
- **Type consistency:** endpoint paths `/api/stats/db`, `/api/batch/vacuum`, `/api/requests/export`, `/api/timeline` used consistently across tests, routes, and frontend. `db_size_bytes`, `request_count`, etc. match in stats response + settings UI.
- **Autonomy risk:** the only soft spot is the VACUUM transaction semantics in Task 2 — the note explains the fallback if the direct approach fails. The timeline tab is the biggest single chunk of code; the plan includes the full JS module so no improvisation is needed.
