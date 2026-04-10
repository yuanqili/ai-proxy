"""Background retention: prune old request bodies + chunks to keep DB small.

The design keeps metadata + cost + token counts for every request forever,
but strips out req_body, resp_body, req_headers, resp_headers, and deletes
the chunks table rows for requests older than the Nth most recent.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from aiproxy.db.models import Chunk, Request

logger = logging.getLogger(__name__)


async def prune_old_requests(
    sessionmaker: async_sessionmaker,
    *,
    full_count: int,
) -> int:
    """Null out bodies + delete chunks for rows older than the Nth newest.

    Returns the number of requests trimmed.
    """
    async with sessionmaker() as session:
        # Find the started_at of the Nth most recent request.
        result = await session.execute(
            select(Request.started_at)
            .order_by(Request.started_at.desc())
            .offset(full_count - 1)
            .limit(1)
        )
        cutoff = result.scalar()
        if cutoff is None:
            return 0  # fewer than full_count rows — nothing to trim

        # Count how many rows will be trimmed
        to_trim_result = await session.execute(
            select(Request.req_id)
            .where(Request.started_at < cutoff)
            .where(Request.req_body.is_not(None))
        )
        req_ids = [row[0] for row in to_trim_result]
        if not req_ids:
            return 0

        # Null out bodies and headers
        await session.execute(
            update(Request)
            .where(Request.req_id.in_(req_ids))
            .values(
                req_body=None,
                resp_body=None,
                req_headers="{}",
                resp_headers=None,
            )
        )

        # Delete all chunks belonging to those requests
        await session.execute(
            delete(Chunk).where(Chunk.req_id.in_(req_ids))
        )

        await session.commit()
        return len(req_ids)


async def retention_loop(
    sessionmaker: async_sessionmaker,
    *,
    get_full_count: callable,   # () -> int, e.g. reads from Settings or config table
    interval_seconds: float = 60.0,
) -> None:
    """Run prune_old_requests in a loop. Swallows exceptions so the task keeps running."""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            full_count = get_full_count()
            if full_count > 0:
                trimmed = await prune_old_requests(sessionmaker, full_count=full_count)
                if trimmed:
                    logger.info("retention: pruned %d old requests", trimmed)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("retention loop error (continuing)")
