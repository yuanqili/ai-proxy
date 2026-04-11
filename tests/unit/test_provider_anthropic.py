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


from aiproxy.providers.base import Usage


def test_parse_usage_non_streaming() -> None:
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="x")
    body = b'{"content":[{"type":"text","text":"hi"}],"usage":{"input_tokens":15,"output_tokens":7,"cache_read_input_tokens":3}}'
    u = p.parse_usage(is_streaming=False, resp_body=body, chunks=None)
    assert u is not None
    assert u.input_tokens == 15
    assert u.output_tokens == 7
    assert u.cached_tokens == 3


def test_parse_usage_streaming_message_delta() -> None:
    """Anthropic streaming sends usage in the message_delta event near the end.

    The `message_start` event carries an initial input_tokens count; the final
    `message_delta` event carries the output_tokens.
    """
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="x")
    chunks = [
        b'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":20,"output_tokens":1}}}\n\n',
        b'event: content_block_start\ndata: {"type":"content_block_start"}\n\n',
        b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}\n\n',
        b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":" world"}}\n\n',
        b'event: content_block_stop\ndata: {"type":"content_block_stop"}\n\n',
        b'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":12}}\n\n',
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    ]
    u = p.parse_usage(is_streaming=True, resp_body=None, chunks=chunks)
    assert u is not None
    assert u.input_tokens == 20
    assert u.output_tokens == 12


def test_parse_usage_streaming_without_usage_returns_none() -> None:
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="x")
    chunks = [b'event: content_block_delta\ndata: {"delta":{"text":"hi"}}\n\n']
    u = p.parse_usage(is_streaming=True, resp_body=None, chunks=chunks)
    assert u is None


# ── ChunkParser: text-only (regression guard) ──────────────────────────────

def test_chunk_parser_plain_text_flow() -> None:
    """Pure-text streams still extract text_delta exactly as before."""
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="x")
    parser = p.make_chunk_parser()
    t1, _ = parser.feed(
        b'event: message_start\ndata: {"type":"message_start"}\n\n'
    )
    t2, _ = parser.feed(
        b'event: content_block_start\n'
        b'data: {"type":"content_block_start","index":0,"content_block":{"type":"text"}}\n\n'
    )
    t3, _ = parser.feed(
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}\n\n'
    )
    t4, _ = parser.feed(
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" world"}}\n\n'
    )
    assert t1 == ""           # message_start carries no text
    assert t2 == ""           # text content_block_start has no header
    assert t3 == "Hello"
    assert t4 == " world"


# ── ChunkParser: tool_use with input_json_delta ────────────────────────────

def test_chunk_parser_tool_use_emits_header_and_json_deltas() -> None:
    """Tool-use blocks: the parser must emit a synthetic header when the
    content_block_start arrives AND stream each input_json_delta's partial_json
    into text_delta so the player screen can animate the argument generation.
    """
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="x")
    parser = p.make_chunk_parser()
    parser.feed(b'event: message_start\ndata: {"type":"message_start"}\n\n')
    t_header, _ = parser.feed(
        b'event: content_block_start\n'
        b'data: {"type":"content_block_start","index":0,'
        b'"content_block":{"type":"tool_use","name":"submit_topic_taxonomy","id":"toolu_1"}}\n\n'
    )
    assert "[tool_use: submit_topic_taxonomy]" in t_header

    t1, _ = parser.feed(
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","index":0,'
        b'"delta":{"type":"input_json_delta","partial_json":"{\\"tags\\":"}}\n\n'
    )
    t2, _ = parser.feed(
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","index":0,'
        b'"delta":{"type":"input_json_delta","partial_json":"[{\\"id\\":\\"a\\"}]}"}}\n\n'
    )
    assert t1 == '{"tags":'
    assert t2 == '[{"id":"a"}]}'


def test_chunk_parser_mixed_text_then_tool_use() -> None:
    """A response that starts with a text block and then emits a tool_use
    block: text first, then a newline-separated header + JSON."""
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="x")
    parser = p.make_chunk_parser()
    parser.feed(b'event: message_start\ndata: {"type":"message_start"}\n\n')
    parser.feed(
        b'event: content_block_start\n'
        b'data: {"type":"content_block_start","index":0,"content_block":{"type":"text"}}\n\n'
    )
    t_text, _ = parser.feed(
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Using tool."}}\n\n'
    )
    t_hdr, _ = parser.feed(
        b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
        b'event: content_block_start\n'
        b'data: {"type":"content_block_start","index":1,'
        b'"content_block":{"type":"tool_use","name":"lookup","id":"toolu_2"}}\n\n'
    )
    t_json, _ = parser.feed(
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","index":1,'
        b'"delta":{"type":"input_json_delta","partial_json":"{\\"q\\":\\"x\\"}"}}\n\n'
    )
    assert t_text == "Using tool."
    assert "[tool_use: lookup]" in t_hdr
    assert t_json == '{"q":"x"}'
