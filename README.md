# ai-proxy (MVP)

AI API proxy with a **live-streaming observability dashboard**. Unlike existing
tools (LiteLLM, Helicone, Langfuse, Phoenix, etc.) that log requests *after*
completion, this proxy tees upstream streaming chunks to a dashboard in real
time, so you can watch tokens appear as the model generates them.

## Architecture

```
[Downstream Client] <--> [ai-proxy] <--> [Upstream: OpenRouter]
                            |
                            └── tee ──> [StreamBus] --> [Dashboard WebSocket]
```

- **Passthrough proxy** — no format translation, forwards request bodies as-is
- **Tee** — each upstream chunk goes to both the downstream client and the bus
- **In-memory pub/sub bus** — bounded queues, slow subscribers get dropped
  events (dashboard is observer, proxy hot path is never blocked)
- **Request registry** — tracks active + recent requests with lifecycle events

## Layout

```
src/aiproxy/
├── app.py                  FastAPI factory + httpx client lifespan
├── __main__.py             Entry point: python -m aiproxy
├── settings.py             Pydantic settings from .env
├── bus.py                  StreamBus + RequestRegistry
├── core/
│   └── headers.py          Hop-by-hop header stripping
├── proxy/
│   └── openrouter.py       Passthrough proxy w/ streaming tee
└── dashboard/
    ├── routes.py           WS endpoints + static
    └── static/index.html   Vanilla JS dashboard
scripts/
└── test_stream.py          Manual client test
```

## Run

```bash
cp .env.example .env   # put your OPENROUTER_KEY in .env
uv sync
uv run python -m aiproxy
```

Then visit **http://127.0.0.1:8000/dashboard/** and in another terminal:

```bash
uv run python scripts/test_stream.py --model openai/gpt-4o-mini
```

Tokens appear live in the dashboard as they're generated.

## Endpoints

| Path | Purpose |
|------|---------|
| `POST /openrouter/{path}` | Passthrough to `openrouter.ai/api/v1/{path}` |
| `GET /dashboard/` | Static HTML dashboard |
| `GET /dashboard/api/active` | Snapshot of active + recent requests |
| `WS /dashboard/ws/active` | Live lifecycle events (start/update/finish) |
| `WS /dashboard/ws/stream/{req_id}` | Live chunks for one request |
| `GET /healthz` | Liveness probe |

## Verified

- Streaming 32 chunks over 0.57s visible in dashboard in real time
- First chunk visible in dashboard +2.5s from request start
- Non-streaming requests (e.g. `GET /models`) also tracked in registry
- Error status codes (404, 429) captured correctly

## Next steps

- [ ] Add Anthropic, OpenAI, Gemini providers (same pattern)
- [ ] Persist request history to PostgreSQL (with optional chunk log)
- [ ] API key management + per-key rate limiting
- [ ] Format-aware rendering (Anthropic SSE event types, Gemini etc.)
- [ ] WebSocket passthrough for Realtime API
