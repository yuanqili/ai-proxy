"""OpenAI provider — Bearer token auth, /v1/ path prefix, OpenAI-style SSE."""
from __future__ import annotations

import json

from aiproxy.providers.base import (
    ChunkParser,
    Provider,
    Usage,
    _iter_complete_sse_events,
    iter_sse_data_events,
)


class OpenAIChunkParser(ChunkParser):
    """Stateful SSE parser for OpenAI / OpenRouter chat completions streams.

    Buffers partial SSE frames across TCP chunks. Events that span multiple
    raw chunks are attributed (text + json) to the chunk that completes them.

    Handles three kinds of content in a chat-completion delta:
      - ``delta.content`` → plain assistant text
      - ``delta.tool_calls[]`` with ``function.name`` / ``function.arguments``
        → streamed tool-call argument JSON. A synthetic ``[tool_use: <name>]``
        header is emitted the first time each tool call's name is seen, and
        subsequent argument deltas are concatenated into the text stream so
        the player screen can animate them.
      - (future) ``delta.refusal`` → passed through as plain text if present.

    Tool call state is tracked per (choice_index, tool_index) so multi-choice
    responses and parallel tool calls both work.
    """

    def __init__(self) -> None:
        self._buf = b""
        # (choice_index, tool_index) → True once we've emitted its header
        self._tool_header_sent: set[tuple[int, int]] = set()

    def feed(self, chunk: bytes) -> tuple[str, list[dict]]:
        self._buf += chunk
        events, self._buf = _iter_complete_sse_events(self._buf)
        parts: list[str] = []
        for ev in events:
            choices = ev.get("choices")
            if not isinstance(choices, list):
                continue
            for ch in choices:
                if not isinstance(ch, dict):
                    continue
                ci = ch.get("index", 0)
                if not isinstance(ci, int):
                    ci = 0
                delta = ch.get("delta")
                if not isinstance(delta, dict):
                    continue
                content = delta.get("content")
                if isinstance(content, str):
                    parts.append(content)
                refusal = delta.get("refusal")
                if isinstance(refusal, str):
                    parts.append(refusal)
                tcs = delta.get("tool_calls")
                if isinstance(tcs, list):
                    for tc in tcs:
                        if not isinstance(tc, dict):
                            continue
                        ti = tc.get("index", 0)
                        if not isinstance(ti, int):
                            ti = 0
                        fn = tc.get("function")
                        if not isinstance(fn, dict):
                            continue
                        name = fn.get("name")
                        key = (ci, ti)
                        if isinstance(name, str) and name and key not in self._tool_header_sent:
                            self._tool_header_sent.add(key)
                            parts.append(f"\n\n[tool_use: {name}]\n")
                        args = fn.get("arguments")
                        if isinstance(args, str):
                            parts.append(args)
        return ("".join(parts), events)


def _parse_openai_usage(obj: dict) -> Usage | None:
    """Shared parser for OpenAI-compatible response shapes.

    Used by both OpenAIProvider and OpenRouterProvider.
    """
    usage = obj.get("usage")
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("prompt_tokens")
    output_tokens = usage.get("completion_tokens")
    cached = None
    reasoning = None
    ptd = usage.get("prompt_tokens_details")
    if isinstance(ptd, dict):
        cached = ptd.get("cached_tokens")
    ctd = usage.get("completion_tokens_details")
    if isinstance(ctd, dict):
        reasoning = ctd.get("reasoning_tokens")
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached,
        reasoning_tokens=reasoning,
    )


class OpenAIProvider(Provider):
    name = "openai"
    upstream_path_prefix = "/v1"

    def __init__(self, *, base_url: str, api_key: str) -> None:
        self.base_url = base_url
        self._api_key = api_key

    def upstream_api_key(self) -> str:
        return self._api_key

    def inject_auth(self, headers: dict[str, str]) -> dict[str, str]:
        headers["authorization"] = f"Bearer {self._api_key}"
        return headers

    def rewrite_request_body(self, body: bytes, is_streaming: bool) -> bytes:
        # Only touch streaming requests — non-stream already returns usage in body.
        if not is_streaming or not body:
            return body
        try:
            obj = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return body
        if not isinstance(obj, dict):
            return body
        opts = obj.get("stream_options")
        if isinstance(opts, dict):
            # Respect explicit client intent either way (true or false).
            if "include_usage" in opts:
                return body
            opts["include_usage"] = True
        else:
            obj["stream_options"] = {"include_usage": True}
        return json.dumps(obj, separators=(",", ":")).encode("utf-8")

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
            return _parse_openai_usage(obj) if isinstance(obj, dict) else None

        # Streaming: parse SSE chunks, look for the last event containing `usage`.
        # This requires the client to have set stream_options.include_usage=true.
        if not chunks:
            return None
        events = iter_sse_data_events(chunks)
        for event in reversed(events):
            if isinstance(event, dict) and "usage" in event:
                return _parse_openai_usage(event)
        return None

    def make_chunk_parser(self) -> ChunkParser:
        return OpenAIChunkParser()
