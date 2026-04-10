"""Dashboard HTTP + WebSocket routes (Phase 2 — no login yet).

- GET  /dashboard/                 static HTML
- GET  /dashboard/api/active       snapshot of active + history (from in-memory registry)
- WS   /dashboard/ws/active        live lifecycle events (start/update/finish)
- WS   /dashboard/ws/stream/{rid}  live chunks for one request
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from aiproxy.bus import StreamBus
from aiproxy.registry import ACTIVE_CHANNEL, RequestRegistry

_STATIC_DIR = Path(__file__).parent / "static"


def create_dashboard_router(
    *,
    bus: StreamBus,
    registry: RequestRegistry,
) -> APIRouter:
    router = APIRouter(prefix="/dashboard")

    @router.get("/")
    async def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    @router.get("/api/active")
    async def list_requests() -> JSONResponse:
        return JSONResponse({
            "active": registry.active(),
            "history": registry.history(),
        })

    @router.websocket("/ws/active")
    async def ws_active(ws: WebSocket) -> None:
        await ws.accept()
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
        await ws.accept()
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
