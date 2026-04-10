"""OpenAI provider — Bearer token auth, /v1/ path prefix."""
from __future__ import annotations

from aiproxy.providers.base import Provider


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
