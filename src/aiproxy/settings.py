"""Application configuration loaded from environment / .env file."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Upstream providers
    openai_base_url: str = "https://api.openai.com"
    openai_api_key: str
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_api_key: str
    openrouter_base_url: str = "https://openrouter.ai"
    openrouter_api_key: str

    # Proxy self-auth
    proxy_master_key: str
    session_secret: str

    # Database
    database_url: str = "sqlite+aiosqlite:///./aiproxy.db"

    # Server
    host: str = "127.0.0.1"
    port: int = 8000
    dashboard_origin: str | None = None
    secure_cookies: bool = False


settings = Settings()  # type: ignore[call-arg]
