# AI Proxy with Live-Streaming Dashboard — Design

**Date:** 2026-04-10
**Status:** Approved for implementation
**Author:** Claude + Yuanqi Li
**Reference implementation:** `/Users/yuanqili/Developments/humble/bollegecoard-server/aiproxy` (passthrough architecture)
**Existing MVP:** `/Users/yuanqili/Developments/ai-proxy` (OpenRouter-only, in-memory tee, working end-to-end)

---

## 1. Goals and Non-Goals

### Goals (v1)

1. **Transparent passthrough** for three providers: OpenAI, Anthropic, OpenRouter. Path mappings:
   - `POST /openai/{path}` → `https://api.openai.com/v1/{path}`, injects `Authorization: Bearer <server_key>`
   - `POST /anthropic/{path}` → `https://api.anthropic.com/{path}`, injects `x-api-key` + `anthropic-version`
   - `POST /openrouter/{path}` → `https://openrouter.ai/api/v1/{path}`, injects `Authorization: Bearer <server_key>`
2. **Streaming and non-streaming both supported.** Streaming requests are tee'd to the dashboard via an in-memory bus in real time.
3. **SQLite persistence** of request metadata, full bodies, chunk-level timestamps, and versioned pricing.
4. **Multi-key proxy auth system**: issue/revoke/rename/deactivate client keys. Each key has name, note, validity window, activity state, and last-used timestamp. Keys are stored **in plaintext** (personal tool — convenience over marginal security gain).
5. **Dashboard master-key login**: single admin password in `.env`, signed session cookie.
6. **Live dashboard** with:
   - **Requests** main view (A-layout: two-pane list+detail, with C's top filter bar + full-text search + sortable columns).
   - **Timeline** secondary tab (D-layout: horizontal time-axis showing concurrent requests, grouped by model/provider/key).
   - **Keys**, **Pricing**, **Settings** management tabs.
7. **Streaming replay** with two modes inside a single Replay tab:
   - **Player mode**: scrubber with per-chunk tick marks, speed controls (0.5× / 1× / 2× / 4× / instant), dim-preview ↔ typewriter toggle, TTFT jump button.
   - **JSON Log mode**: searchable chunk list with expandable rows showing parsed JSON + raw bytes.
   - Live in-flight requests auto-enter "follow live" mode (YouTube Live semantics).
8. **Cost tracking** with versioned pricing table. `cost_usd` is computed and snapshotted at request write time so price changes do not affect historical records. Full price history is retained for audit and retroactive analysis.
9. **Batch maintenance operations** in the dashboard: select records and strip binary payloads (base64 images, PDFs, etc.) to placeholder markers.
10. **Single codebase serves both local and remote deployments**, differentiated by `.env` configuration (bind host, CORS origin, cookie security flags).

### Non-Goals (v1)

- Google Gemini proxy (user does not have a key)
- WebSocket passthrough (OpenAI Realtime, Gemini Live)
- Semantic caching
- Guardrails / content filtering
- Multi-user, RBAC, teams
- Alerting / webhook notifications
- Request replay-and-edit ("resend this request with modified parameters")
- The `bollegecoard` exam solver feature from the reference project

---

## 2. Architecture Overview

```
                                 ┌─────────────────┐
                                 │  Downstream     │
                                 │  Client (app /  │
                                 │  script / cli)  │
                                 └────────┬────────┘
                                          │ HTTPS
                                          │ Authorization: Bearer <proxy_key>
                                          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  FastAPI App (single uvicorn process)                                    │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  Middleware                                                        │  │
│  │    • verify_proxy_key (reads ApiKey table, caches in-memory)       │  │
│  │    • X-Request-Id generation                                       │  │
│  └────────────┬───────────────────────────────────────────────────────┘  │
│               ▼                                                          │
│  ┌────────────────────────┐   ┌────────────────────────┐                 │
│  │ /{provider}/{path}     │──▶│ Passthrough Engine     │                 │
│  │ router (thin)          │   │ (shared)               │                 │
│  └────────────────────────┘   └──────────┬─────────────┘                 │
│                                          ▼                               │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  Passthrough Engine responsibilities                               │  │
│  │                                                                    │  │
│  │  1. provider.build_upstream_request(request, body)                 │  │
│  │  2. registry.start(meta)   ← write metadata to SQLite (sync)       │  │
│  │  3. await client.send(upstream_req, stream=True)                   │  │
│  │  4. body_iter():                                                   │  │
│  │       async for chunk in resp.aiter_raw():                         │  │
│  │         ts = monotonic_ns()                                        │  │
│  │         live_buffer.append((offset_ns, chunk))   ← memory          │  │
│  │         bus.publish(req_id, chunk_event)   ← dashboard WS          │  │
│  │         yield chunk                       ← downstream             │  │
│  │  5. on finish (normal / canceled / interrupted):                   │  │
│  │       parse SSE → extract usage                                    │  │
│  │       cost = pricing.compute(model, input_tok, output_tok)         │  │
│  │       db.flush_chunks(req_id, live_buffer)                         │  │
│  │       registry.finish(meta, status, cost, ...)                     │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                 │                                        │
│                                 ▼                                        │
│  ┌────────────────┐   ┌──────────────────┐   ┌────────────────────────┐  │
│  │  StreamBus     │   │  RequestRegistry │   │  SQLite                │  │
│  │  (in-memory    │   │  (active dict +  │   │  (requests / chunks /  │  │
│  │   pub/sub)     │   │   history list)  │   │   pricing / api_keys)  │  │
│  └───────┬────────┘   └────────┬─────────┘   └────────┬───────────────┘  │
│          │                     │                      │                 │
└──────────┼─────────────────────┼──────────────────────┼─────────────────┘
           │                     │                      │
           ▼                     ▼                      ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Dashboard (served from the same FastAPI app)                            │
│                                                                          │
│  HTTP:                                                                   │
│    GET  /dashboard/              → HTML SPA                              │
│    POST /dashboard/login         → master key → signed session cookie    │
│    GET  /dashboard/api/requests  → paginated list (filter/sort/search)   │
│    GET  /dashboard/api/requests/{req_id}   → full detail incl. chunks    │
│    GET  /dashboard/api/keys      → list client api keys                  │
│    POST /dashboard/api/keys      → create key                            │
│    PATCH /dashboard/api/keys/{id} → update/revoke                        │
│    GET  /dashboard/api/pricing   → list pricing versions                 │
│    POST /dashboard/api/pricing   → upsert (closes current row)           │
│    GET  /dashboard/api/config    → runtime settings (retention N, etc)   │
│    POST /dashboard/api/config    → update settings                       │
│    POST /dashboard/api/batch/strip-binaries  → batch cleanup             │
│                                                                          │
│  WebSocket:                                                              │
│    /dashboard/ws/active          → live lifecycle events                 │
│    /dashboard/ws/stream/{req_id} → live chunks for one request           │
└──────────────────────────────────────────────────────────────────────────┘
```

### Core design principles

1. **Proxy hot path does no disk IO beyond one synchronous metadata insert at start.** Chunks go to an in-memory buffer and the bus; they are batched-flushed to SQLite only when the stream finishes (or is interrupted / canceled).
2. **The StreamBus (realtime) and SQLite (persistent) are two independent paths.** The dashboard consumes both: active requests via WebSocket, historical requests via DB queries.
3. **SSE parsing only happens on the flush path** — it never runs in the hot path. The live dashboard does its own per-chunk parsing in the browser (cheap).
4. **Pricing and cost computation also run on the flush path** because the `usage` field appears in the last chunk of a streaming response.
5. **SSE chunk timestamps are relative (`offset_ns` from `first_chunk_at`)**, not absolute. This avoids clock drift issues and makes normalized cross-request comparison trivial.

---

## 3. Data Model

SQLite with SQLAlchemy async engine (`aiosqlite` driver). Migrations via Alembic. UTC timestamps stored as `REAL` epoch seconds.

### 3.1 `requests`

```sql
CREATE TABLE requests (
    req_id          TEXT PRIMARY KEY,              -- 12-char uuid hex
    api_key_id      INTEGER,                       -- FK → api_keys.id (who called us)
    provider        TEXT NOT NULL,                 -- 'openai' | 'anthropic' | 'openrouter'
    endpoint        TEXT NOT NULL,                 -- path after provider prefix
    method          TEXT NOT NULL,                 -- 'POST', 'GET', ...
    model           TEXT,                          -- extracted from request body
    is_streaming    INTEGER NOT NULL DEFAULT 0,    -- 1 if client asked for SSE
    client_ip       TEXT,
    client_ua       TEXT,

    -- request payload (stored faithfully by default)
    req_headers     TEXT NOT NULL,                 -- JSON, stripped of Authorization/Cookie
    req_query       TEXT,                          -- JSON
    req_body        BLOB,                          -- raw bytes; may contain base64 images
    req_body_size   INTEGER NOT NULL DEFAULT 0,

    -- response payload
    status_code     INTEGER,                       -- upstream HTTP status
    resp_headers    TEXT,                          -- JSON
    resp_body       BLOB,                          -- full body (or reassembled from chunks)
    resp_body_size  INTEGER NOT NULL DEFAULT 0,

    -- lifecycle timestamps (UTC epoch seconds, microsecond precision)
    started_at       REAL NOT NULL,
    upstream_sent_at REAL,
    first_chunk_at   REAL,
    finished_at      REAL,

    -- result classification
    status          TEXT NOT NULL,                 -- 'pending' | 'streaming' | 'done' | 'error' | 'canceled'
    error_class     TEXT,                          -- see §7
    error_message   TEXT,

    -- token + cost (filled after parsing usage on flush)
    input_tokens      INTEGER,
    output_tokens     INTEGER,
    cached_tokens     INTEGER,                     -- prompt caching (Anthropic/OpenAI)
    reasoning_tokens  INTEGER,                     -- OpenAI o1/o3 reasoning tokens
    cost_usd          REAL,                        -- snapshotted at write time
    pricing_id        INTEGER,                     -- FK → pricing.id

    -- housekeeping
    chunk_count       INTEGER NOT NULL DEFAULT 0,
    binaries_stripped INTEGER NOT NULL DEFAULT 0,  -- 0/1 flag set by batch cleanup

    FOREIGN KEY (api_key_id) REFERENCES api_keys(id),
    FOREIGN KEY (pricing_id) REFERENCES pricing(id)
);

CREATE INDEX idx_requests_started      ON requests(started_at DESC);
CREATE INDEX idx_requests_provider_model ON requests(provider, model);
CREATE INDEX idx_requests_status       ON requests(status);
CREATE INDEX idx_requests_api_key      ON requests(api_key_id);
```

### 3.2 `requests_fts` — full-text search shadow table

```sql
CREATE VIRTUAL TABLE requests_fts USING fts5(
    req_id UNINDEXED,
    req_body_text,      -- utf-8 decoded body if possible, else ''
    resp_body_text,
    model,
    endpoint,
    content=''
);
```

Populated by triggers on `requests` insert/update. Non-decodable bodies leave the `_text` columns empty.

### 3.3 `chunks` — streaming chunks with precise per-chunk timestamps

```sql
CREATE TABLE chunks (
    req_id      TEXT NOT NULL,
    seq         INTEGER NOT NULL,                  -- 0, 1, 2, ...
    offset_ns   INTEGER NOT NULL,                  -- ns since first_chunk_at
    size        INTEGER NOT NULL,
    data        BLOB NOT NULL,                     -- raw chunk bytes
    PRIMARY KEY (req_id, seq),
    FOREIGN KEY (req_id) REFERENCES requests(req_id) ON DELETE CASCADE
);

CREATE INDEX idx_chunks_req ON chunks(req_id);
```

Only written for `is_streaming=1` requests.

### 3.4 `api_keys` — proxy auth keys

```sql
CREATE TABLE api_keys (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    key             TEXT NOT NULL UNIQUE,          -- plaintext, prefixed "sk-aiprx-..."
    name            TEXT NOT NULL,
    note            TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      REAL NOT NULL,
    valid_from      REAL,
    valid_to        REAL,
    last_used_at    REAL
);
```

### 3.5 `pricing` — versioned, append-only

```sql
CREATE TABLE pricing (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    provider            TEXT NOT NULL,
    model               TEXT NOT NULL,
    input_per_1m_usd    REAL NOT NULL,
    output_per_1m_usd   REAL NOT NULL,
    cached_per_1m_usd   REAL,                      -- prompt caching discount
    effective_from      REAL NOT NULL,
    effective_to        REAL,                      -- NULL = still current
    note                TEXT,                      -- human memo, e.g. "price cut"
    created_at          REAL NOT NULL
);

CREATE INDEX idx_pricing_lookup ON pricing(provider, model, effective_from DESC);
```

Cost computation at request-flush time joins `pricing WHERE provider=? AND model=? AND effective_from <= now AND (effective_to IS NULL OR effective_to > now) ORDER BY effective_from DESC LIMIT 1`, stores the matched `pricing.id` into `requests.pricing_id`, and writes the computed `cost_usd`. Subsequent price changes leave historical records untouched.

### 3.6 `config` — runtime-mutable settings

```sql
CREATE TABLE config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,                      -- JSON-encoded
    updated_at REAL NOT NULL
);
```

Seed keys:
- `retention.full_count` → `500` (default; editable in dashboard Settings)

Secrets like the master key are **not** in this table — they live in `.env`.

### 3.7 Retention enforcement

A background task scheduled every 60 seconds:

```sql
-- find the cutoff: the started_at of the Nth most recent request
WITH ranked AS (
  SELECT started_at, ROW_NUMBER() OVER (ORDER BY started_at DESC) AS rn
  FROM requests
)
SELECT started_at FROM ranked WHERE rn = :N;

-- null out body/headers and delete chunks for older rows
UPDATE requests
SET req_body = NULL, resp_body = NULL, req_headers = '{}', resp_headers = '{}'
WHERE started_at < :cutoff AND req_body IS NOT NULL;

DELETE FROM chunks WHERE req_id IN (
  SELECT req_id FROM requests WHERE started_at < :cutoff
);
```

Metadata, token counts, and costs are retained indefinitely for long-term analytics.

---

## 4. Provider Abstraction

```
aiproxy/providers/
├── __init__.py        # registry
├── base.py            # Provider ABC + UpstreamRequest dataclass + Usage dataclass
├── openai.py          # OpenAIProvider
├── anthropic.py       # AnthropicProvider
└── openrouter.py      # OpenRouterProvider (OpenAI-compatible, subclasses for defaults)
```

### 4.1 Base interface

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class Usage:
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None

class Provider(ABC):
    name: str                     # 'openai' | 'anthropic' | 'openrouter'
    base_url: str                 # from settings
    upstream_path_prefix: str     # '/v1', '', '/api/v1'

    @abstractmethod
    def upstream_api_key(self) -> str: ...

    @abstractmethod
    def inject_auth(self, headers: dict[str, str]) -> dict[str, str]: ...

    def map_path(self, client_path: str) -> str:
        return f"{self.upstream_path_prefix}/{client_path.lstrip('/')}"

    def extract_model(self, body: bytes) -> str | None: ...

    def is_streaming_request(self, body: bytes, headers: dict[str, str]) -> bool: ...

    def parse_usage(self, *, is_streaming: bool, resp_body: bytes | None,
                    chunks: list[bytes] | None) -> Usage | None: ...
```

### 4.2 Concrete implementations

| Provider | `upstream_path_prefix` | `inject_auth` | Streaming detection | Usage source |
|----------|------------------------|---------------|---------------------|--------------|
| **openai** | `/v1` | `Authorization: Bearer <key>` | `body.stream == true` | Non-stream: `response.usage`. Stream: last non-`[DONE]` data event (requires `stream_options.include_usage=true`). |
| **anthropic** | `` | `x-api-key: <key>`, `anthropic-version: 2023-06-01`; pops `authorization` | `body.stream == true` | Non-stream: `response.usage`. Stream: `message_delta.usage` in the `message_delta` event. |
| **openrouter** | `/api/v1` | `Authorization: Bearer <key>`; optional `HTTP-Referer` | `body.stream == true` | Same as OpenAI (OpenRouter is OpenAI-compatible). |

### 4.3 Router layer is a thin dispatcher

```python
_registry: dict[str, Provider] = {
    "openai": OpenAIProvider(),
    "anthropic": AnthropicProvider(),
    "openrouter": OpenRouterProvider(),
}

router = APIRouter()

@router.api_route("/{provider}/{full_path:path}",
                  methods=["GET","POST","PUT","DELETE","PATCH","OPTIONS"])
async def proxy(provider: str, full_path: str, request: Request):
    p = _registry.get(provider)
    if not p:
        raise HTTPException(404, f"unknown provider: {provider}")
    return await passthrough_engine.handle(p, full_path, request)
```

**Adding a new provider costs ~50 lines of Python** (a new `Provider` subclass). The passthrough engine, dashboard, persistence, and pricing layer all remain unchanged.

---

## 5. Dashboard UX

### 5.1 Top-level navigation

```
┌─────────────────────────────────────────────────────────────────┐
│  AI Proxy  [Requests] [Timeline] [Keys] [Pricing] [Settings]  🔓│
└─────────────────────────────────────────────────────────────────┘
```

| Tab       | Content |
|-----------|---------|
| Requests  | Main view. A-layout (left list + right detail) with C-style top filter bar and full-text search. Default landing page. |
| Timeline  | D-layout concurrent timeline. Grouped by model/provider/key. Live mode scrolls automatically. |
| Keys      | Manage client API keys — issue/revoke/rename/toggle active. See per-key usage. |
| Pricing   | View and edit pricing history. Adding a row auto-closes the current effective row. |
| Settings  | Runtime configuration: retention threshold, batch operations, DB size, vacuum. |

### 5.2 Requests main view

The layout is A (two-pane) augmented with C's filtering/sorting capabilities, rejecting B's dedicated filter sidebar to preserve screen width.

```
┌─────────────────────────────────────────────────────────────────────────┐
│ [provider ▾] [model ▾] [status ▾] [key ▾]                               │
│ [🔍 search body/response/model...                    ] [🕐 last 24h ▾]  │
├──────────────────────────┬──────────────────────────────────────────────┤
│ 234 matches · sort ▾     │  req_id  model  · status                     │
│                          ├──────────────────────────────────────────────┤
│ list (chronological by   │ [Overview][Request][Response][Chunks][Replay]│
│  default; sortable by    ├──────────────────────────────────────────────┤
│  duration / cost /       │                                              │
│  tokens)                 │  (tab content)                               │
│                          │                                              │
└──────────────────────────┴──────────────────────────────────────────────┘
```

**List item** shows: status dot · req_id · model · duration · cost · token count · api key name.
**Status dot colors**:
- `green` — done, 2xx
- `red` — done with 4xx/5xx, or error_class set
- `yellow pulsing` — streaming/pending
- `gray` — canceled

**Sort dropdown**: time ↓ (default) / duration ↓ / cost ↓ / tokens ↓.

### 5.3 Request detail tabs

| Tab | Content |
|-----|---------|
| Overview | Meta table (model, api key, timestamps, ttft, duration, tokens, cost with pricing version). For active streaming requests, shows a "LIVE OUTPUT" panel at the bottom receiving tokens via WebSocket. |
| Request | Sub-sections: headers / query params / body. JSON bodies pretty-printed with syntax highlight. Image data URIs rendered as thumbnails. Unrecognized binary falls back to hex dump. |
| Response | Same structure as Request, showing the final response (for streaming, reassembled from chunks). |
| Chunks | List of all chunks: `seq · +offset · size · preview`. Click a row to see raw bytes + parsed delta. Multi-select + export JSON. |
| Replay | See §5.4. **Hidden for non-streaming requests.** |

### 5.4 Replay tab — two modes

**Mode toggle** at top of tab: `▶ Player` ↔ `{ } JSON Log`.

#### 5.4.1 Player mode

- **Screen area** shows rendered tokens. Two sub-modes toggle-switchable:
  - **Dim preview** (default for historical): entire text visible; already-played portion in full color, upcoming text greyed out. Inspired by audio waveform editors.
  - **Typewriter**: only the played-so-far text is visible, future is hidden. More immersive.
  - For **live in-flight** requests, only typewriter is available (no future to dim).
- **Cursor**: blinking `▍` marking the current position in the text.
- **Scrubber bar** below the screen:
  - Gradient fill shows progress.
  - **Vertical tick for every real chunk** → visually displays timing density (burst clusters vs gaps).
  - Draggable handle; clicking anywhere seeks.
- **Time labels**: current offset (e.g., `+2.14s`) / total duration (e.g., `5.84s`).
- **Transport controls**:
  - `⏮ Restart` — seek to 0
  - `⏸ Pause` / `▶ Play`
  - `⏹ Stop`
  - `⏪ TTFT` — seek to the first content chunk, skipping handshake preamble
- **Speed group** (right-aligned): `0.5× / 1× / 2× / 4× / ∞ (instant)`.
  - `instant` fills the entire output immediately (effectively shows final rendered result).
- **Stats strip** below controls: current chunk index, rendered char count, avg inter-chunk Δ, current chunk Δ.
- **Live follow mode**: when the request is still streaming, the scrubber handle is locked to the right edge and advances with each new chunk (like YouTube Live). Dragging left enters manual scrub mode; dragging back to the right edge re-attaches follow mode.

#### 5.4.2 JSON Log mode

- **Header**: chunk count, total bytes, time span, `Export JSON` button, `Filter (text/regex)` input.
- **Chunk table columns**: seq · offset · size · preview · event_type.
- **Row click** expands inline, showing:
  - Pretty-printed JSON (syntax highlighted)
  - Raw bytes (utf-8 representation)
  - Action row: `⏵ jump player here · copy json · copy raw`
- **Regex filter**: filter rows by content text or offset pattern.
- **Live append**: for in-flight streaming requests, new chunks append to the table in real time.

### 5.5 Keys tab

Table with columns: name · key (plaintext, copy button) · active toggle · last used · usage count (last 24h).
`+ Issue new key` button opens a modal for: name, note (optional), valid_from (optional), valid_to (optional).

### 5.6 Pricing tab

Table of current effective rows (✓ marker), with collapsed "Expired rows" section below. `+ Add pricing row` opens a modal; submitting auto-sets the current matching row's `effective_to = now`.

### 5.7 Settings tab

- **Retention**: numeric input for `full_count` (default 500), save button.
- **Batch operations**: `Strip binaries from selected…` (opens filter builder) / `Strip from all…` (confirm dialog).
- **Database**: size, request count, chunk count, `Vacuum` button, `Export selected as JSON` button.

### 5.8 Real-time delivery

Two WebSocket channels:
- `/dashboard/ws/active` — the dashboard subscribes once on load. Receives initial snapshot, then `start`/`update`/`finish` delta events. Drives the live list UI.
- `/dashboard/ws/stream/{req_id}` — the dashboard subscribes when user opens a request that is still active. Receives per-chunk events. Drives the live Overview output panel, the Player's live-follow mode, and the JSON Log's live append.

Both channels use bounded queues per subscriber; slow subscribers get events dropped rather than blocking the proxy. The proxy hot path checks `bus.has_subscribers(req_id)` as a short-circuit to skip all dashboard-related work when no one is watching.

---

## 6. Security

### 6.1 Proxy inbound auth

Middleware accepts three header variants for compatibility:
- `Authorization: Bearer <proxy_key>`
- `Authorization: <proxy_key>`
- `X-Api-Key: <proxy_key>`

Validation flow:
1. Check in-memory key cache (refreshed from DB every 60 seconds; the keys table is small, full cache is feasible).
2. If present: check `is_active`, `valid_from`, `valid_to`.
3. Update `last_used_at` asynchronously (fire-and-forget, does not block the request).
4. Attach `api_key_id` to `request.state`.

The client's `Authorization` / `X-Api-Key` header is **stripped before forwarding to upstream** and replaced by the provider's `inject_auth` output.

### 6.2 Dashboard auth

- `GET /dashboard/` — if not logged in, redirects to `/dashboard/login`.
- `POST /dashboard/login` with `{master_key}` — validated against `settings.proxy_master_key` (from `.env`). On success, issues an `httpOnly`, `SameSite=Strict` cookie containing a signed JWT (`iat`, `exp`). Signing secret from `settings.session_secret`.
- Session validity: 30 days.
- WebSocket handshake reads the cookie; unauthenticated connections are closed with code 4401.
- No CSRF tokens (single origin + `httpOnly` + `SameSite=Strict` is sufficient).
- Master key changes require editing `.env` and restarting the service. No in-dashboard modification.

### 6.3 Secrets handling — what is never stored or logged

| Item | Where it lives |
|------|----------------|
| Upstream API keys (OpenAI, Anthropic, OpenRouter) | `.env` and process memory only. Never written to DB, never logged. |
| Client `Authorization` / `x-api-key` header | Stripped by the passthrough engine before `req_headers` is serialized to DB. |
| Dashboard `Cookie` header | Same — stripped before serialization. |
| `proxy_master_key` / `session_secret` | `.env` only. |
| Auth failures (invalid/missing proxy key) | Logged to application log only (structured), **not** written to DB — avoids scanner/crawler traffic polluting the request history. |

### 6.4 Local vs remote deployment

Configured via `.env`:

```python
class Settings(BaseSettings):
    host: str = "127.0.0.1"                  # 0.0.0.0 for prod behind Caddy
    port: int = 8000
    dashboard_origin: str | None = None      # set in prod for CORS allow-origin
    secure_cookies: bool = False             # True in prod (HTTPS-only cookies)

    proxy_master_key: str
    session_secret: str
    openai_api_key: str
    anthropic_api_key: str
    openrouter_api_key: str
```

**Local**: bind to loopback, cookies not secure (HTTP), no CORS.

**Remote**: bind to `0.0.0.0`, set CORS origin to the dashboard hostname, cookies secure, placed behind Caddy for TLS termination. Suggested Caddy site:

```
aiproxy.sat1600ap5.online {
    reverse_proxy aiproxy:8000
    encode zstd gzip
    # optional: rate_limit on /dashboard/login to 5/min per IP
}
```

---

## 7. Error Handling

### 7.1 Error classes

| `error_class` | Trigger | HTTP to client | DB record |
|---------------|---------|----------------|-----------|
| `auth_missing` | No key sent | 401 | **Not recorded** (avoid scanner pollution) |
| `auth_invalid` | Key unknown / inactive / out of validity window | 401 | Not recorded |
| `upstream_connect` | Cannot connect to upstream (DNS/TCP/TLS) | 502 | `status='error', error_class='upstream_connect'` |
| `upstream_http` | Upstream returns 4xx/5xx | Upstream status passed through | `status='done', status_code=<upstream>` |
| `upstream_timeout` | Read timeout triggered | 504 | `status='error', error_class='upstream_timeout'` |
| `stream_interrupted` | Upstream closes stream mid-response | Connection closed (already streaming) | `status='error', error_class='stream_interrupted'`; **chunks received so far are persisted** |
| `client_disconnect` | Client closes connection | N/A | `status='canceled'`; chunks received so far are persisted |
| `proxy_internal` | Bug in our code | 500 | `status='error', error_class='proxy_internal'`, traceback in `error_message` |

### 7.2 Key principles

1. **`upstream_http` is not treated as an error.** Upstream 429/500/400 are first-class business signals and are recorded as `status='done'` with the real upstream status code. The dashboard visually distinguishes them by coloring the status dot red.
2. **Interrupted streams still persist their chunks.** Whatever chunks were received and forwarded before the interruption are flushed to the DB along with the error record. This is invaluable for debugging.
3. **Client disconnects are not errors.** They are normal behavior (user pressed ESC, client timed out, etc.). Recorded as `status='canceled'` with gray status dot.
4. **Auth failures are not recorded to the DB.** They go to structured application logs (which can be exported to Grafana Loki in the remote deployment).

### 7.3 Engine error-handling structure

```python
async def handle(provider, path, request) -> Response:
    req_id = uuid.uuid4().hex[:12]
    meta = RequestMeta(req_id=req_id, provider=provider.name, ...)
    try:
        registry.start(meta)
        upstream_req = provider.build_upstream_request(path, request, body)
        try:
            upstream_resp = await client.send(upstream_req, stream=True)
        except httpx.ConnectError as e:
            registry.finish(req_id, status="error",
                            error_class="upstream_connect", error_message=str(e))
            return JSONResponse({"error": "upstream connection failed"}, status_code=502)
        except httpx.ReadTimeout as e:
            registry.finish(req_id, status="error",
                            error_class="upstream_timeout", error_message=str(e))
            return JSONResponse({"error": "upstream timeout"}, status_code=504)
        return StreamingResponse(body_iter(upstream_resp, req_id, provider), ...)
    except Exception as e:
        logger.exception("proxy internal error", req_id=req_id)
        registry.finish(req_id, status="error",
                        error_class="proxy_internal", error_message=repr(e))
        return JSONResponse({"error": "internal proxy error", "req_id": req_id},
                            status_code=500)
```

```python
async def body_iter(upstream_resp, req_id, provider):
    live_buffer, first_ns = [], None
    try:
        async for chunk in upstream_resp.aiter_raw():
            ts_ns = time.monotonic_ns()
            if first_ns is None:
                first_ns = ts_ns
            offset_ns = ts_ns - first_ns
            live_buffer.append((offset_ns, chunk))
            bus.publish(req_id, {"type": "chunk", "offset_ns": offset_ns, ...})
            yield chunk
        await finalize(req_id, provider, live_buffer, status="done")
    except asyncio.CancelledError:
        await finalize(req_id, provider, live_buffer, status="canceled")
        raise
    except httpx.ReadError as e:
        await finalize(req_id, provider, live_buffer, status="error",
                       error_class="stream_interrupted", error_message=str(e))
        raise
    finally:
        await upstream_resp.aclose()
```

`finalize()` is the single place where SSE parsing, usage extraction, cost computation, chunk flushing, and registry updates happen — so all three termination paths (done, canceled, interrupted) produce consistent DB state.

### 7.4 Dashboard error presentation

Requests whose `error_class IS NOT NULL` show an error banner in their Overview tab:

```
┌──────────────────────────────────────────────────────────┐
│ ⚠ upstream_connect                                       │
│   Upstream (api.openai.com) did not respond within 10s   │
└──────────────────────────────────────────────────────────┘
```

`proxy_internal` banners additionally show the traceback (admin only — this surface is already behind dashboard auth).

---

## 8. Testing Strategy

### 8.1 Unit tests (no IO)

- `tests/unit/test_headers.py` — hop-by-hop stripping
- `tests/unit/test_bus.py` — pub/sub, bounded queue drop behavior on slow subscribers
- `tests/unit/test_registry.py` — lifecycle transitions
- `tests/unit/test_providers.py` — each `Provider`'s `inject_auth`, `map_path`, `extract_model`, `parse_usage` against static fixtures
- `tests/unit/test_pricing.py` — version selection, cost math
- `tests/unit/test_retention.py` — cutoff computation, cleanup SQL assertions

### 8.2 Integration tests (mocked upstream via `respx`)

End-to-end request path without hitting real providers.

Coverage checklist:
- Non-streaming happy path: `/openai/chat/completions`, `/anthropic/v1/messages`, `/openrouter/chat/completions` — response body stored, usage parsed, cost computed.
- Streaming happy path: chunks yielded to client, published to bus, flushed to DB with monotonic `offset_ns`.
- Auth: missing / wrong / expired / inactive key → 401, no DB row.
- Upstream 4xx/5xx passthrough: recorded as `status='done'` with real status code.
- `upstream_connect` / `upstream_timeout`: correct error_class set, no chunks in DB.
- `stream_interrupted`: partial chunks persisted, error_class set.
- `client_disconnect`: partial chunks persisted, `status='canceled'`.
- Binary body (base64 image): stored faithfully; batch strip-binaries replaces with placeholder and sets `binaries_stripped=1`.
- FTS: search on request body text finds matching records.

### 8.3 Dashboard API + WebSocket tests (FastAPI TestClient)

- Unauthenticated `/dashboard/api/*` → 401
- Login flow → session cookie → authenticated list/detail
- Key CRUD, pricing CRUD, config GET/POST
- `/ws/active` snapshot + delta events
- `/ws/stream/{req_id}` catches up an active request

### 8.4 Manual smoke tests (real API, not in CI)

- `scripts/test_openai.py` — real OpenAI call
- `scripts/test_anthropic.py` — real Anthropic call
- `scripts/test_openrouter.py` — already exists
- `scripts/test_all.py` — runs all three, prints TTFT and cost per provider

### 8.5 Coverage targets

- `core/`, `providers/`, `db/` ≥ 85%
- `routers/`, `dashboard/` ≥ 70%

### 8.6 Test runner

```bash
uv run pytest tests/unit/ tests/integration/ -v
uv run pytest --cov=src/aiproxy --cov-report=term-missing
uv run python scripts/test_all.py       # manual only
```

---

## 9. Implementation Phases

Each phase is independently runnable and verifiable. Each ends with a git commit checkpoint, tests green, and a manual smoke verification.

### Phase 1 — Foundation
- Data model, SQLAlchemy async setup, Alembic migrations
- `Provider` ABC + three concrete implementations
- Shared `passthrough_engine` (without dashboard tee)
- Proxy API key middleware
- Manual smoke tests for all three providers
- **Verification**: `scripts/test_all.py` passes for all three providers

### Phase 2 — Persistence + basic dashboard
- `finalize()` flush path writes request + chunks to DB
- SSE parsing, usage extraction, cost computation, pricing version lookup
- Port MVP dashboard HTML to new schema
- `/ws/active` and `/ws/stream/{req_id}` reading from both live buffer and DB
- Background retention task
- **Verification**: send streaming request → dashboard live view → reload page → DB-backed view shows the same data

### Phase 3 — Rich dashboard UX
- Requests main view (A layout + top filter bar + full-text search)
- Detail tabs: Overview, Request, Response, Chunks
- Keys management page
- Pricing management page
- Settings page (retention threshold, batch strip binaries)
- Login flow
- **Verification**: full proxy + monitoring loop usable without any CLI

### Phase 4 — Replay player
- Replay tab with Player mode
  - Scrubber with per-chunk ticks
  - Speed controls 0.5×/1×/2×/4×/∞
  - Dim preview ↔ typewriter toggle
  - TTFT jump
  - Live follow mode
- Replay tab with JSON Log mode
  - Searchable chunk table
  - Row expansion with parsed JSON + raw bytes
  - Export JSON
- **Verification**: any historical streaming request replays end-to-end

### Phase 5 — Polish
- Timeline tab (D layout)
- JSON data export for selected records
- Vacuum button
- Error banner and optional "retry this request"
- **Verification**: dashboard feels like a finished product

---

## 10. Project layout

Keeping the `src/aiproxy/` nesting (not flattening) to preserve the Python src-layout benefits and to make room for sibling modules later.

```
ai-proxy/
├── pyproject.toml
├── README.md
├── .env.example
├── .gitignore
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-04-10-ai-proxy-with-live-dashboard-design.md
├── scripts/
│   ├── test_openai.py
│   ├── test_anthropic.py
│   ├── test_openrouter.py
│   ├── test_all.py
│   └── test_stream.py
├── src/
│   └── aiproxy/
│       ├── __init__.py
│       ├── __main__.py
│       ├── app.py                   # FastAPI factory + lifespan
│       ├── settings.py
│       ├── bus.py                   # StreamBus
│       ├── registry.py              # RequestRegistry
│       ├── core/
│       │   ├── headers.py
│       │   ├── streaming.py         # body_iter, finalize
│       │   └── passthrough.py       # shared handle()
│       ├── providers/
│       │   ├── __init__.py          # registry
│       │   ├── base.py              # Provider ABC, Usage
│       │   ├── openai.py
│       │   ├── anthropic.py
│       │   └── openrouter.py
│       ├── db/
│       │   ├── engine.py
│       │   ├── models.py            # SQLAlchemy models
│       │   ├── crud/
│       │   │   ├── requests.py
│       │   │   ├── chunks.py
│       │   │   ├── api_keys.py
│       │   │   ├── pricing.py
│       │   │   └── config.py
│       │   └── retention.py         # background cleanup task
│       ├── auth/
│       │   ├── proxy_auth.py        # verify_proxy_key middleware
│       │   └── dashboard_auth.py    # master key login + session cookie
│       ├── pricing/
│       │   └── compute.py           # cost calculation from usage
│       ├── routers/
│       │   ├── proxy.py             # the thin dispatcher
│       │   └── dashboard/
│       │       ├── api.py           # REST endpoints
│       │       ├── ws.py            # WebSocket endpoints
│       │       └── static/
│       │           ├── index.html
│       │           ├── app.js       # (or split into modules)
│       │           └── style.css
│       └── __pycache__/             # (gitignored)
└── tests/
    ├── unit/
    │   ├── test_headers.py
    │   ├── test_bus.py
    │   ├── test_registry.py
    │   ├── test_providers.py
    │   ├── test_pricing.py
    │   └── test_retention.py
    ├── integration/
    │   ├── test_proxy_openai.py
    │   ├── test_proxy_anthropic.py
    │   ├── test_proxy_openrouter.py
    │   ├── test_streaming_tee.py
    │   ├── test_errors.py
    │   └── test_dashboard_api.py
    └── conftest.py
```

---

## 11. Open questions deferred to implementation

- **Alembic vs hand-written migrations** — default to Alembic for clean history, but a hand-written `init.sql` for Phase 1 is acceptable if Alembic setup proves annoying.
- **JSON in `requests.req_headers`** — encode as compact JSON string. If later we want to search by header key, add an FTS column.
- **FTS trigger ordering** — SQLite FTS5 with triggers works, but for large body sizes the insert cost may be noticeable. If so, consider FTS population as a background task instead of a trigger.
- **Streaming `include_usage`** — OpenAI's streaming format only includes `usage` when the client sets `stream_options.include_usage=true`. The proxy should consider injecting this option automatically, but only if the client didn't set `stream_options` explicitly (to avoid breaking client expectations). Decide during Phase 2.
- **Pricing seed data** — ship a `pricing_seed.json` with current known prices for the major models, loaded on first boot. Users add/update via dashboard from there.
