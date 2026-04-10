"""In-memory pub/sub bus + active request registry.

Design notes:
- Each request has a per-request channel (keyed by req_id) for chunk events.
- One global channel ("__active__") broadcasts registry changes (start/finish)
  so the dashboard's active-list view updates live.
- Subscribers get a bounded Queue. If a slow subscriber fills it up, we drop
  events for that subscriber rather than blocking the proxy hot path.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from typing import Any

ACTIVE_CHANNEL = "__active__"
QUEUE_MAXSIZE = 1000


@dataclass
class RequestMeta:
    req_id: str
    provider: str
    model: str | None
    method: str
    path: str
    client_ip: str | None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    status: str = "pending"  # pending | streaming | done | error
    status_code: int | None = None
    error: str | None = None
    bytes_out: int = 0  # bytes forwarded to downstream
    chunks: int = 0

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


class StreamBus:
    """Per-channel bounded pub/sub with tolerant drops on slow consumers."""

    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = {}

    def publish(self, channel: str, event: dict[str, Any]) -> None:
        """Non-blocking publish. Drops event for any full subscriber queue."""
        subs = self._subs.get(channel)
        if not subs:
            return
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Dashboard can't keep up; drop rather than block proxy.
                pass

    def has_subscribers(self, channel: str) -> bool:
        return bool(self._subs.get(channel))

    def subscribe(self, channel: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._subs.setdefault(channel, []).append(q)
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(channel)
        if not subs:
            return
        try:
            subs.remove(q)
        except ValueError:
            pass
        if not subs:
            self._subs.pop(channel, None)


class RequestRegistry:
    """Tracks in-flight and recently finished requests.

    Publishes lifecycle events on the ACTIVE_CHANNEL so a dashboard
    can live-update its list.
    """

    def __init__(self, bus: StreamBus, history_limit: int = 100) -> None:
        self._bus = bus
        self._active: dict[str, RequestMeta] = {}
        self._history: list[RequestMeta] = []
        self._history_limit = history_limit

    def start(self, meta: RequestMeta) -> None:
        self._active[meta.req_id] = meta
        self._bus.publish(ACTIVE_CHANNEL, {"type": "start", "req": meta.snapshot()})

    def update(self, req_id: str, **fields: Any) -> None:
        meta = self._active.get(req_id)
        if not meta:
            return
        for k, v in fields.items():
            setattr(meta, k, v)
        self._bus.publish(ACTIVE_CHANNEL, {"type": "update", "req": meta.snapshot()})

    def finish(self, req_id: str, **fields: Any) -> None:
        meta = self._active.pop(req_id, None)
        if not meta:
            return
        for k, v in fields.items():
            setattr(meta, k, v)
        meta.finished_at = time.time()
        self._history.append(meta)
        if len(self._history) > self._history_limit:
            self._history.pop(0)
        self._bus.publish(ACTIVE_CHANNEL, {"type": "finish", "req": meta.snapshot()})

    def get(self, req_id: str) -> RequestMeta | None:
        return self._active.get(req_id) or next(
            (m for m in reversed(self._history) if m.req_id == req_id), None
        )

    def active(self) -> list[dict[str, Any]]:
        return [m.snapshot() for m in self._active.values()]

    def history(self) -> list[dict[str, Any]]:
        return [m.snapshot() for m in reversed(self._history)]


# Global singletons. FastAPI could also inject these via app.state, but for
# an MVP this is simpler and the whole app is single-process.
bus = StreamBus()
registry = RequestRegistry(bus)
