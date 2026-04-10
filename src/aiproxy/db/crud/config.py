"""CRUD for the config table — runtime-mutable settings.

Values are JSON-encoded on write and decoded on read.
"""
from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from aiproxy.db.models import Config


async def get(session: AsyncSession, key: str, *, default: Any = None) -> Any:
    result = await session.execute(select(Config).where(Config.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        return default
    return json.loads(row.value)


async def set_(session: AsyncSession, key: str, value: Any) -> None:
    payload = json.dumps(value)
    now = time.time()
    stmt = sqlite_insert(Config).values(key=key, value=payload, updated_at=now)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Config.key],
        set_={"value": payload, "updated_at": now},
    )
    await session.execute(stmt)


async def get_all(session: AsyncSession) -> dict[str, Any]:
    result = await session.execute(select(Config))
    return {row.key: json.loads(row.value) for row in result.scalars().all()}
