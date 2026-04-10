"""Anthropic provider — x-api-key auth, no path prefix, custom SSE format."""
from __future__ import annotations

import json

from aiproxy.providers.base import Provider, Usage, iter_sse_data_events

ANTHROPIC_VERSION = "2023-06-01"


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

    @staticmethod
    def _usage_from_dict(usage: dict | None) -> Usage | None:
        if not isinstance(usage, dict):
            return None
        return Usage(
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cached_tokens=usage.get("cache_read_input_tokens"),
        )
