"""Unit tests for Provider.extract_chunk_text — parse one raw upstream chunk
into (text_delta, events_list).

A single raw chunk may contain multiple SSE events (or partial events). The
extractor concatenates text across complete events in the chunk and returns
the full list of parsed JSON events for the JSON Log UI.
"""
from aiproxy.providers.openai import OpenAIProvider
from aiproxy.providers.anthropic import AnthropicProvider
from aiproxy.providers.openrouter import OpenRouterProvider


def test_openai_single_content_chunk() -> None:
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    raw = b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
    text, events = p.extract_chunk_text(raw)
    assert text == "Hello"
    assert len(events) == 1
    assert events[0]["choices"][0]["delta"]["content"] == "Hello"


def test_openai_multiple_events_in_one_chunk() -> None:
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    raw = (
        b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
    )
    text, events = p.extract_chunk_text(raw)
    assert text == "Hello"
    assert len(events) == 2


def test_openai_done_sentinel_is_ignored() -> None:
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    raw = b'data: [DONE]\n\n'
    text, events = p.extract_chunk_text(raw)
    assert text == ""
    assert events == []


def test_openai_role_header_has_no_text() -> None:
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    raw = b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
    text, events = p.extract_chunk_text(raw)
    assert text == ""
    assert len(events) == 1


def test_openai_empty_chunk() -> None:
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    text, events = p.extract_chunk_text(b"")
    assert text == ""
    assert events == []


def test_openai_malformed_json_is_skipped() -> None:
    p = OpenAIProvider(base_url="https://api.openai.com", api_key="x")
    raw = b'data: {not json\n\ndata: {"choices":[{"delta":{"content":"x"}}]}\n\n'
    text, events = p.extract_chunk_text(raw)
    assert text == "x"
    assert len(events) == 1


def test_anthropic_content_block_delta() -> None:
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="x")
    raw = (
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","index":0,'
        b'"delta":{"type":"text_delta","text":"Hello"}}\n\n'
    )
    text, events = p.extract_chunk_text(raw)
    assert text == "Hello"
    assert len(events) == 1
    assert events[0]["type"] == "content_block_delta"


def test_anthropic_message_start_has_no_text() -> None:
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="x")
    raw = (
        b'event: message_start\n'
        b'data: {"type":"message_start","message":{"id":"msg_1","usage":{"input_tokens":10}}}\n\n'
    )
    text, events = p.extract_chunk_text(raw)
    assert text == ""
    assert len(events) == 1
    assert events[0]["type"] == "message_start"


def test_anthropic_multiple_events_in_chunk() -> None:
    p = AnthropicProvider(base_url="https://api.anthropic.com", api_key="x")
    raw = (
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"foo"}}\n\n'
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"bar"}}\n\n'
    )
    text, events = p.extract_chunk_text(raw)
    assert text == "foobar"
    assert len(events) == 2


def test_openrouter_delegates_to_openai_parser() -> None:
    p = OpenRouterProvider(base_url="https://openrouter.ai", api_key="x")
    raw = b'data: {"choices":[{"delta":{"content":"via OR"}}]}\n\n'
    text, events = p.extract_chunk_text(raw)
    assert text == "via OR"
    assert len(events) == 1
