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


import json


def test_rewrite_injects_include_usage_on_streaming() -> None:
    """Streaming request without stream_options → inject {include_usage: true}."""
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    body = b'{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}],"stream":true}'
    out = p.rewrite_request_body(body, is_streaming=True)
    obj = json.loads(out)
    assert obj["stream_options"] == {"include_usage": True}
    # Original fields preserved.
    assert obj["model"] == "gpt-4o-mini"
    assert obj["stream"] is True


def test_rewrite_merges_include_usage_into_existing_stream_options() -> None:
    """Client set stream_options but not include_usage → merge, don't overwrite."""
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    body = b'{"model":"gpt-4o","stream":true,"stream_options":{"some_future_option":42}}'
    out = p.rewrite_request_body(body, is_streaming=True)
    obj = json.loads(out)
    assert obj["stream_options"]["include_usage"] is True
    assert obj["stream_options"]["some_future_option"] == 42


def test_rewrite_respects_explicit_include_usage_true() -> None:
    """Client explicitly set include_usage=true → leave untouched (body returned as-is)."""
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    body = b'{"model":"gpt-4o","stream":true,"stream_options":{"include_usage":true}}'
    out = p.rewrite_request_body(body, is_streaming=True)
    assert out == body  # byte-for-byte


def test_rewrite_respects_explicit_include_usage_false() -> None:
    """Client explicitly opted out → respect their choice, don't force-enable."""
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    body = b'{"model":"gpt-4o","stream":true,"stream_options":{"include_usage":false}}'
    out = p.rewrite_request_body(body, is_streaming=True)
    obj = json.loads(out)
    assert obj["stream_options"]["include_usage"] is False


def test_rewrite_untouched_for_non_streaming() -> None:
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    body = b'{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}'
    out = p.rewrite_request_body(body, is_streaming=False)
    assert out == body


def test_rewrite_untouched_for_invalid_json() -> None:
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    body = b'not json at all'
    out = p.rewrite_request_body(body, is_streaming=True)
    assert out == body


def test_rewrite_untouched_for_empty_body() -> None:
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    assert p.rewrite_request_body(b"", is_streaming=True) == b""


# ── ChunkParser: tool-call streaming ───────────────────────────────────────

def test_chunk_parser_plain_content_flow() -> None:
    from aiproxy.providers.openai import OpenAIChunkParser
    parser = OpenAIChunkParser()
    t1, _ = parser.feed(
        b'data: {"choices":[{"index":0,"delta":{"content":"Hello"}}]}\n\n'
    )
    t2, _ = parser.feed(
        b'data: {"choices":[{"index":0,"delta":{"content":" world"}}]}\n\n'
    )
    assert t1 == "Hello"
    assert t2 == " world"


def test_chunk_parser_tool_call_header_and_arguments() -> None:
    """First chunk has name + initial empty arguments; subsequent chunks only
    carry argument deltas. The parser emits the [tool_use: name] header once
    and streams the argument JSON pieces."""
    from aiproxy.providers.openai import OpenAIChunkParser
    parser = OpenAIChunkParser()
    t_hdr, _ = parser.feed(
        b'data: {"choices":[{"index":0,"delta":{'
        b'"tool_calls":[{"index":0,"id":"call_1","type":"function",'
        b'"function":{"name":"lookup","arguments":""}}]}}]}\n\n'
    )
    t1, _ = parser.feed(
        b'data: {"choices":[{"index":0,"delta":{'
        b'"tool_calls":[{"index":0,"function":{"arguments":"{\\"q\\":"}}]}}]}\n\n'
    )
    t2, _ = parser.feed(
        b'data: {"choices":[{"index":0,"delta":{'
        b'"tool_calls":[{"index":0,"function":{"arguments":"\\"hello\\"}"}}]}}]}\n\n'
    )
    assert "[tool_use: lookup]" in t_hdr
    assert t1 == '{"q":'
    assert t2 == '"hello"}'


def test_chunk_parser_parallel_tool_calls() -> None:
    """Two tool calls at indexes 0 and 1 should each get their own header."""
    from aiproxy.providers.openai import OpenAIChunkParser
    parser = OpenAIChunkParser()
    t1, _ = parser.feed(
        b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,'
        b'"function":{"name":"foo","arguments":"{}"}}]}}]}\n\n'
    )
    t2, _ = parser.feed(
        b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":1,'
        b'"function":{"name":"bar","arguments":"{}"}}]}}]}\n\n'
    )
    # Subsequent delta for index=0 should NOT re-emit the foo header.
    t3, _ = parser.feed(
        b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,'
        b'"function":{"arguments":",\\"x\\":1}"}}]}}]}\n\n'
    )
    assert "[tool_use: foo]" in t1
    assert "[tool_use: bar]" in t2
    assert "[tool_use: foo]" not in t3
    assert t3.endswith(',"x":1}')


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
