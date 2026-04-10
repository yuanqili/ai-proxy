"""Tests for OpenAIProvider."""
from aiproxy.providers.openai import OpenAIProvider


def test_inject_auth_sets_bearer(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    # Re-instantiate to pick up env
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="sk-test")
    headers = {"content-type": "application/json"}
    out = p.inject_auth(headers)
    assert out["authorization"] == "Bearer sk-test"
    assert out["content-type"] == "application/json"


def test_map_path() -> None:
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    assert p.map_path("chat/completions") == "/v1/chat/completions"
    assert p.map_path("models") == "/v1/models"


def test_name_and_prefix() -> None:
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    assert p.name == "openai"
    assert p.upstream_path_prefix == "/v1"
