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


def clean_upstream_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip hop-by-hop headers from a client request before forwarding upstream."""
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


def clean_downstream_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip hop-by-hop headers from an upstream response before returning to client."""
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}
