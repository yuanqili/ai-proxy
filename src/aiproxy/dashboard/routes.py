"""Dashboard HTTP + WebSocket routes.

Phase 3 adds:
- Login endpoint + signed session cookie
- Auth dependency on every /dashboard/api/* and /dashboard/ws/* route
- List endpoint with filters + search
- Detail endpoint
- Keys/pricing/config management endpoints
- Batch operations

GET /dashboard/                       static HTML (if logged in) or redirect to login
GET /dashboard/login                   static login form
POST /dashboard/login                  validate master key, issue cookie
POST /dashboard/logout                 clear cookie
GET /dashboard/api/active              (protected) snapshot of active + history
GET /dashboard/api/requests            (protected) paginated list with filters + search
GET /dashboard/api/requests/{req_id}   (protected) full detail + chunks
GET /dashboard/api/keys                (protected) list keys
POST /dashboard/api/keys               (protected) create key
PATCH /dashboard/api/keys/{id}         (protected) update (active/name/note/valid_to)
GET /dashboard/api/pricing             (protected) list all pricing rows
POST /dashboard/api/pricing            (protected) upsert current
GET /dashboard/api/config              (protected) get all runtime settings
POST /dashboard/api/config             (protected) upsert one key
POST /dashboard/api/batch/strip-binaries  (protected) strip base64 binaries
WS  /dashboard/ws/active               (protected via cookie)
WS  /dashboard/ws/stream/{req_id}      (protected via cookie)
"""
from __future__ import annotations

import asyncio
import base64
import hmac
import re
import secrets
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from aiproxy.providers.base import Provider
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker

from aiproxy.auth.dashboard_auth import (
    COOKIE_NAME,
    issue_session_token,
    make_session_cookie_response,
    require_dashboard_session,
    verify_session_token,
)
from aiproxy.bus import StreamBus
from aiproxy.db.crud import api_keys as api_keys_crud
from aiproxy.db.crud import chunks as chunks_crud
from aiproxy.db.crud import config as config_crud
from aiproxy.db.crud import pricing as pricing_crud
from aiproxy.db.crud import requests as req_crud
from aiproxy.db.fts import search_requests
from aiproxy.db.models import Request as RequestModel
from aiproxy.registry import ACTIVE_CHANNEL, RequestRegistry

_STATIC_DIR = Path(__file__).parent / "static"
_SESSION_TTL = 30 * 24 * 60 * 60  # 30 days


# ---- Pydantic request bodies ----

class LoginBody(BaseModel):
    master_key: str


class CreateKeyBody(BaseModel):
    name: str
    note: str | None = None
    valid_from: float | None = None
    valid_to: float | None = None


class UpdateKeyBody(BaseModel):
    name: str | None = None
    note: str | None = None
    is_active: bool | None = None
    valid_to: float | None = None


class UpsertPricingBody(BaseModel):
    provider: str
    model: str
    input_per_1m_usd: float
    output_per_1m_usd: float
    cached_per_1m_usd: float | None = None
    note: str | None = None


class SetConfigBody(BaseModel):
    key: str
    value: Any


class StripBinariesBody(BaseModel):
    req_ids: list[str] | None = None
    providers: list[str] | None = None
    all: bool = False


def create_dashboard_router(
    *,
    bus: StreamBus,
    registry: RequestRegistry,
    sessionmaker: async_sessionmaker | None,
    master_key: str,
    session_secret: str,
    secure_cookies: bool,
    providers_map: "dict[str, Provider] | None" = None,
) -> APIRouter:
    router = APIRouter(prefix="/dashboard")
    require_session = require_dashboard_session(session_secret)
    providers_map = providers_map or {}

    # ---- static ----

    @router.get("/")
    async def index(request: Request) -> Any:
        # Gate the HTML behind the cookie too — if not logged in, send to login
        token = request.cookies.get(COOKIE_NAME)
        result = verify_session_token(token, secret=session_secret)
        if not result.valid:
            return RedirectResponse(url="/dashboard/login", status_code=303)
        return FileResponse(_STATIC_DIR / "index.html")

    @router.get("/login")
    async def login_page() -> FileResponse:
        return FileResponse(_STATIC_DIR / "login.html")

    # ---- auth ----

    @router.post("/login")
    async def login(body: LoginBody) -> JSONResponse:
        if not hmac.compare_digest(body.master_key.encode(), master_key.encode()):
            raise HTTPException(status_code=401, detail="invalid master key")
        token = issue_session_token(secret=session_secret, ttl_seconds=_SESSION_TTL)
        return make_session_cookie_response(
            token, secure=secure_cookies, max_age_seconds=_SESSION_TTL,
        )

    @router.post("/logout")
    async def logout(_: dict = Depends(require_session)) -> JSONResponse:
        resp = JSONResponse({"ok": True})
        resp.set_cookie(
            key=COOKIE_NAME, value="", httponly=True, samesite="strict",
            secure=secure_cookies, max_age=0, path="/",
        )
        return resp

    # ---- protected: active + live ----

    @router.get("/api/active")
    async def api_active(_: dict = Depends(require_session)) -> JSONResponse:
        return JSONResponse({
            "active": registry.active(),
            "history": registry.history(),
        })

    # ---- protected: list + search + detail ----

    @router.get("/api/requests")
    async def api_list_requests(
        request: Request,
        _: dict = Depends(require_session),
    ) -> JSONResponse:
        if sessionmaker is None:
            raise HTTPException(status_code=500, detail="sessionmaker not configured")

        qp = request.query_params
        providers = qp.getlist("provider") or None
        models = qp.getlist("model") or None
        statuses = qp.getlist("status") or None
        search = qp.get("q")
        limit = int(qp.get("limit", "50"))
        offset = int(qp.get("offset", "0"))
        since = float(qp["since"]) if qp.get("since") else None
        until = float(qp["until"]) if qp.get("until") else None

        async with sessionmaker() as s:
            req_ids = None
            if search:
                req_ids = await search_requests(s, query=search, limit=500)
                if not req_ids:
                    return JSONResponse({"rows": [], "total": 0, "limit": limit, "offset": offset})

            rows, total = await req_crud.list_with_filters(
                s,
                providers=providers,
                models=models,
                statuses=statuses,
                since=since,
                until=until,
                req_ids=req_ids,
                limit=limit,
                offset=offset,
            )
            return JSONResponse({
                "rows": [_serialize_row_summary(r) for r in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
            })

    @router.get("/api/requests/{req_id}")
    async def api_request_detail(
        req_id: str,
        _: dict = Depends(require_session),
    ) -> JSONResponse:
        if sessionmaker is None:
            raise HTTPException(status_code=500, detail="sessionmaker not configured")
        async with sessionmaker() as s:
            row = await req_crud.get_by_id(s, req_id)
            if row is None:
                raise HTTPException(status_code=404, detail="request not found")
            chunks = await chunks_crud.list_for_request(s, req_id)
            return JSONResponse({
                "request": _serialize_row_full(row),
                "chunks": [
                    {
                        "seq": c.seq,
                        "offset_ns": c.offset_ns,
                        "size": c.size,
                        "data_b64": base64.b64encode(c.data).decode("ascii"),
                    }
                    for c in chunks
                ],
            })

    @router.get("/api/requests/{req_id}/replay")
    async def api_request_replay(
        req_id: str,
        _: dict = Depends(require_session),
    ) -> JSONResponse:
        if sessionmaker is None:
            raise HTTPException(status_code=500, detail="sessionmaker not configured")
        async with sessionmaker() as s:
            row = await req_crud.get_by_id(s, req_id)
            if row is None:
                raise HTTPException(status_code=404, detail="request not found")
            chunks = await chunks_crud.list_for_request(s, req_id)

        provider_name = row.provider or ""
        provider = providers_map.get(provider_name)

        parsed: list[dict] = []
        first_content_offset_ns: int | None = None
        total_duration_ns = 0

        for c in chunks:
            if provider is not None:
                text_delta, events = provider.extract_chunk_text(c.data)
            else:
                text_delta, events = ("", [])
            if first_content_offset_ns is None and text_delta:
                first_content_offset_ns = c.offset_ns
            if c.offset_ns > total_duration_ns:
                total_duration_ns = c.offset_ns
            parsed.append({
                "seq": c.seq,
                "offset_ns": c.offset_ns,
                "size": c.size,
                "text_delta": text_delta,
                "events": events,
                "raw_b64": base64.b64encode(c.data).decode("ascii"),
            })

        return JSONResponse({
            "req_id": req_id,
            "provider": provider_name,
            "model": row.model,
            "is_streaming": bool(row.is_streaming),
            "status": row.status,
            "chunks": parsed,
            "first_content_offset_ns": first_content_offset_ns,
            "total_duration_ns": total_duration_ns,
        })

    # ---- protected: keys ----

    @router.get("/api/keys")
    async def api_list_keys(_: dict = Depends(require_session)) -> JSONResponse:
        async with sessionmaker() as s:
            keys = await api_keys_crud.list_all(s)
        return JSONResponse({"keys": [_serialize_key(k) for k in keys]})

    @router.post("/api/keys")
    async def api_create_key(
        body: CreateKeyBody,
        _: dict = Depends(require_session),
    ) -> JSONResponse:
        key_value = "sk-aiprx-" + secrets.token_urlsafe(24)
        async with sessionmaker() as s:
            row = await api_keys_crud.create(
                s,
                key=key_value,
                name=body.name,
                note=body.note,
                valid_from=body.valid_from,
                valid_to=body.valid_to,
            )
            await s.commit()
        return JSONResponse({"key": _serialize_key(row)})

    @router.patch("/api/keys/{key_id}")
    async def api_update_key(
        key_id: int,
        body: UpdateKeyBody,
        _: dict = Depends(require_session),
    ) -> JSONResponse:
        async with sessionmaker() as s:
            # Find the key first to get its plaintext value
            all_keys = await api_keys_crud.list_all(s)
            match = next((k for k in all_keys if k.id == key_id), None)
            if match is None:
                raise HTTPException(status_code=404, detail="key not found")
            # Apply updates via raw values
            from sqlalchemy import update
            from aiproxy.db.models import ApiKey
            updates: dict[str, Any] = {}
            if body.name is not None:
                updates["name"] = body.name
            if body.note is not None:
                updates["note"] = body.note
            if body.is_active is not None:
                updates["is_active"] = 1 if body.is_active else 0
            if body.valid_to is not None:
                updates["valid_to"] = body.valid_to
            if updates:
                await s.execute(update(ApiKey).where(ApiKey.id == key_id).values(**updates))
                await s.commit()
            # Re-fetch
            all_keys = await api_keys_crud.list_all(s)
            updated = next((k for k in all_keys if k.id == key_id), None)
        return JSONResponse({"key": _serialize_key(updated)})

    # ---- protected: pricing ----

    @router.get("/api/pricing")
    async def api_list_pricing(_: dict = Depends(require_session)) -> JSONResponse:
        async with sessionmaker() as s:
            from sqlalchemy import select
            from aiproxy.db.models import Pricing
            rows = (await s.execute(
                select(Pricing).order_by(Pricing.provider, Pricing.model, Pricing.effective_from.desc())
            )).scalars().all()
        return JSONResponse({"pricing": [_serialize_pricing(p) for p in rows]})

    @router.post("/api/pricing")
    async def api_upsert_pricing(
        body: UpsertPricingBody,
        _: dict = Depends(require_session),
    ) -> JSONResponse:
        async with sessionmaker() as s:
            row = await pricing_crud.upsert_current(
                s,
                provider=body.provider,
                model=body.model,
                input_per_1m_usd=body.input_per_1m_usd,
                output_per_1m_usd=body.output_per_1m_usd,
                cached_per_1m_usd=body.cached_per_1m_usd,
                note=body.note,
            )
            await s.commit()
        return JSONResponse({"pricing": _serialize_pricing(row)})

    # ---- protected: config ----

    @router.get("/api/config")
    async def api_get_config(_: dict = Depends(require_session)) -> JSONResponse:
        async with sessionmaker() as s:
            all_cfg = await config_crud.get_all(s)
        # Always return defaults for known settings
        all_cfg.setdefault("retention.full_count", 500)
        return JSONResponse({"config": all_cfg})

    @router.post("/api/config")
    async def api_set_config(
        body: SetConfigBody,
        _: dict = Depends(require_session),
    ) -> JSONResponse:
        async with sessionmaker() as s:
            await config_crud.set_(s, body.key, body.value)
            await s.commit()
        return JSONResponse({"ok": True, "key": body.key, "value": body.value})

    # ---- protected: batch strip binaries ----

    _BINARY_RE = re.compile(rb"data:([^;]+);base64,[A-Za-z0-9+/=]+")

    def _strip_binaries(blob: bytes | None) -> bytes | None:
        if blob is None:
            return None
        def replace(m: re.Match) -> bytes:
            mime = m.group(1)
            size = len(m.group(0))
            return f"[{mime.decode('ascii', 'replace')}: {size}B stripped]".encode()
        return _BINARY_RE.sub(replace, blob)

    @router.post("/api/batch/strip-binaries")
    async def api_strip_binaries(
        body: StripBinariesBody,
        _: dict = Depends(require_session),
    ) -> JSONResponse:
        async with sessionmaker() as s:
            from sqlalchemy import select, update as sa_update
            stmt = select(RequestModel)
            if body.req_ids:
                stmt = stmt.where(RequestModel.req_id.in_(body.req_ids))
            elif body.providers:
                stmt = stmt.where(RequestModel.provider.in_(body.providers))
            elif not body.all:
                raise HTTPException(status_code=400, detail="must specify req_ids, providers, or all=true")
            rows = (await s.execute(stmt)).scalars().all()
            stripped = 0
            for row in rows:
                new_req = _strip_binaries(row.req_body)
                new_resp = _strip_binaries(row.resp_body)
                if new_req != row.req_body or new_resp != row.resp_body:
                    await s.execute(
                        sa_update(RequestModel)
                        .where(RequestModel.req_id == row.req_id)
                        .values(
                            req_body=new_req,
                            resp_body=new_resp,
                            binaries_stripped=1,
                        )
                    )
                    stripped += 1
            await s.commit()
        return JSONResponse({"stripped": stripped})

    # ---- WebSockets ----

    async def _ws_auth_or_close(ws: WebSocket) -> bool:
        await ws.accept()
        token = ws.cookies.get(COOKIE_NAME)
        result = verify_session_token(token, secret=session_secret)
        if not result.valid:
            await ws.close(code=4401)
            return False
        return True

    @router.websocket("/ws/active")
    async def ws_active(ws: WebSocket) -> None:
        if not await _ws_auth_or_close(ws):
            return
        q = bus.subscribe(ACTIVE_CHANNEL)
        try:
            await ws.send_json({
                "type": "snapshot",
                "active": registry.active(),
                "history": registry.history(),
            })
            while True:
                event = await q.get()
                await ws.send_json(event)
        except WebSocketDisconnect:
            pass
        finally:
            bus.unsubscribe(ACTIVE_CHANNEL, q)

    @router.websocket("/ws/stream/{req_id}")
    async def ws_stream(ws: WebSocket, req_id: str) -> None:
        if not await _ws_auth_or_close(ws):
            return
        q = bus.subscribe(req_id)
        try:
            meta = registry.get(req_id)
            if meta is None:
                await ws.send_json({"type": "not_found", "req_id": req_id})
                return
            await ws.send_json({"type": "meta", "req": meta.snapshot()})
            if meta.status in ("done", "error", "canceled"):
                await ws.send_json({"type": "already_done", "status": meta.status})
                return
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=60.0)
                except asyncio.TimeoutError:
                    await ws.send_json({"type": "heartbeat"})
                    continue
                await ws.send_json(event)
                if event.get("type") in ("done", "error"):
                    break
        except WebSocketDisconnect:
            pass
        finally:
            bus.unsubscribe(req_id, q)

    return router


# ---- serializers ----

def _serialize_row_summary(row: RequestModel) -> dict:
    return {
        "req_id": row.req_id,
        "provider": row.provider,
        "endpoint": row.endpoint,
        "method": row.method,
        "model": row.model,
        "is_streaming": bool(row.is_streaming),
        "status": row.status,
        "status_code": row.status_code,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "cost_usd": row.cost_usd,
        "chunk_count": row.chunk_count,
        "binaries_stripped": bool(row.binaries_stripped),
        "api_key_id": row.api_key_id,
    }


def _serialize_row_full(row: RequestModel) -> dict:
    base = _serialize_row_summary(row)
    base.update({
        "req_headers": row.req_headers,
        "req_query": row.req_query,
        "req_body_b64": base64.b64encode(row.req_body).decode("ascii") if row.req_body else None,
        "req_body_size": row.req_body_size,
        "resp_headers": row.resp_headers,
        "resp_body_b64": base64.b64encode(row.resp_body).decode("ascii") if row.resp_body else None,
        "resp_body_size": row.resp_body_size,
        "error_class": row.error_class,
        "error_message": row.error_message,
        "cached_tokens": row.cached_tokens,
        "reasoning_tokens": row.reasoning_tokens,
        "pricing_id": row.pricing_id,
        "upstream_sent_at": row.upstream_sent_at,
        "first_chunk_at": row.first_chunk_at,
    })
    return base


def _serialize_key(row) -> dict:
    return {
        "id": row.id,
        "key": row.key,
        "name": row.name,
        "note": row.note,
        "is_active": bool(row.is_active),
        "created_at": row.created_at,
        "valid_from": row.valid_from,
        "valid_to": row.valid_to,
        "last_used_at": row.last_used_at,
    }


def _serialize_pricing(row) -> dict:
    return {
        "id": row.id,
        "provider": row.provider,
        "model": row.model,
        "input_per_1m_usd": row.input_per_1m_usd,
        "output_per_1m_usd": row.output_per_1m_usd,
        "cached_per_1m_usd": row.cached_per_1m_usd,
        "effective_from": row.effective_from,
        "effective_to": row.effective_to,
        "note": row.note,
        "created_at": row.created_at,
    }
