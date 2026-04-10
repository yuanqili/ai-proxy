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
