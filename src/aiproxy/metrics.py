"""Prometheus metrics for the proxy.

Exposed at GET /metrics (unauthenticated — intended to be reachable only from
the internal network where Prometheus scrapes).

Metrics are module-level singletons registered on the default Prometheus
registry, so they persist across the app lifetime and are shared by whichever
module emits them.

Cardinality notes:
  - `provider` is bounded to the 3 registered providers.
  - `model` is user-driven but practically bounded to a few dozen names even
    on OpenRouter. We accept it for the observability value.
  - We DO NOT expose user-supplied `X-AIProxy-Labels` as Prometheus labels —
    those are unbounded and would blow up cardinality.
"""
from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

__all__ = [
    "REQUESTS_TOTAL",
    "REQUEST_DURATION_SECONDS",
    "TTFT_SECONDS",
    "TOKENS_TOTAL",
    "COST_USD_TOTAL",
    "ACTIVE_REQUESTS",
    "ERRORS_TOTAL",
    "record_completion",
    "render_latest",
    "METRICS_CONTENT_TYPE",
]

METRICS_CONTENT_TYPE = CONTENT_TYPE_LATEST


REQUESTS_TOTAL = Counter(
    "aiproxy_requests_total",
    "Total proxied requests, terminal-state only.",
    labelnames=("provider", "model", "status", "stream"),
)

REQUEST_DURATION_SECONDS = Histogram(
    "aiproxy_request_duration_seconds",
    "End-to-end request duration (client dispatch to finalize).",
    labelnames=("provider", "model"),
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

TTFT_SECONDS = Histogram(
    "aiproxy_ttft_seconds",
    "Time-to-first-byte on streaming responses (first chunk minus upstream dispatch).",
    labelnames=("provider", "model"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)

TOKENS_TOTAL = Counter(
    "aiproxy_tokens_total",
    "Tokens reported by upstream usage blocks, split by kind.",
    labelnames=("provider", "model", "kind"),  # kind = input | output | cached | reasoning
)

COST_USD_TOTAL = Counter(
    "aiproxy_cost_usd_total",
    "Cost in USD, computed from the versioned pricing table at finalize.",
    labelnames=("provider", "model"),
)

ACTIVE_REQUESTS = Gauge(
    "aiproxy_active_requests",
    "Requests currently in flight (registered but not yet finalized).",
)

ERRORS_TOTAL = Counter(
    "aiproxy_errors_total",
    "Finalized requests with error_class set.",
    labelnames=("provider", "error_class"),
)


def record_completion(
    *,
    provider: str,
    model: str | None,
    status: str,
    is_streaming: bool,
    duration_s: float | None,
    ttft_s: float | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_tokens: int | None,
    reasoning_tokens: int | None,
    cost_usd: float | None,
    error_class: str | None,
) -> None:
    """Emit the full set of metrics for a terminal-state request."""
    m = model or "unknown"
    stream = "true" if is_streaming else "false"
    REQUESTS_TOTAL.labels(provider, m, status, stream).inc()
    if duration_s is not None and duration_s >= 0:
        REQUEST_DURATION_SECONDS.labels(provider, m).observe(duration_s)
    if ttft_s is not None and ttft_s >= 0:
        TTFT_SECONDS.labels(provider, m).observe(ttft_s)
    if input_tokens:
        TOKENS_TOTAL.labels(provider, m, "input").inc(input_tokens)
    if output_tokens:
        TOKENS_TOTAL.labels(provider, m, "output").inc(output_tokens)
    if cached_tokens:
        TOKENS_TOTAL.labels(provider, m, "cached").inc(cached_tokens)
    if reasoning_tokens:
        TOKENS_TOTAL.labels(provider, m, "reasoning").inc(reasoning_tokens)
    if cost_usd:
        COST_USD_TOTAL.labels(provider, m).inc(cost_usd)
    if error_class:
        ERRORS_TOTAL.labels(provider, error_class).inc()


def render_latest() -> bytes:
    """Render the current registry snapshot in Prometheus text format."""
    return generate_latest()
