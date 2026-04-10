"""Provider registry."""
from __future__ import annotations

from aiproxy.providers.anthropic import AnthropicProvider
from aiproxy.providers.base import Provider, Usage
from aiproxy.providers.openai import OpenAIProvider
from aiproxy.providers.openrouter import OpenRouterProvider

__all__ = ["Provider", "Usage", "build_registry"]


def build_registry(
    *,
    openai_base_url: str,
    openai_api_key: str,
    anthropic_base_url: str,
    anthropic_api_key: str,
    openrouter_base_url: str,
    openrouter_api_key: str,
) -> dict[str, Provider]:
    return {
        "openai": OpenAIProvider(base_url=openai_base_url, api_key=openai_api_key),
        "anthropic": AnthropicProvider(base_url=anthropic_base_url, api_key=anthropic_api_key),
        "openrouter": OpenRouterProvider(base_url=openrouter_base_url, api_key=openrouter_api_key),
    }
