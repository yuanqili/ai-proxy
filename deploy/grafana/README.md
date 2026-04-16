# Grafana provisioning for ai-proxy

Mount this directory into the Grafana container at `/etc/grafana/provisioning`
and Grafana will pick up the datasource + dashboard automatically (no UI clicks).

## One-time server-side setup

Edit `logs-stack/docker-compose.yml` — add the `grafana` service one bind mount
pointing at this tree. If the repo is cloned at `/home/ubuntu/stacks/ai-proxy/`:

```yaml
grafana:
  # ... existing config ...
  volumes:
    - grafana-data:/var/lib/grafana
    - /home/ubuntu/stacks/ai-proxy/deploy/grafana/provisioning:/etc/grafana/provisioning:ro
```

Then recreate the container:

```bash
cd /home/ubuntu/stacks/logs-stack
docker compose up -d grafana
```

## Updating the dashboard

Edit `dashboards/aiproxy-overview.json` in this repo, push to main, pull on the
server. `updateIntervalSeconds: 30` in `provider.yaml` means Grafana auto-reloads
within 30 seconds — no container restart needed.

## What's inside

- `datasources/prometheus.yaml` — points at the `prometheus` service on the
  `observability-net` Docker network (same network the Grafana container is on).
- `dashboards/provider.yaml` — tells Grafana to watch this directory for JSON
  dashboard files.
- `dashboards/aiproxy-overview.json` — 11-panel overview dashboard.
