"""Unit tests for stateful Provider chunk parsers.

Each provider returns a `ChunkParser` from `make_chunk_parser()`. Callers
feed raw upstream chunks in order; the parser buffers partial SSE frames
across feeds and returns (text_delta, events) for each complete event
as it lands.
"""
from aiproxy.providers.anthropic import AnthropicProvider
from aiproxy.providers.openai import OpenAIProvider
from aiproxy.providers.openrouter import OpenRouterProvider


def _openai() -> OpenAIProvider:
    return OpenAIProvider(base_url="https://api.openai.com", api_key="x")


def _anthropic() -> AnthropicProvider:
    return AnthropicProvider(base_url="https://api.anthropic.com", api_key="x")


def test_openai_single_complete_chunk() -> None:
    parser = _openai().make_chunk_parser()
    raw = b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
    text, events = parser.feed(raw)
    assert text == "Hello"
    assert len(events) == 1
    assert events[0]["choices"][0]["delta"]["content"] == "Hello"


def test_openai_multiple_events_in_one_chunk() -> None:
    parser = _openai().make_chunk_parser()
    raw = (
        b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
    )
    text, events = parser.feed(raw)
    assert text == "Hello"
    assert len(events) == 2


def test_openai_event_split_across_two_chunks() -> None:
    """Real-world streaming: TCP chunks DO NOT align with SSE event boundaries.
    An event begun in one chunk finishes in the next and its text is attributed
    to the chunk that closed it."""
    parser = _openai().make_chunk_parser()
    chunk1 = b'data: {"choices":[{"delta":{"cont'
    chunk2 = b'ent":"Hello"}}]}\n\n'
    t1, e1 = parser.feed(chunk1)
    assert t1 == ""
    assert e1 == []
    t2, e2 = parser.feed(chunk2)
    assert t2 == "Hello"
    assert len(e2) == 1


def test_openai_three_way_split() -> None:
    parser = _openai().make_chunk_parser()
    chunk1 = b'data: {"choices":[{"delta":'
    chunk2 = b'{"content":"Hello"'
    chunk3 = b'}}]}\n\n'
    text = ""
    for ch in (chunk1, chunk2, chunk3):
        t, _ = parser.feed(ch)
        text += t
    assert text == "Hello"


def test_openai_done_sentinel_is_ignored() -> None:
    parser = _openai().make_chunk_parser()
    text, events = parser.feed(b'data: [DONE]\n\n')
    assert text == ""
    assert events == []


def test_openai_role_header_has_no_text() -> None:
    parser = _openai().make_chunk_parser()
    text, events = parser.feed(b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n')
    assert text == ""
    assert len(events) == 1


def test_openai_empty_chunk() -> None:
    parser = _openai().make_chunk_parser()
    text, events = parser.feed(b"")
    assert text == ""
    assert events == []


def test_openai_malformed_json_is_skipped() -> None:
    parser = _openai().make_chunk_parser()
    raw = b'data: {not json\n\ndata: {"choices":[{"delta":{"content":"x"}}]}\n\n'
    text, events = parser.feed(raw)
    assert text == "x"
    assert len(events) == 1


def test_openai_realistic_multi_chunk_stream() -> None:
    """Simulate a real openai stream with ~60% of events split across chunks."""
    parser = _openai().make_chunk_parser()
    pieces = [
        b'data: {"choices":[{"delta":{"role":"assistant","content":""}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"Cats"}}]}\n',
        b'\ndata: {"choices":[{"delta":{"content":" are"}}]}',
        b'\n\ndata: {"choices":[{"delta":{"cont',
        b'ent":" playful"}}]}\n\ndata: {"choices":[{"delta":{"content":" and"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":" mysterious"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":" companions"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"."}}]}\n\ndata: [DONE]\n\n',
    ]
    full = ""
    for p in pieces:
        t, _ = parser.feed(p)
        full += t
    assert full == "Cats are playful and mysterious companions."


def test_anthropic_content_block_delta() -> None:
    parser = _anthropic().make_chunk_parser()
    raw = (
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","index":0,'
        b'"delta":{"type":"text_delta","text":"Hello"}}\n\n'
    )
    text, events = parser.feed(raw)
    assert text == "Hello"
    assert len(events) == 1
    assert events[0]["type"] == "content_block_delta"


def test_anthropic_message_start_has_no_text() -> None:
    parser = _anthropic().make_chunk_parser()
    raw = (
        b'event: message_start\n'
        b'data: {"type":"message_start","message":{"id":"msg_1","usage":{"input_tokens":10}}}\n\n'
    )
    text, events = parser.feed(raw)
    assert text == ""
    assert len(events) == 1
    assert events[0]["type"] == "message_start"


def test_anthropic_multiple_events_in_chunk() -> None:
    parser = _anthropic().make_chunk_parser()
    raw = (
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"foo"}}\n\n'
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"bar"}}\n\n'
    )
    text, events = parser.feed(raw)
    assert text == "foobar"
    assert len(events) == 2


def test_anthropic_event_split_across_chunks() -> None:
    parser = _anthropic().make_chunk_parser()
    c1 = b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type'
    c2 = b'":"text_delta","text":"spans"}}\n\n'
    t1, _ = parser.feed(c1)
    t2, _ = parser.feed(c2)
    assert t1 == ""
    assert t2 == "spans"


def test_openrouter_uses_openai_parser() -> None:
    parser = OpenRouterProvider(base_url="https://openrouter.ai", api_key="x").make_chunk_parser()
    raw = b'data: {"choices":[{"delta":{"content":"via OR"}}]}\n\n'
    text, events = parser.feed(raw)
    assert text == "via OR"
    assert len(events) == 1
