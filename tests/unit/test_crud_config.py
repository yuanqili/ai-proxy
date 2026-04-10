"""Tests for aiproxy.db.crud.config — runtime settings read/write."""
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.db.crud import config as config_crud
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


async def test_get_with_default_returns_default_when_missing(session_factory) -> None:
    async with session_factory() as s:
        val = await config_crud.get(s, "retention.full_count", default=500)
    assert val == 500


async def test_set_and_get_roundtrip(session_factory) -> None:
    async with session_factory() as s:
        await config_crud.set_(s, "retention.full_count", 100)
        await s.commit()

    async with session_factory() as s:
        val = await config_crud.get(s, "retention.full_count", default=500)
    assert val == 100


async def test_set_overwrites(session_factory) -> None:
    async with session_factory() as s:
        await config_crud.set_(s, "retention.full_count", 100)
        await config_crud.set_(s, "retention.full_count", 250)
        await s.commit()

    async with session_factory() as s:
        val = await config_crud.get(s, "retention.full_count", default=500)
    assert val == 250


async def test_get_all(session_factory) -> None:
    async with session_factory() as s:
        await config_crud.set_(s, "a", 1)
        await config_crud.set_(s, "b", "two")
        await config_crud.set_(s, "c", [1, 2, 3])
        await s.commit()

    async with session_factory() as s:
        all_cfg = await config_crud.get_all(s)
    assert all_cfg == {"a": 1, "b": "two", "c": [1, 2, 3]}
