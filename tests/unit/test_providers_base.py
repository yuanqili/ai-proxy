"""Tests for the Provider base class defaults."""
from aiproxy.providers.base import Provider, Usage


class _Dummy(Provider):
    name = "dummy"
    base_url = "https://example.com"
    upstream_path_prefix = "/v1"

    def upstream_api_key(self) -> str:
        return "dummy-key"

    def inject_auth(self, headers):
        headers["x-dummy"] = self.upstream_api_key()
        return headers


def test_map_path_strips_leading_slash() -> None:
    p = _Dummy()
    assert p.map_path("chat/completions") == "/v1/chat/completions"
    assert p.map_path("/chat/completions") == "/v1/chat/completions"


def test_extract_model_from_json_body() -> None:
    p = _Dummy()
    assert p.extract_model(b'{"model":"gpt-4o"}') == "gpt-4o"
    assert p.extract_model(b'{"other":"value"}') is None
    assert p.extract_model(b"not json") is None
    assert p.extract_model(b"") is None


def test_is_streaming_request_json_body() -> None:
    p = _Dummy()
    assert p.is_streaming_request(b'{"stream":true}', {}) is True
    assert p.is_streaming_request(b'{"stream":false}', {}) is False
    assert p.is_streaming_request(b'{"other":1}', {}) is False
    assert p.is_streaming_request(b"", {}) is False


def test_usage_dataclass() -> None:
    u = Usage(input_tokens=10, output_tokens=5)
    assert u.input_tokens == 10
    assert u.cached_tokens is None
