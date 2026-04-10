"""Tests for aiproxy.db.crud.pricing."""
import time

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.db.crud import pricing as pricing_crud
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


async def test_upsert_creates_first_version(session_factory) -> None:
    async with session_factory() as s:
        row = await pricing_crud.upsert_current(
            s,
            provider="openai",
            model="gpt-4o",
            input_per_1m_usd=2.50,
            output_per_1m_usd=10.00,
            cached_per_1m_usd=None,
            note="initial",
        )
        await s.commit()
        assert row.id is not None
        assert row.effective_to is None


async def test_upsert_closes_previous_version(session_factory) -> None:
    async with session_factory() as s:
        v1 = await pricing_crud.upsert_current(
            s,
            provider="openai",
            model="gpt-4o",
            input_per_1m_usd=2.50,
            output_per_1m_usd=10.00,
            cached_per_1m_usd=None,
        )
        await s.commit()
        v1_id = v1.id
        v2 = await pricing_crud.upsert_current(
            s,
            provider="openai",
            model="gpt-4o",
            input_per_1m_usd=2.00,
            output_per_1m_usd=8.00,
            cached_per_1m_usd=None,
            note="price cut",
        )
        await s.commit()

    async with session_factory() as s:
        from sqlalchemy import select
        from aiproxy.db.models import Pricing
        rows = (await s.execute(
            select(Pricing).where(
                Pricing.provider == "openai", Pricing.model == "gpt-4o"
            ).order_by(Pricing.effective_from)
        )).scalars().all()
        assert len(rows) == 2
        assert rows[0].id == v1_id
        assert rows[0].effective_to is not None
        assert rows[1].effective_to is None


async def test_find_effective_returns_current(session_factory) -> None:
    async with session_factory() as s:
        v1 = await pricing_crud.upsert_current(
            s, provider="openai", model="gpt-4o",
            input_per_1m_usd=2.50, output_per_1m_usd=10.00,
            cached_per_1m_usd=None,
        )
        await s.commit()
        await pricing_crud.upsert_current(
            s, provider="openai", model="gpt-4o",
            input_per_1m_usd=2.00, output_per_1m_usd=8.00,
            cached_per_1m_usd=None,
        )
        await s.commit()

    async with session_factory() as s:
        effective = await pricing_crud.find_effective(
            s, provider="openai", model="gpt-4o", at=time.time(),
        )
        assert effective is not None
        assert effective.input_per_1m_usd == 2.00


async def test_find_effective_none_for_unknown_model(session_factory) -> None:
    async with session_factory() as s:
        result = await pricing_crud.find_effective(
            s, provider="openai", model="unknown", at=time.time(),
        )
        assert result is None
