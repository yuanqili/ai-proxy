"""Tests for the daily LiteLLM catalog auto-refresh background task.

The network call is mocked via respx; the scheduler is exercised with
sub-second timings so the first iteration completes before teardown.
"""
import asyncio
import time

import httpx
import pytest
import respx

from aiproxy.db.crud import pricing as pricing_crud
from aiproxy.pricing.catalog import LITELLM_CATALOG_URL
from aiproxy.pricing.refresher import periodic_refresh, refresh_once

FAKE_CATALOG = {
    "gpt-4o-mini": {
        "litellm_provider": "openai",
        "mode": "chat",
        "input_cost_per_token": 2e-7,
        "output_cost_per_token": 8e-7,
    },
}


async def _find(sm, provider: str, model: str):
    async with sm() as s:
        return await pricing_crud.find_effective(
            s, provider=provider, model=model, at=time.time()
        )


async def test_refresh_once_writes_catalog(db_sessionmaker) -> None:
    with respx.mock:
        respx.get(LITELLM_CATALOG_URL).mock(
            return_value=httpx.Response(200, json=FAKE_CATALOG)
        )
        result = await refresh_once(db_sessionmaker)

    assert result == {"added": 1, "superseded": 0, "unchanged": 0}
    row = await _find(db_sessionmaker, "openai", "gpt-4o-mini")
    assert row is not None
    assert row.input_per_1m_usd == pytest.approx(0.2)


async def test_periodic_refresh_survives_http_error(db_sessionmaker) -> None:
    with respx.mock:
        respx.get(LITELLM_CATALOG_URL).mock(return_value=httpx.Response(500))
        task = asyncio.create_task(periodic_refresh(
            db_sessionmaker, startup_delay_s=0.0, interval_s=3600.0,
        ))
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Task should have been cancelled cleanly, no row written.
    assert await _find(db_sessionmaker, "openai", "gpt-4o-mini") is None


async def test_periodic_refresh_applies_catalog(db_sessionmaker) -> None:
    with respx.mock:
        respx.get(LITELLM_CATALOG_URL).mock(
            return_value=httpx.Response(200, json=FAKE_CATALOG)
        )
        task = asyncio.create_task(periodic_refresh(
            db_sessionmaker, startup_delay_s=0.0, interval_s=3600.0,
        ))
        row = None
        for _ in range(40):
            await asyncio.sleep(0.05)
            row = await _find(db_sessionmaker, "openai", "gpt-4o-mini")
            if row is not None:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert row is not None
    assert row.input_per_1m_usd == pytest.approx(0.2)
