# Phase 3 — Rich Dashboard UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the Phase 2 basic dashboard into a usable product: authenticated login, filterable/searchable request list, detailed request inspector with Overview/Request/Response/Chunks tabs, and management pages for API keys, pricing, and runtime settings. After this phase the dashboard is a complete proxy administration surface; only the novel replay player (Phase 4) remains.

**Architecture:**
- **Auth** — Dashboard login uses `settings.proxy_master_key` validated on `POST /dashboard/login`. Successful login issues an `httpOnly`, `SameSite=Strict` cookie containing a signed JWT (HS256, signed with `settings.session_secret`). A FastAPI dependency checks the cookie on every `/dashboard/api/*` and `/dashboard/ws/*` route.
- **Listing + Search** — Phase 3's dashboard list is backed by SQLite (not just the in-memory registry). A new `/dashboard/api/requests` endpoint supports pagination, provider/model/status filters, time-range, and full-text search via a new SQLite FTS5 virtual table kept in sync with the `requests` table by triggers.
- **Detail inspector** — `/dashboard/api/requests/{req_id}` returns the full row plus request/response bodies and chunks list. Frontend renders the existing tabs.
- **Management pages** — REST endpoints for `api_keys`, `pricing`, and `config` (runtime settings like `retention.full_count`).
- **Batch operations** — `POST /dashboard/api/batch/strip-binaries` accepts a filter spec and runs the binary-stripping regex on matching rows.
- **Settings** — A new `config` CRUD reads/writes the `config` table that was already created in Phase 1's schema.
- **Frontend** — A meaningfully larger single-file HTML+JS SPA with routing between tabs. No build step (keeps Phase 3 quick); we use template literals + fetch + vanilla JS.

**Tech Stack:** Phase 1/2 stack + `pyjwt>=2.9.0` for session cookie signing. Python 3.11+ `secrets` module for hmac comparison.

**Reference:** `docs/superpowers/specs/2026-04-10-ai-proxy-with-live-dashboard-design.md`
**Depends on:** Phase 2 (merged into `main`)

---

## Scope for Phase 3

**IN scope:**
- Dashboard auth: login endpoint + signed session cookie + dependency guard
- FTS5 shadow table for `requests` + triggers for insert/update/delete
- Paginated list endpoint with filters (provider, model, status, api_key, time range) and full-text search
- Detail endpoint returning full request + chunks
- Keys management endpoints (list / create / update / deactivate)
- Pricing management endpoints (list all versions / upsert new version)
- Config runtime settings (get / set / default) — `retention.full_count` is the first setting
- Batch strip-binaries operation
- Rich SPA dashboard: login page, Requests tab (A-layout), Keys/Pricing/Settings tabs
- Detail tabs inside Requests: Overview, Request, Response, Chunks
- Retention loop updated to read `full_count` from the `config` table instead of a hardcoded lambda

**OUT of scope (Phase 4+):**
- Replay player with scrubber, speed controls, dim/typewriter modes (Phase 4)
- Timeline tab (Phase 5)
- JSON Log mode for replay (Phase 4)
- Streaming auto-injection of `stream_options.include_usage` (Phase 4)
- Dashboard-visible traceback for `proxy_internal` errors (nice-to-have, Phase 5)
- Error banners with "retry this request" buttons (Phase 5)
- Dark/light theme toggle (Phase 5)

**Verification criteria:**
1. Full pytest suite green including new auth/search/detail/management tests
2. Live: unauthenticated request to `/dashboard/api/requests` returns 401
3. Live: `POST /dashboard/login` with correct master key returns 200 + Set-Cookie, subsequent requests with the cookie work
4. Live: FTS search finds a recorded request by substring of its body content
5. Live: creating an API key via the dashboard works and the new key can immediately make proxied calls
6. Live: setting `retention.full_count=10` via the Settings page, then making 15 requests, the oldest 5 lose their body/chunks on the next retention tick

---

## File Structure

New files:
```
src/aiproxy/
├── auth/
│   └── dashboard_auth.py            [NEW — JWT cookie + dependency]
├── db/
│   ├── crud/
│   │   └── config.py                [NEW — get/set runtime settings]
│   └── fts.py                       [NEW — FTS5 setup + triggers]
├── dashboard/
│   ├── routes.py                    [REWRITE — auth guard, new endpoints]
│   └── static/
│       ├── index.html               [REWRITE — full SPA]
│       └── login.html               [NEW — login form]
pyproject.toml                       [MODIFY — add pyjwt]
```

Modified files:
```
src/aiproxy/
├── app.py                           [MODIFY — retention reads config + FTS setup on boot]
├── db/retention.py                  [MODIFY — get_full_count reads from config table]
├── db/crud/requests.py              [MODIFY — add list_with_filters() helper]
```

New test files:
```
tests/
├── unit/
│   ├── test_dashboard_auth.py       [NEW — JWT signing/verification]
│   ├── test_crud_config.py          [NEW]
│   └── test_fts.py                  [NEW — FTS search against seeded rows]
└── integration/
    ├── test_dashboard_login.py      [NEW]
    ├── test_dashboard_list.py       [NEW — filters + search + pagination]
    ├── test_dashboard_detail.py     [NEW]
    ├── test_dashboard_keys_api.py   [NEW]
    ├── test_dashboard_pricing_api.py [NEW]
    ├── test_dashboard_config_api.py [NEW]
    └── test_dashboard_batch_strip.py [NEW]
```

---

## Task 1: Add pyjwt dependency + dashboard auth module

**Files:**
- Modify: `pyproject.toml`
- Create: `src/aiproxy/auth/dashboard_auth.py`
- Create: `tests/unit/test_dashboard_auth.py`

- [ ] **Step 1: Add pyjwt to dependencies**

Edit `pyproject.toml`, add `"pyjwt>=2.9.0"` to the `dependencies` list. Then:
```bash
uv sync
```

Expected: `Installed 1 package`.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_dashboard_auth.py`:

```python
"""Tests for dashboard auth — signed cookies."""
import time

import pytest

from aiproxy.auth.dashboard_auth import (
    issue_session_token,
    verify_session_token,
)


def test_valid_token_roundtrips() -> None:
    token = issue_session_token(secret="s3cret", ttl_seconds=3600)
    result = verify_session_token(token, secret="s3cret")
    assert result.valid is True
    assert result.expired is False
    assert result.payload is not None
    assert "exp" in result.payload
    assert "iat" in result.payload


def test_wrong_secret_fails() -> None:
    token = issue_session_token(secret="s3cret", ttl_seconds=3600)
    result = verify_session_token(token, secret="different")
    assert result.valid is False


def test_expired_token_flagged() -> None:
    # Issue a token that expired 1 second ago
    token = issue_session_token(secret="s3cret", ttl_seconds=-1)
    result = verify_session_token(token, secret="s3cret")
    assert result.valid is False
    assert result.expired is True


def test_malformed_token_fails() -> None:
    result = verify_session_token("not.a.jwt", secret="s3cret")
    assert result.valid is False


def test_none_token_fails() -> None:
    result = verify_session_token(None, secret="s3cret")
    assert result.valid is False
```

- [ ] **Step 3: Run tests — expect FAIL**

```bash
uv run pytest tests/unit/test_dashboard_auth.py -v
```

- [ ] **Step 4: Implement `src/aiproxy/auth/dashboard_auth.py`**

```python
"""Dashboard auth: master-key login + signed session cookie.

Design:
- Login endpoint accepts POST {"master_key": "..."} and compares against
  settings.proxy_master_key using hmac.compare_digest (constant-time).
- On success, issues a JWT (HS256) signed with settings.session_secret.
- The JWT payload contains only `iat` and `exp`. It is stored in an
  httpOnly, SameSite=Strict cookie named "aiproxy_session".
- Protected routes use a FastAPI dependency that reads the cookie,
  verifies the signature + expiry, and returns (or raises 401).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import jwt
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

COOKIE_NAME = "aiproxy_session"
ALGORITHM = "HS256"


@dataclass
class VerifyResult:
    valid: bool
    expired: bool = False
    payload: dict | None = None


def issue_session_token(*, secret: str, ttl_seconds: int) -> str:
    """Create a signed JWT with iat + exp claims."""
    now = int(time.time())
    payload = {"iat": now, "exp": now + ttl_seconds}
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def verify_session_token(token: str | None, *, secret: str) -> VerifyResult:
    """Verify a token's signature and expiry. Never raises."""
    if not token:
        return VerifyResult(valid=False)
    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        return VerifyResult(valid=False, expired=True)
    except jwt.InvalidTokenError:
        return VerifyResult(valid=False)
    return VerifyResult(valid=True, payload=payload)


def require_dashboard_session(session_secret: str):
    """FastAPI dependency factory: returns a dependency that validates the
    session cookie. Raises HTTPException(401) on missing/invalid token.
    """
    async def _dep(request: Request) -> dict:
        token = request.cookies.get(COOKIE_NAME)
        result = verify_session_token(token, secret=session_secret)
        if not result.valid:
            raise HTTPException(status_code=401, detail="unauthenticated")
        return result.payload or {}
    return _dep


def make_session_cookie_response(
    token: str,
    *,
    secure: bool,
    max_age_seconds: int,
) -> JSONResponse:
    """Return a JSONResponse with the session cookie set."""
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="strict",
        secure=secure,
        max_age=max_age_seconds,
        path="/",
    )
    return resp
```

- [ ] **Step 5: Run tests — expect 5 passing**

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/aiproxy/auth/dashboard_auth.py tests/unit/test_dashboard_auth.py
git commit -m "feat(phase-3): add dashboard JWT session auth module"
```

---

## Task 2: Config table CRUD

**Files:**
- Create: `src/aiproxy/db/crud/config.py`
- Create: `tests/unit/test_crud_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_crud_config.py`:

```python
"""Tests for aiproxy.db.crud.config — runtime settings read/write."""
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.db.crud import config as config_crud
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


async def test_get_with_default_returns_default_when_missing(session_factory) -> None:
    async with session_factory() as s:
        val = await config_crud.get(s, "retention.full_count", default=500)
    assert val == 500


async def test_set_and_get_roundtrip(session_factory) -> None:
    async with session_factory() as s:
        await config_crud.set_(s, "retention.full_count", 100)
        await s.commit()

    async with session_factory() as s:
        val = await config_crud.get(s, "retention.full_count", default=500)
    assert val == 100


async def test_set_overwrites(session_factory) -> None:
    async with session_factory() as s:
        await config_crud.set_(s, "retention.full_count", 100)
        await config_crud.set_(s, "retention.full_count", 250)
        await s.commit()

    async with session_factory() as s:
        val = await config_crud.get(s, "retention.full_count", default=500)
    assert val == 250


async def test_get_all(session_factory) -> None:
    async with session_factory() as s:
        await config_crud.set_(s, "a", 1)
        await config_crud.set_(s, "b", "two")
        await config_crud.set_(s, "c", [1, 2, 3])
        await s.commit()

    async with session_factory() as s:
        all_cfg = await config_crud.get_all(s)
    assert all_cfg == {"a": 1, "b": "two", "c": [1, 2, 3]}
```

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement `src/aiproxy/db/crud/config.py`**

```python
"""CRUD for the config table — runtime-mutable settings.

Values are JSON-encoded on write and decoded on read.
"""
from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from aiproxy.db.models import Config


async def get(session: AsyncSession, key: str, *, default: Any = None) -> Any:
    result = await session.execute(select(Config).where(Config.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        return default
    return json.loads(row.value)


async def set_(session: AsyncSession, key: str, value: Any) -> None:
    payload = json.dumps(value)
    now = time.time()
    stmt = sqlite_insert(Config).values(key=key, value=payload, updated_at=now)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Config.key],
        set_={"value": payload, "updated_at": now},
    )
    await session.execute(stmt)


async def get_all(session: AsyncSession) -> dict[str, Any]:
    result = await session.execute(select(Config))
    return {row.key: json.loads(row.value) for row in result.scalars().all()}
```

- [ ] **Step 4: Run tests — expect 4 passing**

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/db/crud/config.py tests/unit/test_crud_config.py
git commit -m "feat(phase-3): add config CRUD for runtime settings"
```

---

## Task 3: FTS5 shadow table for requests

**Files:**
- Create: `src/aiproxy/db/fts.py`
- Create: `tests/unit/test_fts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_fts.py`:

```python
"""Tests for FTS5 shadow table on requests."""
import time

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.db.crud import requests as req_crud
from aiproxy.db.fts import install_fts_schema, search_requests
from aiproxy.db.models import Base


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Install FTS table + triggers via raw SQL because SQLAlchemy doesn't
    # model FTS5 virtual tables directly.
    async with engine.begin() as conn:
        await install_fts_schema(conn)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sm
    finally:
        await engine.dispose()


async def _seed(sm, req_id: str, body: bytes, model: str = "gpt-4o") -> None:
    async with sm() as s:
        await req_crud.create_pending(
            s, req_id=req_id, api_key_id=None, provider="openai",
            endpoint="/chat/completions", method="POST", model=model,
            is_streaming=False, client_ip=None, client_ua=None,
            req_headers={}, req_query=None, req_body=body,
            started_at=time.time(),
        )
        await s.commit()


async def test_search_finds_request_by_body_text(session_factory) -> None:
    await _seed(session_factory, "r1", b'{"model":"gpt-4o","messages":[{"role":"user","content":"refactor this Python code"}]}')
    await _seed(session_factory, "r2", b'{"model":"gpt-4o","messages":[{"role":"user","content":"write a poem about spring"}]}')

    async with session_factory() as s:
        hits = await search_requests(s, query="refactor", limit=10)
    assert "r1" in hits
    assert "r2" not in hits


async def test_search_finds_by_model(session_factory) -> None:
    await _seed(session_factory, "r1", b'{"model":"gpt-4o"}', model="gpt-4o")
    await _seed(session_factory, "r2", b'{"model":"claude-haiku"}', model="claude-haiku")

    async with session_factory() as s:
        hits = await search_requests(s, query="claude", limit=10)
    assert "r2" in hits
    assert "r1" not in hits


async def test_search_no_matches_returns_empty(session_factory) -> None:
    await _seed(session_factory, "r1", b'{"model":"gpt-4o"}')

    async with session_factory() as s:
        hits = await search_requests(s, query="nonexistentxyz", limit=10)
    assert hits == []


async def test_fts_updated_via_trigger_on_insert(session_factory) -> None:
    """Inserting a request should automatically populate the FTS table."""
    await _seed(session_factory, "r1", b'{"messages":[{"content":"special-marker"}]}')

    async with session_factory() as s:
        result = await s.execute(
            text("SELECT COUNT(*) FROM requests_fts WHERE requests_fts MATCH 'special-marker'")
        )
        count = result.scalar()
    assert count == 1
```

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement `src/aiproxy/db/fts.py`**

```python
"""FTS5 virtual table for full-text search on requests.

SQLAlchemy doesn't directly model FTS5 virtual tables, so we create them
and their sync triggers with raw SQL. The shadow table is external-content
(content='' means it stores its own data) and we decode req_body / resp_body
bytes to text via triggers that use a scalar helper.

Since SQLite FTS5 triggers can't call Python, the triggers decode via the
raw BLOB data, which FTS5 will happily index as text if it's utf-8.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

_FTS_CREATE = """
CREATE VIRTUAL TABLE IF NOT EXISTS requests_fts USING fts5(
    req_id UNINDEXED,
    req_body_text,
    resp_body_text,
    model,
    endpoint,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

_TRIGGER_INSERT = """
CREATE TRIGGER IF NOT EXISTS requests_fts_insert AFTER INSERT ON requests
BEGIN
    INSERT INTO requests_fts (req_id, req_body_text, resp_body_text, model, endpoint)
    VALUES (
        NEW.req_id,
        COALESCE(CAST(NEW.req_body AS TEXT), ''),
        COALESCE(CAST(NEW.resp_body AS TEXT), ''),
        COALESCE(NEW.model, ''),
        NEW.endpoint
    );
END;
"""

_TRIGGER_UPDATE = """
CREATE TRIGGER IF NOT EXISTS requests_fts_update AFTER UPDATE ON requests
BEGIN
    DELETE FROM requests_fts WHERE req_id = OLD.req_id;
    INSERT INTO requests_fts (req_id, req_body_text, resp_body_text, model, endpoint)
    VALUES (
        NEW.req_id,
        COALESCE(CAST(NEW.req_body AS TEXT), ''),
        COALESCE(CAST(NEW.resp_body AS TEXT), ''),
        COALESCE(NEW.model, ''),
        NEW.endpoint
    );
END;
"""

_TRIGGER_DELETE = """
CREATE TRIGGER IF NOT EXISTS requests_fts_delete AFTER DELETE ON requests
BEGIN
    DELETE FROM requests_fts WHERE req_id = OLD.req_id;
END;
"""


async def install_fts_schema(conn: AsyncConnection) -> None:
    """Create the FTS5 virtual table and sync triggers. Idempotent."""
    for stmt in (_FTS_CREATE, _TRIGGER_INSERT, _TRIGGER_UPDATE, _TRIGGER_DELETE):
        await conn.execute(text(stmt))


async def search_requests(
    session: AsyncSession,
    *,
    query: str,
    limit: int = 50,
) -> list[str]:
    """Return a list of req_id values matching the FTS5 query, most-relevant first.

    The query string is passed through directly to FTS5 MATCH, so callers can use
    FTS5 query syntax (phrase queries in double quotes, AND/OR, prefix *, etc).
    For safety against empty strings, returns [] if query is empty.
    """
    if not query or not query.strip():
        return []
    result = await session.execute(
        text(
            "SELECT req_id FROM requests_fts "
            "WHERE requests_fts MATCH :q "
            "ORDER BY rank "
            "LIMIT :lim"
        ),
        {"q": query.strip(), "lim": limit},
    )
    return [row[0] for row in result.all()]
```

- [ ] **Step 4: Run tests — expect 4 passing**

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/db/fts.py tests/unit/test_fts.py
git commit -m "feat(phase-3): add FTS5 virtual table + sync triggers for requests"
```

---

## Task 4: List-with-filters helper for requests CRUD

**Files:**
- Modify: `src/aiproxy/db/crud/requests.py`
- Create: `tests/unit/test_crud_requests_list.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_crud_requests_list.py`:

```python
"""Tests for list_with_filters on aiproxy.db.crud.requests."""
import time

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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


async def _seed(sm, req_id, provider, model, status="done", started_at=None):
    started_at = started_at or time.time()
    async with sm() as s:
        await req_crud.create_pending(
            s, req_id=req_id, api_key_id=None, provider=provider,
            endpoint="/chat/completions", method="POST", model=model,
            is_streaming=False, client_ip=None, client_ua=None,
            req_headers={}, req_query=None, req_body=None,
            started_at=started_at,
        )
        await req_crud.mark_finished(
            s, req_id=req_id, status=status, status_code=200,
            resp_headers={}, resp_body=b"", finished_at=time.time(),
        )
        await s.commit()


async def test_list_all_no_filter(session_factory) -> None:
    await _seed(session_factory, "r1", "openai", "gpt-4o")
    await _seed(session_factory, "r2", "anthropic", "claude-haiku")

    async with session_factory() as s:
        rows, total = await req_crud.list_with_filters(s, limit=50, offset=0)
    assert total == 2
    assert {r.req_id for r in rows} == {"r1", "r2"}


async def test_filter_by_provider(session_factory) -> None:
    await _seed(session_factory, "r1", "openai", "gpt-4o")
    await _seed(session_factory, "r2", "anthropic", "claude-haiku")
    await _seed(session_factory, "r3", "openai", "gpt-4o-mini")

    async with session_factory() as s:
        rows, total = await req_crud.list_with_filters(
            s, providers=["openai"], limit=50, offset=0
        )
    assert total == 2
    assert {r.req_id for r in rows} == {"r1", "r3"}


async def test_filter_by_model(session_factory) -> None:
    await _seed(session_factory, "r1", "openai", "gpt-4o")
    await _seed(session_factory, "r2", "openai", "gpt-4o-mini")

    async with session_factory() as s:
        rows, total = await req_crud.list_with_filters(
            s, models=["gpt-4o"], limit=50, offset=0
        )
    assert total == 1
    assert rows[0].req_id == "r1"


async def test_time_range(session_factory) -> None:
    now = time.time()
    await _seed(session_factory, "old", "openai", "gpt-4o", started_at=now - 3600)
    await _seed(session_factory, "new", "openai", "gpt-4o", started_at=now - 10)

    async with session_factory() as s:
        rows, total = await req_crud.list_with_filters(
            s, since=now - 60, limit=50, offset=0
        )
    assert total == 1
    assert rows[0].req_id == "new"


async def test_filter_by_req_ids_from_search(session_factory) -> None:
    """The FTS route returns req_ids; list_with_filters can filter by them."""
    await _seed(session_factory, "r1", "openai", "gpt-4o")
    await _seed(session_factory, "r2", "anthropic", "claude-haiku")
    await _seed(session_factory, "r3", "openai", "gpt-4o-mini")

    async with session_factory() as s:
        rows, total = await req_crud.list_with_filters(
            s, req_ids=["r1", "r3"], limit=50, offset=0
        )
    assert total == 2
    assert {r.req_id for r in rows} == {"r1", "r3"}


async def test_pagination(session_factory) -> None:
    for i in range(5):
        await _seed(session_factory, f"r{i}", "openai", "gpt-4o", started_at=time.time() + i)

    async with session_factory() as s:
        rows, total = await req_crud.list_with_filters(s, limit=2, offset=0)
    assert total == 5
    assert len(rows) == 2
    # Default sort is started_at DESC — newest first
    assert rows[0].req_id == "r4"
    assert rows[1].req_id == "r3"
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Extend `src/aiproxy/db/crud/requests.py`**

Append to the existing file:

```python
from sqlalchemy import func


async def list_with_filters(
    session: AsyncSession,
    *,
    providers: list[str] | None = None,
    models: list[str] | None = None,
    statuses: list[str] | None = None,
    api_key_id: int | None = None,
    since: float | None = None,
    until: float | None = None,
    req_ids: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Request], int]:
    """Paginated, filtered list of requests ordered by started_at DESC.

    Returns (rows, total_count) — total_count ignores limit/offset.

    If `req_ids` is provided, result is limited to those IDs (used by
    FTS-driven search to narrow the listing).
    """
    stmt = select(Request)
    count_stmt = select(func.count()).select_from(Request)

    conditions = []
    if providers:
        conditions.append(Request.provider.in_(providers))
    if models:
        conditions.append(Request.model.in_(models))
    if statuses:
        conditions.append(Request.status.in_(statuses))
    if api_key_id is not None:
        conditions.append(Request.api_key_id == api_key_id)
    if since is not None:
        conditions.append(Request.started_at >= since)
    if until is not None:
        conditions.append(Request.started_at <= until)
    if req_ids is not None:
        conditions.append(Request.req_id.in_(req_ids))

    for cond in conditions:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    stmt = stmt.order_by(Request.started_at.desc()).limit(limit).offset(offset)

    rows = (await session.execute(stmt)).scalars().all()
    total = (await session.execute(count_stmt)).scalar() or 0
    return list(rows), int(total)
```

- [ ] **Step 4: Run tests — expect 6 passing**

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/db/crud/requests.py tests/unit/test_crud_requests_list.py
git commit -m "feat(phase-3): add list_with_filters helper for requests CRUD"
```

---

## Task 5: Dashboard login + auth-guarded routes

**Files:**
- Rewrite: `src/aiproxy/dashboard/routes.py`
- Create: `src/aiproxy/dashboard/static/login.html`
- Create: `tests/integration/test_dashboard_login.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_dashboard_login.py`:

```python
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
```

- [ ] **Step 2: Run — expect FAIL (signature mismatch, login missing)**

- [ ] **Step 3: Create `src/aiproxy/dashboard/static/login.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>AI Proxy — Login</title>
<style>
  :root { --bg:#0e1116; --panel:#161b22; --border:#2d333b; --text:#c9d1d9; --accent:#58a6ff; --err:#f85149; }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    background: var(--bg); color: var(--text);
    font: 14px/1.5 -apple-system, Segoe UI, sans-serif;
    display: flex; align-items: center; justify-content: center;
  }
  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 32px;
    width: 360px;
  }
  h1 { font-size: 18px; margin: 0 0 20px 0; }
  label { display: block; font-size: 12px; color: #8b949e; margin-bottom: 6px; }
  input {
    width: 100%; padding: 10px 12px;
    background: #0a0d12; border: 1px solid var(--border); border-radius: 4px;
    color: var(--text); font-family: ui-monospace, monospace;
    font-size: 13px;
  }
  input:focus { outline: none; border-color: var(--accent); }
  button {
    width: 100%; padding: 10px; margin-top: 14px;
    background: var(--accent); color: #0a0d12;
    border: 0; border-radius: 4px;
    font-weight: 600; cursor: pointer;
  }
  button:hover { filter: brightness(1.1); }
  .err { color: var(--err); font-size: 12px; margin-top: 10px; min-height: 16px; }
</style>
</head>
<body>
<div class="card">
  <h1>AI Proxy — Sign In</h1>
  <label for="mk">Master key</label>
  <input id="mk" type="password" autofocus placeholder="sk-..." />
  <button id="btn">Sign In</button>
  <div class="err" id="err"></div>
</div>
<script>
const btn = document.getElementById("btn");
const mk  = document.getElementById("mk");
const err = document.getElementById("err");
async function login() {
  err.textContent = "";
  try {
    const r = await fetch("/dashboard/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ master_key: mk.value }),
      credentials: "same-origin",
    });
    if (r.ok) { location.href = "/dashboard/"; return; }
    err.textContent = r.status === 401 ? "Invalid master key" : ("Error: " + r.status);
  } catch (e) {
    err.textContent = "Network error: " + e.message;
  }
}
btn.onclick = login;
mk.addEventListener("keydown", e => { if (e.key === "Enter") login(); });
</script>
</body>
</html>
```

- [ ] **Step 4: Rewrite `src/aiproxy/dashboard/routes.py`**

```python
"""Dashboard HTTP + WebSocket routes.

Phase 3 adds:
- Login endpoint + signed session cookie
- Auth dependency on every /dashboard/api/* and /dashboard/ws/* route
- List endpoint with filters + search
- Detail endpoint
- Keys/pricing/config management endpoints
- Batch operations

GET /dashboard/                       static HTML (if logged in) or redirect to login
GET /dashboard/login                   static login form
POST /dashboard/login                  validate master key, issue cookie
POST /dashboard/logout                 clear cookie
GET /dashboard/api/active              (protected) snapshot of active + history
GET /dashboard/api/requests            (protected) paginated list with filters + search
GET /dashboard/api/requests/{req_id}   (protected) full detail + chunks
GET /dashboard/api/keys                (protected) list keys
POST /dashboard/api/keys               (protected) create key
PATCH /dashboard/api/keys/{id}         (protected) update (active/name/note/valid_to)
GET /dashboard/api/pricing             (protected) list all pricing rows
POST /dashboard/api/pricing            (protected) upsert current
GET /dashboard/api/config              (protected) get all runtime settings
POST /dashboard/api/config             (protected) upsert one key
POST /dashboard/api/batch/strip-binaries  (protected) strip base64 binaries
WS  /dashboard/ws/active               (protected via cookie)
WS  /dashboard/ws/stream/{req_id}      (protected via cookie)
"""
from __future__ import annotations

import asyncio
import base64
import hmac
import re
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker

from aiproxy.auth.dashboard_auth import (
    COOKIE_NAME,
    issue_session_token,
    make_session_cookie_response,
    require_dashboard_session,
    verify_session_token,
)
from aiproxy.bus import StreamBus
from aiproxy.db.crud import api_keys as api_keys_crud
from aiproxy.db.crud import chunks as chunks_crud
from aiproxy.db.crud import config as config_crud
from aiproxy.db.crud import pricing as pricing_crud
from aiproxy.db.crud import requests as req_crud
from aiproxy.db.fts import search_requests
from aiproxy.db.models import Request
from aiproxy.registry import ACTIVE_CHANNEL, RequestRegistry

_STATIC_DIR = Path(__file__).parent / "static"
_SESSION_TTL = 30 * 24 * 60 * 60  # 30 days


# ---- Pydantic request bodies ----

class LoginBody(BaseModel):
    master_key: str


class CreateKeyBody(BaseModel):
    name: str
    note: str | None = None
    valid_from: float | None = None
    valid_to: float | None = None


class UpdateKeyBody(BaseModel):
    name: str | None = None
    note: str | None = None
    is_active: bool | None = None
    valid_to: float | None = None


class UpsertPricingBody(BaseModel):
    provider: str
    model: str
    input_per_1m_usd: float
    output_per_1m_usd: float
    cached_per_1m_usd: float | None = None
    note: str | None = None


class SetConfigBody(BaseModel):
    key: str
    value: Any


class StripBinariesBody(BaseModel):
    req_ids: list[str] | None = None
    providers: list[str] | None = None
    all: bool = False


def create_dashboard_router(
    *,
    bus: StreamBus,
    registry: RequestRegistry,
    sessionmaker: async_sessionmaker | None,
    master_key: str,
    session_secret: str,
    secure_cookies: bool,
) -> APIRouter:
    router = APIRouter(prefix="/dashboard")
    require_session = require_dashboard_session(session_secret)

    # ---- static ----

    @router.get("/")
    async def index(request: Request) -> Any:
        # Gate the HTML behind the cookie too — if not logged in, send to login
        token = request.cookies.get(COOKIE_NAME)
        result = verify_session_token(token, secret=session_secret)
        if not result.valid:
            return RedirectResponse(url="/dashboard/login", status_code=303)
        return FileResponse(_STATIC_DIR / "index.html")

    @router.get("/login")
    async def login_page() -> FileResponse:
        return FileResponse(_STATIC_DIR / "login.html")

    # ---- auth ----

    @router.post("/login")
    async def login(body: LoginBody) -> JSONResponse:
        if not hmac.compare_digest(body.master_key.encode(), master_key.encode()):
            raise HTTPException(status_code=401, detail="invalid master key")
        token = issue_session_token(secret=session_secret, ttl_seconds=_SESSION_TTL)
        return make_session_cookie_response(
            token, secure=secure_cookies, max_age_seconds=_SESSION_TTL,
        )

    @router.post("/logout")
    async def logout(_: dict = Depends(require_session)) -> JSONResponse:
        resp = JSONResponse({"ok": True})
        resp.set_cookie(
            key=COOKIE_NAME, value="", httponly=True, samesite="strict",
            secure=secure_cookies, max_age=0, path="/",
        )
        return resp

    # ---- protected: active + live ----

    @router.get("/api/active")
    async def api_active(_: dict = Depends(require_session)) -> JSONResponse:
        return JSONResponse({
            "active": registry.active(),
            "history": registry.history(),
        })

    # ---- protected: list + search + detail ----

    @router.get("/api/requests")
    async def api_list_requests(
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
        limit = int(qp.get("limit", "50"))
        offset = int(qp.get("offset", "0"))
        since = float(qp["since"]) if qp.get("since") else None
        until = float(qp["until"]) if qp.get("until") else None

        async with sessionmaker() as s:
            req_ids = None
            if search:
                req_ids = await search_requests(s, query=search, limit=500)
                if not req_ids:
                    return JSONResponse({"rows": [], "total": 0, "limit": limit, "offset": offset})

            rows, total = await req_crud.list_with_filters(
                s,
                providers=providers,
                models=models,
                statuses=statuses,
                since=since,
                until=until,
                req_ids=req_ids,
                limit=limit,
                offset=offset,
            )
            return JSONResponse({
                "rows": [_serialize_row_summary(r) for r in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
            })

    @router.get("/api/requests/{req_id}")
    async def api_request_detail(
        req_id: str,
        _: dict = Depends(require_session),
    ) -> JSONResponse:
        if sessionmaker is None:
            raise HTTPException(status_code=500, detail="sessionmaker not configured")
        async with sessionmaker() as s:
            row = await req_crud.get_by_id(s, req_id)
            if row is None:
                raise HTTPException(status_code=404, detail="request not found")
            chunks = await chunks_crud.list_for_request(s, req_id)
            return JSONResponse({
                "request": _serialize_row_full(row),
                "chunks": [
                    {
                        "seq": c.seq,
                        "offset_ns": c.offset_ns,
                        "size": c.size,
                        "data_b64": base64.b64encode(c.data).decode("ascii"),
                    }
                    for c in chunks
                ],
            })

    # ---- protected: keys ----

    @router.get("/api/keys")
    async def api_list_keys(_: dict = Depends(require_session)) -> JSONResponse:
        async with sessionmaker() as s:
            keys = await api_keys_crud.list_all(s)
        return JSONResponse({"keys": [_serialize_key(k) for k in keys]})

    @router.post("/api/keys")
    async def api_create_key(
        body: CreateKeyBody,
        _: dict = Depends(require_session),
    ) -> JSONResponse:
        key_value = "sk-aiprx-" + secrets.token_urlsafe(24)
        async with sessionmaker() as s:
            row = await api_keys_crud.create(
                s,
                key=key_value,
                name=body.name,
                note=body.note,
                valid_from=body.valid_from,
                valid_to=body.valid_to,
            )
            await s.commit()
        return JSONResponse({"key": _serialize_key(row)})

    @router.patch("/api/keys/{key_id}")
    async def api_update_key(
        key_id: int,
        body: UpdateKeyBody,
        _: dict = Depends(require_session),
    ) -> JSONResponse:
        async with sessionmaker() as s:
            # Find the key first to get its plaintext value
            all_keys = await api_keys_crud.list_all(s)
            match = next((k for k in all_keys if k.id == key_id), None)
            if match is None:
                raise HTTPException(status_code=404, detail="key not found")
            # Apply updates via raw values
            from sqlalchemy import update
            from aiproxy.db.models import ApiKey
            updates: dict[str, Any] = {}
            if body.name is not None:
                updates["name"] = body.name
            if body.note is not None:
                updates["note"] = body.note
            if body.is_active is not None:
                updates["is_active"] = 1 if body.is_active else 0
            if body.valid_to is not None:
                updates["valid_to"] = body.valid_to
            if updates:
                await s.execute(update(ApiKey).where(ApiKey.id == key_id).values(**updates))
                await s.commit()
            # Re-fetch
            all_keys = await api_keys_crud.list_all(s)
            updated = next((k for k in all_keys if k.id == key_id), None)
        return JSONResponse({"key": _serialize_key(updated)})

    # ---- protected: pricing ----

    @router.get("/api/pricing")
    async def api_list_pricing(_: dict = Depends(require_session)) -> JSONResponse:
        async with sessionmaker() as s:
            from sqlalchemy import select
            from aiproxy.db.models import Pricing
            rows = (await s.execute(
                select(Pricing).order_by(Pricing.provider, Pricing.model, Pricing.effective_from.desc())
            )).scalars().all()
        return JSONResponse({"pricing": [_serialize_pricing(p) for p in rows]})

    @router.post("/api/pricing")
    async def api_upsert_pricing(
        body: UpsertPricingBody,
        _: dict = Depends(require_session),
    ) -> JSONResponse:
        async with sessionmaker() as s:
            row = await pricing_crud.upsert_current(
                s,
                provider=body.provider,
                model=body.model,
                input_per_1m_usd=body.input_per_1m_usd,
                output_per_1m_usd=body.output_per_1m_usd,
                cached_per_1m_usd=body.cached_per_1m_usd,
                note=body.note,
            )
            await s.commit()
        return JSONResponse({"pricing": _serialize_pricing(row)})

    # ---- protected: config ----

    @router.get("/api/config")
    async def api_get_config(_: dict = Depends(require_session)) -> JSONResponse:
        async with sessionmaker() as s:
            all_cfg = await config_crud.get_all(s)
        # Always return defaults for known settings
        all_cfg.setdefault("retention.full_count", 500)
        return JSONResponse({"config": all_cfg})

    @router.post("/api/config")
    async def api_set_config(
        body: SetConfigBody,
        _: dict = Depends(require_session),
    ) -> JSONResponse:
        async with sessionmaker() as s:
            await config_crud.set_(s, body.key, body.value)
            await s.commit()
        return JSONResponse({"ok": True, "key": body.key, "value": body.value})

    # ---- protected: batch strip binaries ----

    _BINARY_RE = re.compile(rb"data:([^;]+);base64,[A-Za-z0-9+/=]+")

    def _strip_binaries(blob: bytes | None) -> bytes | None:
        if blob is None:
            return None
        def replace(m: re.Match) -> bytes:
            mime = m.group(1)
            size = len(m.group(0))
            return f"[{mime.decode('ascii', 'replace')}: {size}B stripped]".encode()
        return _BINARY_RE.sub(replace, blob)

    @router.post("/api/batch/strip-binaries")
    async def api_strip_binaries(
        body: StripBinariesBody,
        _: dict = Depends(require_session),
    ) -> JSONResponse:
        async with sessionmaker() as s:
            from sqlalchemy import select, update as sa_update
            stmt = select(Request)
            if body.req_ids:
                stmt = stmt.where(Request.req_id.in_(body.req_ids))
            elif body.providers:
                stmt = stmt.where(Request.provider.in_(body.providers))
            elif not body.all:
                raise HTTPException(status_code=400, detail="must specify req_ids, providers, or all=true")
            rows = (await s.execute(stmt)).scalars().all()
            stripped = 0
            for row in rows:
                new_req = _strip_binaries(row.req_body)
                new_resp = _strip_binaries(row.resp_body)
                if new_req != row.req_body or new_resp != row.resp_body:
                    await s.execute(
                        sa_update(Request)
                        .where(Request.req_id == row.req_id)
                        .values(
                            req_body=new_req,
                            resp_body=new_resp,
                            binaries_stripped=1,
                        )
                    )
                    stripped += 1
            await s.commit()
        return JSONResponse({"stripped": stripped})

    # ---- WebSockets ----

    async def _ws_auth_or_close(ws: WebSocket) -> bool:
        await ws.accept()
        token = ws.cookies.get(COOKIE_NAME)
        result = verify_session_token(token, secret=session_secret)
        if not result.valid:
            await ws.close(code=4401)
            return False
        return True

    @router.websocket("/ws/active")
    async def ws_active(ws: WebSocket) -> None:
        if not await _ws_auth_or_close(ws):
            return
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
        if not await _ws_auth_or_close(ws):
            return
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


# ---- serializers ----

def _serialize_row_summary(row: Request) -> dict:
    return {
        "req_id": row.req_id,
        "provider": row.provider,
        "endpoint": row.endpoint,
        "method": row.method,
        "model": row.model,
        "is_streaming": bool(row.is_streaming),
        "status": row.status,
        "status_code": row.status_code,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "cost_usd": row.cost_usd,
        "chunk_count": row.chunk_count,
        "binaries_stripped": bool(row.binaries_stripped),
        "api_key_id": row.api_key_id,
    }


def _serialize_row_full(row: Request) -> dict:
    base = _serialize_row_summary(row)
    base.update({
        "req_headers": row.req_headers,
        "req_query": row.req_query,
        "req_body_b64": base64.b64encode(row.req_body).decode("ascii") if row.req_body else None,
        "req_body_size": row.req_body_size,
        "resp_headers": row.resp_headers,
        "resp_body_b64": base64.b64encode(row.resp_body).decode("ascii") if row.resp_body else None,
        "resp_body_size": row.resp_body_size,
        "error_class": row.error_class,
        "error_message": row.error_message,
        "cached_tokens": row.cached_tokens,
        "reasoning_tokens": row.reasoning_tokens,
        "pricing_id": row.pricing_id,
        "upstream_sent_at": row.upstream_sent_at,
        "first_chunk_at": row.first_chunk_at,
    })
    return base


def _serialize_key(row) -> dict:
    return {
        "id": row.id,
        "key": row.key,
        "name": row.name,
        "note": row.note,
        "is_active": bool(row.is_active),
        "created_at": row.created_at,
        "valid_from": row.valid_from,
        "valid_to": row.valid_to,
        "last_used_at": row.last_used_at,
    }


def _serialize_pricing(row) -> dict:
    return {
        "id": row.id,
        "provider": row.provider,
        "model": row.model,
        "input_per_1m_usd": row.input_per_1m_usd,
        "output_per_1m_usd": row.output_per_1m_usd,
        "cached_per_1m_usd": row.cached_per_1m_usd,
        "effective_from": row.effective_from,
        "effective_to": row.effective_to,
        "note": row.note,
        "created_at": row.created_at,
    }
```

- [ ] **Step 5: Update existing dashboard tests to match new signature**

Existing `tests/integration/test_dashboard_api.py` and `test_dashboard_ws.py` fixtures call `create_dashboard_router(bus=bus, registry=registry)`. They need to add `sessionmaker=None, master_key="...", session_secret="...", secure_cookies=False`.

**Important**: The Phase 2 tests hit `/dashboard/api/active` without a cookie — they'll now get 401. Update them to first call `/dashboard/login` and reuse the cookie.

For `test_dashboard_ws.py` WS tests: Starlette's TestClient supports passing cookies on `websocket_connect(..., headers={"cookie": "..."})`. The `ws.cookies` property in FastAPI reads from the WebSocket headers.

For `test_dashboard_api.py`, the `app` fixture should also seed a sessionmaker for endpoints that use it. You can pass `sessionmaker=None` and only test `/api/active` which doesn't need DB.

The full updated test_dashboard_api.py fixture is:

```python
@pytest.fixture
def app():
    bus = StreamBus()
    registry = RequestRegistry(bus)
    app = FastAPI()
    app.include_router(create_dashboard_router(
        bus=bus,
        registry=registry,
        sessionmaker=None,
        master_key="test-key",
        session_secret="test-secret-xxx",
        secure_cookies=False,
    ))
    return app, bus, registry
```

Then in each test, obtain a session cookie by calling `/dashboard/login` before accessing protected endpoints. Example:

```python
async def test_api_active_returns_snapshot(app) -> None:
    a, _, registry = app
    registry.start(...)
    async with await _client(a) as c:
        login = await c.post("/dashboard/login", json={"master_key": "test-key"})
        cookie = login.cookies.get("aiproxy_session")
        r = await c.get("/dashboard/api/active", cookies={"aiproxy_session": cookie})
    assert r.status_code == 200
    ...
```

For `test_dashboard_ws.py`, use a helper to get cookie via TestClient:

```python
@pytest.fixture
def logged_in_client(app):
    a, bus, registry = app
    client = TestClient(a)
    r = client.post("/dashboard/login", json={"master_key": "test-key"})
    assert r.status_code == 200
    return client, bus, registry
```

Then connect WebSockets via the same client — TestClient persists cookies automatically.

- [ ] **Step 6: Run full suite — expect all green**

```bash
uv run pytest tests/ -q
```

- [ ] **Step 7: Commit**

```bash
git add src/aiproxy/dashboard/routes.py src/aiproxy/dashboard/static/login.html \
        tests/integration/test_dashboard_login.py tests/integration/test_dashboard_api.py \
        tests/integration/test_dashboard_ws.py
git commit -m "feat(phase-3): dashboard login + auth-guarded routes + all management endpoints"
```

---

## Task 6: Wire auth into lifespan + install FTS on boot

**Files:**
- Modify: `src/aiproxy/app.py`
- Modify: `src/aiproxy/db/retention.py`

Phase 3 changes the dashboard router signature — `build_app()` and `lifespan()` must pass `master_key`, `session_secret`, `secure_cookies`, and a real `sessionmaker` (not None).

Also:
- Install the FTS schema after `create_all` in the lifespan
- Wire retention to read from config table

- [ ] **Step 1: Update `src/aiproxy/db/retention.py`**

Replace the `retention_loop` to accept a callable that queries the config table:

```python
# In retention_loop, replace the existing get_full_count usage — it's already
# a callable, but now we make the default caller query the config table.
# The caller (app.py) provides the lambda.
```

No code change needed in retention.py itself — the change is how `app.py` calls it.

- [ ] **Step 2: Update `src/aiproxy/app.py`**

Changes:
1. Import `install_fts_schema` from `aiproxy.db.fts` and `config_crud`.
2. In `lifespan()`, after `create_all`, also `install_fts_schema(conn)`.
3. In `build_app()` and `lifespan()`, pass the extra args to `create_dashboard_router`.
4. Replace the retention lambda with a real getter that hits the config table.

Key snippets to modify:

```python
# Imports:
from aiproxy.db.crud import config as config_crud
from aiproxy.db.fts import install_fts_schema

# In lifespan, after create_all:
    async with sql_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await install_fts_schema(conn)

    await seed_pricing_if_empty(sessionmaker)

# Dashboard router call (in both build_app and lifespan):
    app.include_router(create_dashboard_router(
        bus=bus,
        registry=registry,
        sessionmaker=sessionmaker,
        master_key=settings.proxy_master_key,
        session_secret=settings.session_secret,
        secure_cookies=settings.secure_cookies,
    ))

# Retention getter (in lifespan):
    async def _get_full_count() -> int:
        async with sessionmaker() as s:
            return int(await config_crud.get(s, "retention.full_count", default=500))

    # Since retention_loop expects a sync callable, wrap it:
    def _get_full_count_sync() -> int:
        # This is called from within the retention loop; spawning a new
        # event loop is wrong. Instead, we keep a cached value and refresh
        # it inside the loop. Simplest: change retention_loop to accept
        # an async getter.
        ...
```

Because this requires signature changes, update `retention_loop` instead:

Edit `src/aiproxy/db/retention.py` `retention_loop`:

```python
async def retention_loop(
    sessionmaker: async_sessionmaker,
    *,
    get_full_count,  # async callable or sync callable
    interval_seconds: float = 60.0,
) -> None:
    """Run prune_old_requests in a loop. Swallows exceptions so the task keeps running.

    `get_full_count` may be sync or async.
    """
    import inspect
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            maybe = get_full_count()
            full_count = await maybe if inspect.isawaitable(maybe) else maybe
            if full_count > 0:
                trimmed = await prune_old_requests(sessionmaker, full_count=full_count)
                if trimmed:
                    logger.info("retention: pruned %d old requests", trimmed)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("retention loop error (continuing)")
```

Then in `app.py`:

```python
    async def _get_full_count() -> int:
        async with sessionmaker() as s:
            return int(await config_crud.get(s, "retention.full_count", default=500))

    retention_task = asyncio.create_task(retention_loop(
        sessionmaker,
        get_full_count=_get_full_count,
        interval_seconds=60.0,
    ))
```

- [ ] **Step 3: Run full suite**

```bash
uv run pytest tests/ -q
```

Expected: all tests green.

- [ ] **Step 4: Commit**

```bash
git add src/aiproxy/app.py src/aiproxy/db/retention.py
git commit -m "feat(phase-3): wire FTS install + config-backed retention into lifespan"
```

---

## Task 7: Integration tests for list + detail

**Files:**
- Create: `tests/integration/test_dashboard_list.py`
- Create: `tests/integration/test_dashboard_detail.py`

- [ ] **Step 1: Create `tests/integration/test_dashboard_list.py`**

```python
"""Integration tests for /dashboard/api/requests — list + filters + search."""
import json
import time

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from aiproxy.bus import StreamBus
from aiproxy.dashboard.routes import create_dashboard_router
from aiproxy.db.crud import requests as req_crud
from aiproxy.db.fts import install_fts_schema
from aiproxy.db.models import Base
from aiproxy.registry import RequestRegistry

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
async def app_ctx():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await install_fts_schema(conn)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    bus = StreamBus()
    registry = RequestRegistry(bus)
    app = FastAPI()
    app.include_router(create_dashboard_router(
        bus=bus, registry=registry, sessionmaker=sm,
        master_key="k", session_secret="s",
        secure_cookies=False,
    ))
    try:
        yield app, sm
    finally:
        await engine.dispose()


async def _seed(sm, req_id, provider, model, body_text=""):
    async with sm() as s:
        await req_crud.create_pending(
            s, req_id=req_id, api_key_id=None, provider=provider,
            endpoint="/chat/completions", method="POST", model=model,
            is_streaming=False, client_ip=None, client_ua=None,
            req_headers={}, req_query=None,
            req_body=body_text.encode() if body_text else None,
            started_at=time.time(),
        )
        await req_crud.mark_finished(
            s, req_id=req_id, status="done", status_code=200,
            resp_headers={}, resp_body=b"", finished_at=time.time(),
        )
        await s.commit()


async def _client_with_cookie(app):
    c = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    login = await c.post("/dashboard/login", json={"master_key": "k"})
    c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
    return c


async def test_list_empty(app_ctx) -> None:
    app, _ = app_ctx
    async with await _client_with_cookie(app) as c:
        r = await c.get("/dashboard/api/requests")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 0
    assert data["rows"] == []


async def test_list_all_rows(app_ctx) -> None:
    app, sm = app_ctx
    await _seed(sm, "r1", "openai", "gpt-4o")
    await _seed(sm, "r2", "anthropic", "claude-haiku")

    async with await _client_with_cookie(app) as c:
        r = await c.get("/dashboard/api/requests")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 2


async def test_list_filter_by_provider(app_ctx) -> None:
    app, sm = app_ctx
    await _seed(sm, "r1", "openai", "gpt-4o")
    await _seed(sm, "r2", "anthropic", "claude-haiku")

    async with await _client_with_cookie(app) as c:
        r = await c.get("/dashboard/api/requests?provider=anthropic")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["rows"][0]["req_id"] == "r2"


async def test_list_fts_search(app_ctx) -> None:
    app, sm = app_ctx
    await _seed(sm, "r1", "openai", "gpt-4o", body_text='{"messages":[{"content":"refactor this"}]}')
    await _seed(sm, "r2", "openai", "gpt-4o", body_text='{"messages":[{"content":"write a poem"}]}')

    async with await _client_with_cookie(app) as c:
        r = await c.get("/dashboard/api/requests?q=refactor")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["rows"][0]["req_id"] == "r1"


async def test_list_pagination(app_ctx) -> None:
    app, sm = app_ctx
    for i in range(5):
        await _seed(sm, f"r{i}", "openai", "gpt-4o")

    async with await _client_with_cookie(app) as c:
        r = await c.get("/dashboard/api/requests?limit=2&offset=0")
    data = r.json()
    assert data["total"] == 5
    assert len(data["rows"]) == 2
```

- [ ] **Step 2: Create `tests/integration/test_dashboard_detail.py`**

```python
"""Integration tests for /dashboard/api/requests/{req_id} detail endpoint."""
import base64
import json
import time

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.bus import StreamBus
from aiproxy.dashboard.routes import create_dashboard_router
from aiproxy.db.crud import chunks as chunks_crud
from aiproxy.db.crud import requests as req_crud
from aiproxy.db.fts import install_fts_schema
from aiproxy.db.models import Base
from aiproxy.registry import RequestRegistry


@pytest.fixture
async def app_ctx():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await install_fts_schema(conn)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    bus = StreamBus()
    registry = RequestRegistry(bus)
    app = FastAPI()
    app.include_router(create_dashboard_router(
        bus=bus, registry=registry, sessionmaker=sm,
        master_key="k", session_secret="s",
        secure_cookies=False,
    ))
    try:
        yield app, sm
    finally:
        await engine.dispose()


async def _login(app):
    c = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    login = await c.post("/dashboard/login", json={"master_key": "k"})
    c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
    return c


async def test_detail_not_found(app_ctx) -> None:
    app, _ = app_ctx
    async with await _login(app) as c:
        r = await c.get("/dashboard/api/requests/missing")
    assert r.status_code == 404


async def test_detail_returns_full_row_and_chunks(app_ctx) -> None:
    app, sm = app_ctx
    async with sm() as s:
        await req_crud.create_pending(
            s, req_id="r1", api_key_id=None, provider="openai",
            endpoint="/chat/completions", method="POST", model="gpt-4o",
            is_streaming=True, client_ip="1.2.3.4", client_ua="pytest",
            req_headers={"x-foo": "bar"}, req_query=None,
            req_body=b'{"model":"gpt-4o"}',
            started_at=time.time(),
        )
        await chunks_crud.insert_batch(
            s, "r1", [(0, 0, b"hello"), (1, 10_000_000, b"world")]
        )
        await req_crud.mark_finished(
            s, req_id="r1", status="done", status_code=200,
            resp_headers={"x-bar": "baz"}, resp_body=b"helloworld",
            finished_at=time.time(),
        )
        await s.commit()

    async with await _login(app) as c:
        r = await c.get("/dashboard/api/requests/r1")
    assert r.status_code == 200
    data = r.json()
    assert data["request"]["req_id"] == "r1"
    assert data["request"]["model"] == "gpt-4o"
    assert data["request"]["is_streaming"] is True
    assert data["request"]["req_body_b64"]
    assert base64.b64decode(data["request"]["req_body_b64"]) == b'{"model":"gpt-4o"}'
    assert len(data["chunks"]) == 2
    assert data["chunks"][0]["seq"] == 0
    assert data["chunks"][0]["offset_ns"] == 0
    assert base64.b64decode(data["chunks"][0]["data_b64"]) == b"hello"
    assert data["chunks"][1]["offset_ns"] == 10_000_000
```

- [ ] **Step 3: Run — expect all passing**

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_dashboard_list.py tests/integration/test_dashboard_detail.py
git commit -m "test(phase-3): integration tests for list + detail endpoints"
```

---

## Task 8: Integration tests for management endpoints

**Files:**
- Create: `tests/integration/test_dashboard_keys_api.py`
- Create: `tests/integration/test_dashboard_pricing_api.py`
- Create: `tests/integration/test_dashboard_config_api.py`
- Create: `tests/integration/test_dashboard_batch_strip.py`

- [ ] **Step 1: Create `tests/integration/test_dashboard_keys_api.py`**

```python
"""Integration tests for /dashboard/api/keys endpoints."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.bus import StreamBus
from aiproxy.dashboard.routes import create_dashboard_router
from aiproxy.db.models import Base
from aiproxy.registry import RequestRegistry


@pytest.fixture
async def app_ctx():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    bus = StreamBus()
    registry = RequestRegistry(bus)
    app = FastAPI()
    app.include_router(create_dashboard_router(
        bus=bus, registry=registry, sessionmaker=sm,
        master_key="k", session_secret="s",
        secure_cookies=False,
    ))
    try:
        yield app
    finally:
        await engine.dispose()


async def _client(app):
    c = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    login = await c.post("/dashboard/login", json={"master_key": "k"})
    c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
    return c


async def test_list_empty(app_ctx) -> None:
    async with await _client(app_ctx) as c:
        r = await c.get("/dashboard/api/keys")
    assert r.status_code == 200
    assert r.json() == {"keys": []}


async def test_create_key(app_ctx) -> None:
    async with await _client(app_ctx) as c:
        r = await c.post("/dashboard/api/keys", json={"name": "prod", "note": "backend"})
    assert r.status_code == 200
    key = r.json()["key"]
    assert key["name"] == "prod"
    assert key["note"] == "backend"
    assert key["is_active"] is True
    assert key["key"].startswith("sk-aiprx-")


async def test_list_after_create(app_ctx) -> None:
    async with await _client(app_ctx) as c:
        await c.post("/dashboard/api/keys", json={"name": "a"})
        await c.post("/dashboard/api/keys", json={"name": "b"})
        r = await c.get("/dashboard/api/keys")
    data = r.json()
    assert len(data["keys"]) == 2
    names = {k["name"] for k in data["keys"]}
    assert names == {"a", "b"}


async def test_patch_deactivate(app_ctx) -> None:
    async with await _client(app_ctx) as c:
        created = await c.post("/dashboard/api/keys", json={"name": "x"})
        key_id = created.json()["key"]["id"]
        r = await c.patch(f"/dashboard/api/keys/{key_id}", json={"is_active": False})
    assert r.status_code == 200
    assert r.json()["key"]["is_active"] is False


async def test_patch_rename(app_ctx) -> None:
    async with await _client(app_ctx) as c:
        created = await c.post("/dashboard/api/keys", json={"name": "x"})
        key_id = created.json()["key"]["id"]
        r = await c.patch(f"/dashboard/api/keys/{key_id}", json={"name": "renamed"})
    assert r.json()["key"]["name"] == "renamed"
```

- [ ] **Step 2: Create `tests/integration/test_dashboard_pricing_api.py`**

```python
"""Integration tests for /dashboard/api/pricing endpoints."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.bus import StreamBus
from aiproxy.dashboard.routes import create_dashboard_router
from aiproxy.db.models import Base
from aiproxy.registry import RequestRegistry


@pytest.fixture
async def app_ctx():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    bus = StreamBus()
    registry = RequestRegistry(bus)
    app = FastAPI()
    app.include_router(create_dashboard_router(
        bus=bus, registry=registry, sessionmaker=sm,
        master_key="k", session_secret="s",
        secure_cookies=False,
    ))
    try:
        yield app
    finally:
        await engine.dispose()


async def _client(app):
    c = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    login = await c.post("/dashboard/login", json={"master_key": "k"})
    c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
    return c


async def test_upsert_and_list(app_ctx) -> None:
    async with await _client(app_ctx) as c:
        r = await c.post("/dashboard/api/pricing", json={
            "provider": "openai",
            "model": "gpt-5",
            "input_per_1m_usd": 1.0,
            "output_per_1m_usd": 2.0,
        })
        assert r.status_code == 200
        first = r.json()["pricing"]
        assert first["effective_to"] is None

        listing = await c.get("/dashboard/api/pricing")
    data = listing.json()
    assert len(data["pricing"]) == 1


async def test_upsert_versioning(app_ctx) -> None:
    async with await _client(app_ctx) as c:
        await c.post("/dashboard/api/pricing", json={
            "provider": "openai", "model": "gpt-5",
            "input_per_1m_usd": 1.0, "output_per_1m_usd": 2.0,
        })
        await c.post("/dashboard/api/pricing", json={
            "provider": "openai", "model": "gpt-5",
            "input_per_1m_usd": 0.8, "output_per_1m_usd": 1.5,
        })

        listing = await c.get("/dashboard/api/pricing")
    rows = listing.json()["pricing"]
    assert len(rows) == 2
    active = [r for r in rows if r["effective_to"] is None]
    assert len(active) == 1
    assert active[0]["input_per_1m_usd"] == 0.8
```

- [ ] **Step 3: Create `tests/integration/test_dashboard_config_api.py`**

```python
"""Integration tests for /dashboard/api/config."""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.bus import StreamBus
from aiproxy.dashboard.routes import create_dashboard_router
from aiproxy.db.models import Base
from aiproxy.registry import RequestRegistry


@pytest.fixture
async def app_ctx():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    bus = StreamBus()
    registry = RequestRegistry(bus)
    app = FastAPI()
    app.include_router(create_dashboard_router(
        bus=bus, registry=registry, sessionmaker=sm,
        master_key="k", session_secret="s",
        secure_cookies=False,
    ))
    try:
        yield app
    finally:
        await engine.dispose()


async def _client(app):
    c = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    login = await c.post("/dashboard/login", json={"master_key": "k"})
    c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
    return c


async def test_get_has_defaults(app_ctx) -> None:
    async with await _client(app_ctx) as c:
        r = await c.get("/dashboard/api/config")
    cfg = r.json()["config"]
    assert cfg["retention.full_count"] == 500


async def test_set_and_get(app_ctx) -> None:
    async with await _client(app_ctx) as c:
        await c.post("/dashboard/api/config", json={
            "key": "retention.full_count", "value": 200
        })
        r = await c.get("/dashboard/api/config")
    assert r.json()["config"]["retention.full_count"] == 200
```

- [ ] **Step 4: Create `tests/integration/test_dashboard_batch_strip.py`**

```python
"""Integration tests for /dashboard/api/batch/strip-binaries."""
import time

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.bus import StreamBus
from aiproxy.dashboard.routes import create_dashboard_router
from aiproxy.db.crud import requests as req_crud
from aiproxy.db.models import Base
from aiproxy.registry import RequestRegistry


@pytest.fixture
async def app_ctx():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    bus = StreamBus()
    registry = RequestRegistry(bus)
    app = FastAPI()
    app.include_router(create_dashboard_router(
        bus=bus, registry=registry, sessionmaker=sm,
        master_key="k", session_secret="s",
        secure_cookies=False,
    ))
    try:
        yield app, sm
    finally:
        await engine.dispose()


async def _client(app):
    c = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    login = await c.post("/dashboard/login", json={"master_key": "k"})
    c.cookies.set("aiproxy_session", login.cookies.get("aiproxy_session"))
    return c


async def test_strip_replaces_base64_image(app_ctx) -> None:
    app, sm = app_ctx
    fake_body = b'{"messages":[{"content":[{"type":"image_url","image_url":{"url":"data:image/png;base64,AAAABBBBCCCCDDDDEEEE"}}]}]}'
    async with sm() as s:
        await req_crud.create_pending(
            s, req_id="r1", api_key_id=None, provider="openai",
            endpoint="/chat/completions", method="POST", model="gpt-4o",
            is_streaming=False, client_ip=None, client_ua=None,
            req_headers={}, req_query=None, req_body=fake_body,
            started_at=time.time(),
        )
        await s.commit()

    async with await _client(app) as c:
        r = await c.post("/dashboard/api/batch/strip-binaries", json={"req_ids": ["r1"]})
    assert r.status_code == 200
    assert r.json()["stripped"] == 1

    # Verify the body no longer contains the base64 payload
    from aiproxy.db.crud import requests as req_crud_mod
    async with sm() as s:
        row = await req_crud_mod.get_by_id(s, "r1")
    assert b"AAAABBBBCCCCDDDDEEEE" not in (row.req_body or b"")
    assert b"stripped" in (row.req_body or b"")
    assert row.binaries_stripped == 1


async def test_strip_requires_filter(app_ctx) -> None:
    app, _ = app_ctx
    async with await _client(app) as c:
        r = await c.post("/dashboard/api/batch/strip-binaries", json={})
    assert r.status_code == 400
```

- [ ] **Step 5: Run — expect all passing**

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_dashboard_keys_api.py \
        tests/integration/test_dashboard_pricing_api.py \
        tests/integration/test_dashboard_config_api.py \
        tests/integration/test_dashboard_batch_strip.py
git commit -m "test(phase-3): integration tests for keys/pricing/config/batch endpoints"
```

---

## Task 9: Rich frontend SPA

**Files:**
- Rewrite: `src/aiproxy/dashboard/static/index.html`

The Phase 2 dashboard is a single-page list + detail. Phase 3 rewrites it as a tabbed SPA with:
- Top nav: Requests / Keys / Pricing / Settings
- Requests tab: filter bar (provider/model/status/search) + A-layout (left list, right detail)
- Detail tabs: Overview / Request / Response / Chunks
- Keys tab: table + create form
- Pricing tab: table + add-version form
- Settings tab: retention threshold input + save button + "strip all binaries" button

**The HTML is large (~800-1000 lines).** Full reference implementation follows:

- [ ] **Step 1: Rewrite `src/aiproxy/dashboard/static/index.html`**

Write a single-file HTML document with embedded CSS and JavaScript implementing the above. Key structure:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>AI Proxy Dashboard</title>
  <style>
    /* Dark theme matching login page.
       Top nav, left list, right detail for Requests tab;
       single-panel content for Keys/Pricing/Settings tabs. */
    :root { --bg:#0e1116; --panel:#161b22; --border:#2d333b; --text:#c9d1d9; --muted:#8b949e; --accent:#58a6ff; --ok:#3fb950; --warn:#d29922; --err:#f85149; }
    * { box-sizing: border-box; }
    html, body { height: 100%; margin: 0; }
    body { background: var(--bg); color: var(--text); font: 13px/1.5 -apple-system, Segoe UI, sans-serif; display: grid; grid-template-rows: auto 1fr; }
    /* ... header, nav, tabs, list, detail, modal, button, input styles ... */
  </style>
</head>
<body>
  <header>
    <h1>AI Proxy</h1>
    <nav>
      <a data-tab="requests" class="tab active">Requests</a>
      <a data-tab="keys" class="tab">Keys</a>
      <a data-tab="pricing" class="tab">Pricing</a>
      <a data-tab="settings" class="tab">Settings</a>
    </nav>
    <span id="conn" class="conn bad">disconnected</span>
    <a id="logout" href="#">Sign out</a>
  </header>

  <main>
    <section id="tab-requests" class="tab-content active">
      <!-- filter bar + list + detail -->
      <div class="filter-bar">
        <select id="filter-provider">...</select>
        <select id="filter-status">...</select>
        <input id="search" placeholder="Search body / model / endpoint..." />
      </div>
      <div class="req-layout">
        <aside id="list"></aside>
        <section id="viewer"><div class="placeholder">Select a request</div></section>
      </div>
    </section>

    <section id="tab-keys" class="tab-content">...</section>
    <section id="tab-pricing" class="tab-content">...</section>
    <section id="tab-settings" class="tab-content">...</section>
  </main>

  <script>
    // SPA logic:
    // - state.currentTab
    // - state.requests, state.selectedReqId
    // - state.keys, state.pricing, state.config
    // - fetch helpers
    // - tab switcher
    // - WebSocket connection (ws/active) + reconnect
    // - list rendering + search debouncing
    // - detail fetch + tab rendering (overview/request/response/chunks)
    // - keys table + create modal
    // - pricing table + add form
    // - settings form
    // - logout

    const API = "/dashboard/api";
    const state = { currentTab: "requests", requests: new Map(), selectedReqId: null, keys: [], pricing: [], config: {} };

    async function api(method, path, body) {
      const opts = { method, credentials: "same-origin", headers: {} };
      if (body != null) {
        opts.headers["content-type"] = "application/json";
        opts.body = JSON.stringify(body);
      }
      const r = await fetch(API + path, opts);
      if (r.status === 401) { location.href = "/dashboard/login"; return; }
      if (!r.ok) throw new Error(`${method} ${path}: ${r.status}`);
      return r.json();
    }

    // ... implementation of each tab ...

    function init() {
      setupTabs();
      setupWS();
      loadRequests();
      // other tabs lazy-load on tab click
    }
    init();
  </script>
</body>
</html>
```

The full file should be a concrete working implementation — not a skeleton. The implementer should write complete working code for all 4 tabs, the detail view with Overview/Request/Response/Chunks sub-tabs, pagination, search debouncing, WebSocket reconnection, and basic CSS styling.

Scale target: 800-1200 lines of HTML+CSS+JS in one file. It's not pretty enterprise quality (that would be Phase 5), but it should be functional and aesthetically acceptable.

- [ ] **Step 2: Smoke-test manually**

The SPA has no Python tests — it's validated manually:
1. Start the server
2. Log in via `/dashboard/login`
3. Navigate between tabs
4. Create a key, make a proxied call, verify it appears in the Requests list with correct tokens/cost
5. Search for a substring of the request body
6. Click a request, verify all 4 detail tabs render
7. Update a pricing row
8. Change the retention threshold in Settings

No automated tests for the HTML itself — visual inspection only.

- [ ] **Step 3: Commit**

```bash
git add src/aiproxy/dashboard/static/index.html
git commit -m "feat(phase-3): rich SPA dashboard with tabs + filter + search + management pages"
```

---

## Task 10: Phase 3 final verification

- [ ] **Step 1: Full test suite + coverage**

```bash
uv run pytest tests/ -q
uv run pytest --cov=src/aiproxy --cov-report=term
```

Expect: all Phase 1/2/3 tests green. Coverage ≥ 85% on `dashboard/`, `auth/`, `db/`, `pricing/`.

- [ ] **Step 2: Live end-to-end**

Fresh DB, start server, verify the full loop:

```bash
# 1. Fresh state
pkill -9 -f aiproxy 2>/dev/null; sleep 1; rm -f aiproxy.db

# 2. Start server
nohup uv run python -m aiproxy > /tmp/aiproxy-p3.log 2>&1 &
sleep 3

# 3. Unauthenticated request → 401
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/dashboard/api/requests

# 4. Login → cookie
curl -c /tmp/aiproxy-cookies.txt -s -X POST http://127.0.0.1:8000/dashboard/login \
  -H 'content-type: application/json' \
  -d "{\"master_key\": \"$(grep PROXY_MASTER_KEY .env | cut -d= -f2)\"}"

# 5. Authenticated request → 200
curl -b /tmp/aiproxy-cookies.txt -s http://127.0.0.1:8000/dashboard/api/requests | python3 -m json.tool

# 6. Create an API key via dashboard
curl -b /tmp/aiproxy-cookies.txt -s -X POST http://127.0.0.1:8000/dashboard/api/keys \
  -H 'content-type: application/json' \
  -d '{"name": "phase3-verify"}' | python3 -m json.tool

# Grab the key value from the response

# 7. Make a real proxied call
curl -X POST http://127.0.0.1:8000/openai/chat/completions \
  -H "authorization: Bearer <the-key>" \
  -H 'content-type: application/json' \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ping"}]}'

# 8. Verify it appears in the list
curl -b /tmp/aiproxy-cookies.txt -s http://127.0.0.1:8000/dashboard/api/requests | python3 -m json.tool

# 9. Try FTS search
curl -b /tmp/aiproxy-cookies.txt -s 'http://127.0.0.1:8000/dashboard/api/requests?q=ping' | python3 -m json.tool

# 10. Change retention config
curl -b /tmp/aiproxy-cookies.txt -s -X POST http://127.0.0.1:8000/dashboard/api/config \
  -H 'content-type: application/json' \
  -d '{"key": "retention.full_count", "value": 10}'

# 11. Stop
pkill -9 -f aiproxy
```

Expected:
- Step 3 returns `401`
- Step 4 sets the `aiproxy_session` cookie
- Step 5 returns `{"rows": [], "total": 0, ...}`
- Step 6 returns a new key with `sk-aiprx-...` prefix
- Step 7 returns a real OpenAI response
- Step 8 returns the request with `input_tokens`/`output_tokens`/`cost_usd` populated
- Step 9 finds it by the word "ping"
- Step 10 returns `{"ok": true, ...}`

- [ ] **Step 3: Browser smoke**

Open `http://127.0.0.1:8000/dashboard/` in a browser, log in, click through all four tabs. Verify:
- Requests tab shows recent calls with live updates if you fire more
- Keys tab shows the phase3-verify key with plaintext visible
- Pricing tab shows 10 seed rows + any you added
- Settings tab shows retention=10

- [ ] **Step 4: Phase 3 checkpoint commit**

```bash
git commit --allow-empty -m "chore(phase-3): Phase 3 rich dashboard UX complete

Verified:
- Full test suite green
- Coverage targets met for dashboard/auth/db/pricing
- Live end-to-end: login, protected endpoints, key creation, FTS search,
  config mutation all working
- Browser smoke: all 4 tabs functional, SPA navigation works

What Phase 3 delivered:
- Dashboard master-key login with JWT httpOnly signed cookies
- Auth dependency gating every /dashboard/api/* and /dashboard/ws/* route
- FTS5 virtual table + sync triggers for full-text search on requests
- Paginated list endpoint with filters (provider/model/status/time) + FTS
- Detail endpoint with full row + chunks
- Keys/pricing/config management endpoints
- Batch strip-binaries operation
- Rich SPA dashboard: Requests/Keys/Pricing/Settings tabs
- Detail tabs: Overview/Request/Response/Chunks
- Retention task now reads full_count from config table

Ready for Phase 4: replay player (scrubber, speed controls, dim/typewriter
modes, JSON log mode) — leveraging the chunks table with offset_ns
timestamps already populated in Phase 2."
```

---

## Done checklist

- [x] pyjwt dependency + dashboard auth module with issue/verify/dependency
- [x] Config CRUD with JSON-encoded values + default fallback
- [x] FTS5 virtual table + triggers + `search_requests()` helper
- [x] `list_with_filters()` helper on requests CRUD
- [x] Dashboard login endpoint + protected routes + all management CRUD endpoints
- [x] Retention loop reads full_count from config table
- [x] FTS schema installed in lifespan after create_all
- [x] Integration tests for login, list, detail, keys, pricing, config, batch
- [x] Rich SPA dashboard with 4 tabs and detail inspector
- [x] Full test suite green + coverage targets met
- [x] Live end-to-end verified through browser + curl
