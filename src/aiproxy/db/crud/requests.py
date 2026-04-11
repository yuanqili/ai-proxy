"""CRUD operations for the requests table.

Phase 1 covers: create_pending, mark_finished, mark_error, get_by_id.
Chunk persistence and cost fields are written by Phase 2.
"""
from __future__ import annotations

import json

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aiproxy.db.models import Request


def _dumps(obj: object) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


async def create_pending(
    session: AsyncSession,
    *,
    req_id: str,
    api_key_id: int | None,
    provider: str,
    endpoint: str,
    method: str,
    model: str | None,
    is_streaming: bool,
    client_ip: str | None,
    client_ua: str | None,
    req_headers: dict[str, str],
    req_query: list[tuple[str, str]] | None,
    req_body: bytes | None,
    started_at: float,
    labels: str | None = None,
    note: str | None = None,
    request_has_image: bool = False,
    request_has_file: bool = False,
    response_is_json: bool = False,
) -> None:
    row = Request(
        req_id=req_id,
        api_key_id=api_key_id,
        provider=provider,
        endpoint=endpoint,
        method=method,
        model=model,
        is_streaming=1 if is_streaming else 0,
        client_ip=client_ip,
        client_ua=client_ua,
        req_headers=_dumps(req_headers),
        req_query=_dumps(req_query) if req_query else None,
        req_body=req_body,
        req_body_size=len(req_body) if req_body else 0,
        started_at=started_at,
        status="pending",
        labels=labels,
        note=note,
        request_has_image=int(request_has_image),
        request_has_file=int(request_has_file),
        response_is_json=int(response_is_json),
    )
    session.add(row)
    await session.flush()


async def mark_finished(
    session: AsyncSession,
    *,
    req_id: str,
    status: str,
    status_code: int | None,
    resp_headers: dict[str, str] | None,
    resp_body: bytes | None,
    finished_at: float,
) -> None:
    await session.execute(
        update(Request)
        .where(Request.req_id == req_id)
        .values(
            status=status,
            status_code=status_code,
            resp_headers=_dumps(resp_headers) if resp_headers is not None else None,
            resp_body=resp_body,
            resp_body_size=len(resp_body) if resp_body else 0,
            finished_at=finished_at,
        )
    )


async def mark_error(
    session: AsyncSession,
    *,
    req_id: str,
    error_class: str,
    error_message: str,
    finished_at: float,
) -> None:
    await session.execute(
        update(Request)
        .where(Request.req_id == req_id)
        .values(
            status="error",
            error_class=error_class,
            error_message=error_message,
            finished_at=finished_at,
        )
    )


async def get_by_id(session: AsyncSession, req_id: str) -> Request | None:
    result = await session.execute(select(Request).where(Request.req_id == req_id))
    return result.scalar_one_or_none()


from sqlalchemy import func


async def list_with_filters(
    session: AsyncSession,
    *,
    providers: list[str] | None = None,
    models: list[str] | None = None,
    statuses: list[str] | None = None,
    api_key_id: int | None = None,
    since: float | None = None,
    until: float | None = None,
    req_ids: list[str] | None = None,
    labels: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Request], int]:
    """Paginated, filtered list of requests ordered by started_at DESC.

    Returns (rows, total_count) — total_count ignores limit/offset.

    If `req_ids` is provided, result is limited to those IDs (used by
    FTS-driven search to narrow the listing).

    `labels` filters to rows that contain *all* of the given labels.
    Labels are stored as normalized comma-joined strings, so membership
    is tested via ',' || labels || ',' LIKE '%,<label>,%'.
    """
    stmt = select(Request)
    count_stmt = select(func.count()).select_from(Request)

    conditions = []
    if providers:
        conditions.append(Request.provider.in_(providers))
    if models:
        conditions.append(Request.model.in_(models))
    if statuses:
        conditions.append(Request.status.in_(statuses))
    if api_key_id is not None:
        conditions.append(Request.api_key_id == api_key_id)
    if since is not None:
        conditions.append(Request.started_at >= since)
    if until is not None:
        conditions.append(Request.started_at <= until)
    if req_ids is not None:
        conditions.append(Request.req_id.in_(req_ids))
    if labels:
        from sqlalchemy import or_
        for label in labels:
            # Escape LIKE wildcards that may appear inside a user-supplied
            # label token, so `%prod` doesn't match everything.
            esc = (
                label.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            conditions.append(Request.labels.is_not(None))
            conditions.append(
                or_(
                    Request.labels == label,
                    Request.labels.like(f"{esc},%", escape="\\"),
                    Request.labels.like(f"%,{esc}", escape="\\"),
                    Request.labels.like(f"%,{esc},%", escape="\\"),
                )
            )

    for cond in conditions:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    stmt = stmt.order_by(Request.started_at.desc()).limit(limit).offset(offset)

    rows = (await session.execute(stmt)).scalars().all()
    total = (await session.execute(count_stmt)).scalar() or 0
    return list(rows), int(total)
