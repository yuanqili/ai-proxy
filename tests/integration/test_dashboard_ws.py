"""Integration tests for the dashboard WebSocket endpoints."""
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aiproxy.bus import StreamBus
from aiproxy.dashboard.routes import create_dashboard_router
from aiproxy.registry import ACTIVE_CHANNEL, RequestMeta, RequestRegistry


@pytest.fixture
def app():
    bus = StreamBus()
    registry = RequestRegistry(bus)
    app = FastAPI()
    app.include_router(create_dashboard_router(bus=bus, registry=registry))
    return app, bus, registry


def test_ws_active_snapshot_and_start(app) -> None:
    a, bus, registry = app
    client = TestClient(a)
    with client.websocket_connect("/dashboard/ws/active") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
        assert snap["active"] == []

        # Start a request on the server side
        registry.start(RequestMeta(
            req_id="live1", provider="openai", model="gpt-4o",
            method="POST", path="/chat/completions", client_ip=None,
        ))
        ev = ws.receive_json()
        assert ev["type"] == "start"
        assert ev["req"]["req_id"] == "live1"


def test_ws_stream_not_found(app) -> None:
    a, _, _ = app
    client = TestClient(a)
    with client.websocket_connect("/dashboard/ws/stream/nonexistent") as ws:
        ev = ws.receive_json()
        assert ev["type"] == "not_found"


def test_ws_stream_already_done(app) -> None:
    a, _, registry = app
    registry.start(RequestMeta(
        req_id="old", provider="openai", model=None,
        method="POST", path="/p", client_ip=None,
    ))
    registry.finish("old", status="done")
    client = TestClient(a)
    with client.websocket_connect("/dashboard/ws/stream/old") as ws:
        meta = ws.receive_json()
        assert meta["type"] == "meta"
        done = ws.receive_json()
        assert done["type"] == "already_done"
        assert done["status"] == "done"


def test_ws_stream_receives_chunks(app) -> None:
    a, bus, registry = app
    registry.start(RequestMeta(
        req_id="streaming", provider="openai", model="gpt-4o",
        method="POST", path="/chat/completions", client_ip=None,
    ))
    registry.update("streaming", status="streaming")

    client = TestClient(a)
    with client.websocket_connect("/dashboard/ws/stream/streaming") as ws:
        meta = ws.receive_json()
        assert meta["type"] == "meta"

        # Publish a chunk — the ws handler should forward it
        bus.publish("streaming", {
            "type": "chunk", "req_id": "streaming", "seq": 0,
            "offset_ns": 0, "size": 5,
        })
        ev = ws.receive_json()
        assert ev["type"] == "chunk"
        assert ev["seq"] == 0

        bus.publish("streaming", {"type": "done", "req_id": "streaming", "status": "done"})
        ev = ws.receive_json()
        assert ev["type"] == "done"
