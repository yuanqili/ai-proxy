"""Provider abstraction.

A Provider encapsulates everything that differs between upstream vendors:
URL prefix, auth header injection, and (in Phase 2) usage parsing.
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
