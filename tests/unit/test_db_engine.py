"""Tests for aiproxy.db.engine."""
import pytest
from sqlalchemy import text

from aiproxy.db.engine import create_engine_and_sessionmaker


@pytest.mark.asyncio
async def test_engine_and_sessionmaker_roundtrip() -> None:
    engine, sessionmaker = create_engine_and_sessionmaker("sqlite+aiosqlite:///:memory:")
    try:
        async with sessionmaker() as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar() == 1
    finally:
        await engine.dispose()
