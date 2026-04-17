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
- **`X-AIProxy-*` private header namespace.** Clients can attach classification metadata to any proxied request with `X-AIProxy-Labels: prod,v1,regression` (normalized: trimmed, deduped, order preserved) and `X-AIProxy-Note: free-form text`. The proxy reads these into the `requests.labels` / `requests.note` columns and then strips them via `clean_upstream_headers` so the upstream never sees them. Labels are filterable via `/api/requests?label=prod&label=v1` (intersection semantics — row must contain *all* passed labels). Stored as a comma-joined string; matched via the 4-pattern LIKE trick with wildcard escaping. Adding a new header under this namespace only requires a new branch in `PassthroughEngine.forward`; the stripping rule already covers the whole prefix.
- **Additive column migrations via `db/migrate.py`.** We don't use Alembic. For additive schema changes (new nullable columns), `ensure_requests_columns` runs `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` on startup — idempotent, safe to call every boot. Anything needing a table rewrite (drop column, change type, add NOT NULL without default) should NOT go through this helper.
- **LiteLLM pricing catalog refresh** (`pricing/catalog.py`). Rather than manually curate `pricing_seed.json` when a new model lands, the Pricing tab has a "Refresh from LiteLLM catalog" button that pulls `model_prices_and_context_window.json` from the upstream BerriAI/litellm repo, normalizes the 2600+ entries down to our three providers (OpenAI / Anthropic / OpenRouter chat models only — Azure, Bedrock, fine-tunes, embeddings, image gen all filtered out), and diff-previews the changes (would_add / would_supersede / unchanged) before writing. OpenRouter entries have the `openrouter/` key prefix stripped so the model matches what clients send in `body.model`. Apply uses the existing `upsert_current`, which closes the prior effective row and inserts a new one — full versioned history is preserved.
- **Request trait detection** (`core/traits.py`). At forward time, `detect_request_traits(req_body)` scans the JSON body once and returns three booleans — `request_has_image`, `request_has_file`, `response_is_json` — stored as nullable `Integer` columns on the `requests` row. Detection covers: OpenAI `image_url`/`input_image`/`file`/`input_file`, Anthropic `image`/`document`, the responses API `input` field, OpenAI/OpenRouter `response_format.{json_object,json_schema}`, and OpenAI/Anthropic `tools` (non-empty tools array implies structured JSON output). The dashboard list renders these as colored trait-icons (stream / image / file / json) slotted after each row's request id. **Version bump → full rescan**: `DETECTOR_VERSION` is compared against `config["traits.detector_version"]` on startup; if they differ, every row's flags are re-computed via `backfill_request_traits(rescan_all=True)` and the version is bumped. Otherwise only NULL-flagged rows are processed. Bump `DETECTOR_VERSION` in `core/traits.py` whenever you add or change a detection rule.
- **Design-token CSS system** (`dashboard/static/index.html`). The `<style>` block defines a ~40-token palette in `:root` split into size/spacing/motion/radii tokens (theme-agnostic) and color tokens duplicated under `:root,[data-theme="dark"]` and `:root[data-theme="light"]`. Every component rule reads from tokens — no inline hex colors except a couple of `rgba()` shadow edge-cases. The three-state theme switch (light / dark / system) lives in the top nav; it persists preference to `localStorage["aiproxy.theme"]`, resolves "system" via `matchMedia("(prefers-color-scheme: dark)")`, and applies the resolved value via a `<head>`-inline pre-script to avoid FOUC on first paint.
- **List pane UX**: infinite scroll via `IntersectionObserver` (sentinel `#req-list-sentinel` re-observed after each render, `rootMargin: 200px`, `inflightLoadMore` lock prevents concurrent fetches), and a draggable `#req-list-resizer` between list and detail that persists width to `localStorage["aiproxy.listPaneWidth"]` (clamped to [240, 720]). The "Load more" button is gone.
- **SQLite pragmas on every connect** (`db/engine.py`). A sqlite-only `event.listen(engine.sync_engine, "connect", ...)` applies `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=10000`, `foreign_keys=ON` per-connection. WAL is a DB-file setting (persisted); the other three are per-connection and must re-apply. No-op on `:memory:` DBs so the test suite is unaffected.
- **Prometheus metrics** (`src/aiproxy/metrics.py`). Seven metric families: `aiproxy_requests_total` / `aiproxy_request_duration_seconds` / `aiproxy_ttft_seconds` / `aiproxy_tokens_total` / `aiproxy_cost_usd_total` / `aiproxy_active_requests` / `aiproxy_errors_total`. Emission is centralized in `record_completion()` called from `_finalize` and from the two pre-stream error paths (`upstream_connect` / `upstream_timeout`). Active gauge inc/dec lives in `RequestRegistry.start/finish`. `GET /metrics` returns Prometheus text format, unauthenticated — meant for internal-network scrapes. **Never** label metrics with `X-AIProxy-Labels` values or `api_key_id`: user-supplied labels are unbounded and api_key_id would leak client identity into the metric namespace. Per-key and per-label breakdowns live on the SQLite side (`/dashboard/api/requests`).

## Project layout

```
src/aiproxy/
├── __main__.py             uvicorn entry (python -m aiproxy)
├── app.py                  FastAPI factory + production lifespan
├── settings.py             pydantic-settings from .env
├── bus.py                  StreamBus — bounded-queue in-memory pub/sub
├── registry.py             RequestRegistry — active + recent meta + lifecycle events
├── metrics.py              Prometheus counter/histogram/gauge + record_completion()
│
├── auth/
│   ├── proxy_auth.py       Client API-key validation (TTL cache over api_keys)
│   └── dashboard_auth.py   Master-key login → signed JWT session cookie
│
├── core/
│   ├── headers.py          Hop-by-hop header stripping (upstream + downstream)
│   ├── traits.py           detect_request_traits() — image/file/json flags from body
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
│   ├── migrate.py          ensure_requests_columns + backfill_request_traits
│   └── crud/
│       ├── requests.py     list_with_filters, create_pending, mark_finished, mark_error
│       ├── chunks.py       insert_batch, list_for_request
│       ├── api_keys.py     CRUD + TTL-cache loader
│       ├── pricing.py      upsert_current (auto-closes prior effective row)
│       └── config.py       runtime key/value config
│
├── pricing/
│   ├── catalog.py          LiteLLM catalog refresh (fetch → diff → apply)
│   ├── compute.py          compute_cost(provider, model, usage) → (pricing_id, cost_usd)
│   └── seed.py             Bootstrap pricing rows on first run
│
└── dashboard/
    ├── routes.py           All HTTP + WS endpoints (the monolith of the dashboard)
    └── static/index.html   Single-file vanilla JS SPA (~3600 lines)

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

docs/
├── operations.md           Comprehensive deployment + ops runbook
└── sessions/               Per-session retrospective logs

deploy/
└── grafana/
    ├── README.md
    └── provisioning/
        ├── datasources/prometheus.yaml
        └── dashboards/
            ├── provider.yaml            file-based dashboard provider
            └── aiproxy-overview.json    11-panel overview dashboard

Dockerfile                  Multi-stage uv build → python:3.12-slim runtime
.dockerignore
compose.local.yml           Local bind-mount + 127.0.0.1:8000
compose.server.yml          External caddy-net + named volume + SECURE_COOKIES

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
| Metrics | `prometheus-client` (default registry) | scraped at `/metrics`, rendered in Grafana |
| Deploy | Docker (multi-stage uv build) + Caddy reverse proxy | one image, two compose files (local / server) |

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
| `requests` | one row per request — pending/streaming/done/error/canceled, full headers + bodies, token counts, cost, pricing snapshot, client-supplied `labels` + `note`, and trait flags (`request_has_image`, `request_has_file`, `response_is_json`) |
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
- `GET /dashboard/api/requests` — paginated, filter (incl. `label=` repeatable) + FTS search
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
- `GET /dashboard/api/pricing/catalog/preview` — fetches LiteLLM's model catalog and returns a diff vs. the currently-effective pricing rows (no writes)
- `POST /dashboard/api/pricing/catalog/apply` — re-fetches and applies the additions + supersessions
- `POST /dashboard/api/config`
- `POST /dashboard/api/batch/strip-binaries` — replace base64 blobs with placeholders
- `POST /dashboard/api/batch/vacuum` — SQLite `VACUUM`, returns before/after sizes

### WebSockets
- `WS /dashboard/ws/active` — lifecycle events for the live list
- `WS /dashboard/ws/stream/{req_id}` — per-chunk events for the Overview live panel + Replay live-follow

## Dashboard UI (`static/index.html`)

Single vanilla-JS SPA. Top-level tabs: **Requests · Timeline · Keys · Pricing · Settings**. Top-right corner has a 3-state theme switch (light / dark / system) with SVG icons; preference persists to localStorage.

- **Requests** — two-pane list + detail with a **draggable splitter** between them and **infinite scroll** (IntersectionObserver) on the list. Each list row shows a status dot, the req id, a row of **trait icons** (stream / image / file / json in distinct colors, where the stream icon is a 3-bar equalizer that stays visible — dim when the request has finished — for any request that used a streaming response), duration / cost / tokens, and label pills. Detail sub-tabs: Overview · Request · Response · Replay (last one hidden for non-streaming). Request + Response panels each show a collapsible Preview / Headers / Body trio — Preview renders chat messages as **collapsible `<details>` blocks per role** (system/user/assistant/tool) with text + image content parts for OpenAI / Anthropic / OpenRouter chat bodies; streaming response previews reuse the Replay tab's cached text.
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

All phases merged to `main`. 236 tests passing, overall coverage ≈ 90%.

**Post-phase additions** (not a numbered phase, grouped under deployment + observability):
- Docker packaging (`Dockerfile` + `compose.local.yml` + `compose.server.yml`)
- SQLite WAL + pragmas on every connect
- Prometheus `/metrics` endpoint with 7 metric families
- Grafana file-based provisioning + 11-panel overview dashboard
- Live at `https://ai.sat1600ap5.online` behind Caddy

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

For a containerized local dev loop (identical to production image):

```bash
docker compose -f compose.local.yml up --build -d
```

## Production deployment (tencent-singapore)

Live at **`https://ai.sat1600ap5.online`**. Server hostname (SSH alias): **`tencent-singapore`**. All stacks live under `/home/ubuntu/stacks/`. Four Docker Compose projects share two external bridge networks (`caddy-net`, `observability-net`) plus a third for the postgres tenant (`postgres-net`).

### Stack layout on the server

```
/home/ubuntu/stacks/
├── ai-proxy/              ← this repo, cloned from github.com/yuanqili/ai-proxy
│   ├── .env               (chmod 600, not in git; holds all provider + proxy secrets)
│   ├── compose.server.yml (brings up the aiproxy container)
│   └── deploy/grafana/    (mounted into the Grafana container below)
├── caddy/                 ← global reverse proxy + auto-HTTPS
│   ├── docker-compose.yml
│   ├── Caddyfile          (imports sites/*.caddy)
│   └── sites/             (per-subdomain rules)
├── logs-stack/            ← observability: Grafana + Prometheus + Loki + Promtail + cAdvisor + node-exporter
│   ├── docker-compose.yml (patched to bind-mount ai-proxy/deploy/grafana/provisioning)
│   ├── prometheus.yml     (scrape config — already has job aiproxy → aiproxy:8000/metrics)
│   └── promtail.yml
├── postgres/              ← pgvector (unrelated tenant: bollegecoard)
└── openai-proxy/          ← DEPRECATED old stack, containers stopped, dir retained for ~1 week cool-down
```

### Docker networks

| Network | Scope | Who's on it |
|---|---|---|
| `caddy-net` | external bridge | `caddy`, `aiproxy`, `prometheus` (scrapes aiproxy), `grafana` |
| `observability-net` | internal to logs-stack | `grafana`, `prometheus`, `loki`, `promtail`, `cadvisor` |
| `postgres-net` | external bridge | `postgres`, `grafana` (if ever wired), other tenants |

### Caddy sites → upstream container mapping

| Subdomain | Caddy rule file | Upstream |
|---|---|---|
| `ai.sat1600ap5.online` | `002-bollegecoard.caddy` | `aiproxy:8000` (this app) |
| `grafana.sat1600ap5.online` | `003-logs-stack.caddy` | `grafana:3000` |
| `promtail.sat1600ap5.online` | `003-logs-stack.caddy` | `promtail:9080` |
| `sat1600ap5.online` (root) | `001-bollegecoard-old.caddy` | **DEPRECATED**: points at the stopped old stack, currently 502s. Safe to rename to `.disabled` and `docker exec caddy caddy reload`. |
| `:80` and `:443` health | `000-health.caddy` | Built-in `respond /healthz 200 "ok"` |

The `aiproxy` container alias on `caddy-net` comes from the service name in `compose.server.yml` — **don't rename it** or Caddy's `reverse_proxy aiproxy:8000` breaks.

### Key operational facts

- **Persistence**: SQLite lives in the Docker named volume `aiproxy-data` (mounted at `/app/data` inside the container). Survives `docker compose down`.
- **Secrets provenance**: `PROXY_MASTER_KEY` and `SESSION_SECRET` are generated **on the server** with `openssl rand -hex 32` and never leave it. Provider API keys come from the laptop via `scp`.
- **`/metrics` scraping**: Prometheus already has `job_name: aiproxy` in its static config; scrape interval 15s. No config change needed when redeploying ai-proxy as long as the service name stays `aiproxy`.
- **Grafana provisioning**: the `logs-stack` compose has a bind-mount `/home/ubuntu/stacks/ai-proxy/deploy/grafana/provisioning → /etc/grafana/provisioning:ro`. Edit dashboard JSON in this repo → `git pull` on server → Grafana auto-reloads within 30 s (`updateIntervalSeconds: 30`).
- **Datasource provisioning gotcha**: `deploy/grafana/provisioning/datasources/prometheus.yaml` pins `uid: prometheus` and declares `deleteDatasources` to wipe any stale UI-created "Prometheus" row. Without the delete block, Grafana crashes with "data source not found" during provisioning on an already-initialized instance.
- **Log rotation**: `compose.server.yml` caps the aiproxy JSON-file log driver at `max-size=20m, max-file=5` (100 MB total). Promtail tails it into Loki.
- **Workers**: `uvicorn --workers 1` on purpose — SQLite file locking and the in-memory `StreamBus` both require single-process.

### Redeploy cycle

```bash
ssh tencent-singapore '
  cd /home/ubuntu/stacks/ai-proxy && \
  git pull && \
  docker compose -f compose.server.yml up --build -d
'
```

Incremental: src-only changes rebuild in ~20 s; dependency changes (pyproject.toml / uv.lock) ~3–5 min because the uv install layer re-runs.

See **`docs/operations.md`** for the full runbook — first-time setup, Grafana bring-up, client onboarding, backup recipe, troubleshooting cheat sheet, and known gaps.

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

# Tag a request with labels + a note (stripped before forwarding, stored for filtering)
curl -H "Authorization: Bearer $CLIENT_KEY" \
  -H "Content-Type: application/json" \
  -H "X-AIProxy-Labels: prod,eval-pipeline,v2" \
  -H "X-AIProxy-Note: regression check for JSON output format" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ok"}]}' \
  http://127.0.0.1:8000/openai/chat/completions
```

Or run the bundled scripts:

```bash
uv run python scripts/test_all.py   # hits all 3 providers, prints TTFT + cost
```

To see live replay: while a stream is in flight, open the Requests tab, click the live row, switch to the **Replay** detail tab — it'll join live-follow mode automatically.

## Tests

```bash
uv run pytest -q                                      # 234 tests, ~2.3 s
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
- **No rate limiting on `/dashboard/login`.** Master key is 64-hex random so brute force is impractical, but a shorter key would be a liability.
- **`/metrics` reachable externally** at `https://ai.sat1600ap5.online/metrics`. Metrics don't carry secrets but tidier would be Caddy `handle /metrics* { respond 404 }` before `reverse_proxy`.
- **Client API keys are plaintext in SQLite.** The `api_keys.key` column is not hashed; `GET /dashboard/api/keys` returns the full key. Treat `.db` backups like `.env`. Rotate by creating new + deactivating old, not by editing the existing row.
- **No retry endpoint.** Deferred from Phase 5.
- **Retention loop is time-based only** (strip bodies for requests older than the `full_count`-th most recent). No size-based cap.
- **Pricing seed is static.** Admin has to add new models via the Pricing tab.
- **Single-node only.** `StreamBus` is in-memory, so horizontal scaling would need Redis/NATS.

## Useful file pointers

- Passthrough hot path — `src/aiproxy/core/passthrough.py` (`stream_and_persist`)
- Dashboard router factory — `src/aiproxy/dashboard/routes.py` (`create_dashboard_router`)
- Provider ABC — `src/aiproxy/providers/base.py`
- Chunk parser implementations — `src/aiproxy/providers/openai.py`, `.../anthropic.py`
- Trait detector + DETECTOR_VERSION — `src/aiproxy/core/traits.py`
- Migration + trait backfill — `src/aiproxy/db/migrate.py`
- Prometheus metrics + `record_completion()` — `src/aiproxy/metrics.py`
- SQLite pragma listener — `src/aiproxy/db/engine.py` (`_apply_sqlite_pragmas`)
- Grafana dashboard JSON — `deploy/grafana/provisioning/dashboards/aiproxy-overview.json`
- Frontend entry — `src/aiproxy/dashboard/static/index.html`
- Design spec — `docs/superpowers/specs/2026-04-10-ai-proxy-with-live-dashboard-design.md`
- Operations runbook — `docs/operations.md`
