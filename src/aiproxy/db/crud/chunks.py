"""CRUD operations for the chunks table.

Chunks are batch-inserted at stream finalize time. They're never inserted
one-at-a-time to keep the proxy's hot path free of DB writes.
"""
from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiproxy.db.models import Chunk


async def insert_batch(
    session: AsyncSession,
    req_id: str,
    batch: list[tuple[int, int, bytes]],
) -> None:
    """Insert a batch of chunks. Each item is (seq, offset_ns, data)."""
    if not batch:
        return
    rows = [
        Chunk(req_id=req_id, seq=seq, offset_ns=offset_ns, size=len(data), data=data)
        for (seq, offset_ns, data) in batch
    ]
    session.add_all(rows)
    await session.flush()


async def list_for_request(session: AsyncSession, req_id: str) -> Sequence[Chunk]:
    result = await session.execute(
        select(Chunk).where(Chunk.req_id == req_id).order_by(Chunk.seq)
    )
    return result.scalars().all()


async def count_for_request(session: AsyncSession, req_id: str) -> int:
    result = await session.execute(
        select(func.count()).select_from(Chunk).where(Chunk.req_id == req_id)
    )
    return int(result.scalar() or 0)


async def delete_for_request(session: AsyncSession, req_id: str) -> None:
    from sqlalchemy import delete
    await session.execute(delete(Chunk).where(Chunk.req_id == req_id))
