"""Tests for aiproxy.registry.RequestRegistry."""
import asyncio

import pytest

from aiproxy.bus import StreamBus
from aiproxy.registry import ACTIVE_CHANNEL, RequestMeta, RequestRegistry


def test_start_adds_to_active_and_publishes() -> None:
    bus = StreamBus()
    reg = RequestRegistry(bus)
    q = bus.subscribe(ACTIVE_CHANNEL)

    meta = RequestMeta(
        req_id="abc", provider="openai", model="gpt-4o",
        method="POST", path="/chat/completions", client_ip="127.0.0.1",
    )
    reg.start(meta)

    assert len(reg.active()) == 1
    assert reg.active()[0]["req_id"] == "abc"
    ev = q.get_nowait()
    assert ev["type"] == "start"
    assert ev["req"]["req_id"] == "abc"


def test_update_mutates_and_publishes() -> None:
    bus = StreamBus()
    reg = RequestRegistry(bus)
    q = bus.subscribe(ACTIVE_CHANNEL)
    reg.start(RequestMeta(
        req_id="abc", provider="openai", model=None,
        method="POST", path="/p", client_ip=None,
    ))
    q.get_nowait()  # consume the start event

    reg.update("abc", status="streaming", status_code=200)
    ev = q.get_nowait()
    assert ev["type"] == "update"
    assert ev["req"]["status"] == "streaming"
    assert ev["req"]["status_code"] == 200


def test_finish_moves_to_history_and_publishes() -> None:
    bus = StreamBus()
    reg = RequestRegistry(bus, history_limit=3)
    q = bus.subscribe(ACTIVE_CHANNEL)
    reg.start(RequestMeta(
        req_id="abc", provider="openai", model=None,
        method="POST", path="/p", client_ip=None,
    ))
    q.get_nowait()

    reg.finish("abc", status="done", chunks=5, bytes_out=1024)
    assert reg.active() == []
    assert len(reg.history()) == 1
    assert reg.history()[0]["status"] == "done"
    assert reg.history()[0]["chunks"] == 5
    ev = q.get_nowait()
    assert ev["type"] == "finish"


def test_history_limit_enforced() -> None:
    bus = StreamBus()
    reg = RequestRegistry(bus, history_limit=2)
    for i in range(5):
        reg.start(RequestMeta(
            req_id=f"r{i}", provider="openai", model=None,
            method="POST", path="/p", client_ip=None,
        ))
        reg.finish(f"r{i}", status="done")
    assert len(reg.history()) == 2
    # Most recent first
    assert reg.history()[0]["req_id"] == "r4"
    assert reg.history()[1]["req_id"] == "r3"


def test_get_returns_active_or_history() -> None:
    bus = StreamBus()
    reg = RequestRegistry(bus)
    reg.start(RequestMeta(
        req_id="live", provider="openai", model=None,
        method="POST", path="/p", client_ip=None,
    ))
    reg.start(RequestMeta(
        req_id="old", provider="openai", model=None,
        method="POST", path="/p", client_ip=None,
    ))
    reg.finish("old", status="done")

    assert reg.get("live") is not None
    assert reg.get("live").status == "pending"
    assert reg.get("old") is not None
    assert reg.get("old").status == "done"
    assert reg.get("missing") is None
