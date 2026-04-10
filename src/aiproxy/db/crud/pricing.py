"""CRUD for the versioned pricing table."""
from __future__ import annotations

import time

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aiproxy.db.models import Pricing


async def upsert_current(
    session: AsyncSession,
    *,
    provider: str,
    model: str,
    input_per_1m_usd: float,
    output_per_1m_usd: float,
    cached_per_1m_usd: float | None,
    note: str | None = None,
) -> Pricing:
    """Add a new pricing version. Closes any currently-effective row for
    the same (provider, model) by setting its effective_to to now."""
    now = time.time()
    # Close any currently-effective row
    await session.execute(
        update(Pricing)
        .where(
            Pricing.provider == provider,
            Pricing.model == model,
            Pricing.effective_to.is_(None),
        )
        .values(effective_to=now)
    )
    row = Pricing(
        provider=provider,
        model=model,
        input_per_1m_usd=input_per_1m_usd,
        output_per_1m_usd=output_per_1m_usd,
        cached_per_1m_usd=cached_per_1m_usd,
        effective_from=now,
        effective_to=None,
        note=note,
        created_at=now,
    )
    session.add(row)
    await session.flush()
    return row


async def find_effective(
    session: AsyncSession,
    *,
    provider: str,
    model: str,
    at: float,
) -> Pricing | None:
    """Find the pricing row that was in effect at the given timestamp."""
    result = await session.execute(
        select(Pricing)
        .where(
            Pricing.provider == provider,
            Pricing.model == model,
            Pricing.effective_from <= at,
        )
        .where((Pricing.effective_to.is_(None)) | (Pricing.effective_to > at))
        .order_by(Pricing.effective_from.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
