"""Tests for aiproxy.settings."""
from pathlib import Path

import pytest

from aiproxy.settings import Settings


def test_settings_loads_all_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "OPENAI_BASE_URL=https://api.openai.com\n"
        "OPENAI_API_KEY=sk-test-openai\n"
        "ANTHROPIC_BASE_URL=https://api.anthropic.com\n"
        "ANTHROPIC_API_KEY=sk-ant-test\n"
        "OPENROUTER_BASE_URL=https://openrouter.ai\n"
        "OPENROUTER_API_KEY=sk-or-test\n"
        "PROXY_MASTER_KEY=master-xxx\n"
        "SESSION_SECRET=session-xxx\n"
        "DATABASE_URL=sqlite+aiosqlite:///:memory:\n"
        "HOST=127.0.0.1\n"
        "PORT=8000\n"
    )
    monkeypatch.chdir(tmp_path)
    # pydantic-settings caches the parse, so build a fresh instance
    s = Settings(_env_file=str(env))  # type: ignore[call-arg]
    assert s.openai_api_key == "sk-test-openai"
    assert s.anthropic_api_key == "sk-ant-test"
    assert s.openrouter_api_key == "sk-or-test"
    assert s.proxy_master_key == "master-xxx"
    assert s.session_secret == "session-xxx"
    assert s.database_url == "sqlite+aiosqlite:///:memory:"
    assert s.host == "127.0.0.1"
    assert s.port == 8000


def test_settings_defaults_for_optional_fields() -> None:
    s = Settings(
        openai_api_key="a",
        anthropic_api_key="b",
        openrouter_api_key="c",
        proxy_master_key="d",
        session_secret="e",
    )
    assert s.openai_base_url == "https://api.openai.com"
    assert s.anthropic_base_url == "https://api.anthropic.com"
    assert s.openrouter_base_url == "https://openrouter.ai"
    assert s.database_url == "sqlite+aiosqlite:///./aiproxy.db"
    assert s.host == "127.0.0.1"
    assert s.port == 8000
    assert s.secure_cookies is False
