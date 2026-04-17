# Operations & deployment

Everything you need to run ai-proxy locally, deploy it to a server behind
Caddy, hook it into an existing Grafana stack, and operate it over time.

The **server specifics** here (`tencent-singapore`, `sat1600ap5.online`,
`/home/ubuntu/stacks/`) reflect the current production deployment. If you
move to a different host, substitute values as needed — the generic flow is
the same.

## Quick reference

| Thing | Where |
|---|---|
| Public API base | `https://ai.sat1600ap5.online` |
| Dashboard | `https://ai.sat1600ap5.online/dashboard/` |
| Metrics (Prometheus format) | `aiproxy:8000/metrics` — internal only, external `/metrics` returns 404 via Caddy |
| Grafana | `https://grafana.sat1600ap5.online` → folder `ai-proxy` |
| Server | `ssh tencent-singapore` |
| App stack dir | `/home/ubuntu/stacks/ai-proxy/` |
| Caddy stack dir | `/home/ubuntu/stacks/caddy/` |
| Logs / monitoring stack | `/home/ubuntu/stacks/logs-stack/` |
| Repo | `https://github.com/yuanqili/ai-proxy` |

## Local development

```bash
cp .env.example .env                            # fill provider keys + PROXY_MASTER_KEY
docker compose -f compose.local.yml up --build -d
open http://127.0.0.1:8000/dashboard/
```

- Binds `127.0.0.1:8000` only (no external exposure).
- SQLite lives at `./data/aiproxy.db` (bind-mount, survives `compose down`).
- `SECURE_COOKIES=false` in compose (no HTTPS locally).
- No Grafana locally — metrics scrapable at `http://127.0.0.1:8000/metrics`
  but you'd need to point your own Prometheus at it.

## Server deployment

### First-time setup

```bash
ssh tencent-singapore

# 1. Clone
cd /home/ubuntu/stacks
git clone https://github.com/yuanqili/ai-proxy.git
cd ai-proxy

# 2. .env — scp provider keys from your laptop (where you keep them):
scp /path/to/.env tencent-singapore:/home/ubuntu/stacks/ai-proxy/.env
ssh tencent-singapore 'chmod 600 /home/ubuntu/stacks/ai-proxy/.env'

# Then overwrite the proxy-local secrets so they're generated server-side
# and never hit your shell history. Command substitution happens on the
# server because the whole command is single-quoted:
ssh tencent-singapore 'cd /home/ubuntu/stacks/ai-proxy && \
    sed -i "s|^PROXY_MASTER_KEY=.*|PROXY_MASTER_KEY=$(openssl rand -hex 32)|" .env && \
    sed -i "s|^SESSION_SECRET=.*|SESSION_SECRET=$(openssl rand -hex 32)|" .env'

# 3. Build + start
ssh tencent-singapore 'cd /home/ubuntu/stacks/ai-proxy && \
    docker compose -f compose.server.yml up --build -d'

# 4. Verify
curl -sS https://ai.sat1600ap5.online/healthz        # → {"status":"ok"}
# /metrics is 404 externally; scrape from inside the server or via Grafana:
ssh tencent-singapore 'docker exec logs-stack-prometheus-1 \
    wget -q -O - "http://localhost:9090/api/v1/targets" | head'
```

### Caddy

Already configured at `caddy/sites/002-bollegecoard.caddy`:

```caddy
ai.sat1600ap5.online {
    reverse_proxy aiproxy:8000
    encode zstd gzip
}
```

The service name in `compose.server.yml` is `aiproxy` (matching the upstream
in the Caddy rule) and it joins the external `caddy-net` bridge. No Caddy
changes needed when redeploying ai-proxy.

### Updating (zero-downtime-ish)

```bash
ssh tencent-singapore '
  cd /home/ubuntu/stacks/ai-proxy && \
  git pull && \
  docker compose -f compose.server.yml up --build -d
'
```

Build is incremental — only re-runs from the first changed layer. Dependency
changes (pyproject.toml / uv.lock) invalidate everything below and take a
few minutes; src-only changes are ~20 seconds.

## Grafana integration

### How it works

The `logs-stack` Grafana container has this bind mount (added to its
compose):

```yaml
volumes:
  - grafana-data:/var/lib/grafana
  - /home/ubuntu/stacks/ai-proxy/deploy/grafana/provisioning:/etc/grafana/provisioning:ro
```

That makes `deploy/grafana/provisioning/` in this repo the source of truth
for:

- `datasources/prometheus.yaml` — `Prometheus` datasource pinned to
  `uid: prometheus` (used by the dashboard JSON). Also declares
  `deleteDatasources` to wipe any pre-existing UI-created row with the same
  name, since Grafana can't "upgrade" a datasource to a new uid.
- `dashboards/provider.yaml` — tells Grafana to watch the dashboards
  directory with `updateIntervalSeconds: 30` (edits go live within 30s, no
  restart).
- `dashboards/aiproxy-overview.json` — 11-panel overview dashboard.
- `alerting/aiproxy-rules.yaml` — 3 alert rules (instance down, high error
  rate, slow p95). Rules appear in Grafana → Alerting → Alert rules under
  folder `ai-proxy`. Contact points / notification policies are NOT
  provisioned — configure Slack / email via the Grafana UI when you want
  to receive pages.

Prometheus already had `aiproxy` in its scrape config (`logs-stack/prometheus.yml`),
so it picks up the `/metrics` endpoint automatically. Retention is pinned
to 15 days via `--storage.tsdb.retention.time=15d` on the Prometheus
service command.

### Updating the dashboard

Edit the JSON in this repo, `git push`, then on server:

```bash
cd /home/ubuntu/stacks/ai-proxy && git pull
# Grafana re-loads within 30s — no restart.
```

### Resetting Grafana datasource config

If the provisioner gets into a bad state ("data source not found" or UID
mismatch), restart Grafana so it replays provisioning:

```bash
ssh tencent-singapore 'cd /home/ubuntu/stacks/logs-stack && docker compose restart grafana'
ssh tencent-singapore 'docker logs logs-stack-grafana-1 --tail 30 | grep -i datasource'
# Expect: "deleted datasource based on configuration name=Prometheus"
#       + "inserting datasource from configuration name=Prometheus uid=prometheus"
```

## Client onboarding

### Get the master key (one time, to log into the dashboard)

```bash
ssh tencent-singapore 'grep "^PROXY_MASTER_KEY=" /home/ubuntu/stacks/ai-proxy/.env | cut -d= -f2'
```

Paste into `https://ai.sat1600ap5.online/dashboard/`. If you want something
memorable, replace the value in `.env` and `docker compose restart aiproxy`.

### Create a client API key

Easiest: log into the dashboard → **Keys** tab → `+ new`.

Scripted (so the key never lands in your shell history — the output stays on
the server in a root-only file):

```bash
ssh tencent-singapore '
  docker cp /home/ubuntu/stacks/ai-proxy/scripts/create_api_key.py aiproxy:/tmp/create_api_key.py && \
  docker exec aiproxy python /tmp/create_api_key.py --name <name> --note "<note>" \
    > /home/ubuntu/stacks/ai-proxy/.client-key && \
  chmod 600 /home/ubuntu/stacks/ai-proxy/.client-key
'
# then retrieve from your laptop:
ssh tencent-singapore 'cat /home/ubuntu/stacks/ai-proxy/.client-key && rm /home/ubuntu/stacks/ai-proxy/.client-key'
```

### Have each caller use its own key

Benefits:

- Dashboard requests list filters by API key — see who's burning budget.
  (Prometheus metrics stay keyed on `provider` / `model` / `status` for
  cardinality safety, so the breakdown only exists in the SQLite side.)
- Deactivating one caller doesn't affect others.
- Rotations stay scoped.

Per-caller tagging via private headers is also supported — see README for
`X-AIProxy-Labels: prod,foo,v2` and `X-AIProxy-Note: ...`.

### SDK base URL

Point your SDK at `https://ai.sat1600ap5.online/<provider>/` where
`<provider>` is `openai`, `anthropic`, or `openrouter`. Send your client
API key as `Authorization: Bearer sk-aiprx-...`. Upstream paths work
unchanged (e.g. `/openai/chat/completions`).

## Ongoing operations

### Backups

SQLite is in the named volume `aiproxy-data`. Add a nightly backup. The
runtime image is `python:3.12-slim` and does **not** include the `sqlite3`
CLI, so use Python's stdlib backup API (which goes through SQLite's online
backup, safe against a live writer):

```bash
# crontab -e on the server
0 3 * * * docker exec aiproxy python -c "import sqlite3,datetime; \
src=sqlite3.connect('/app/data/aiproxy.db'); \
dst=sqlite3.connect('/app/data/backup-'+datetime.date.today().isoformat()+'.db'); \
src.backup(dst); src.close(); dst.close()"
```

Rotate by filename (`find /var/lib/docker/volumes/.../backup-*.db -mtime +14 -delete`)
or copy off-host — note that keeping backups inside the same volume gives
you no disaster-recovery benefit, so wire up an off-host copy once this
matters.

### Retention — Prometheus

Currently `--storage.tsdb.retention.time=15d` in `logs-stack/docker-compose.yml`.
Bump to 90d (or longer) if you want longer trend history on cost/tokens —
storage cost is tiny for this signal density.

### Retention — SQLite bodies

Controlled via the runtime config key `retention.full_count` (Dashboard →
Settings → Retention). Request/response bodies older than the Nth-most-recent
row are stripped; metadata + chunk counts + tokens remain.

### Log rotation

`compose.server.yml` caps the aiproxy container's JSON log driver at
`max-size=20m, max-file=5` (100 MB total). The `caddy` and `logs-stack`
containers follow their own configs; Promtail tails them all into Loki.

### Metrics scrape targets (Prometheus)

Edit `logs-stack/prometheus.yml`, then:

```bash
ssh tencent-singapore 'cd /home/ubuntu/stacks/logs-stack && docker compose restart prometheus'
```

## Troubleshooting cheat sheet

| Symptom | Likely cause | Fix |
|---|---|---|
| `docker compose build` times out on `uv sync` | PyPI slow, BuildKit cache corruption | `docker builder prune -a -f && retry`. `UV_HTTP_TIMEOUT=300` is already baked into the Dockerfile. |
| Grafana panels say "datasource not found" | Datasource UID mismatch between YAML and dashboard JSON | Dashboard JSON uses `uid: prometheus`; `prometheus.yaml` now pins it + has `deleteDatasources`. Restart Grafana. |
| `aiproxy` target stays DOWN in Prometheus | Container unreachable on `caddy-net` | `docker exec logs-stack-prometheus-1 wget -qO- http://aiproxy:8000/healthz` — should return `{"status":"ok"}`. |
| `ai.sat1600ap5.online` 502 Bad Gateway | Container not on `caddy-net`, or service name drift | `docker inspect aiproxy \| grep -i caddy-net` — service name must stay `aiproxy`. |
| Dashboard login refuses valid key | `SECURE_COOKIES=true` but plaintext HTTP | Caddy auto-HTTPS; confirm the URL is `https://` not `http://`. |
| `docker compose up` says "network caddy-net declared as external" | Caddy stack not running | `cd /home/ubuntu/stacks/caddy && docker compose up -d`. |

## Known gaps (not blocking production, worth knowing)

- **No rate limit on `/dashboard/login`.** Master key is 64-hex random so
  brute-force is impractical, but a shorter key would be a liability.
  Adding a simple per-IP counter is a future P1.
- **No scheduled DB backup.** The Python recipe above is documented but
  not wired into cron / a systemd timer, and there's no off-host copy.
  A disk failure right now loses all request history. Wire it up when
  historical data becomes load-bearing.
- **No retry-request endpoint in dashboard.** Deferred from Phase 5.
- **Prometheus + app are single-node.** Fine for self-hosted; if you ever
  horizontally scale, you'd need Redis/NATS for the StreamBus and a
  managed TSDB.
- **No alert contact point provisioned.** The three alert rules in
  `deploy/grafana/provisioning/alerting/aiproxy-rules.yaml` fire in
  Grafana's Alerts UI but don't notify anyone until a Slack / email
  contact point is wired via the Grafana UI and routed to the `ai-proxy`
  folder. Add one when you're ready.
- **Old `openai-proxy` stack archived at**
  `/home/ubuntu/archives/openai-proxy-YYYY-MM-DD.tar.gz` (chmod 600).
  The source dir at `/home/ubuntu/stacks/openai-proxy/` is inert — safe
  to `rm -rf` whenever. The Caddy site is already `.disabled`.

## Secrets hygiene reminders

- `.env` on server: `chmod 600`, `ubuntu:ubuntu`, gitignored.
- `PROXY_MASTER_KEY` and `SESSION_SECRET`: generate server-side with
  `openssl rand -hex 32` so the values never leave the server.
- Client API keys are stored as plaintext in the `api_keys` table (readable
  back via the Keys tab API), not hashed. That means a DB leak exposes every
  client key — treat `.db` backups with the same care as `.env`. Rotate by
  creating a new key and deactivating the old one, not by editing the
  existing row.
- Never commit `.env`, `.client-key`, or any `*.db` file.
