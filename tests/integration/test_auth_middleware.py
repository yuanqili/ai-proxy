"""Integration tests for the proxy auth middleware."""
import pytest

from aiproxy.auth.proxy_auth import ApiKeyCache, AuthResult, verify_header
from aiproxy.db.crud import api_keys as api_keys_crud


@pytest.fixture
async def seeded_session(db_sessionmaker):
    async with db_sessionmaker() as s:
        await api_keys_crud.create(
            s, key="sk-aiprx-valid", name="prod", note=None,
            valid_from=None, valid_to=None,
        )
        await api_keys_crud.create(
            s, key="sk-aiprx-inactive", name="old", note=None,
            valid_from=None, valid_to=None,
        )
        await api_keys_crud.set_active(s, "sk-aiprx-inactive", False)
        await s.commit()
    yield db_sessionmaker


async def test_valid_bearer(seeded_session) -> None:
    cache = ApiKeyCache(seeded_session, ttl_seconds=60)
    result = await verify_header("Bearer sk-aiprx-valid", cache)
    assert result.ok
    assert result.api_key_id is not None
    assert result.reason is None


async def test_valid_bare_token(seeded_session) -> None:
    cache = ApiKeyCache(seeded_session, ttl_seconds=60)
    result = await verify_header("sk-aiprx-valid", cache)
    assert result.ok


async def test_missing(seeded_session) -> None:
    cache = ApiKeyCache(seeded_session, ttl_seconds=60)
    result = await verify_header(None, cache)
    assert not result.ok
    assert result.reason == "missing"


async def test_unknown_key(seeded_session) -> None:
    cache = ApiKeyCache(seeded_session, ttl_seconds=60)
    result = await verify_header("Bearer sk-aiprx-nope", cache)
    assert not result.ok
    assert result.reason == "not_found"


async def test_inactive_key(seeded_session) -> None:
    cache = ApiKeyCache(seeded_session, ttl_seconds=60)
    result = await verify_header("Bearer sk-aiprx-inactive", cache)
    assert not result.ok
    assert result.reason == "inactive"


async def test_cache_refreshes_after_ttl(seeded_session) -> None:
    # Cache a miss first
    cache = ApiKeyCache(seeded_session, ttl_seconds=0.0)  # effectively no cache
    await verify_header("Bearer sk-aiprx-missing", cache)
    # Now create the key
    async with seeded_session() as s:
        await api_keys_crud.create(
            s, key="sk-aiprx-fresh", name="fresh", note=None,
            valid_from=None, valid_to=None,
        )
        await s.commit()
    # Next lookup should find it (ttl=0 forces reload)
    result = await verify_header("Bearer sk-aiprx-fresh", cache)
    assert result.ok
