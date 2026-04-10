"""Cost calculator: look up current pricing, compute cost from Usage."""
from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from aiproxy.db.crud import pricing as pricing_crud
from aiproxy.providers.base import Usage


async def compute_cost(
    session: AsyncSession,
    *,
    provider: str,
    model: str,
    usage: Usage | None,
) -> tuple[int, float] | None:
    """Return (pricing_id, cost_usd) or None if no pricing is found or no usage."""
    if usage is None:
        return None
    pricing = await pricing_crud.find_effective(
        session, provider=provider, model=model, at=time.time(),
    )
    if pricing is None:
        return None

    input_tokens = usage.input_tokens or 0
    output_tokens = usage.output_tokens or 0
    cached_tokens = usage.cached_tokens or 0

    # Non-cached portion of the input is billed at the standard input rate
    non_cached_input = max(0, input_tokens - cached_tokens)
    non_cached_cost = non_cached_input * pricing.input_per_1m_usd / 1_000_000
    output_cost = output_tokens * pricing.output_per_1m_usd / 1_000_000

    cached_cost = 0.0
    if cached_tokens and pricing.cached_per_1m_usd is not None:
        cached_cost = cached_tokens * pricing.cached_per_1m_usd / 1_000_000
    elif cached_tokens:
        # No discounted cached rate — treat as standard input rate
        cached_cost = cached_tokens * pricing.input_per_1m_usd / 1_000_000

    total = non_cached_cost + output_cost + cached_cost
    return (pricing.id, total)
