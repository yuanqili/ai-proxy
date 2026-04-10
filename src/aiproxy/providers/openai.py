"""OpenAI provider — Bearer token auth, /v1/ path prefix, OpenAI-style SSE."""
from __future__ import annotations

import json

from aiproxy.providers.base import Provider, Usage, iter_sse_data_events


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

    def extract_chunk_text(self, chunk_data: bytes) -> tuple[str, list[dict]]:
        if not chunk_data:
            return ("", [])
        events = iter_sse_data_events([chunk_data])
        text_parts: list[str] = []
        for ev in events:
            choices = ev.get("choices")
            if not isinstance(choices, list):
                continue
            for ch in choices:
                delta = ch.get("delta") if isinstance(ch, dict) else None
                if isinstance(delta, dict):
                    content = delta.get("content")
                    if isinstance(content, str):
                        text_parts.append(content)
        return ("".join(text_parts), events)
