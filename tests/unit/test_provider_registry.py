"""Tests for provider registry."""
from aiproxy.providers import build_registry
from aiproxy.providers.openai import OpenAIProvider
from aiproxy.providers.anthropic import AnthropicProvider
from aiproxy.providers.openrouter import OpenRouterProvider


def test_build_registry_contains_all_three() -> None:
    reg = build_registry(
        openai_base_url="https://api.openai.com",
        openai_api_key="sk-o",
        anthropic_base_url="https://api.anthropic.com",
        anthropic_api_key="sk-a",
        openrouter_base_url="https://openrouter.ai",
        openrouter_api_key="sk-r",
    )
    assert set(reg.keys()) == {"openai", "anthropic", "openrouter"}
    assert isinstance(reg["openai"], OpenAIProvider)
    assert isinstance(reg["anthropic"], AnthropicProvider)
    assert isinstance(reg["openrouter"], OpenRouterProvider)
