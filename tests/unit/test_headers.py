"""Tests for aiproxy.core.headers hop-by-hop stripping."""
from aiproxy.core.headers import clean_downstream_headers, clean_upstream_headers


def test_strips_hop_by_hop_headers() -> None:
    src = {
        "connection": "keep-alive",
        "host": "example.com",
        "content-length": "100",
        "content-type": "application/json",
        "authorization": "Bearer abc",
        "transfer-encoding": "chunked",
        "keep-alive": "timeout=5",
        "te": "trailers",
        "upgrade": "h2c",
        "proxy-authenticate": "Basic",
        "proxy-authorization": "Basic xyz",
        "trailers": "expires",
    }
    out = clean_upstream_headers(src)
    assert "connection" not in out
    assert "host" not in out
    assert "content-length" not in out
    assert "transfer-encoding" not in out
    assert "keep-alive" not in out
    assert "te" not in out
    assert "upgrade" not in out
    assert "proxy-authenticate" not in out
    assert "proxy-authorization" not in out
    assert "trailers" not in out
    # Non-hop-by-hop headers are kept
    assert out["content-type"] == "application/json"
    assert out["authorization"] == "Bearer abc"


def test_clean_downstream_same_logic() -> None:
    src = {"connection": "close", "x-custom": "ok"}
    out = clean_downstream_headers(src)
    assert "connection" not in out
    assert out["x-custom"] == "ok"


def test_case_insensitive() -> None:
    src = {"Connection": "close", "CONTENT-LENGTH": "10", "X-Foo": "bar"}
    out = clean_upstream_headers(src)
    assert "Connection" not in out
    assert "CONTENT-LENGTH" not in out
    assert out["X-Foo"] == "bar"


def test_strips_aiproxy_namespace() -> None:
    """Private X-AIProxy-* headers must never be forwarded to upstreams."""
    src = {
        "authorization": "Bearer abc",
        "content-type": "application/json",
        "x-aiproxy-labels": "prod,v1",
        "x-aiproxy-note": "regression test",
        "X-AIProxy-Future-Field": "something",
    }
    out = clean_upstream_headers(src)
    assert out == {
        "authorization": "Bearer abc",
        "content-type": "application/json",
    }
