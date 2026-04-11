"""HTTP hop-by-hop header handling for proxy forwarding."""

from __future__ import annotations

# Hop-by-hop headers defined by RFC 2616 section 13.5.1, plus Host/Content-Length
# which the HTTP client will set correctly for the upstream request.
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)

# Private header namespace the proxy reserves for client-supplied classification
# metadata (labels, notes, future fields). Stripped before forwarding so the
# upstream never sees them.
AIPROXY_HEADER_PREFIX = "x-aiproxy-"


def _is_aiproxy_header(name: str) -> bool:
    return name.lower().startswith(AIPROXY_HEADER_PREFIX)


def clean_upstream_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip hop-by-hop + X-AIProxy-* headers from a client request before
    forwarding upstream."""
    return {
        k: v for k, v in headers.items()
        if k.lower() not in _HOP_BY_HOP and not _is_aiproxy_header(k)
    }


def clean_downstream_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip hop-by-hop headers from an upstream response before returning to client."""
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}
