"""Tests for aiproxy.db.crud.api_keys."""
import time

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.db.crud import api_keys as api_keys_crud
from aiproxy.db.models import Base


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sm
    finally:
        await engine.dispose()


async def test_create_and_get(session_factory) -> None:
    async with session_factory() as s:
        row = await api_keys_crud.create(
            s, key="sk-aiprx-abc", name="test", note=None,
            valid_from=None, valid_to=None,
        )
        await s.commit()
        assert row.id is not None

    async with session_factory() as s:
        fetched = await api_keys_crud.get_by_key(s, "sk-aiprx-abc")
        assert fetched is not None
        assert fetched.name == "test"
        assert fetched.is_active == 1


async def test_get_nonexistent_returns_none(session_factory) -> None:
    async with session_factory() as s:
        assert await api_keys_crud.get_by_key(s, "sk-aiprx-missing") is None


async def test_update_last_used(session_factory) -> None:
    async with session_factory() as s:
        await api_keys_crud.create(s, key="k1", name="n", note=None,
                                   valid_from=None, valid_to=None)
        await s.commit()

    ts = time.time()
    async with session_factory() as s:
        await api_keys_crud.update_last_used(s, "k1", ts)
        await s.commit()

    async with session_factory() as s:
        row = await api_keys_crud.get_by_key(s, "k1")
        assert row is not None
        assert row.last_used_at == ts


async def test_list_all(session_factory) -> None:
    async with session_factory() as s:
        await api_keys_crud.create(s, key="a", name="a", note=None,
                                   valid_from=None, valid_to=None)
        await api_keys_crud.create(s, key="b", name="b", note=None,
                                   valid_from=None, valid_to=None)
        await s.commit()

    async with session_factory() as s:
        rows = await api_keys_crud.list_all(s)
        assert {r.key for r in rows} == {"a", "b"}
