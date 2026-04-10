"""Tests for the pricing seed loader."""
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aiproxy.db.models import Base, Pricing
from aiproxy.pricing.seed import seed_pricing_if_empty


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


async def test_seeds_when_empty(session_factory) -> None:
    await seed_pricing_if_empty(session_factory)
    async with session_factory() as s:
        count = (await s.execute(select(func.count()).select_from(Pricing))).scalar()
    assert count > 0


async def test_does_not_seed_when_non_empty(session_factory) -> None:
    # Pre-seed with one row
    from aiproxy.db.crud import pricing as pricing_crud
    async with session_factory() as s:
        await pricing_crud.upsert_current(
            s, provider="custom", model="my-model",
            input_per_1m_usd=1.0, output_per_1m_usd=2.0, cached_per_1m_usd=None,
        )
        await s.commit()

    await seed_pricing_if_empty(session_factory)

    async with session_factory() as s:
        count = (await s.execute(select(func.count()).select_from(Pricing))).scalar()
    # Only the custom row we inserted — seeder noticed the table is non-empty
    assert count == 1


async def test_idempotent_after_first_seed(session_factory) -> None:
    await seed_pricing_if_empty(session_factory)
    async with session_factory() as s:
        count1 = (await s.execute(select(func.count()).select_from(Pricing))).scalar()
    await seed_pricing_if_empty(session_factory)
    async with session_factory() as s:
        count2 = (await s.execute(select(func.count()).select_from(Pricing))).scalar()
    assert count1 == count2
