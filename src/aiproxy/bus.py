"""In-memory pub/sub bus for fanning out stream chunks to dashboard subscribers.

Design notes:
- Each channel is a string (usually a req_id, or the global '__active__' channel).
- Publish is non-blocking. If a subscriber's bounded queue is full, the event
  is dropped for that subscriber rather than stalling the proxy hot path.
- Single-process only. If we later need multi-worker, swap in Redis pub/sub.
"""
from __future__ import annotations

import asyncio
from typing import Any


class StreamBus:
    """Per-channel bounded pub/sub with tolerant drops on slow consumers."""

    def __init__(self, queue_maxsize: int = 1000) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = {}
        self._queue_maxsize = queue_maxsize

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
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_maxsize)
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
