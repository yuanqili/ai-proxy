"""Anthropic provider — x-api-key auth, no path prefix, custom SSE format."""
from __future__ import annotations

import json

from aiproxy.providers.base import (
    ChunkParser,
    Provider,
    Usage,
    _iter_complete_sse_events,
    iter_sse_data_events,
)

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicChunkParser(ChunkParser):
    """Stateful SSE parser for Anthropic streams.

    Extracts the progressive "payload" of every content block — regardless of
    whether it's a plain text block (``delta.type == "text_delta"``) or a
    tool-use block (``delta.type == "input_json_delta"``, where the tool-call
    JSON arguments are generated token by token). A tool-use block is
    prefixed with a synthetic ``[tool_use: <name>]`` header emitted when the
    corresponding ``content_block_start`` arrives, so the player screen and
    the streaming-response preview render the tool invocation in
    chronological order.

    The parser is stateful across chunks (buffers partial SSE frames) AND
    across content blocks (remembers which index is which type).
    """

    def __init__(self) -> None:
        self._buf = b""
        # index → {"type": str, "name": str | None}; populated on content_block_start.
        self._blocks: dict[int, dict] = {}

    def feed(self, chunk: bytes) -> tuple[str, list[dict]]:
        self._buf += chunk
        events, self._buf = _iter_complete_sse_events(self._buf)
        parts: list[str] = []
        for ev in events:
            t = ev.get("type")
            if t == "content_block_start":
                idx = ev.get("index")
                block = ev.get("content_block") or {}
                btype = block.get("type")
                if isinstance(idx, int) and isinstance(btype, str):
                    self._blocks[idx] = {"type": btype, "name": block.get("name")}
                    if btype == "tool_use":
                        name = block.get("name") or "?"
                        parts.append(f"\n\n[tool_use: {name}]\n")
            elif t == "content_block_delta":
                delta = ev.get("delta")
                if not isinstance(delta, dict):
                    continue
                dtype = delta.get("type")
                if dtype == "text_delta":
                    v = delta.get("text")
                    if isinstance(v, str):
                        parts.append(v)
                elif dtype == "input_json_delta":
                    v = delta.get("partial_json")
                    if isinstance(v, str):
                        parts.append(v)
                # Ignore other delta types (thinking_delta, signature_delta, …)
                # — they land in the JSON Log tab via the raw `events` return.
        return ("".join(parts), events)


class AnthropicProvider(Provider):
    name = "anthropic"
    upstream_path_prefix = ""  # clients already send /v1/messages etc.

    def __init__(self, *, base_url: str, api_key: str) -> None:
        self.base_url = base_url
        self._api_key = api_key

    def upstream_api_key(self) -> str:
        return self._api_key

    def inject_auth(self, headers: dict[str, str]) -> dict[str, str]:
        headers["x-api-key"] = self._api_key
        headers["anthropic-version"] = ANTHROPIC_VERSION
        # Client's Authorization was the proxy key; Anthropic doesn't want it
        headers.pop("authorization", None)
        return headers

    def parse_usage(
        self,
        *,
        is_streaming: bool,
        resp_body: bytes | None,
        chunks: list[bytes] | None,
    ) -> Usage | None:
        if not is_streaming:
            if not resp_body:
                return None
            try:
                obj = json.loads(resp_body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return None
            return self._usage_from_dict(obj.get("usage") if isinstance(obj, dict) else None)

        # Streaming: scan events for the message_start (initial input_tokens)
        # and the final message_delta (output_tokens).
        if not chunks:
            return None
        events = iter_sse_data_events(chunks)
        input_tokens: int | None = None
        output_tokens: int | None = None
        cached: int | None = None

        for event in events:
            t = event.get("type")
            if t == "message_start":
                msg = event.get("message", {})
                u = msg.get("usage")
                if isinstance(u, dict):
                    input_tokens = u.get("input_tokens", input_tokens)
                    cached = u.get("cache_read_input_tokens", cached)
            elif t == "message_delta":
                u = event.get("usage")
                if isinstance(u, dict):
                    output_tokens = u.get("output_tokens", output_tokens)

        if input_tokens is None and output_tokens is None:
            return None
        return Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached,
        )

    def make_chunk_parser(self) -> ChunkParser:
        return AnthropicChunkParser()

    @staticmethod
    def _usage_from_dict(usage: dict | None) -> Usage | None:
        if not isinstance(usage, dict):
            return None
        return Usage(
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cached_tokens=usage.get("cache_read_input_tokens"),
        )
