"""Tests for OpenRouterProvider."""
from aiproxy.providers.openrouter import OpenRouterProvider


def test_inject_auth_sets_bearer_and_referer() -> None:
    p = OpenRouterProvider(base_url="https://openrouter.ai", api_key="sk-or-test")
    headers = {"content-type": "application/json"}
    out = p.inject_auth(headers)
    assert out["authorization"] == "Bearer sk-or-test"
    assert "http-referer" in out


def test_map_path() -> None:
    p = OpenRouterProvider(base_url="https://openrouter.ai", api_key="x")
    assert p.map_path("chat/completions") == "/api/v1/chat/completions"
    assert p.map_path("models") == "/api/v1/models"


def test_name_and_prefix() -> None:
    p = OpenRouterProvider(base_url="https://openrouter.ai", api_key="x")
    assert p.name == "openrouter"
    assert p.upstream_path_prefix == "/api/v1"


from aiproxy.providers.base import Usage


def test_parse_usage_matches_openai_format() -> None:
    """OpenRouter is OpenAI-compatible, so it reuses the OpenAI parser."""
    p = OpenRouterProvider(base_url="https://openrouter.ai", api_key="x")
    body = b'{"usage":{"prompt_tokens":7,"completion_tokens":3}}'
    u = p.parse_usage(is_streaming=False, resp_body=body, chunks=None)
    assert u.input_tokens == 7
    assert u.output_tokens == 3
