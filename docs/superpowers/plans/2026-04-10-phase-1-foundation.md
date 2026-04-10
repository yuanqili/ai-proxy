# Phase 1 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the v0 MVP into a clean provider-agnostic foundation — transparent passthrough for OpenAI, Anthropic, and OpenRouter with SQLite persistence of request metadata and a multi-key auth system. No dashboard yet (comes in Phase 2-3).

**Architecture:** A thin FastAPI dispatcher routes `/{provider}/{path}` requests to a shared `passthrough_engine.handle()` function. Each provider is a small `Provider` subclass that knows only its own auth injection and path mapping. Request metadata (start/finish/status/error) is persisted to SQLite via SQLAlchemy async. Inbound auth uses an API key table with a 60-second in-memory cache.

**Tech Stack:**
- FastAPI (existing) + uvicorn
- httpx[http2] for upstream calls (existing)
- SQLAlchemy 2.x async + aiosqlite for persistence
- pydantic-settings (existing) for config
- pytest + pytest-asyncio + respx for tests
- uv for dependency management (existing)

**Reference:** `docs/superpowers/specs/2026-04-10-ai-proxy-with-live-dashboard-design.md`

---

## Scope for Phase 1

**IN scope:**
- Full SQLite schema created (all tables from §3 of spec)
- SQLAlchemy async engine + session factory
- CRUD for `api_keys` and `requests` tables (other tables just have schema, no CRUD yet)
- Three `Provider` subclasses (OpenAI, Anthropic, OpenRouter) with unit tests
- Shared `passthrough_engine.handle()` that: forwards request, streams response, records metadata at start + end
- Inbound proxy auth middleware with in-memory cache
- CLI script to bootstrap the first API key
- Smoke-test scripts for each provider
- Integration tests using `respx` mocks for upstream

**OUT of scope (later phases):**
- Chunk-level persistence (Phase 2)
- StreamBus tee to dashboard (Phase 2)
- SSE parsing + usage extraction + cost computation (Phase 2)
- Retention cleanup task (Phase 2)
- Dashboard UI and endpoints (Phase 3)
- Replay player (Phase 4)
- Timeline tab (Phase 5)

**Verification criteria:** `scripts/test_all.py` runs all three providers successfully, each smoke test prints TTFT and writes a row to the `requests` table.

---

## File Structure

```
ai-proxy/
├── pyproject.toml                             [MODIFY — add deps]
├── .env.example                               [MODIFY — add all provider + master keys]
├── src/aiproxy/
│   ├── __init__.py                            [KEEP — empty marker]
│   ├── __main__.py                            [REWRITE]
│   ├── app.py                                 [REWRITE]
│   ├── settings.py                            [REWRITE — expand for all providers]
│   ├── core/
│   │   ├── __init__.py                        [KEEP]
│   │   ├── headers.py                         [KEEP — unchanged from v0]
│   │   └── passthrough.py                     [NEW]
│   ├── providers/
│   │   ├── __init__.py                        [NEW — provider registry]
│   │   ├── base.py                            [NEW — Provider ABC, Usage dataclass]
│   │   ├── openai.py                          [NEW]
│   │   ├── anthropic.py                       [NEW]
│   │   └── openrouter.py                      [NEW]
│   ├── db/
│   │   ├── __init__.py                        [NEW]
│   │   ├── engine.py                          [NEW — async engine + session factory]
│   │   ├── models.py                          [NEW — all tables from spec §3]
│   │   └── crud/
│   │       ├── __init__.py                    [NEW]
│   │       ├── api_keys.py                    [NEW]
│   │       └── requests.py                    [NEW]
│   ├── auth/
│   │   ├── __init__.py                        [NEW]
│   │   └── proxy_auth.py                      [NEW — verify_proxy_key middleware]
│   └── routers/
│       ├── __init__.py                        [NEW]
│       └── proxy.py                           [NEW — dispatcher]
├── scripts/
│   ├── create_api_key.py                      [NEW — bootstrap CLI]
│   ├── test_openai.py                         [NEW]
│   ├── test_anthropic.py                      [NEW]
│   ├── test_openrouter.py                     [NEW]
│   └── test_all.py                            [NEW]
└── tests/
    ├── __init__.py                            [NEW]
    ├── conftest.py                            [NEW — shared fixtures]
    ├── unit/
    │   ├── __init__.py                        [NEW]
    │   ├── test_headers.py                    [NEW]
    │   ├── test_settings.py                   [NEW]
    │   ├── test_db_models.py                  [NEW]
    │   ├── test_crud_api_keys.py              [NEW]
    │   ├── test_crud_requests.py              [NEW]
    │   ├── test_providers_base.py             [NEW]
    │   ├── test_provider_openai.py            [NEW]
    │   ├── test_provider_anthropic.py         [NEW]
    │   └── test_provider_openrouter.py        [NEW]
    └── integration/
        ├── __init__.py                        [NEW]
        ├── conftest.py                        [NEW]
        ├── test_auth_middleware.py            [NEW]
        ├── test_proxy_engine.py               [NEW]
        └── test_end_to_end.py                 [NEW]
```

**Files deleted from v0:**
- `src/aiproxy/bus.py` — not used in Phase 1 (Phase 2 will reintroduce it)
- `src/aiproxy/proxy/` (whole directory, including `openrouter.py`)
- `src/aiproxy/dashboard/` (whole directory)
- `scripts/test_stream.py` — replaced by new per-provider smoke scripts

---

## Task 1: Clean slate — delete v0 MVP code and set up dependencies

**Files:**
- Delete: `src/aiproxy/bus.py`
- Delete: `src/aiproxy/proxy/` (directory)
- Delete: `src/aiproxy/dashboard/` (directory)
- Delete: `scripts/test_stream.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Delete obsolete v0 files**

```bash
rm -f src/aiproxy/bus.py
rm -rf src/aiproxy/proxy
rm -rf src/aiproxy/dashboard
rm -f scripts/test_stream.py
```

- [ ] **Step 2: Delete obsolete v0 app code that references removed modules**

The old `src/aiproxy/app.py` and `src/aiproxy/__main__.py` import `bus` and the deleted routers. Empty them out so imports no longer fail; they'll be rewritten in Task 15.

Replace `src/aiproxy/app.py` with a minimal placeholder:

```python
"""Placeholder — will be rebuilt in Task 15."""
from fastapi import FastAPI

def create_app() -> FastAPI:
    return FastAPI(title="AI Proxy")

app = create_app()
```

Replace `src/aiproxy/__main__.py` with a minimal placeholder:

```python
"""Placeholder — will be rebuilt in Task 15."""
import uvicorn

def main() -> None:
    uvicorn.run("aiproxy.app:app", host="127.0.0.1", port=8000, reload=False)

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Add new dependencies to pyproject.toml**

Open `pyproject.toml` and replace the `[project]` `dependencies` block and add a `[dependency-groups]` block:

```toml
[project]
name = "aiproxy"
version = "0.1.0"
description = "AI API proxy with live-streaming observability dashboard"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "httpx[http2]>=0.27.0",
    "pydantic>=2.9.0",
    "pydantic-settings>=2.5.0",
    "sqlalchemy[asyncio]>=2.0.36",
    "aiosqlite>=0.20.0",
    "websockets>=13.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/aiproxy"]

[dependency-groups]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "pytest-cov>=6.0.0",
    "respx>=0.22.0",
    "httpx>=0.27.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 4: Sync dependencies**

Run: `uv sync`
Expected: `Installed N packages` with no errors.

- [ ] **Step 5: Verify placeholder app imports cleanly**

Run: `uv run python -c "from aiproxy.app import app; print(app.title)"`
Expected output: `AI Proxy`

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore(phase-1): clean slate — remove v0 code, add async SQL deps"
```

---

## Task 2: Rewrite settings.py for all providers + auth + database

**Files:**
- Rewrite: `src/aiproxy/settings.py`
- Create: `tests/__init__.py`, `tests/unit/__init__.py`, `tests/unit/test_settings.py`
- Create: `tests/conftest.py`
- Modify: `.env.example`

- [ ] **Step 1: Create empty test package markers**

```bash
mkdir -p tests/unit tests/integration
touch tests/__init__.py tests/unit/__init__.py tests/integration/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_settings.py`:

```python
"""Tests for aiproxy.settings."""
import os
from pathlib import Path

import pytest

from aiproxy.settings import Settings


def test_settings_loads_all_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "OPENAI_BASE_URL=https://api.openai.com\n"
        "OPENAI_API_KEY=sk-test-openai\n"
        "ANTHROPIC_BASE_URL=https://api.anthropic.com\n"
        "ANTHROPIC_API_KEY=sk-ant-test\n"
        "OPENROUTER_BASE_URL=https://openrouter.ai\n"
        "OPENROUTER_API_KEY=sk-or-test\n"
        "PROXY_MASTER_KEY=master-xxx\n"
        "SESSION_SECRET=session-xxx\n"
        "DATABASE_URL=sqlite+aiosqlite:///:memory:\n"
        "HOST=127.0.0.1\n"
        "PORT=8000\n"
    )
    monkeypatch.chdir(tmp_path)
    # pydantic-settings caches the parse, so build a fresh instance
    s = Settings(_env_file=str(env))  # type: ignore[call-arg]
    assert s.openai_api_key == "sk-test-openai"
    assert s.anthropic_api_key == "sk-ant-test"
    assert s.openrouter_api_key == "sk-or-test"
    assert s.proxy_master_key == "master-xxx"
    assert s.session_secret == "session-xxx"
    assert s.database_url == "sqlite+aiosqlite:///:memory:"
    assert s.host == "127.0.0.1"
    assert s.port == 8000


def test_settings_defaults_for_optional_fields() -> None:
    s = Settings(
        openai_api_key="a",
        anthropic_api_key="b",
        openrouter_api_key="c",
        proxy_master_key="d",
        session_secret="e",
    )
    assert s.openai_base_url == "https://api.openai.com"
    assert s.anthropic_base_url == "https://api.anthropic.com"
    assert s.openrouter_base_url == "https://openrouter.ai"
    assert s.database_url == "sqlite+aiosqlite:///./aiproxy.db"
    assert s.host == "127.0.0.1"
    assert s.port == 8000
    assert s.secure_cookies is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_settings.py -v`
Expected: FAIL — `Settings` is missing the new fields.

- [ ] **Step 4: Rewrite `src/aiproxy/settings.py`**

```python
"""Application configuration loaded from environment / .env file."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Upstream providers
    openai_base_url: str = "https://api.openai.com"
    openai_api_key: str
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_api_key: str
    openrouter_base_url: str = "https://openrouter.ai"
    openrouter_api_key: str

    # Proxy self-auth
    proxy_master_key: str
    session_secret: str

    # Database
    database_url: str = "sqlite+aiosqlite:///./aiproxy.db"

    # Server
    host: str = "127.0.0.1"
    port: int = 8000
    dashboard_origin: str | None = None
    secure_cookies: bool = False


settings = Settings()  # type: ignore[call-arg]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_settings.py -v`
Expected: PASS (2 tests).

Note: the module-level `settings = Settings()` at import time requires env vars or an `.env` file. The test uses `Settings(_env_file=...)` or explicit kwargs so it doesn't hit the module-level instance.

- [ ] **Step 6: Update `.env.example`**

Rewrite `/Users/yuanqili/Developments/ai-proxy/.env.example`:

```
# Upstream provider keys
OPENAI_BASE_URL=https://api.openai.com
OPENAI_API_KEY=sk-proj-...

ANTHROPIC_BASE_URL=https://api.anthropic.com
ANTHROPIC_API_KEY=sk-ant-...

OPENROUTER_BASE_URL=https://openrouter.ai
OPENROUTER_API_KEY=sk-or-v1-...

# Proxy self-auth
PROXY_MASTER_KEY=change-me-long-random-string
SESSION_SECRET=change-me-32-random-bytes-hex

# Database
DATABASE_URL=sqlite+aiosqlite:///./aiproxy.db

# Server
HOST=127.0.0.1
PORT=8000
```

- [ ] **Step 7: Commit**

```bash
git add src/aiproxy/settings.py tests/ .env.example
git commit -m "feat(phase-1): expand settings for all providers + auth + database"
```

---

## Task 3: SQLAlchemy async engine and session factory

**Files:**
- Create: `src/aiproxy/db/__init__.py`, `src/aiproxy/db/engine.py`

- [ ] **Step 1: Write a minimal smoke test (no DB logic yet)**

Create `tests/unit/test_db_engine.py`:

```python
"""Tests for aiproxy.db.engine."""
import pytest
from sqlalchemy import text

from aiproxy.db.engine import create_engine_and_sessionmaker


@pytest.mark.asyncio
async def test_engine_and_sessionmaker_roundtrip() -> None:
    engine, sessionmaker = create_engine_and_sessionmaker("sqlite+aiosqlite:///:memory:")
    try:
        async with sessionmaker() as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar() == 1
    finally:
        await engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_db_engine.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `db/engine.py`**

Create `src/aiproxy/db/__init__.py` (empty).

Create `src/aiproxy/db/engine.py`:

```python
"""SQLAlchemy async engine and session factory."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine_and_sessionmaker(
    database_url: str,
    *,
    echo: bool = False,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create an async engine and a session factory bound to it."""
    engine = create_async_engine(database_url, echo=echo, future=True)
    sessionmaker = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return engine, sessionmaker
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_db_engine.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/db/ tests/unit/test_db_engine.py
git commit -m "feat(phase-1): add async SQLAlchemy engine and sessionmaker"
```

---

## Task 4: ORM models for all tables from the spec

**Files:**
- Create: `src/aiproxy/db/models.py`
- Create: `tests/unit/test_db_models.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_db_models.py`:

```python
"""Tests for aiproxy.db.models — ensure all tables are declared and schema is creatable."""
import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from aiproxy.db.models import Base


EXPECTED_TABLES = {
    "requests",
    "chunks",
    "api_keys",
    "pricing",
    "config",
}


@pytest.mark.asyncio
async def test_all_tables_created() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        def list_tables(sync_conn):  # type: ignore[no-untyped-def]
            return set(inspect(sync_conn).get_table_names())

        names = await conn.run_sync(list_tables)

    await engine.dispose()
    assert EXPECTED_TABLES.issubset(names), f"missing tables: {EXPECTED_TABLES - names}"


@pytest.mark.asyncio
async def test_requests_columns_present() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        def inspect_cols(sync_conn):  # type: ignore[no-untyped-def]
            return {c["name"] for c in inspect(sync_conn).get_columns("requests")}

        cols = await conn.run_sync(inspect_cols)
    await engine.dispose()

    required = {
        "req_id", "api_key_id", "provider", "endpoint", "method", "model",
        "is_streaming", "client_ip", "client_ua",
        "req_headers", "req_query", "req_body", "req_body_size",
        "status_code", "resp_headers", "resp_body", "resp_body_size",
        "started_at", "upstream_sent_at", "first_chunk_at", "finished_at",
        "status", "error_class", "error_message",
        "input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens",
        "cost_usd", "pricing_id",
        "chunk_count", "binaries_stripped",
    }
    missing = required - cols
    assert not missing, f"missing columns in requests: {missing}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_db_models.py -v`
Expected: FAIL — `aiproxy.db.models` not found.

- [ ] **Step 3: Implement `db/models.py`**

Create `src/aiproxy/db/models.py`:

```python
"""SQLAlchemy ORM models mirroring the spec §3 schema.

Only `api_keys` and `requests` are actively read/written in Phase 1. The
other tables (`chunks`, `pricing`, `config`) are created so migrations
stay in sync with the spec; their CRUD layers arrive in Phase 2.
"""
from __future__ import annotations

from sqlalchemy import (
    BLOB,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Real,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[float] = mapped_column(Real, nullable=False)
    valid_from: Mapped[float | None] = mapped_column(Real)
    valid_to: Mapped[float | None] = mapped_column(Real)
    last_used_at: Mapped[float | None] = mapped_column(Real)


class Request(Base):
    __tablename__ = "requests"

    req_id: Mapped[str] = mapped_column(Text, primary_key=True)
    api_key_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("api_keys.id"))
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text)
    is_streaming: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    client_ip: Mapped[str | None] = mapped_column(Text)
    client_ua: Mapped[str | None] = mapped_column(Text)

    req_headers: Mapped[str] = mapped_column(Text, nullable=False)
    req_query: Mapped[str | None] = mapped_column(Text)
    req_body: Mapped[bytes | None] = mapped_column(LargeBinary)
    req_body_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status_code: Mapped[int | None] = mapped_column(Integer)
    resp_headers: Mapped[str | None] = mapped_column(Text)
    resp_body: Mapped[bytes | None] = mapped_column(LargeBinary)
    resp_body_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    started_at: Mapped[float] = mapped_column(Real, nullable=False)
    upstream_sent_at: Mapped[float | None] = mapped_column(Real)
    first_chunk_at: Mapped[float | None] = mapped_column(Real)
    finished_at: Mapped[float | None] = mapped_column(Real)

    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_class: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)

    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Real)
    pricing_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("pricing.id"))

    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    binaries_stripped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("idx_requests_started", "started_at"),
        Index("idx_requests_provider_model", "provider", "model"),
        Index("idx_requests_status", "status"),
        Index("idx_requests_api_key", "api_key_id"),
    )


class Chunk(Base):
    __tablename__ = "chunks"

    req_id: Mapped[str] = mapped_column(
        Text, ForeignKey("requests.req_id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    offset_ns: Mapped[int] = mapped_column(Integer, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[bytes] = mapped_column(BLOB, nullable=False)

    __table_args__ = (Index("idx_chunks_req", "req_id"),)


class Pricing(Base):
    __tablename__ = "pricing"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    input_per_1m_usd: Mapped[float] = mapped_column(Real, nullable=False)
    output_per_1m_usd: Mapped[float] = mapped_column(Real, nullable=False)
    cached_per_1m_usd: Mapped[float | None] = mapped_column(Real)
    effective_from: Mapped[float] = mapped_column(Real, nullable=False)
    effective_to: Mapped[float | None] = mapped_column(Real)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Real, nullable=False)

    __table_args__ = (
        Index("idx_pricing_lookup", "provider", "model", "effective_from"),
    )


class Config(Base):
    __tablename__ = "config"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[float] = mapped_column(Real, nullable=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_db_models.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/db/models.py tests/unit/test_db_models.py
git commit -m "feat(phase-1): add ORM models for all tables (§3 of spec)"
```

---

## Task 5: CRUD for api_keys table

**Files:**
- Create: `src/aiproxy/db/crud/__init__.py`, `src/aiproxy/db/crud/api_keys.py`
- Create: `tests/unit/test_crud_api_keys.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_crud_api_keys.py`:

```python
"""Tests for aiproxy.db.crud.api_keys."""
import time

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.db.crud import api_keys as api_keys_crud
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


async def test_create_and_get(session_factory) -> None:
    async with session_factory() as s:
        row = await api_keys_crud.create(
            s, key="sk-aiprx-abc", name="test", note=None,
            valid_from=None, valid_to=None,
        )
        await s.commit()
        assert row.id is not None

    async with session_factory() as s:
        fetched = await api_keys_crud.get_by_key(s, "sk-aiprx-abc")
        assert fetched is not None
        assert fetched.name == "test"
        assert fetched.is_active == 1


async def test_get_nonexistent_returns_none(session_factory) -> None:
    async with session_factory() as s:
        assert await api_keys_crud.get_by_key(s, "sk-aiprx-missing") is None


async def test_update_last_used(session_factory) -> None:
    async with session_factory() as s:
        await api_keys_crud.create(s, key="k1", name="n", note=None,
                                   valid_from=None, valid_to=None)
        await s.commit()

    ts = time.time()
    async with session_factory() as s:
        await api_keys_crud.update_last_used(s, "k1", ts)
        await s.commit()

    async with session_factory() as s:
        row = await api_keys_crud.get_by_key(s, "k1")
        assert row is not None
        assert row.last_used_at == ts


async def test_list_all(session_factory) -> None:
    async with session_factory() as s:
        await api_keys_crud.create(s, key="a", name="a", note=None,
                                   valid_from=None, valid_to=None)
        await api_keys_crud.create(s, key="b", name="b", note=None,
                                   valid_from=None, valid_to=None)
        await s.commit()

    async with session_factory() as s:
        rows = await api_keys_crud.list_all(s)
        assert {r.key for r in rows} == {"a", "b"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_crud_api_keys.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the CRUD module**

Create `src/aiproxy/db/crud/__init__.py` (empty).

Create `src/aiproxy/db/crud/api_keys.py`:

```python
"""CRUD operations for the api_keys table."""
from __future__ import annotations

import time
from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aiproxy.db.models import ApiKey


async def create(
    session: AsyncSession,
    *,
    key: str,
    name: str,
    note: str | None,
    valid_from: float | None,
    valid_to: float | None,
) -> ApiKey:
    row = ApiKey(
        key=key,
        name=name,
        note=note,
        is_active=1,
        created_at=time.time(),
        valid_from=valid_from,
        valid_to=valid_to,
        last_used_at=None,
    )
    session.add(row)
    await session.flush()
    return row


async def get_by_key(session: AsyncSession, key: str) -> ApiKey | None:
    result = await session.execute(select(ApiKey).where(ApiKey.key == key))
    return result.scalar_one_or_none()


async def list_all(session: AsyncSession) -> Sequence[ApiKey]:
    result = await session.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
    return result.scalars().all()


async def update_last_used(session: AsyncSession, key: str, when: float) -> None:
    await session.execute(
        update(ApiKey).where(ApiKey.key == key).values(last_used_at=when)
    )


async def set_active(session: AsyncSession, key: str, active: bool) -> None:
    await session.execute(
        update(ApiKey).where(ApiKey.key == key).values(is_active=1 if active else 0)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_crud_api_keys.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/db/crud/ tests/unit/test_crud_api_keys.py
git commit -m "feat(phase-1): add CRUD for api_keys table"
```

---

## Task 6: CRUD for requests table

**Files:**
- Create: `src/aiproxy/db/crud/requests.py`
- Create: `tests/unit/test_crud_requests.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_crud_requests.py`:

```python
"""Tests for aiproxy.db.crud.requests."""
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


async def test_create_pending_and_mark_finished(session_factory) -> None:
    async with session_factory() as s:
        await req_crud.create_pending(
            s,
            req_id="abc123",
            api_key_id=None,
            provider="openai",
            endpoint="/chat/completions",
            method="POST",
            model="gpt-4o",
            is_streaming=True,
            client_ip="127.0.0.1",
            client_ua="pytest/1.0",
            req_headers={"content-type": "application/json"},
            req_query=None,
            req_body=b'{"model":"gpt-4o"}',
            started_at=time.time(),
        )
        await s.commit()

    async with session_factory() as s:
        row = await req_crud.get_by_id(s, "abc123")
        assert row is not None
        assert row.status == "pending"
        assert row.provider == "openai"
        assert row.is_streaming == 1
        assert row.req_body == b'{"model":"gpt-4o"}'
        assert row.req_body_size == len(b'{"model":"gpt-4o"}')

    async with session_factory() as s:
        await req_crud.mark_finished(
            s,
            req_id="abc123",
            status="done",
            status_code=200,
            resp_headers={"content-type": "text/event-stream"},
            resp_body=b"",
            finished_at=time.time(),
        )
        await s.commit()

    async with session_factory() as s:
        row = await req_crud.get_by_id(s, "abc123")
        assert row is not None
        assert row.status == "done"
        assert row.status_code == 200
        assert row.finished_at is not None


async def test_mark_error(session_factory) -> None:
    async with session_factory() as s:
        await req_crud.create_pending(
            s,
            req_id="err1",
            api_key_id=None,
            provider="anthropic",
            endpoint="/v1/messages",
            method="POST",
            model=None,
            is_streaming=False,
            client_ip=None,
            client_ua=None,
            req_headers={},
            req_query=None,
            req_body=None,
            started_at=time.time(),
        )
        await s.commit()
        await req_crud.mark_error(
            s,
            req_id="err1",
            error_class="upstream_connect",
            error_message="DNS failure",
            finished_at=time.time(),
        )
        await s.commit()

    async with session_factory() as s:
        row = await req_crud.get_by_id(s, "err1")
        assert row is not None
        assert row.status == "error"
        assert row.error_class == "upstream_connect"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_crud_requests.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the CRUD module**

Create `src/aiproxy/db/crud/requests.py`:

```python
"""CRUD operations for the requests table.

Phase 1 covers: create_pending, mark_finished, mark_error, get_by_id.
Chunk persistence and cost fields are written by Phase 2.
"""
from __future__ import annotations

import json

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aiproxy.db.models import Request


def _dumps(obj: object) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


async def create_pending(
    session: AsyncSession,
    *,
    req_id: str,
    api_key_id: int | None,
    provider: str,
    endpoint: str,
    method: str,
    model: str | None,
    is_streaming: bool,
    client_ip: str | None,
    client_ua: str | None,
    req_headers: dict[str, str],
    req_query: list[tuple[str, str]] | None,
    req_body: bytes | None,
    started_at: float,
) -> None:
    row = Request(
        req_id=req_id,
        api_key_id=api_key_id,
        provider=provider,
        endpoint=endpoint,
        method=method,
        model=model,
        is_streaming=1 if is_streaming else 0,
        client_ip=client_ip,
        client_ua=client_ua,
        req_headers=_dumps(req_headers),
        req_query=_dumps(req_query) if req_query else None,
        req_body=req_body,
        req_body_size=len(req_body) if req_body else 0,
        started_at=started_at,
        status="pending",
    )
    session.add(row)
    await session.flush()


async def mark_finished(
    session: AsyncSession,
    *,
    req_id: str,
    status: str,
    status_code: int | None,
    resp_headers: dict[str, str] | None,
    resp_body: bytes | None,
    finished_at: float,
) -> None:
    await session.execute(
        update(Request)
        .where(Request.req_id == req_id)
        .values(
            status=status,
            status_code=status_code,
            resp_headers=_dumps(resp_headers) if resp_headers is not None else None,
            resp_body=resp_body,
            resp_body_size=len(resp_body) if resp_body else 0,
            finished_at=finished_at,
        )
    )


async def mark_error(
    session: AsyncSession,
    *,
    req_id: str,
    error_class: str,
    error_message: str,
    finished_at: float,
) -> None:
    await session.execute(
        update(Request)
        .where(Request.req_id == req_id)
        .values(
            status="error",
            error_class=error_class,
            error_message=error_message,
            finished_at=finished_at,
        )
    )


async def get_by_id(session: AsyncSession, req_id: str) -> Request | None:
    result = await session.execute(select(Request).where(Request.req_id == req_id))
    return result.scalar_one_or_none()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_crud_requests.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/db/crud/requests.py tests/unit/test_crud_requests.py
git commit -m "feat(phase-1): add CRUD for requests table"
```

---

## Task 7: Proxy auth middleware with in-memory cache

**Files:**
- Create: `src/aiproxy/auth/__init__.py`, `src/aiproxy/auth/proxy_auth.py`
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_auth_middleware.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/conftest.py`:

```python
"""Shared fixtures for integration tests."""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.db.models import Base


@pytest.fixture
async def db_sessionmaker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sm
    finally:
        await engine.dispose()
```

Create `tests/integration/test_auth_middleware.py`:

```python
"""Integration tests for the proxy auth middleware."""
import pytest

from aiproxy.auth.proxy_auth import ApiKeyCache, AuthResult, verify_header
from aiproxy.db.crud import api_keys as api_keys_crud


@pytest.fixture
async def seeded_session(db_sessionmaker):
    async with db_sessionmaker() as s:
        await api_keys_crud.create(
            s, key="sk-aiprx-valid", name="prod", note=None,
            valid_from=None, valid_to=None,
        )
        await api_keys_crud.create(
            s, key="sk-aiprx-inactive", name="old", note=None,
            valid_from=None, valid_to=None,
        )
        await api_keys_crud.set_active(s, "sk-aiprx-inactive", False)
        await s.commit()
    yield db_sessionmaker


async def test_valid_bearer(seeded_session) -> None:
    cache = ApiKeyCache(seeded_session, ttl_seconds=60)
    result = await verify_header("Bearer sk-aiprx-valid", cache)
    assert result.ok
    assert result.api_key_id is not None
    assert result.reason is None


async def test_valid_bare_token(seeded_session) -> None:
    cache = ApiKeyCache(seeded_session, ttl_seconds=60)
    result = await verify_header("sk-aiprx-valid", cache)
    assert result.ok


async def test_missing(seeded_session) -> None:
    cache = ApiKeyCache(seeded_session, ttl_seconds=60)
    result = await verify_header(None, cache)
    assert not result.ok
    assert result.reason == "missing"


async def test_unknown_key(seeded_session) -> None:
    cache = ApiKeyCache(seeded_session, ttl_seconds=60)
    result = await verify_header("Bearer sk-aiprx-nope", cache)
    assert not result.ok
    assert result.reason == "not_found"


async def test_inactive_key(seeded_session) -> None:
    cache = ApiKeyCache(seeded_session, ttl_seconds=60)
    result = await verify_header("Bearer sk-aiprx-inactive", cache)
    assert not result.ok
    assert result.reason == "inactive"


async def test_cache_refreshes_after_ttl(seeded_session) -> None:
    # Cache a miss first
    cache = ApiKeyCache(seeded_session, ttl_seconds=0.0)  # effectively no cache
    await verify_header("Bearer sk-aiprx-missing", cache)
    # Now create the key
    async with seeded_session() as s:
        await api_keys_crud.create(
            s, key="sk-aiprx-fresh", name="fresh", note=None,
            valid_from=None, valid_to=None,
        )
        await s.commit()
    # Next lookup should find it (ttl=0 forces reload)
    result = await verify_header("Bearer sk-aiprx-fresh", cache)
    assert result.ok
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_auth_middleware.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the auth module**

Create `src/aiproxy/auth/__init__.py` (empty).

Create `src/aiproxy/auth/proxy_auth.py`:

```python
"""Proxy inbound auth: verify client API key against the api_keys table.

Uses a simple in-memory TTL cache keyed by the raw key string. The whole
api_keys table is small (dozens of rows at most), so cache management is
trivial. We refresh the cache atomically from the DB every `ttl_seconds`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from aiproxy.db.crud import api_keys as api_keys_crud
from aiproxy.db.models import ApiKey


@dataclass
class AuthResult:
    ok: bool
    api_key_id: int | None = None
    reason: str | None = None  # 'missing' | 'not_found' | 'inactive' | 'not_yet_valid' | 'expired'


class ApiKeyCache:
    """Reloads the full api_keys table every ttl_seconds seconds.

    Not thread-safe — fine for single-process FastAPI.
    """

    def __init__(self, sessionmaker: async_sessionmaker, ttl_seconds: float = 60.0) -> None:
        self._sessionmaker = sessionmaker
        self._ttl = ttl_seconds
        self._by_key: dict[str, ApiKey] = {}
        self._loaded_at: float = 0.0

    async def get(self, key: str) -> ApiKey | None:
        now = time.monotonic()
        if now - self._loaded_at >= self._ttl:
            await self._reload()
        return self._by_key.get(key)

    async def _reload(self) -> None:
        async with self._sessionmaker() as session:
            rows = await api_keys_crud.list_all(session)
        self._by_key = {row.key: row for row in rows}
        self._loaded_at = time.monotonic()


def extract_key(header_value: str | None) -> str | None:
    """Accept 'Bearer X', bare 'X', or None."""
    if not header_value:
        return None
    value = header_value.strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip() or None
    return value or None


async def verify_header(header_value: str | None, cache: ApiKeyCache) -> AuthResult:
    key = extract_key(header_value)
    if key is None:
        return AuthResult(ok=False, reason="missing")

    row = await cache.get(key)
    if row is None:
        return AuthResult(ok=False, reason="not_found")
    if row.is_active != 1:
        return AuthResult(ok=False, reason="inactive")
    now = time.time()
    if row.valid_from is not None and now < row.valid_from:
        return AuthResult(ok=False, reason="not_yet_valid")
    if row.valid_to is not None and now >= row.valid_to:
        return AuthResult(ok=False, reason="expired")
    return AuthResult(ok=True, api_key_id=row.id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_auth_middleware.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/auth/ tests/integration/
git commit -m "feat(phase-1): add proxy auth middleware with cached api_keys lookup"
```

---

## Task 8: Provider base class

**Files:**
- Create: `src/aiproxy/providers/__init__.py`, `src/aiproxy/providers/base.py`
- Create: `tests/unit/test_providers_base.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_providers_base.py`:

```python
"""Tests for the Provider base class defaults."""
from aiproxy.providers.base import Provider, Usage


class _Dummy(Provider):
    name = "dummy"
    base_url = "https://example.com"
    upstream_path_prefix = "/v1"

    def upstream_api_key(self) -> str:
        return "dummy-key"

    def inject_auth(self, headers):
        headers["x-dummy"] = self.upstream_api_key()
        return headers


def test_map_path_strips_leading_slash() -> None:
    p = _Dummy()
    assert p.map_path("chat/completions") == "/v1/chat/completions"
    assert p.map_path("/chat/completions") == "/v1/chat/completions"


def test_extract_model_from_json_body() -> None:
    p = _Dummy()
    assert p.extract_model(b'{"model":"gpt-4o"}') == "gpt-4o"
    assert p.extract_model(b'{"other":"value"}') is None
    assert p.extract_model(b"not json") is None
    assert p.extract_model(b"") is None


def test_is_streaming_request_json_body() -> None:
    p = _Dummy()
    assert p.is_streaming_request(b'{"stream":true}', {}) is True
    assert p.is_streaming_request(b'{"stream":false}', {}) is False
    assert p.is_streaming_request(b'{"other":1}', {}) is False
    assert p.is_streaming_request(b"", {}) is False


def test_usage_dataclass() -> None:
    u = Usage(input_tokens=10, output_tokens=5)
    assert u.input_tokens == 10
    assert u.cached_tokens is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_providers_base.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the base**

Create `src/aiproxy/providers/__init__.py` (empty for now — registry added in Task 12).

Create `src/aiproxy/providers/base.py`:

```python
"""Provider abstraction.

A Provider encapsulates everything that differs between upstream vendors:
URL prefix, auth header injection, and (in Phase 2) usage parsing.
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_providers_base.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/providers/ tests/unit/test_providers_base.py
git commit -m "feat(phase-1): add Provider base class with default model/stream extraction"
```

---

## Task 9: OpenAI provider implementation

**Files:**
- Create: `src/aiproxy/providers/openai.py`
- Create: `tests/unit/test_provider_openai.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_provider_openai.py`:

```python
"""Tests for OpenAIProvider."""
from aiproxy.providers.openai import OpenAIProvider


def test_inject_auth_sets_bearer(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    # Re-instantiate to pick up env
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="sk-test")
    headers = {"content-type": "application/json"}
    out = p.inject_auth(headers)
    assert out["authorization"] == "Bearer sk-test"
    assert out["content-type"] == "application/json"


def test_map_path() -> None:
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    assert p.map_path("chat/completions") == "/v1/chat/completions"
    assert p.map_path("models") == "/v1/models"


def test_name_and_prefix() -> None:
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    assert p.name == "openai"
    assert p.upstream_path_prefix == "/v1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_provider_openai.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the provider**

Create `src/aiproxy/providers/openai.py`:

```python
"""OpenAI provider — Bearer token auth, /v1/ path prefix."""
from __future__ import annotations

from aiproxy.providers.base import Provider


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_provider_openai.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/providers/openai.py tests/unit/test_provider_openai.py
git commit -m "feat(phase-1): add OpenAIProvider"
```

---

## Task 10: Anthropic provider implementation

**Files:**
- Create: `src/aiproxy/providers/anthropic.py`
- Create: `tests/unit/test_provider_anthropic.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_provider_anthropic.py`:

```python
"""Tests for AnthropicProvider."""
from aiproxy.providers.anthropic import AnthropicProvider


def test_inject_auth_sets_x_api_key_and_version() -> None:
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="sk-ant-test")
    headers = {"content-type": "application/json", "authorization": "Bearer leftover"}
    out = p.inject_auth(headers)
    assert out["x-api-key"] == "sk-ant-test"
    assert out["anthropic-version"] == "2023-06-01"
    # Client's bearer token (which was the proxy key) must be removed
    assert "authorization" not in out


def test_map_path_has_empty_prefix() -> None:
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="x")
    # Anthropic's client uses /v1/messages directly, so we pass through as-is
    assert p.map_path("v1/messages") == "/v1/messages"
    assert p.map_path("/v1/messages") == "/v1/messages"


def test_name_and_prefix() -> None:
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="x")
    assert p.name == "anthropic"
    assert p.upstream_path_prefix == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_provider_anthropic.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the provider**

Create `src/aiproxy/providers/anthropic.py`:

```python
"""Anthropic provider — x-api-key auth, no path prefix."""
from __future__ import annotations

from aiproxy.providers.base import Provider

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
        # Client's Authorization was the proxy key; Anthropic doesn't want it
        headers.pop("authorization", None)
        return headers
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_provider_anthropic.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/providers/anthropic.py tests/unit/test_provider_anthropic.py
git commit -m "feat(phase-1): add AnthropicProvider"
```

---

## Task 11: OpenRouter provider implementation

**Files:**
- Create: `src/aiproxy/providers/openrouter.py`
- Create: `tests/unit/test_provider_openrouter.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_provider_openrouter.py`:

```python
"""Tests for OpenRouterProvider."""
from aiproxy.providers.openrouter import OpenRouterProvider


def test_inject_auth_sets_bearer_and_referer() -> None:
    p = OpenRouterProvider(base_url="https://openrouter.ai", api_key="sk-or-test")
    headers = {"content-type": "application/json"}
    out = p.inject_auth(headers)
    assert out["authorization"] == "Bearer sk-or-test"
    assert "http-referer" in out


def test_map_path() -> None:
    p = OpenRouterProvider(base_url="https://openrouter.ai", api_key="x")
    assert p.map_path("chat/completions") == "/api/v1/chat/completions"
    assert p.map_path("models") == "/api/v1/models"


def test_name_and_prefix() -> None:
    p = OpenRouterProvider(base_url="https://openrouter.ai", api_key="x")
    assert p.name == "openrouter"
    assert p.upstream_path_prefix == "/api/v1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_provider_openrouter.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the provider**

Create `src/aiproxy/providers/openrouter.py`:

```python
"""OpenRouter provider — OpenAI-compatible Bearer auth, /api/v1/ prefix."""
from __future__ import annotations

from aiproxy.providers.base import Provider


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_provider_openrouter.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/providers/openrouter.py tests/unit/test_provider_openrouter.py
git commit -m "feat(phase-1): add OpenRouterProvider"
```

---

## Task 12: Provider registry

**Files:**
- Modify: `src/aiproxy/providers/__init__.py`
- Create: `tests/unit/test_provider_registry.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_provider_registry.py`:

```python
"""Tests for provider registry."""
from aiproxy.providers import build_registry
from aiproxy.providers.openai import OpenAIProvider
from aiproxy.providers.anthropic import AnthropicProvider
from aiproxy.providers.openrouter import OpenRouterProvider


def test_build_registry_contains_all_three() -> None:
    reg = build_registry(
        openai_base_url="https://api.openai.com",
        openai_api_key="sk-o",
        anthropic_base_url="https://api.anthropic.com",
        anthropic_api_key="sk-a",
        openrouter_base_url="https://openrouter.ai",
        openrouter_api_key="sk-r",
    )
    assert set(reg.keys()) == {"openai", "anthropic", "openrouter"}
    assert isinstance(reg["openai"], OpenAIProvider)
    assert isinstance(reg["anthropic"], AnthropicProvider)
    assert isinstance(reg["openrouter"], OpenRouterProvider)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_provider_registry.py -v`
Expected: FAIL — `build_registry` not found.

- [ ] **Step 3: Implement the registry**

Replace `src/aiproxy/providers/__init__.py` with:

```python
"""Provider registry."""
from __future__ import annotations

from aiproxy.providers.anthropic import AnthropicProvider
from aiproxy.providers.base import Provider, Usage
from aiproxy.providers.openai import OpenAIProvider
from aiproxy.providers.openrouter import OpenRouterProvider

__all__ = ["Provider", "Usage", "build_registry"]


def build_registry(
    *,
    openai_base_url: str,
    openai_api_key: str,
    anthropic_base_url: str,
    anthropic_api_key: str,
    openrouter_base_url: str,
    openrouter_api_key: str,
) -> dict[str, Provider]:
    return {
        "openai": OpenAIProvider(base_url=openai_base_url, api_key=openai_api_key),
        "anthropic": AnthropicProvider(base_url=anthropic_base_url, api_key=anthropic_api_key),
        "openrouter": OpenRouterProvider(base_url=openrouter_base_url, api_key=openrouter_api_key),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_provider_registry.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/providers/__init__.py tests/unit/test_provider_registry.py
git commit -m "feat(phase-1): add provider registry builder"
```

---

## Task 13: Shared passthrough engine (core of the proxy)

**Files:**
- Create: `src/aiproxy/core/passthrough.py`
- Create: `tests/unit/test_headers.py` (port existing core/headers.py tests)
- Create: `tests/integration/test_proxy_engine.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_proxy_engine.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_proxy_engine.py -v`
Expected: FAIL — `PassthroughEngine` not found.

- [ ] **Step 3: Implement the engine**

Create `src/aiproxy/core/passthrough.py`:

```python
"""Shared passthrough engine used by the provider dispatcher router.

Phase 1 responsibilities:
  - Build upstream request from client request (strip hop-by-hop headers,
    inject provider auth).
  - Stream the upstream response back to the client, yielding each chunk.
  - Persist request metadata at start ('pending') and finish ('done' or 'error').

Phase 1 does NOT:
  - Tee chunks to a dashboard bus.
  - Parse SSE / usage / cost.
  - Persist individual chunks.
"""
from __future__ import annotations

import time
from collections.abc import AsyncIterator

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from aiproxy.core.headers import clean_downstream_headers, clean_upstream_headers
from aiproxy.db.crud import requests as req_crud
from aiproxy.providers.base import Provider


class PassthroughEngine:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        sessionmaker: async_sessionmaker,
    ) -> None:
        self._client = http_client
        self._sessionmaker = sessionmaker

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
        """Forward a client request to the upstream and return (status, headers, stream).

        The caller (FastAPI route) wraps the returned stream in a StreamingResponse.
        All persistence happens inside this method.
        """
        model = provider.extract_model(client_body)
        is_streaming = provider.is_streaming_request(client_body, client_headers)

        # Persist the pending record
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
            raise

        async def stream_and_persist() -> AsyncIterator[bytes]:
            buffer: list[bytes] = []
            try:
                async for chunk in upstream_resp.aiter_raw():
                    buffer.append(chunk)
                    yield chunk
                body_bytes = b"".join(buffer)
                async with self._sessionmaker() as session:
                    await req_crud.mark_finished(
                        session,
                        req_id=req_id,
                        status="done",
                        status_code=upstream_resp.status_code,
                        resp_headers=dict(upstream_resp.headers),
                        resp_body=body_bytes,
                        finished_at=time.time(),
                    )
                    await session.commit()
            except Exception as e:
                async with self._sessionmaker() as session:
                    await req_crud.mark_error(
                        session,
                        req_id=req_id,
                        error_class="stream_interrupted",
                        error_message=str(e),
                        finished_at=time.time(),
                    )
                    await session.commit()
                raise
            finally:
                await upstream_resp.aclose()

        return (
            upstream_resp.status_code,
            clean_downstream_headers(dict(upstream_resp.headers)),
            stream_and_persist(),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_proxy_engine.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/core/passthrough.py tests/integration/test_proxy_engine.py
git commit -m "feat(phase-1): add shared passthrough engine with persistence"
```

---

## Task 14: Proxy dispatcher router

**Files:**
- Create: `src/aiproxy/routers/__init__.py`, `src/aiproxy/routers/proxy.py`
- Create: `tests/integration/test_proxy_router.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_proxy_router.py`:

```python
"""Tests for the proxy dispatcher router via FastAPI TestClient."""
import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport

from aiproxy.auth.proxy_auth import ApiKeyCache
from aiproxy.core.passthrough import PassthroughEngine
from aiproxy.db.crud import api_keys as api_keys_crud
from aiproxy.providers import build_registry
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
    engine = PassthroughEngine(http_client=upstream_client, sessionmaker=db_sessionmaker)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_proxy_router.py -v`
Expected: FAIL — router not found.

- [ ] **Step 3: Implement the router**

Create `src/aiproxy/routers/__init__.py` (empty).

Create `src/aiproxy/routers/proxy.py`:

```python
"""Provider-dispatching proxy router."""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from aiproxy.auth.proxy_auth import ApiKeyCache, verify_header
from aiproxy.core.passthrough import PassthroughEngine
from aiproxy.providers.base import Provider


def create_router(
    *,
    engine: PassthroughEngine,
    providers: dict[str, Provider],
    cache: ApiKeyCache,
) -> APIRouter:
    router = APIRouter()

    @router.api_route(
        "/{provider}/{full_path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    )
    async def dispatch(provider: str, full_path: str, request: Request):
        p = providers.get(provider)
        if p is None:
            raise HTTPException(status_code=404, detail=f"unknown provider: {provider}")

        # Auth
        auth_header = request.headers.get("authorization") or request.headers.get("x-api-key")
        result = await verify_header(auth_header, cache)
        if not result.ok:
            return JSONResponse(
                {"error": "unauthorized", "reason": result.reason},
                status_code=401,
            )

        # Gather client data
        client_headers = dict(request.headers)
        # Strip the proxy key so we don't store it
        client_headers.pop("authorization", None)
        client_headers.pop("x-api-key", None)
        client_headers.pop("cookie", None)

        client_body = await request.body()
        client_ip = request.client.host if request.client else None
        client_ua = request.headers.get("user-agent")
        req_id = uuid.uuid4().hex[:12]

        status_code, resp_headers, stream = await engine.forward(
            provider=p,
            client_path=full_path,
            req_id=req_id,
            method=request.method,
            client_headers=client_headers,
            client_query=list(request.query_params.multi_items()),
            client_body=client_body,
            client_ip=client_ip,
            client_ua=client_ua,
            api_key_id=result.api_key_id,
            started_at=time.time(),
        )

        return StreamingResponse(
            stream,
            status_code=status_code,
            headers=resp_headers,
        )

    return router
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_proxy_router.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/aiproxy/routers/ tests/integration/test_proxy_router.py
git commit -m "feat(phase-1): add provider dispatcher router with auth + streaming"
```

---

## Task 15: App assembly — wire everything into FastAPI lifespan

**Files:**
- Rewrite: `src/aiproxy/app.py`
- Rewrite: `src/aiproxy/__main__.py`
- Create: `tests/integration/test_end_to_end.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_end_to_end.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_end_to_end.py -v`
Expected: FAIL — `build_app` not found.

- [ ] **Step 3: Rewrite `app.py` and `__main__.py`**

Replace `src/aiproxy/app.py`:

```python
"""FastAPI app factory + production lifespan.

Two entry points:
  - `build_app(...)` — synchronous factory for tests. Takes a pre-built
    sessionmaker so tests can inject in-memory SQLite. Stores the created
    http_client on `app.state` so the caller can close it in teardown.
  - `app` (module-level) — production instance. Uses `lifespan()` to
    async-init the DB from settings, then registers the router in-place.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from aiproxy.auth.proxy_auth import ApiKeyCache
from aiproxy.core.passthrough import PassthroughEngine
from aiproxy.db.engine import create_engine_and_sessionmaker
from aiproxy.db.models import Base
from aiproxy.providers import build_registry
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
    """Synchronous factory for tests.

    The caller is responsible for closing `app.state.http_client` on teardown.
    """
    http_client = _make_http_client()
    engine = PassthroughEngine(http_client=http_client, sessionmaker=sessionmaker)
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
    app.include_router(create_router(engine=engine, providers=providers, cache=cache))

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Production lifespan: init DB + http client, register router, dispose on exit."""
    sql_engine, sessionmaker = create_engine_and_sessionmaker(settings.database_url)
    async with sql_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    http_client = _make_http_client()
    engine = PassthroughEngine(http_client=http_client, sessionmaker=sessionmaker)
    providers = build_registry(
        openai_base_url=settings.openai_base_url,
        openai_api_key=settings.openai_api_key,
        anthropic_base_url=settings.anthropic_base_url,
        anthropic_api_key=settings.anthropic_api_key,
        openrouter_base_url=settings.openrouter_base_url,
        openrouter_api_key=settings.openrouter_api_key,
    )
    cache = ApiKeyCache(sessionmaker, ttl_seconds=60)

    # Register the router in-place. FastAPI allows include_router during lifespan
    # before the first request is served.
    app.include_router(create_router(engine=engine, providers=providers, cache=cache))
    app.state.http_client = http_client
    app.state.sessionmaker = sessionmaker

    try:
        yield
    finally:
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

Replace `src/aiproxy/__main__.py`:

```python
"""Run: python -m aiproxy"""
import uvicorn

from aiproxy.settings import settings


def main() -> None:
    uvicorn.run(
        "aiproxy.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_end_to_end.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests from Tasks 2-15 pass.

- [ ] **Step 6: Commit**

```bash
git add src/aiproxy/app.py src/aiproxy/__main__.py tests/integration/test_end_to_end.py
git commit -m "feat(phase-1): wire full app with lifespan and DB bootstrap"
```

---

## Task 16: CLI script to bootstrap the first API key

**Files:**
- Create: `scripts/create_api_key.py`

- [ ] **Step 1: Implement the script**

Create `scripts/create_api_key.py`:

```python
"""Create a new proxy API key and print it.

Usage:
    uv run python scripts/create_api_key.py --name laptop-dev
    uv run python scripts/create_api_key.py --name prod-app --note "backend server"
"""
from __future__ import annotations

import argparse
import asyncio
import secrets
import sys

from aiproxy.db.crud import api_keys as api_keys_crud
from aiproxy.db.engine import create_engine_and_sessionmaker
from aiproxy.db.models import Base
from aiproxy.settings import settings


def generate_key() -> str:
    """Generate a key like sk-aiprx-<32 random url-safe chars>."""
    return "sk-aiprx-" + secrets.token_urlsafe(24)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="Human label for the key")
    parser.add_argument("--note", default=None, help="Optional note")
    args = parser.parse_args()

    engine, sessionmaker = create_engine_and_sessionmaker(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    key = generate_key()
    async with sessionmaker() as session:
        await api_keys_crud.create(
            session,
            key=key,
            name=args.name,
            note=args.note,
            valid_from=None,
            valid_to=None,
        )
        await session.commit()
    await engine.dispose()

    print(f"Created API key '{args.name}':")
    print(f"  {key}")
    print()
    print("Use it as: Authorization: Bearer <key>")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 2: Smoke test the script**

```bash
# First ensure you have a .env with real (or dummy) provider keys
cp .env.example .env   # if not already done
# Edit .env to add at least PROXY_MASTER_KEY, SESSION_SECRET, and any placeholder provider keys
uv run python scripts/create_api_key.py --name smoke-test
```

Expected output: a printed `sk-aiprx-...` key.

Verify it landed in the DB:

```bash
uv run python -c "
import asyncio
from sqlalchemy import select
from aiproxy.db.crud import api_keys
from aiproxy.db.engine import create_engine_and_sessionmaker
from aiproxy.db.models import Base
from aiproxy.settings import settings

async def main():
    engine, sm = create_engine_and_sessionmaker(settings.database_url)
    async with sm() as s:
        rows = await api_keys.list_all(s)
        for r in rows:
            print(r.name, r.key)
    await engine.dispose()

asyncio.run(main())
"
```

Expected: `smoke-test sk-aiprx-...`

- [ ] **Step 3: Commit**

```bash
git add scripts/create_api_key.py
git commit -m "feat(phase-1): add create_api_key CLI script"
```

---

## Task 17: Per-provider smoke test scripts

**Files:**
- Create: `scripts/test_openai.py`, `scripts/test_anthropic.py`, `scripts/test_openrouter.py`, `scripts/test_all.py`

- [ ] **Step 1: Create the OpenAI smoke test**

Create `scripts/test_openai.py`:

```python
"""Smoke test: call OpenAI through the proxy.

Usage:
    # In one terminal:
    uv run python -m aiproxy
    # In another:
    uv run python scripts/test_openai.py --key sk-aiprx-xxx
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import httpx


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--proxy", default="http://127.0.0.1:8000")
    p.add_argument("--key", required=True, help="Proxy API key (sk-aiprx-...)")
    p.add_argument("--model", default="gpt-4o-mini")
    p.add_argument("--prompt", default="Say 'hello from openai' in one short sentence.")
    p.add_argument("--stream", action="store_true")
    args = p.parse_args()

    url = f"{args.proxy}/openai/chat/completions"
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": args.prompt}],
        "stream": args.stream,
    }
    headers = {"Authorization": f"Bearer {args.key}", "Content-Type": "application/json"}

    t0 = time.time()
    with httpx.Client(timeout=60.0) as client:
        if args.stream:
            first = None
            text = ""
            with client.stream("POST", url, json=payload, headers=headers) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = obj.get("choices", [{}])[0].get("delta", {}).get("content")
                    if delta:
                        if first is None:
                            first = time.time()
                        text += delta
                        sys.stdout.write(delta)
                        sys.stdout.flush()
            print()
            print(f"[openai] streaming ttft={first - t0:.2f}s total={time.time() - t0:.2f}s chars={len(text)}")
        else:
            r = client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            print(content)
            print(f"[openai] non-stream total={time.time() - t0:.2f}s chars={len(content)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Create the Anthropic smoke test**

Create `scripts/test_anthropic.py`:

```python
"""Smoke test: call Anthropic through the proxy."""
from __future__ import annotations

import argparse
import json
import sys
import time

import httpx


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--proxy", default="http://127.0.0.1:8000")
    p.add_argument("--key", required=True)
    p.add_argument("--model", default="claude-haiku-4-5-20251001")
    p.add_argument("--prompt", default="Say 'hello from anthropic' in one short sentence.")
    p.add_argument("--stream", action="store_true")
    args = p.parse_args()

    url = f"{args.proxy}/anthropic/v1/messages"
    payload = {
        "model": args.model,
        "max_tokens": 128,
        "messages": [{"role": "user", "content": args.prompt}],
        "stream": args.stream,
    }
    headers = {"Authorization": f"Bearer {args.key}", "Content-Type": "application/json"}

    t0 = time.time()
    with httpx.Client(timeout=60.0) as client:
        if args.stream:
            first = None
            text = ""
            with client.stream("POST", url, json=payload, headers=headers) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    # Anthropic streaming events
                    if obj.get("type") == "content_block_delta":
                        piece = obj.get("delta", {}).get("text", "")
                        if piece:
                            if first is None:
                                first = time.time()
                            text += piece
                            sys.stdout.write(piece)
                            sys.stdout.flush()
            print()
            print(f"[anthropic] streaming ttft={first - t0:.2f}s total={time.time() - t0:.2f}s chars={len(text)}")
        else:
            r = client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
            content = data["content"][0]["text"]
            print(content)
            print(f"[anthropic] non-stream total={time.time() - t0:.2f}s chars={len(content)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Create the OpenRouter smoke test**

Create `scripts/test_openrouter.py`:

```python
"""Smoke test: call OpenRouter through the proxy."""
from __future__ import annotations

import argparse
import json
import sys
import time

import httpx


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--proxy", default="http://127.0.0.1:8000")
    p.add_argument("--key", required=True)
    p.add_argument("--model", default="openai/gpt-4o-mini")
    p.add_argument("--prompt", default="Say 'hello from openrouter' in one short sentence.")
    p.add_argument("--stream", action="store_true")
    args = p.parse_args()

    url = f"{args.proxy}/openrouter/chat/completions"
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": args.prompt}],
        "stream": args.stream,
    }
    headers = {"Authorization": f"Bearer {args.key}", "Content-Type": "application/json"}

    t0 = time.time()
    with httpx.Client(timeout=60.0) as client:
        if args.stream:
            first = None
            text = ""
            with client.stream("POST", url, json=payload, headers=headers) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = obj.get("choices", [{}])[0].get("delta", {}).get("content")
                    if delta:
                        if first is None:
                            first = time.time()
                        text += delta
                        sys.stdout.write(delta)
                        sys.stdout.flush()
            print()
            print(f"[openrouter] streaming ttft={first - t0:.2f}s total={time.time() - t0:.2f}s chars={len(text)}")
        else:
            r = client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            print(content)
            print(f"[openrouter] non-stream total={time.time() - t0:.2f}s chars={len(content)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Create the all-in-one script**

Create `scripts/test_all.py`:

```python
"""Run all three provider smoke tests in sequence.

Usage:
    uv run python scripts/test_all.py --key sk-aiprx-xxx [--stream]
"""
from __future__ import annotations

import argparse
import subprocess
import sys


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--proxy", default="http://127.0.0.1:8000")
    p.add_argument("--key", required=True)
    p.add_argument("--stream", action="store_true")
    args = p.parse_args()

    scripts = [
        ("OpenAI",     ["python", "scripts/test_openai.py"]),
        ("Anthropic",  ["python", "scripts/test_anthropic.py"]),
        ("OpenRouter", ["python", "scripts/test_openrouter.py"]),
    ]
    results: list[tuple[str, int]] = []
    for label, cmd in scripts:
        print(f"\n========== {label} ==========")
        full = cmd + ["--proxy", args.proxy, "--key", args.key]
        if args.stream:
            full.append("--stream")
        rc = subprocess.call(full)
        results.append((label, rc))

    print("\n========== Summary ==========")
    for label, rc in results:
        status = "✓" if rc == 0 else f"✗ (rc={rc})"
        print(f"  {status} {label}")
    return 0 if all(rc == 0 for _, rc in results) else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Commit**

```bash
git add scripts/
git commit -m "feat(phase-1): add per-provider smoke test scripts"
```

---

## Task 18: Port headers.py tests (verify they still pass)

**Files:**
- Create: `tests/unit/test_headers.py`

This is a formal unit test for the existing `core/headers.py` which was carried over from v0.

- [ ] **Step 1: Write the tests**

Create `tests/unit/test_headers.py`:

```python
"""Tests for aiproxy.core.headers hop-by-hop stripping."""
from aiproxy.core.headers import clean_downstream_headers, clean_upstream_headers


def test_strips_hop_by_hop_headers() -> None:
    src = {
        "connection": "keep-alive",
        "host": "example.com",
        "content-length": "100",
        "content-type": "application/json",
        "authorization": "Bearer abc",
        "transfer-encoding": "chunked",
        "keep-alive": "timeout=5",
        "te": "trailers",
        "upgrade": "h2c",
        "proxy-authenticate": "Basic",
        "proxy-authorization": "Basic xyz",
        "trailers": "expires",
    }
    out = clean_upstream_headers(src)
    assert "connection" not in out
    assert "host" not in out
    assert "content-length" not in out
    assert "transfer-encoding" not in out
    assert "keep-alive" not in out
    assert "te" not in out
    assert "upgrade" not in out
    assert "proxy-authenticate" not in out
    assert "proxy-authorization" not in out
    assert "trailers" not in out
    # Non-hop-by-hop headers are kept
    assert out["content-type"] == "application/json"
    assert out["authorization"] == "Bearer abc"


def test_clean_downstream_same_logic() -> None:
    src = {"connection": "close", "x-custom": "ok"}
    out = clean_downstream_headers(src)
    assert "connection" not in out
    assert out["x-custom"] == "ok"


def test_case_insensitive() -> None:
    src = {"Connection": "close", "CONTENT-LENGTH": "10", "X-Foo": "bar"}
    out = clean_upstream_headers(src)
    assert "Connection" not in out
    assert "CONTENT-LENGTH" not in out
    assert out["X-Foo"] == "bar"
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/unit/test_headers.py -v`
Expected: PASS (3 tests).

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_headers.py
git commit -m "test(phase-1): add unit tests for core.headers hop-by-hop stripping"
```

---

## Task 19: Final Phase 1 verification

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 2: Run coverage report**

Run: `uv run pytest --cov=src/aiproxy --cov-report=term-missing`
Expected: `core/`, `providers/`, `db/` should be at ≥ 85%.

If coverage is below target, look at what's uncovered and add focused tests. Otherwise proceed.

- [ ] **Step 3: Live smoke test (requires real provider keys)**

```bash
# Make sure .env has real OPENAI_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY
# (Use the master key from the v0 tests.)

# Start the server:
uv run python -m aiproxy &
SERVER_PID=$!
sleep 2

# Create a proxy API key:
PROXY_KEY=$(uv run python scripts/create_api_key.py --name phase-1-verify 2>&1 | grep sk-aiprx | awk '{print $1}')
echo "Created key: $PROXY_KEY"

# Run all three provider smoke tests (non-streaming first):
uv run python scripts/test_all.py --key "$PROXY_KEY"

# Run all three provider smoke tests (streaming):
uv run python scripts/test_all.py --key "$PROXY_KEY" --stream

# Stop the server:
kill $SERVER_PID
```

Expected output: All three providers respond; summary shows `✓ OpenAI`, `✓ Anthropic`, `✓ OpenRouter`.

- [ ] **Step 4: Verify DB persistence**

```bash
uv run python -c "
import asyncio
from sqlalchemy import select, func
from aiproxy.db.engine import create_engine_and_sessionmaker
from aiproxy.db.models import Request
from aiproxy.settings import settings

async def main():
    engine, sm = create_engine_and_sessionmaker(settings.database_url)
    async with sm() as s:
        count = await s.execute(select(func.count()).select_from(Request))
        print('total requests:', count.scalar())
        result = await s.execute(select(Request.provider, Request.status, Request.status_code, Request.model).order_by(Request.started_at.desc()).limit(10))
        for row in result.all():
            print(row)
    await engine.dispose()

asyncio.run(main())
"
```

Expected: at least 6 rows (3 providers x non-streaming + streaming), all with `status='done'` and `status_code=200`.

- [ ] **Step 5: Phase 1 checkpoint commit**

```bash
git commit --allow-empty -m "chore(phase-1): Phase 1 foundation complete

Verified:
- All unit + integration tests pass
- Coverage targets met (core/providers/db ≥ 85%)
- Live end-to-end smoke test passed for OpenAI, Anthropic, OpenRouter
  (both streaming and non-streaming)
- Requests persisted to SQLite with correct provider/model/status/codes

Ready for Phase 2: chunk-level persistence + dashboard tee + SSE parsing
+ usage extraction + cost computation + pricing versioning."
```

---

## Done checklist

After Task 19 passes, Phase 1 is complete. You should have:

- [x] `src/aiproxy/` reorganized per spec §10 layout (only Phase 1 slices)
- [x] Settings module supporting all three providers + proxy self-auth + DB URL
- [x] SQLAlchemy async engine + session factory + all 5 tables created on startup
- [x] CRUD for `api_keys` (create, lookup, list, update_last_used, set_active)
- [x] CRUD for `requests` (create_pending, mark_finished, mark_error, get_by_id)
- [x] Proxy auth middleware with 60s in-memory cache
- [x] `Provider` ABC + `OpenAIProvider`, `AnthropicProvider`, `OpenRouterProvider`
- [x] Provider registry builder
- [x] Shared `PassthroughEngine.forward()` with start/done/error persistence
- [x] Dispatcher router `/{provider}/{path}` with auth gate
- [x] FastAPI app factory + production lifespan
- [x] `scripts/create_api_key.py` CLI bootstrap
- [x] `scripts/test_openai.py`, `test_anthropic.py`, `test_openrouter.py`, `test_all.py`
- [x] Unit tests for settings, db models, CRUD, providers, headers
- [x] Integration tests for auth, engine, router, full app
- [x] Real-provider smoke test passing for all three providers
- [x] `requests` table populated by real requests
