"""OpenRouter provider — OpenAI-compatible Bearer auth, /api/v1/ prefix."""
from __future__ import annotations

from aiproxy.providers.base import Provider


class OpenRouterProvider(Provider):
    name = "openrouter"
    upstream_path_prefix = "/api/v1"

    def __init__(self, *, base_url: str, api_key: str) -> None:
        self.base_url = base_url
        self._api_key = api_key

    def upstream_api_key(self) -> str:
        return self._api_key

    def inject_auth(self, headers: dict[str, str]) -> dict[str, str]:
        headers["authorization"] = f"Bearer {self._api_key}"
        # Optional but helpful for OpenRouter's app rankings
        headers.setdefault("http-referer", "https://github.com/ai-proxy")
        return headers
