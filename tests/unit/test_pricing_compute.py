"""Tests for cost computation."""
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.db.crud import pricing as pricing_crud
from aiproxy.db.models import Base
from aiproxy.pricing.compute import compute_cost
from aiproxy.providers.base import Usage


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


async def test_compute_cost_basic(session_factory) -> None:
    async with session_factory() as s:
        await pricing_crud.upsert_current(
            s, provider="openai", model="gpt-4o",
            input_per_1m_usd=2.50, output_per_1m_usd=10.00,
            cached_per_1m_usd=None,
        )
        await s.commit()
        result = await compute_cost(
            s,
            provider="openai",
            model="gpt-4o",
            usage=Usage(input_tokens=1000, output_tokens=500),
        )
    assert result is not None
    pricing_id, cost = result
    # 1000 / 1M * $2.50 + 500 / 1M * $10.00 = 0.0025 + 0.005 = 0.0075
    assert cost == pytest.approx(0.0075)
    assert pricing_id is not None


async def test_compute_cost_with_cached_tokens(session_factory) -> None:
    async with session_factory() as s:
        await pricing_crud.upsert_current(
            s, provider="openai", model="gpt-4o",
            input_per_1m_usd=2.50, output_per_1m_usd=10.00,
            cached_per_1m_usd=1.25,
        )
        await s.commit()
        result = await compute_cost(
            s,
            provider="openai",
            model="gpt-4o",
            usage=Usage(input_tokens=1000, output_tokens=500, cached_tokens=400),
        )
    pricing_id, cost = result
    # Non-cached input: 600 tokens * $2.50/1M = 0.0015
    # Cached input:     400 tokens * $1.25/1M = 0.0005
    # Output:           500 tokens * $10.00/1M = 0.005
    expected = 0.0015 + 0.0005 + 0.005
    assert cost == pytest.approx(expected)


async def test_compute_cost_unknown_model_returns_none(session_factory) -> None:
    async with session_factory() as s:
        result = await compute_cost(
            s,
            provider="openai",
            model="not-seeded",
            usage=Usage(input_tokens=100, output_tokens=50),
        )
    assert result is None


async def test_compute_cost_no_usage_returns_none(session_factory) -> None:
    async with session_factory() as s:
        await pricing_crud.upsert_current(
            s, provider="openai", model="gpt-4o",
            input_per_1m_usd=2.50, output_per_1m_usd=10.00,
            cached_per_1m_usd=None,
        )
        await s.commit()
        result = await compute_cost(
            s, provider="openai", model="gpt-4o", usage=None,
        )
    assert result is None
