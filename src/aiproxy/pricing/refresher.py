"""Daily LiteLLM pricing catalog refresh (background task).

On app startup we spawn a task that sleeps briefly, fetches the upstream
LiteLLM catalog, and applies any diffs to the pricing table. The cycle
then repeats on a 24h interval. Transient errors (network glitch, bad
JSON, upstream 5xx) are logged and swallowed so the task stays alive.

Unchanged rows are skipped inside `apply_catalog`, so running this on
boot would be harmless but would add a boot-time HTTP dependency; the
startup delay avoids that.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker

from aiproxy.pricing.catalog import apply_catalog, fetch_catalog

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 24 * 60 * 60
DEFAULT_STARTUP_DELAY_S = 60.0


async def refresh_once(sessionmaker: async_sessionmaker) -> dict:
    entries = await fetch_catalog()
    async with sessionmaker() as session:
        result = await apply_catalog(session, entries)
        await session.commit()
    return result


async def periodic_refresh(
    sessionmaker: async_sessionmaker,
    *,
    startup_delay_s: float = DEFAULT_STARTUP_DELAY_S,
    interval_s: float = DEFAULT_INTERVAL_S,
) -> None:
    try:
        await asyncio.sleep(startup_delay_s)
        while True:
            try:
                result = await refresh_once(sessionmaker)
                logger.info(
                    "pricing catalog refresh: added=%d superseded=%d unchanged=%d",
                    result["added"], result["superseded"], result["unchanged"],
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("pricing catalog refresh failed; will retry next cycle")
            await asyncio.sleep(interval_s)
    except asyncio.CancelledError:
        pass
