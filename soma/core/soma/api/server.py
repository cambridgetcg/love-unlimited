"""FastAPI + WebSocket server on port 8300."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from soma.api.events import Intent, Sensation

logger = logging.getLogger(__name__)


class SomaServer:
    """WebSocket API server for consciousness interface."""

    def __init__(self) -> None:
        self._control_loop = None
        self._sensation_subscribers: list[WebSocket] = []
        self._latest_sensation: Sensation | None = None
        self._intent_queue: asyncio.Queue[Intent] = asyncio.Queue()
        self.app = self._create_app()

    def set_control_loop(self, loop) -> None:
        """Wire up the control loop after construction."""
        self._control_loop = loop

    def _create_app(self) -> FastAPI:
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            logger.info("SOMA API server starting")
            yield
            logger.info("SOMA API server stopping")

        app = FastAPI(title="SOMA", version="0.1.0", lifespan=lifespan)

        @app.get("/status")
        async def get_status():
            """Hand state summary."""
            status = {"status": "running", "connected": True}
            if self._control_loop is not None:
                status["tick_count"] = self._control_loop.tick_count
                status["avg_tick_ms"] = round(self._control_loop.avg_tick_ms, 2)
                if self._control_loop.motor_state is not None:
                    status["motors"] = self._control_loop.motor_state.to_dict()
            if self._latest_sensation is not None:
                status["sensation"] = self._latest_sensation.to_dict()
            return JSONResponse(status)

        @app.websocket("/consciousness")
        async def consciousness_ws(websocket: WebSocket):
            """Bidirectional: pushes sensation events, receives intent commands."""
            await websocket.accept()
            self._sensation_subscribers.append(websocket)
            logger.info("Consciousness connected via WebSocket")

            try:
                while True:
                    # Receive intent commands
                    data = await websocket.receive_text()
                    try:
                        msg = json.loads(data)
                        if msg.get("type") == "intent":
                            intent = Intent.from_dict(msg)
                            await self._intent_queue.put(intent)
                            logger.info("Received intent: %s", intent.action)
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning("Invalid message: %s", e)
                        await websocket.send_json({"error": str(e)})
            except WebSocketDisconnect:
                logger.info("Consciousness disconnected")
            finally:
                self._sensation_subscribers.remove(websocket)

        @app.post("/intent")
        async def post_intent(data: dict):
            """HTTP fallback for sending intent."""
            intent = Intent.from_dict(data)
            await self._intent_queue.put(intent)
            return {"status": "accepted", "action": intent.action}

        return app

    async def broadcast_sensation(self, sensation: Sensation) -> None:
        """Push sensation to all connected WebSocket clients."""
        self._latest_sensation = sensation
        if not self._sensation_subscribers:
            return

        msg = json.dumps(sensation.to_dict())
        disconnected = []
        for ws in self._sensation_subscribers:
            try:
                await ws.send_text(msg)
            except Exception:
                disconnected.append(ws)

        for ws in disconnected:
            self._sensation_subscribers.remove(ws)

    async def get_pending_intent(self) -> Intent | None:
        """Non-blocking check for pending intent."""
        try:
            return self._intent_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def process_intents(self) -> None:
        """Process intent queue — called from the main loop."""
        while True:
            intent = await self.get_pending_intent()
            if intent is None:
                break

            if self._control_loop is not None:
                from soma.mind.intent import translate_intent
                result = translate_intent(intent, self._control_loop.motor_state)
                self._control_loop.submit_commands(result.motor_commands)

                # Apply thermal commands
                for tc in result.thermal_commands:
                    await self._control_loop.backend.write_thermal(
                        tc.zone, tc.target_celsius
                    )
