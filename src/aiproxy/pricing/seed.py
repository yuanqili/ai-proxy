"""Seed the pricing table with known prices if it's empty."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aiproxy.db.crud import pricing as pricing_crud
from aiproxy.db.models import Pricing

_SEED_PATH = Path(__file__).parent.parent / "pricing_seed.json"


async def seed_pricing_if_empty(sessionmaker: async_sessionmaker) -> int:
    """Insert seed pricing rows if the table is empty. Returns rows inserted."""
    async with sessionmaker() as session:
        count = (
            await session.execute(select(func.count()).select_from(Pricing))
        ).scalar()
        if count and count > 0:
            return 0

        data = json.loads(_SEED_PATH.read_text())
        entries = data.get("entries", [])
        for entry in entries:
            await pricing_crud.upsert_current(
                session,
                provider=entry["provider"],
                model=entry["model"],
                input_per_1m_usd=entry["input_per_1m_usd"],
                output_per_1m_usd=entry["output_per_1m_usd"],
                cached_per_1m_usd=entry.get("cached_per_1m_usd"),
                note="seed",
            )
        await session.commit()
        return len(entries)
