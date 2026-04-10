"""Provider abstraction.

A Provider encapsulates everything that differs between upstream vendors:
URL prefix, auth header injection, and usage parsing.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None


def iter_sse_data_events(chunks: list[bytes]) -> list[dict]:
    """Parse a list of raw SSE chunks into a list of JSON data events.

    Handles OpenAI-style 'data: {...}\n\n' framing. Skips '[DONE]' and
    non-JSON lines. Concatenates chunks first because chunk boundaries
    may split an event mid-line.
    """
    blob = b"".join(chunks).decode("utf-8", errors="replace")
    events: list[dict] = []
    for line in blob.split("\n"):
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            events.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return events


class Provider(ABC):
    name: str                    # 'openai' | 'anthropic' | 'openrouter'
    base_url: str                # e.g. 'https://api.openai.com'
    upstream_path_prefix: str    # e.g. '/v1', '', '/api/v1'

    @abstractmethod
    def upstream_api_key(self) -> str: ...

    @abstractmethod
    def inject_auth(self, headers: dict[str, str]) -> dict[str, str]:
        """Mutate and return the headers dict with the upstream auth attached."""

    def map_path(self, client_path: str) -> str:
        """Client path (after provider prefix) → upstream path."""
        return f"{self.upstream_path_prefix}/{client_path.lstrip('/')}"

    def extract_model(self, body: bytes) -> str | None:
        """Parse JSON body to find 'model' for display."""
        if not body:
            return None
        try:
            obj = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if isinstance(obj, dict):
            model = obj.get("model")
            return model if isinstance(model, str) else None
        return None

    def is_streaming_request(self, body: bytes, headers: dict[str, str]) -> bool:
        if not body:
            return False
        try:
            obj = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False
        return isinstance(obj, dict) and bool(obj.get("stream"))

    def parse_usage(
        self,
        *,
        is_streaming: bool,
        resp_body: bytes | None,
        chunks: list[bytes] | None,
    ) -> Usage | None:
        """Extract token counts from the upstream response.

        For non-streaming: `resp_body` is the full response JSON.
        For streaming: `chunks` is the list of raw bytes from upstream.
        Provider-specific subclasses should override this.
        Default returns None (no parsing).
        """
        return None

    def make_chunk_parser(self) -> "ChunkParser":
        """Return a stateful per-stream parser for this provider's SSE format.

        Upstream TCP chunks do NOT align with SSE event boundaries — a single
        'data: {...}\\n\\n' event may be split across two or more raw chunks.
        The parser buffers partial data across feeds and returns text_delta +
        events as each complete event lands. Provider subclasses override.
        """
        return _NullChunkParser()


class ChunkParser:
    """Base class for stateful per-stream SSE parsers.

    Call `feed(chunk_bytes)` for each raw upstream chunk in order. The return
    value is the (text_delta, events) that became complete *within that feed*
    — text from events split across earlier chunks is attributed to the chunk
    that completed the event.
    """

    def feed(self, chunk: bytes) -> tuple[str, list[dict]]:  # pragma: no cover
        raise NotImplementedError


class _NullChunkParser(ChunkParser):
    def feed(self, chunk: bytes) -> tuple[str, list[dict]]:
        return ("", [])


def _iter_complete_sse_events(buf: bytes) -> tuple[list[dict], bytes]:
    """Given an accumulated buffer, return (parsed_events, trailing_incomplete).

    SSE events are separated by blank lines (\\n\\n). The final chunk of `buf`
    may not yet be terminated; we return it as `trailing_incomplete` for the
    caller to prepend on the next feed. Non-'data:' lines are ignored.
    '[DONE]' sentinels are dropped.
    """
    # Normalize CRLF → LF first so split works regardless of line endings
    norm = buf.replace(b"\r\n", b"\n")
    parts = norm.split(b"\n\n")
    # Last part is incomplete (no trailing \n\n yet)
    incomplete = parts[-1]
    complete = parts[:-1]
    events: list[dict] = []
    for part in complete:
        for line in part.split(b"\n"):
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == b"[DONE]":
                continue
            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError:
                continue
    return events, incomplete
