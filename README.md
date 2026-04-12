# ai-proxy

Self-hosted AI API proxy with a **live-streaming observability dashboard**. Drop it between your app and OpenAI / Anthropic / OpenRouter — every request is persisted, every streaming chunk is tee'd to a real-time dashboard, and you can replay any past stream byte-exact at variable speed.

Unlike LiteLLM / Helicone / Langfuse / Phoenix (which log after completion), ai-proxy streams chunks to the dashboard **while the model is still generating**.

## Why use this

- **Zero integration cost.** Point your existing OpenAI / Anthropic / OpenRouter SDK at `http://your-proxy:8000/{provider}/` instead of the upstream URL. No code changes beyond the base URL.
- **Live stream visibility.** Watch tokens land in real time from a browser — useful for debugging agent loops, evaluating prompt quality, or demoing to stakeholders.
- **Byte-exact replay.** Every streaming chunk is stored with nanosecond offsets. Replay at 0.5x/1x/2x/4x/instant to review how a response unfolded.
- **Request tagging & search.** Attach labels (`prod`, `eval-v2`, `user-123`) and notes via headers; filter and full-text search across all request/response bodies.
- **Cost tracking.** Per-request token counts and cost, computed from a versioned pricing table that can refresh from the LiteLLM community catalog.
- **Single binary, local-first.** SQLite + FastAPI + vanilla JS. No Redis, no Postgres, no Kafka. One process, one DB file.

## Quick start

```bash
git clone <repo-url> && cd ai-proxy
cp .env.example .env          # fill in your API keys (see Configuration below)
uv sync
uv run python -m aiproxy      # starts on 127.0.0.1:8000
```

Create a client API key (first run only):

```bash
uv run python scripts/create_api_key.py --name "my-app"
# → prints the key, save it
```

Open http://127.0.0.1:8000/dashboard/ and log in with your `PROXY_MASTER_KEY`.

## Integrating your project

The proxy is a transparent passthrough — swap the base URL, use a client API key instead of the provider key, and everything else stays the same.

### Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/openai",   # ← proxy, not api.openai.com
    api_key="your-client-key",                   # ← issued by create_api_key.py
)

resp = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello"}],
    stream=True,
    extra_headers={
        "X-AIProxy-Labels": "my-app,prod",       # optional: tag for filtering
        "X-AIProxy-Note": "onboarding test",      # optional: free-form note
    },
)
for chunk in resp:
    print(chunk.choices[0].delta.content or "", end="")
```

### Python (Anthropic SDK)

```python
import anthropic

client = anthropic.Anthropic(
    base_url="http://127.0.0.1:8000/anthropic",
    api_key="your-client-key",
)

with client.messages.stream(
    model="claude-sonnet-4-20250514",
    max_tokens=256,
    messages=[{"role": "user", "content": "Hello"}],
    extra_headers={"X-AIProxy-Labels": "my-app,staging"},
) as stream:
    for text in stream.text_stream:
        print(text, end="")
```

### curl

```bash
# OpenAI
curl -N -H "Authorization: Bearer $CLIENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Hi"}],"stream":true}' \
  http://127.0.0.1:8000/openai/chat/completions

# Anthropic
curl -N -H "Authorization: Bearer $CLIENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-20250514","max_tokens":256,"messages":[{"role":"user","content":"Hi"}],"stream":true}' \
  http://127.0.0.1:8000/anthropic/v1/messages

# OpenRouter (path does NOT include /api/v1 — provider auto-prepends it)
curl -N -H "Authorization: Bearer $CLIENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"openai/gpt-4o-mini","messages":[{"role":"user","content":"Hi"}],"stream":true}' \
  http://127.0.0.1:8000/openrouter/chat/completions
```

### Request tagging

Any request can carry optional classification headers. They're stored for filtering and stripped before forwarding — the upstream never sees them.

```bash
curl -H "Authorization: Bearer $CLIENT_KEY" \
  -H "Content-Type: application/json" \
  -H "X-AIProxy-Labels: prod,eval-pipeline,v2" \
  -H "X-AIProxy-Note: regression check for JSON output" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ok"}]}' \
  http://127.0.0.1:8000/openai/chat/completions
```

- **Labels** — comma-separated tags. Dashboard supports click-to-filter with intersection semantics (row must match *all* selected labels).
- **Note** — free-form text, shown in the request detail view.

## Configuration

All settings via environment variables or `.env` file:

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENAI_API_KEY` | Yes | — | Your OpenAI API key |
| `ANTHROPIC_API_KEY` | Yes | — | Your Anthropic API key |
| `OPENROUTER_API_KEY` | Yes | — | Your OpenRouter API key |
| `OPENAI_BASE_URL` | No | `https://api.openai.com` | OpenAI upstream URL |
| `ANTHROPIC_BASE_URL` | No | `https://api.anthropic.com` | Anthropic upstream URL |
| `OPENROUTER_BASE_URL` | No | `https://openrouter.ai` | OpenRouter upstream URL |
| `PROXY_MASTER_KEY` | Yes | — | Dashboard login password |
| `SESSION_SECRET` | Yes | — | JWT signing secret (32+ random bytes) |
| `DATABASE_URL` | No | `sqlite+aiosqlite:///./aiproxy.db` | SQLite database path |
| `HOST` | No | `127.0.0.1` | Listen address |
| `PORT` | No | `8000` | Listen port |
| `SECURE_COOKIES` | No | `false` | Set `true` if serving over HTTPS |

## Client API keys

The proxy authenticates clients separately from the upstream provider. Each client gets its own key, which is tracked for usage.

```bash
# Create a new key
uv run python scripts/create_api_key.py --name "agent-loop"

# Manage keys from the dashboard
# Dashboard → Keys tab → create / revoke / rename / toggle active
```

Clients send their key as `Authorization: Bearer <client-key>`. The proxy swaps it for the real provider key before forwarding.

## Dashboard

Open http://127.0.0.1:8000/dashboard/ — log in with `PROXY_MASTER_KEY`.

### Requests tab

Two-pane layout with a draggable splitter. The left list supports infinite scroll and shows:

- Status dot (green = done, yellow = streaming, red = error, gray = canceled)
- Request ID + **trait icons** — at-a-glance markers for streaming / image payload / file payload / structured JSON output
- Duration, cost, token counts
- Label pills (click to filter)

Click a row to see detail sub-tabs:

- **Overview** — summary, model, provider, timing, cost breakdown
- **Request** — collapsible chat preview (per-role `<details>` blocks), headers, raw body
- **Response** — same structure; streaming responses reuse the Replay tab's accumulated text
- **Replay** *(streaming only)* — media-player scrubber with per-chunk ticks, 0.5x/1x/2x/4x/instant speed, dim/typewriter display modes, TTFT jump, live-follow for in-flight requests

### Timeline tab

SVG lane chart grouped by model / provider / API key. Configurable time windows (5/15/60/360 min), 3-second live polling, hover tooltip, click to navigate to the request.

### Keys tab

Create, revoke, rename, and toggle API keys. Tracks last used time and usage count.

### Pricing tab

Versioned per-model pricing rows with effective date ranges. Adding a new price auto-closes the current effective row. One-click **refresh from LiteLLM catalog** pulls the latest community-maintained model pricing.

### Settings tab

- **Retention** — configure how many recent requests keep full bodies (older ones are stripped to save space)
- **Batch operations** — strip base64 blobs from stored bodies
- **Database** — size, row counts, and SQLite VACUUM

### Theme

3-state switch in the top-right corner: light / dark / system. Follows OS preference when set to system. Persists to localStorage.

## API reference

All dashboard endpoints are under `/dashboard/` and require a session cookie (obtained via `POST /dashboard/login`).

### Proxy routes

| Method | Path | Description |
|---|---|---|
| `POST` | `/{provider}/{path}` | Passthrough to upstream. Provider: `openai`, `anthropic`, `openrouter` |
| `GET` | `/healthz` | Liveness check |

### Dashboard API

| Method | Path | Description |
|---|---|---|
| `POST` | `/dashboard/login` | Master key → session cookie |
| `POST` | `/dashboard/logout` | Clear session |
| `GET` | `/dashboard/api/active` | Active + recently finished requests |
| `GET` | `/dashboard/api/requests` | Paginated list with filters + FTS search |
| `GET` | `/dashboard/api/requests/export` | Same filters, no pagination (cap 10,000) |
| `GET` | `/dashboard/api/requests/{id}` | Full detail including chunks |
| `GET` | `/dashboard/api/requests/{id}/replay` | Parsed chunks for the Replay Player |
| `GET` | `/dashboard/api/timeline` | Lane-grouped time window (`group_by=model\|provider\|api_key_id`) |
| `GET` | `/dashboard/api/stats/db` | Database size + row counts |
| `GET` | `/dashboard/api/keys` | List client keys |
| `GET` | `/dashboard/api/pricing` | List pricing versions |
| `GET` | `/dashboard/api/pricing/catalog/preview` | Diff vs. LiteLLM catalog (read-only) |
| `POST` | `/dashboard/api/pricing/catalog/apply` | Apply LiteLLM catalog changes |
| `GET` | `/dashboard/api/config` | Runtime configuration |
| `POST` | `/dashboard/api/keys` | Create key |
| `PATCH` | `/dashboard/api/keys/{id}` | Update key |
| `POST` | `/dashboard/api/pricing` | Add pricing row |
| `POST` | `/dashboard/api/config` | Update config |
| `POST` | `/dashboard/api/batch/strip-binaries` | Replace base64 blobs with placeholders |
| `POST` | `/dashboard/api/batch/vacuum` | SQLite VACUUM |

### WebSockets

| Path | Description |
|---|---|
| `WS /dashboard/ws/active` | Lifecycle events for the live request list |
| `WS /dashboard/ws/stream/{id}` | Per-chunk events for live streaming + replay |

### Query parameters for `/dashboard/api/requests`

| Param | Type | Description |
|---|---|---|
| `status` | string | Filter by status (`done`, `error`, `streaming`, `canceled`) |
| `provider` | string | Filter by provider |
| `model` | string | Filter by model name |
| `label` | string (repeatable) | Intersection filter — row must have *all* specified labels |
| `q` | string | Full-text search across request + response bodies |
| `cursor` | string | Pagination cursor (from previous response) |
| `limit` | int | Page size (default 50) |

## Architecture

```
Client (SDK / curl / agent)
  │
  ▼
ai-proxy (FastAPI)
  ├─ Auth ── client API key validation (SQLite + TTL cache)
  ├─ Dispatcher ── route by URL prefix to Provider
  ├─ PassthroughEngine
  │    ├─ persist pending row
  │    ├─ register in RequestRegistry → emit "start" on StreamBus
  │    ├─ open upstream stream (httpx)
  │    ├─ tee: yield to client + buffer chunks + publish to bus
  │    └─ finalize: batch-insert chunks, compute cost, update row
  ├─ StreamBus ── in-memory bounded-queue pub/sub
  ├─ SQLite ── requests, chunks, api_keys, pricing, config, FTS5
  └─ Dashboard ── FastAPI routes + WebSocket + vanilla JS SPA
```

Key properties:

- **Exact passthrough** — upstream response bytes reach the client untouched. No format translation.
- **Zero-cost when unwatched** — chunk parsing and base64 encoding only happen if a WebSocket subscriber exists for that request.
- **Cancel-safe** — `asyncio.shield` ensures DB writes complete even if the client disconnects mid-stream.
- **Trait detection** — at forward time, the request body is scanned once for image/file/JSON traits, stored as columns for dashboard display and filtering.

## Tech stack

| Layer | Choice |
|---|---|
| Runtime | Python 3.11+, uvicorn |
| Web framework | FastAPI + Starlette WebSockets |
| HTTP client | httpx (HTTP/2, streaming) |
| Database | SQLite + aiosqlite via SQLAlchemy 2.x async |
| Search | SQLite FTS5 |
| Auth | Master key → JWT cookie (PyJWT); per-client API keys |
| Frontend | Vanilla JS + CSS + inline SVG, single `index.html` |
| Packaging | uv + hatchling |
| Testing | pytest + pytest-asyncio + httpx.ASGITransport + respx |

## Project layout

```
src/aiproxy/
├── app.py                  FastAPI factory + production lifespan
├── settings.py             pydantic-settings from .env
├── bus.py                  StreamBus (in-memory pub/sub)
├── registry.py             RequestRegistry (active + recent)
├── auth/                   Proxy-client auth + dashboard session auth
├── core/
│   ├── headers.py          Hop-by-hop header stripping
│   ├── traits.py           Request trait detection (image/file/json)
│   └── passthrough.py      PassthroughEngine
├── providers/
│   ├── base.py             Provider ABC + ChunkParser ABC
│   ├── openai.py           OpenAI provider
│   ├── anthropic.py        Anthropic provider
│   └── openrouter.py       OpenRouter provider (OpenAI-compatible)
├── routers/proxy.py        /{provider}/{path:path} dispatcher
├── db/
│   ├── models.py           SQLAlchemy 2.x async models
│   ├── engine.py           Async engine + sessionmaker
│   ├── fts.py              FTS5 virtual table
│   ├── migrate.py          Additive column migrations + trait backfill
│   ├── retention.py        Background body-stripping loop
│   └── crud/               CRUD modules for each table
├── pricing/
│   ├── catalog.py          LiteLLM catalog refresh
│   ├── compute.py          Cost computation
│   └── seed.py             Bootstrap pricing on first run
└── dashboard/
    ├── routes.py           HTTP + WS endpoints
    └── static/index.html   Single-file vanilla JS SPA

tests/
├── unit/                   Pure unit tests (no IO)
└── integration/            FastAPI TestClient + in-memory SQLite + respx

scripts/                    API key bootstrap + manual smoke tests
docs/sessions/              Session retrospectives
```

## Adding a new provider

1. Subclass `Provider` in `src/aiproxy/providers/`:
   - Implement `inject_auth`, `map_path`, `extract_model`, `is_streaming_request`, `parse_usage`, `make_chunk_parser`
2. Register it in `src/aiproxy/providers/__init__.py:build_registry()`
3. Add base URL + API key settings to `settings.py` and `.env.example`
4. The proxy dispatcher automatically routes `/{provider_name}/{path}` to it

## Tests

```bash
uv run pytest -q                                      # 234 tests, ~2.3 s
uv run pytest --cov=src/aiproxy --cov-report=term     # with coverage (~90%)
uv run pytest tests/unit/ -v                          # unit only
uv run pytest tests/integration/ -v                   # integration only
```

Manual smoke tests against real upstreams:

```bash
uv run python scripts/test_all.py   # hits all 3 providers, prints TTFT + cost
```

## Known limitations

- **Single-node only.** StreamBus is in-memory; horizontal scaling would need Redis/NATS.
- **No rate limiting per client key.** Add if/when needed.
- **No retry endpoint.** You can't replay a request to the upstream from the dashboard (yet).
- **SQLite WAL not configured.** Default journal mode; enable WAL in `db/engine.py` if write throughput becomes an issue.

## License

TBD.
