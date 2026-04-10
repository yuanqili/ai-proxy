"""Dashboard HTTP + WebSocket routes.

- GET /dashboard/          -> static HTML
- GET /dashboard/api/active -> JSON snapshot of active + history requests
- WS  /dashboard/ws/active  -> live stream of registry start/update/finish events
- WS  /dashboard/ws/stream/{req_id} -> live stream of chunks for one request
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from aiproxy.bus import ACTIVE_CHANNEL, bus, registry

router = APIRouter(prefix="/dashboard")

_STATIC_DIR = Path(__file__).parent / "static"


@router.get("/")
async def dashboard_index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


@router.get("/api/active")
async def list_requests() -> JSONResponse:
    return JSONResponse(
        {
            "active": registry.active(),
            "history": registry.history(),
        }
    )


@router.websocket("/ws/active")
async def ws_active(ws: WebSocket) -> None:
    """Live-stream registry lifecycle events.

    On connect, sends a 'snapshot' event with current active+history,
    then streams 'start'/'update'/'finish' deltas.
    """
    await ws.accept()
    q = bus.subscribe(ACTIVE_CHANNEL)
    try:
        await ws.send_json(
            {
                "type": "snapshot",
                "active": registry.active(),
                "history": registry.history(),
            }
        )
        while True:
            event = await q.get()
            await ws.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(ACTIVE_CHANNEL, q)


@router.websocket("/ws/stream/{req_id}")
async def ws_stream(ws: WebSocket, req_id: str) -> None:
    """Live-stream chunks for a single in-flight request.

    The subscription happens immediately so we don't miss early chunks.
    If the request has already finished, we send an 'already_done' event
    and close the socket.
    """
    await ws.accept()
    q = bus.subscribe(req_id)
    try:
        meta = registry.get(req_id)
        if meta is None:
            await ws.send_json({"type": "not_found", "req_id": req_id})
            return
        await ws.send_json({"type": "meta", "req": meta.snapshot()})
        if meta.status in ("done", "error"):
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
