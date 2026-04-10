"""CRUD operations for the api_keys table."""
from __future__ import annotations

import time
from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aiproxy.db.models import ApiKey


async def create(
    session: AsyncSession,
    *,
    key: str,
    name: str,
    note: str | None,
    valid_from: float | None,
    valid_to: float | None,
) -> ApiKey:
    row = ApiKey(
        key=key,
        name=name,
        note=note,
        is_active=1,
        created_at=time.time(),
        valid_from=valid_from,
        valid_to=valid_to,
        last_used_at=None,
    )
    session.add(row)
    await session.flush()
    return row


async def get_by_key(session: AsyncSession, key: str) -> ApiKey | None:
    result = await session.execute(select(ApiKey).where(ApiKey.key == key))
    return result.scalar_one_or_none()


async def list_all(session: AsyncSession) -> Sequence[ApiKey]:
    result = await session.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
    return result.scalars().all()


async def update_last_used(session: AsyncSession, key: str, when: float) -> None:
    await session.execute(
        update(ApiKey).where(ApiKey.key == key).values(last_used_at=when)
    )


async def set_active(session: AsyncSession, key: str, active: bool) -> None:
    await session.execute(
        update(ApiKey).where(ApiKey.key == key).values(is_active=1 if active else 0)
    )
