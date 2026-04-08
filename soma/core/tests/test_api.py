"""Tests for WebSocket API — connect, send intent, receive sensation."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from soma.api.events import Intent, Sensation
from soma.api.server import SomaServer


@pytest.fixture
def server():
    return SomaServer()


@pytest.fixture
def client(server):
    return TestClient(server.app)


def test_status_endpoint(client):
    response = client.get("/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"


def test_post_intent(client, server):
    response = client.post("/intent", json={"action": "release", "params": {}})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"
    assert data["action"] == "release"


def test_websocket_connect(client):
    with client.websocket_connect("/consciousness") as ws:
        # Send an intent
        ws.send_json({
            "type": "intent",
            "action": "hold_gentle",
            "params": {"stiffness": 0.3},
        })
        # Server should have queued the intent


def test_websocket_invalid_message(client):
    with client.websocket_connect("/consciousness") as ws:
        ws.send_text("not json at all {{{")
        # Server should send back an error
        response = ws.receive_json()
        assert "error" in response


def test_broadcast_sensation(server):
    """Test that sensation broadcast works."""
    sensation = Sensation(
        fingers_active=[0, 1],
        pressures=[0.3, 0.5],
        gesture="holding",
        skin_temperature=33.2,
        contact_temperature=35.8,
    )
    # Without subscribers, broadcast should not error
    asyncio.get_event_loop().run_until_complete(
        server.broadcast_sensation(sensation)
    )
    assert server._latest_sensation is not None
    assert server._latest_sensation.gesture == "holding"


def test_intent_queue(server):
    """Test intent queue management."""
    async def _test():
        intent = Intent(action="wave", params={})
        await server._intent_queue.put(intent)
        result = await server.get_pending_intent()
        assert result is not None
        assert result.action == "wave"

        # Queue should be empty now
        result = await server.get_pending_intent()
        assert result is None

    asyncio.get_event_loop().run_until_complete(_test())
