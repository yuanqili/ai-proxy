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


from aiproxy.providers.base import Usage


def test_parse_usage_non_streaming() -> None:
    """Non-streaming OpenAI response has usage in the top level."""
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    body = b'{"choices":[{"message":{"content":"hi"}}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}'
    u = p.parse_usage(is_streaming=False, resp_body=body, chunks=None)
    assert u is not None
    assert u.input_tokens == 10
    assert u.output_tokens == 5


def test_parse_usage_streaming_with_include_usage() -> None:
    """Streaming OpenAI responses include usage only when stream_options.include_usage=true.
    It appears in the last SSE data event before [DONE]."""
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    chunks = [
        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n',
        b'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":8}}\n\n',
        b'data: [DONE]\n\n',
    ]
    u = p.parse_usage(is_streaming=True, resp_body=None, chunks=chunks)
    assert u is not None
    assert u.input_tokens == 12
    assert u.output_tokens == 8


def test_parse_usage_streaming_without_usage_returns_none() -> None:
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    chunks = [
        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
        b'data: [DONE]\n\n',
    ]
    u = p.parse_usage(is_streaming=True, resp_body=None, chunks=chunks)
    assert u is None


def test_parse_usage_cached_and_reasoning_tokens() -> None:
    """OpenAI returns cached_tokens under prompt_tokens_details, reasoning under completion_tokens_details."""
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    body = (
        b'{"usage":{"prompt_tokens":100,"completion_tokens":50,'
        b'"prompt_tokens_details":{"cached_tokens":40},'
        b'"completion_tokens_details":{"reasoning_tokens":20}}}'
    )
    u = p.parse_usage(is_streaming=False, resp_body=body, chunks=None)
    assert u.input_tokens == 100
    assert u.output_tokens == 50
    assert u.cached_tokens == 40
    assert u.reasoning_tokens == 20
