"""Tests for AnthropicProvider."""
from aiproxy.providers.anthropic import AnthropicProvider


def test_inject_auth_sets_x_api_key_and_version() -> None:
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="sk-ant-test")
    headers = {"content-type": "application/json", "authorization": "Bearer leftover"}
    out = p.inject_auth(headers)
    assert out["x-api-key"] == "sk-ant-test"
    assert out["anthropic-version"] == "2023-06-01"
    # Client's bearer token (which was the proxy key) must be removed
    assert "authorization" not in out


def test_map_path_has_empty_prefix() -> None:
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="x")
    # Anthropic's client uses /v1/messages directly, so we pass through as-is
    assert p.map_path("v1/messages") == "/v1/messages"
    assert p.map_path("/v1/messages") == "/v1/messages"


def test_name_and_prefix() -> None:
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="x")
    assert p.name == "anthropic"
    assert p.upstream_path_prefix == ""
