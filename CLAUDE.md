# CLAUDE.md

This file gives Claude Code (and other LLM-based collaborators) the project context, conventions, and current status needed to make good edits without re-deriving everything from scratch.

## What this is

**ai-proxy** is a self-hosted AI API proxy with a **live-streaming observability dashboard**. It sits between a client (curl, SDK, LLM agent) and one or more upstream providers (OpenAI, Anthropic, OpenRouter), passing requests through unchanged while simultaneously persisting every request/response and tee-ing streaming chunks to a dashboard in real time.

Unlike LiteLLM / Helicone / Langfuse / Phoenix, which log requests after completion, ai-proxy streams chunks to the dashboard **while the model is still generating**, and the dashboard can replay any past streaming request at variable speed as if it were live.

### Why it exists

- **Observe streams as they happen.** Watch tokens land live, not after the fact.
- **Exact passthrough.** No provider-format translation — the upstream response bytes reach the client untouched. The client talks to "openai" / "anthropic" / "openrouter" without knowing a proxy is in the middle.
- **Single-binary, local-first.** SQLite + FastAPI + vanilla JS dashboard, no Redis/Postgres/Kafka.
- **Time-indexed chunk storage.** Every streaming chunk is stored with its monotonic `offset_ns`, so the replay player is byte-exact.

## Architecture

```
┌──────────┐     ┌─────────────────────────────────────────┐     ┌─────────────┐
│  Client  │ ──► │   ai-proxy (FastAPI)                    │ ──► │  Upstream   │
│ (curl,   │ ◄── │                                         │ ◄── │  (OpenAI,   │
│  SDK)    │     │   Dispatcher → Provider → Passthrough   │     │  Anthropic, │
└──────────┘     │                  │                      │     │  OpenRouter)│
                 │                  ├─► SQLite (async)     │     └─────────────┘
                 │                  ├─► StreamBus (pub/sub)│
                 │                  └─► Registry (in-mem)  │
                 │                         │               │
                 │                         ▼               │
                 │              ┌──────────────────────┐   │
                 │              │  Dashboard (FastAPI  │   │
                 │              │  + WS + vanilla JS)  │   │
                 │              └──────────────────────┘   │
                 └─────────────────────────────────────────┘
```

### Request lifecycle (streaming)

1. Client → `POST /openai/chat/completions` (or `/anthropic/v1/messages`, `/openrouter/chat/completions`)
2. **Proxy auth** (`auth/proxy_auth.py`): client key checked against SQLite `api_keys` table (TTL cache)
3. **Dispatcher** (`routers/proxy.py`) picks a `Provider` by URL prefix
4. **PassthroughEngine** (`core/passthrough.py`):
   - Persists a pending `requests` row
   - Registers in the in-memory `RequestRegistry` → emits `start` on the `ACTIVE_CHANNEL` bus
   - Opens the upstream request with `httpx.AsyncClient(stream=True)`
   - Wraps the upstream body iterator in a generator that, for each raw chunk:
     - Buffers it as `(seq, offset_ns, data)` in-memory
     - If `bus.has_subscribers(req_id)`: runs it through the provider's stateful `ChunkParser`, base64-encodes the raw bytes, and publishes a `chunk` event (enriched with `text_delta` + parsed `events`) to the bus
     - Yields it downstream to the client
5. On generator exit (done / cancel / error), `_finalize()` runs under `asyncio.shield`:
   - Batch-inserts chunks into SQLite
   - Parses provider usage from full body or chunk list
   - Computes cost via the versioned `pricing` table
   - Snapshots tokens + cost into the `requests` row
   - Updates the registry → emits `finish`
6. Dashboard subscribers of `/dashboard/ws/stream/{req_id}` see the stream in real time; historical requests can be replayed later via `/dashboard/api/requests/{req_id}/replay`.

### Critical design decisions (read these before editing)

- **Stateful SSE parsing.** Upstream TCP chunks do **not** align with SSE event boundaries — a single `data: {...}\n\n` frame often spans 2+ raw chunks. Each provider returns a `ChunkParser` from `make_chunk_parser()` that buffers partial frames across `feed()` calls. Text from an event that *finishes* in chunk N is attributed to chunk N (not where it started). **Never** add a stateless "extract text from this chunk" helper — that was the bug fixed by `2641a2d` in Phase 4.
- **Short-circuit on `has_subscribers()`.** The passthrough hot path only instantiates a parser and base64-encodes chunks **if** someone is subscribed to that request's WS channel. Zero dashboard cost when nobody is watching.
- **`asyncio.shield` on finalize.** Client disconnect cancels the stream generator, but the `finally` block wraps `_finalize()` in `asyncio.shield` so DB writes always complete. Whatever chunks arrived before the cancel are persisted with `status="canceled"`.
- **Dashboard router registered before the proxy dispatcher.** Proxy uses `/{provider}/{full_path:path}`, which would swallow `/dashboard/*` as "unknown provider: dashboard". Order matters in `app.py`.
- **Route ordering inside the dashboard router.** `/api/requests/export` and similar literal paths must be declared **before** `/api/requests/{req_id}`, otherwise FastAPI matches `req_id="export"` and returns 404 from `get_by_id`.
- **VACUUM cannot run inside a transaction.** The vacuum endpoint pulls the AsyncEngine from `sessionmaker.kw["bind"]` and opens a dedicated AUTOCOMMIT connection. Do not re-implement this via a regular `AsyncSession` — it will silently wrap in BEGIN and fail.
- **OpenRouter path convention.** The client sends to `/openrouter/chat/completions`; the provider's `upstream_path_prefix = "/api/v1"` is auto-prepended. Do **not** send to `/openrouter/api/v1/chat/completions` — that double-prefixes and 404s.
- **Auto-injection of `stream_options.include_usage=true`.** OpenAI's streaming responses only return token usage when the client sets `stream_options.include_usage=true`. To make streaming requests billable by default, `Provider.rewrite_request_body(body, is_streaming)` is called in `PassthroughEngine.forward` *before* persist + forward. The OpenAI + OpenRouter implementations inject the flag only if the client omitted it entirely — explicit client intent (`true` or `false`) is respected unchanged. The rewritten bytes are what we persist *and* forward, so dashboard replay reflects the real upstream request. Anthropic doesn't need this (it always returns `usage` in `message_start` / `message_delta`).

## Project layout

```
src/aiproxy/
├── __main__.py             uvicorn entry (python -m aiproxy)
├── app.py                  FastAPI factory + production lifespan
├── settings.py             pydantic-settings from .env
├── bus.py                  StreamBus — bounded-queue in-memory pub/sub
├── registry.py             RequestRegistry — active + recent meta + lifecycle events
│
├── auth/
│   ├── proxy_auth.py       Client API-key validation (TTL cache over api_keys)
│   └── dashboard_auth.py   Master-key login → signed JWT session cookie
│
├── core/
│   ├── headers.py          Hop-by-hop header stripping (upstream + downstream)
│   └── passthrough.py      PassthroughEngine: forward → tee → persist → finalize
│
├── providers/
│   ├── base.py             Provider ABC + ChunkParser ABC + _iter_complete_sse_events
│   ├── openai.py           OpenAI Bearer auth, JSON usage, OpenAIChunkParser
│   ├── anthropic.py        x-api-key + anthropic-version, SSE with `event:` lines
│   └── openrouter.py       OpenAI-compatible, reuses OpenAIChunkParser
│
├── routers/
│   └── proxy.py            /{provider}/{path:path} dispatcher
│
├── db/
│   ├── engine.py           create_async_engine + sessionmaker
│   ├── models.py           SQLAlchemy 2.x async models
│   ├── fts.py              SQLite FTS5 virtual table for body/response search
│   ├── retention.py        Background loop that strips old bodies
│   └── crud/
│       ├── requests.py     list_with_filters, create_pending, mark_finished, mark_error
│       ├── chunks.py       insert_batch, list_for_request
│       ├── api_keys.py     CRUD + TTL-cache loader
│       ├── pricing.py      upsert_current (auto-closes prior effective row)
│       └── config.py       runtime key/value config
│
├── pricing/
│   ├── compute.py          compute_cost(provider, model, usage) → (pricing_id, cost_usd)
│   └── seed.py             Bootstrap pricing rows on first run
│
└── dashboard/
    ├── routes.py           All HTTP + WS endpoints (the monolith of the dashboard)
    └── static/index.html   Single-file vanilla JS SPA (~2300 lines)

tests/
├── unit/                   Pure unit tests (no IO, no sessionmaker)
└── integration/            FastAPI TestClient + in-memory SQLite + respx

docs/superpowers/
├── specs/2026-04-10-ai-proxy-with-live-dashboard-design.md   Design spec
└── plans/
    ├── 2026-04-10-phase-1-foundation.md
    ├── 2026-04-10-phase-2-persistence-dashboard.md
    ├── 2026-04-11-phase-3-rich-dashboard.md
    ├── 2026-04-11-phase-4-replay-player.md
    └── 2026-04-11-phase-5-polish.md

scripts/
├── create_api_key.py       Bootstrap a client key into the DB
├── test_openai.py          Manual real-upstream smoke (non-CI)
├── test_anthropic.py       Manual real-upstream smoke (non-CI)
├── test_openrouter.py      Manual real-upstream smoke (non-CI)
└── test_all.py             Runs all three, prints TTFT + cost per provider
```

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Runtime | Python 3.11+, uvicorn | async FastAPI + httpx stream forwarding |
| Web | FastAPI + Starlette WebSockets | async routing, native WS, zero boilerplate |
| HTTP client | httpx.AsyncClient with `aiter_raw()` | streams bytes without decoding SSE |
| DB | SQLite + aiosqlite via SQLAlchemy 2.x async | single-file, zero-ops, FTS5 built-in |
| Search | SQLite FTS5 virtual table over request/response bodies | |
| Auth | Master key → JWT cookie (PyJWT); per-client API keys in SQLite | |
| Frontend | Vanilla JS + SVG in a single `index.html` | no framework, no build step, dead-simple deploy |
| Packaging | [uv](https://github.com/astral-sh/uv) + hatchling | fast installs, reproducible |
| Testing | pytest + pytest-asyncio + httpx.ASGITransport + respx | fully in-process, no live upstream |

## Providers

A `Provider` (`src/aiproxy/providers/base.py`) is an ABC with:

- `name: str` — URL prefix (`openai`, `anthropic`, `openrouter`)
- `upstream_path_prefix: str` — auto-prepended to the client's remaining path
- `inject_auth(headers)` — swap the client's key for the upstream key
- `map_path(client_path)` → upstream URL path
- `extract_model(body)` → `str | None` — best-effort model name from request body
- `is_streaming_request(body, headers)` → `bool`
- `parse_usage(is_streaming, resp_body, chunks)` → `Usage | None`
- `make_chunk_parser()` → `ChunkParser` — **stateful**, one per request

Adding a new provider: subclass `Provider`, implement the above, register it in `providers/__init__.py:build_registry()`.

## Database schema (SQLAlchemy 2.x, async)

| Table | Purpose |
|---|---|
| `requests` | one row per request — pending/streaming/done/error/canceled, full headers + bodies, token counts, cost, pricing snapshot |
| `chunks` | `(req_id, seq, offset_ns, size, data)` — one row per streaming chunk |
| `api_keys` | client keys — name, active flag, valid_from/to, last_used, usage_count |
| `pricing` | versioned provider/model pricing rows with effective_from / effective_to |
| `config` | runtime key/value (e.g. `retention.full_count`) |
| `requests_fts` | FTS5 virtual table over request body + response body text |

## Dashboard endpoints

### Auth
- `POST /dashboard/login` — master key → session cookie
- `POST /dashboard/logout`

### Reads
- `GET /dashboard/api/active` — snapshot of active + recent requests (registry)
- `GET /dashboard/api/requests` — paginated, filter + FTS search
- `GET /dashboard/api/requests/export` — **same filters, no pagination**, cap 10 000
- `GET /dashboard/api/requests/{req_id}` — full detail + chunks
- `GET /dashboard/api/requests/{req_id}/replay` — parsed chunks for the Replay Player
- `GET /dashboard/api/timeline` — lane-grouped requests in a time window (`group_by=model|provider|api_key_id`)
- `GET /dashboard/api/stats/db` — DB size + row counts
- `GET /dashboard/api/keys` — list client keys
- `GET /dashboard/api/pricing` — list pricing versions
- `GET /dashboard/api/config` — runtime config

### Writes
- `POST /dashboard/api/keys` / `PATCH /dashboard/api/keys/{id}`
- `POST /dashboard/api/pricing`
- `POST /dashboard/api/config`
- `POST /dashboard/api/batch/strip-binaries` — replace base64 blobs with placeholders
- `POST /dashboard/api/batch/vacuum` — SQLite `VACUUM`, returns before/after sizes

### WebSockets
- `WS /dashboard/ws/active` — lifecycle events for the live list
- `WS /dashboard/ws/stream/{req_id}` — per-chunk events for the Overview live panel + Replay live-follow

## Dashboard UI (`static/index.html`)

Single vanilla-JS SPA. Top-level tabs: **Requests · Timeline · Keys · Pricing · Settings**.

- **Requests** — two-pane list + detail. Detail sub-tabs: Overview · Request · Response · Chunks · Replay (last one hidden for non-streaming).
- **Replay Player** — two modes: media-player UI (scrubber with per-chunk ticks, 0.5×/1×/2×/4×/∞ speeds, dim/typewriter views, TTFT jump, live-follow) and JSON Log (filterable chunk table with expand, export, jump-to-player).
- **Timeline** — SVG lane chart grouped by model/provider/api_key, 5/15/60/360-minute windows, 3 s live polling, hover tooltip, click-to-navigate.
- **Settings** — Retention, Batch operations (strip binaries), Database (size/counts + Vacuum).

## Implementation phases — all complete

| Phase | Scope | Status |
|---|---|---|
| 1 | Passthrough foundation: provider ABC, dispatcher, StreamBus, registry, client-key auth | ✅ |
| 2 | Persistence + basic dashboard: SQLite models, chunk storage, pricing, FTS, WS, retention loop | ✅ |
| 3 | Rich dashboard UX: keys/pricing/config UIs, filters + FTS search, batch strip-binaries | ✅ |
| 4 | Replay Player: `/replay` endpoint, Player + JSON Log modes, live-follow, stateful SSE parser | ✅ |
| 5 | Polish: Timeline tab, JSON export, Vacuum + DB stats, Overview error banner | ✅ |

All phases merged to `main`. 173 tests passing, overall coverage ≈ 90%.

**Explicitly deferred** from Phase 5: the spec's optional "retry this request" button. Retry would introduce a second class of request (replayed vs. original) with unclear semantics around headers, auth rewriting, and timeline annotation. Revisit in a future phase if needed.

## How to run

```bash
cp .env.example .env              # fill in OPENAI_API_KEY / ANTHROPIC_API_KEY / OPENROUTER_API_KEY
                                  # set PROXY_MASTER_KEY + SESSION_SECRET to random strings
uv sync
uv run python -m aiproxy          # listens on 127.0.0.1:8000

# first run only — create a client API key
uv run python scripts/create_api_key.py --name "my-cli"
```

Dashboard: http://127.0.0.1:8000/dashboard/ — log in with `PROXY_MASTER_KEY`.

## Manual smoke tests

```bash
# OpenAI (streaming)
curl -N -H "Authorization: Bearer $CLIENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Write a haiku"}],"stream":true}' \
  http://127.0.0.1:8000/openai/chat/completions

# Anthropic (streaming)
curl -N -H "Authorization: Bearer $CLIENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku-4-5","max_tokens":256,"messages":[{"role":"user","content":"Write a haiku"}],"stream":true}' \
  http://127.0.0.1:8000/anthropic/v1/messages

# OpenRouter (streaming) — path does NOT include /api/v1; provider auto-prepends it
curl -N -H "Authorization: Bearer $CLIENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"openai/gpt-4o-mini","messages":[{"role":"user","content":"Write a haiku"}],"stream":true}' \
  http://127.0.0.1:8000/openrouter/chat/completions
```

Or run the bundled scripts:

```bash
uv run python scripts/test_all.py   # hits all 3 providers, prints TTFT + cost
```

To see live replay: while a stream is in flight, open the Requests tab, click the live row, switch to the **Replay** detail tab — it'll join live-follow mode automatically.

## Tests

```bash
uv run pytest -q                                      # 173 tests, ~1.8 s
uv run pytest --cov=src/aiproxy --cov-report=term     # coverage report
uv run pytest tests/unit/ -v                          # unit only
uv run pytest tests/integration/ -v                   # integration only
```

Test conventions:
- **Unit tests** (`tests/unit/`) — no IO, no sessionmaker. Pure logic: parsers, pricing, bus, headers.
- **Integration tests** (`tests/integration/`) — inline fixtures creating an in-memory SQLite + `FastAPI TestClient` via `httpx.ASGITransport`. Each test file defines its own `app_ctx` fixture (project convention — not shared, so tests stay explicit about what they seed).
- **Manual smoke** (`scripts/test_*.py`) — real upstream calls, not in CI.

## Conventions / things to preserve

- **No framework on the frontend.** Vanilla JS, single file, inline CSS. Don't introduce React/Vue/etc.
- **TDD where applicable.** Backend endpoints get a failing integration test first, then the implementation. Frontend changes get manual verification.
- **Stateless at module scope, stateful per request.** Parsers, buffers, registry entries are all keyed by `req_id`.
- **Error classes are meaningful.** See spec §7 — `upstream_http` is **not** an error (it's a business signal with the real upstream status code). Only `upstream_connect`, `upstream_timeout`, `stream_interrupted`, and `proxy_internal` set `error_class`.
- **Client disconnects are `canceled`, not errors.** Gray dot in the UI.
- **Auth failures are not persisted** (spec §7.1, avoid scanner pollution).
- **Commit style.** Conventional commits: `feat(phase-N): ...`, `fix(phase-N): ...`, `chore: ...`, `docs: ...`. Phase labels are historical; ongoing work can drop them.
- **`.coverage` is gitignored.** Don't commit it.

## Known tech debt / open questions

- **No rate limiting per client key.** Each key has unlimited throughput; add if/when needed.
- **No retry endpoint.** Deferred from Phase 5.
- **Retention loop is time-based only** (strip bodies for requests older than the `full_count`-th most recent). No size-based cap.
- **Pricing seed is static.** Admin has to add new models via the Pricing tab.
- **Single-node only.** `StreamBus` is in-memory, so horizontal scaling would need Redis/NATS.
- **SQLite WAL not explicitly configured.** Default journal mode; if write throughput becomes an issue, enable WAL in `db/engine.py`.

## Useful file pointers

- Passthrough hot path — `src/aiproxy/core/passthrough.py:255` (`stream_and_persist`)
- Dashboard router factory — `src/aiproxy/dashboard/routes.py:109`
- Provider ABC — `src/aiproxy/providers/base.py`
- Chunk parser implementations — `src/aiproxy/providers/openai.py`, `.../anthropic.py`
- Frontend entry — `src/aiproxy/dashboard/static/index.html`
- Design spec — `docs/superpowers/specs/2026-04-10-ai-proxy-with-live-dashboard-design.md`
