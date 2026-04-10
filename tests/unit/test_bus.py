"""Tests for aiproxy.bus.StreamBus."""
import asyncio

import pytest

from aiproxy.bus import StreamBus


async def test_publish_to_subscriber() -> None:
    bus = StreamBus()
    q = bus.subscribe("ch1")
    bus.publish("ch1", {"type": "chunk", "n": 1})
    event = await asyncio.wait_for(q.get(), timeout=0.1)
    assert event == {"type": "chunk", "n": 1}


async def test_publish_to_multiple_subscribers() -> None:
    bus = StreamBus()
    q1 = bus.subscribe("ch1")
    q2 = bus.subscribe("ch1")
    bus.publish("ch1", {"n": 1})
    e1 = await asyncio.wait_for(q1.get(), timeout=0.1)
    e2 = await asyncio.wait_for(q2.get(), timeout=0.1)
    assert e1 == e2 == {"n": 1}


async def test_publish_without_subscribers_drops_silently() -> None:
    bus = StreamBus()
    # no subscribers — publish should not raise
    bus.publish("nobody", {"n": 1})


async def test_has_subscribers() -> None:
    bus = StreamBus()
    assert bus.has_subscribers("ch1") is False
    q = bus.subscribe("ch1")
    assert bus.has_subscribers("ch1") is True
    bus.unsubscribe("ch1", q)
    assert bus.has_subscribers("ch1") is False


async def test_unsubscribe_stops_delivery() -> None:
    bus = StreamBus()
    q = bus.subscribe("ch1")
    bus.unsubscribe("ch1", q)
    bus.publish("ch1", {"n": 1})
    # Queue should be empty
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.get(), timeout=0.05)


async def test_slow_subscriber_gets_dropped_not_blocked() -> None:
    """If a subscriber queue is full, publish should drop the event, not block."""
    bus = StreamBus(queue_maxsize=2)
    q = bus.subscribe("ch1")
    bus.publish("ch1", {"n": 1})
    bus.publish("ch1", {"n": 2})
    # Queue is now full. Third publish must not block or raise.
    bus.publish("ch1", {"n": 3})
    # First two events are still deliverable
    assert (await asyncio.wait_for(q.get(), timeout=0.05)) == {"n": 1}
    assert (await asyncio.wait_for(q.get(), timeout=0.05)) == {"n": 2}
    # The third was dropped
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.get(), timeout=0.05)
