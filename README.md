# ai-proxy

A self-hosted **AI API proxy with a live-streaming observability dashboard**. Sits between your client and OpenAI / Anthropic / OpenRouter, persists every request, and tees streaming chunks to a dashboard in real time so you can watch tokens land as they're generated — or replay any past streaming request byte-exact at 0.5×/1×/2×/4×/∞ speed.

Unlike LiteLLM / Helicone / Langfuse / Phoenix (which log requests after completion), ai-proxy publishes chunks to the dashboard **while the model is still generating**.

## Features

- **Passthrough proxy** — exact byte-for-byte forwarding, client talks to the upstream format unchanged
- **Multi-provider** — OpenAI, Anthropic, OpenRouter (add more via a small ABC)
- **Live streaming dashboard** — WebSocket tee, watch tokens appear live
- **Replay Player** — scrubber with per-chunk ticks, variable speed, live-follow, dim/typewriter modes
- **Timeline view** — SVG lane chart grouped by model / provider / API key
- **Full-text search** — SQLite FTS5 over request and response bodies
- **Request tagging** — clients attach `X-AIProxy-Labels` / `X-AIProxy-Note` headers (stripped before forwarding); filter + click-to-narrow in the dashboard
- **Trait icons** — each list row shows at-a-glance icons for streaming response / image payload / file payload / structured-JSON output (response_format or tool_use), computed server-side at create time and backfilled automatically when detection rules change
- **Polished UX** — design-token CSS palette, light/dark/system theme switch (3-state toggle, no FOUC), collapsible chat previews per role, draggable list ⇆ detail splitter, infinite scroll
- **Per-client API keys** — issue, revoke, toggle active, track usage
- **Versioned pricing** — per-model input/output/cached token pricing with effective dates, cost snapshot on every request; one-click refresh from the LiteLLM community catalog
- **Single-file vanilla JS UI** — no framework, no build step
- **SQLite storage** — one file, zero ops, FTS5 built in

## Tech stack

Python 3.11+ · FastAPI · httpx · SQLAlchemy 2.x async · aiosqlite · PyJWT · uv · vanilla JS + inline SVG

## Quick start

```bash
cp .env.example .env                          # fill in upstream API keys + master key
uv sync
uv run python -m aiproxy                      # listens on 127.0.0.1:8000
uv run python scripts/create_api_key.py --name "my-cli"   # first-run only
```

Open http://127.0.0.1:8000/dashboard/ and log in with `PROXY_MASTER_KEY`.

Then, from another terminal, exercise a streaming request:

```bash
curl -N -H "Authorization: Bearer $CLIENT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Write a haiku"}],"stream":true}' \
  http://127.0.0.1:8000/openai/chat/completions
```

The request appears live in the Requests tab. Click it → **Replay** detail tab → it joins live-follow mode and renders tokens as they land.

Or run the bundled provider smoke test:

```bash
uv run python scripts/test_all.py             # hits all 3 providers, prints TTFT + cost
```

## Endpoints

### Proxy
| Path | Purpose |
|------|---------|
| `POST /openai/{path}` | Passthrough to `api.openai.com/{path}` |
| `POST /anthropic/{path}` | Passthrough to `api.anthropic.com/{path}` |
| `POST /openrouter/{path}` | Passthrough to `openrouter.ai/api/v1/{path}` |
| `GET /healthz` | Liveness |

### Dashboard (auth-gated)
| Path | Purpose |
|------|---------|
| `GET /dashboard/` | Vanilla JS SPA |
| `GET /dashboard/api/active` | Active + recent requests snapshot |
| `GET /dashboard/api/requests` | Paginated list + filters + FTS search |
| `GET /dashboard/api/requests/export` | Export current filter as JSON |
| `GET /dashboard/api/requests/{id}` | Full detail + chunks |
| `GET /dashboard/api/requests/{id}/replay` | Parsed chunks for the Replay Player |
| `GET /dashboard/api/timeline` | Lane-grouped request window |
| `GET /dashboard/api/stats/db` | DB size + row counts |
| `POST /dashboard/api/batch/vacuum` | SQLite VACUUM |
| `POST /dashboard/api/batch/strip-binaries` | Replace base64 blobs with placeholders |
| `WS  /dashboard/ws/active` | Live lifecycle events |
| `WS  /dashboard/ws/stream/{id}` | Per-chunk events for one request |

Plus CRUD for keys, pricing, and runtime config.

## Dashboard tabs

- **Requests** — draggable two-pane list + detail with infinite scroll on the list. Rows show status, trait icons (stream / image / file / json), duration / cost / tokens, time-ago, and label pills. Detail tabs: Overview · Request · Response · Replay (last one hidden for non-streaming). Request/Response panels each show collapsible Preview / Headers / Body; the Preview renders chat messages as expandable `<details>` blocks per role
- **Timeline** — SVG lane chart grouped by model / provider / API key; 3 s live polling
- **Keys** — issue, revoke, rename, toggle active
- **Pricing** — versioned pricing rows, one-click refresh from LiteLLM catalog, adding a new row auto-closes the current effective row
- **Settings** — retention threshold, batch strip binaries, DB size / counts / Vacuum

Top-right corner has a 3-state theme switch (light / dark / system).

## Project layout

```
src/aiproxy/
├── app.py              FastAPI factory + production lifespan
├── bus.py              StreamBus (in-memory pub/sub)
├── registry.py         RequestRegistry (active + recent)
├── auth/               Proxy-client auth + dashboard session auth
├── core/               Hop-by-hop headers, PassthroughEngine
├── providers/          Provider ABC + OpenAI / Anthropic / OpenRouter
├── routers/proxy.py    /{provider}/{path:path} dispatcher
├── db/                 SQLAlchemy 2.x async models, CRUD, FTS5, retention loop
├── pricing/            Versioned pricing compute + seed
└── dashboard/          Dashboard router + single-file vanilla JS SPA
tests/unit              Pure unit tests
tests/integration       FastAPI TestClient + in-memory SQLite
scripts/                Manual real-upstream smoke scripts
docs/superpowers/       Design spec + per-phase implementation plans
```

## Status

All five planned phases are complete and merged to `main`:

| Phase | Scope | Status |
|---|---|---|
| 1 | Passthrough foundation + provider ABC + StreamBus + client-key auth | ✅ |
| 2 | SQLite persistence, chunk storage, pricing, FTS, WebSockets, retention | ✅ |
| 3 | Rich dashboard UX — keys / pricing / config UIs, filters + search, batch ops | ✅ |
| 4 | Replay Player (media mode + JSON log) + live-follow + stateful SSE parser | ✅ |
| 5 | Timeline tab, JSON export, Vacuum + DB stats, Overview error banner | ✅ |

**234 tests passing · ~90% coverage · fully in-process test suite (no real upstream required).**

See `CLAUDE.md` for the full design doc, architectural decisions, and conventions, and `docs/superpowers/specs/` + `docs/superpowers/plans/` for the per-phase plans.

## Tests

```bash
uv run pytest -q                                      # full suite
uv run pytest --cov=src/aiproxy --cov-report=term     # with coverage
uv run pytest tests/unit/ -v                          # unit only
uv run pytest tests/integration/ -v                   # integration only
```

## License

TBD.
