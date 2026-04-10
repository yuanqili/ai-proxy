"""Pytest configuration for unit tests.

Clears provider API keys from the OS environment so that tests which
construct Settings with explicit values or a custom _env_file are not
shadowed by variables exported in the developer's shell.
"""
import pytest

_ENV_KEYS_TO_CLEAR = [
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "PROXY_MASTER_KEY",
    "SESSION_SECRET",
    "DATABASE_URL",
    "HOST",
    "PORT",
]


@pytest.fixture(autouse=True)
def clear_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove provider-related env vars so Settings tests are hermetic."""
    for key in _ENV_KEYS_TO_CLEAR:
        monkeypatch.delenv(key, raising=False)
