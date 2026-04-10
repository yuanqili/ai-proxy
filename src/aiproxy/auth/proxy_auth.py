"""Proxy inbound auth: verify client API key against the api_keys table.

Uses a simple in-memory TTL cache keyed by the raw key string. The whole
api_keys table is small (dozens of rows at most), so cache management is
trivial. We refresh the cache atomically from the DB every `ttl_seconds`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from aiproxy.db.crud import api_keys as api_keys_crud
from aiproxy.db.models import ApiKey


@dataclass
class AuthResult:
    ok: bool
    api_key_id: int | None = None
    reason: str | None = None  # 'missing' | 'not_found' | 'inactive' | 'not_yet_valid' | 'expired'


class ApiKeyCache:
    """Reloads the full api_keys table every ttl_seconds seconds.

    Not thread-safe — fine for single-process FastAPI.
    """

    def __init__(self, sessionmaker: async_sessionmaker, ttl_seconds: float = 60.0) -> None:
        self._sessionmaker = sessionmaker
        self._ttl = ttl_seconds
        self._by_key: dict[str, ApiKey] = {}
        self._loaded_at: float = 0.0

    async def get(self, key: str) -> ApiKey | None:
        now = time.monotonic()
        if now - self._loaded_at >= self._ttl:
            await self._reload()
        return self._by_key.get(key)

    async def _reload(self) -> None:
        async with self._sessionmaker() as session:
            rows = await api_keys_crud.list_all(session)
        self._by_key = {row.key: row for row in rows}
        self._loaded_at = time.monotonic()


def extract_key(header_value: str | None) -> str | None:
    """Accept 'Bearer X', bare 'X', or None."""
    if not header_value:
        return None
    value = header_value.strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip() or None
    return value or None


async def verify_header(header_value: str | None, cache: ApiKeyCache) -> AuthResult:
    key = extract_key(header_value)
    if key is None:
        return AuthResult(ok=False, reason="missing")

    row = await cache.get(key)
    if row is None:
        return AuthResult(ok=False, reason="not_found")
    if row.is_active != 1:
        return AuthResult(ok=False, reason="inactive")
    now = time.time()
    if row.valid_from is not None and now < row.valid_from:
        return AuthResult(ok=False, reason="not_yet_valid")
    if row.valid_to is not None and now >= row.valid_to:
        return AuthResult(ok=False, reason="expired")
    return AuthResult(ok=True, api_key_id=row.id)
