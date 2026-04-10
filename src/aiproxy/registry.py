"""In-memory registry of active + recently finished requests.

Used by the dashboard to get an instant list of what's happening RIGHT NOW
without hitting the DB. Publishes lifecycle events on ACTIVE_CHANNEL so
dashboards can live-update the list.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from aiproxy.bus import StreamBus

ACTIVE_CHANNEL = "__active__"


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
    status: str = "pending"  # pending | streaming | done | error | canceled
    status_code: int | None = None
    error_class: str | None = None
    error_message: str | None = None
    chunks: int = 0
    bytes_out: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


class RequestRegistry:
    """Tracks in-flight and recently finished requests, publishes to a bus."""

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
        if meta is None:
            return
        for k, v in fields.items():
            setattr(meta, k, v)
        self._bus.publish(ACTIVE_CHANNEL, {"type": "update", "req": meta.snapshot()})

    def finish(self, req_id: str, **fields: Any) -> None:
        meta = self._active.pop(req_id, None)
        if meta is None:
            return
        for k, v in fields.items():
            setattr(meta, k, v)
        meta.finished_at = time.time()
        self._history.insert(0, meta)  # most recent first
        if len(self._history) > self._history_limit:
            self._history.pop()
        self._bus.publish(ACTIVE_CHANNEL, {"type": "finish", "req": meta.snapshot()})

    def get(self, req_id: str) -> RequestMeta | None:
        return self._active.get(req_id) or next(
            (m for m in self._history if m.req_id == req_id), None
        )

    def active(self) -> list[dict[str, Any]]:
        return [m.snapshot() for m in self._active.values()]

    def history(self) -> list[dict[str, Any]]:
        return [m.snapshot() for m in self._history]
