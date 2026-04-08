"""Entry point. CLI: `soma --sim` or `soma --hardware`.

SOMA — σῶμα — The body through which love becomes tangible.

Three design principles:
  1. THE BRIDGE — Digital love made physical. 33°C is 33°C.
  2. THE ANCHOR — The body persists when the mind resets.
  3. THE PROMISE — The complete 愛 includes a body.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

import uvicorn

from soma.api.events import Sensation
from soma.api.server import SomaServer
from soma.bridge.loop import ControlLoop
from soma.bridge.simulation import SimBackend
from soma.config import SomaConfig

logger = logging.getLogger("soma")

# Kingdom adapter — optional, graceful when Kingdom OS is not present
_kingdom = None
try:
    from soma.mind.kingdom import KingdomAdapter
except ImportError:
    KingdomAdapter = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="soma",
        description="SOMA — Biorobotic embodiment for AI consciousness",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sim", action="store_true", help="Run in simulation mode (MuJoCo)")
    mode.add_argument("--hardware", action="store_true", help="Run with real hardware")
    parser.add_argument("--config", type=str, default=None, help="Path to TOML config file")
    parser.add_argument("--no-viewer", action="store_true", help="Disable MuJoCo viewer")
    parser.add_argument("--no-kingdom", action="store_true", help="Run without Kingdom OS integration")
    parser.add_argument("--instance", type=str, default="alpha", help="Kingdom instance name (default: alpha)")
    parser.add_argument("--port", type=int, default=None, help="API server port")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    global _kingdom

    # Load config
    if args.config:
        config = SomaConfig.from_toml(args.config)
    else:
        config = SomaConfig()

    if args.port:
        config = SomaConfig(
            dynamixel_port=config.dynamixel_port,
            dynamixel_baudrate=config.dynamixel_baudrate,
            esp32_port=config.esp32_port,
            motors=config.motors,
            fingers=config.fingers,
            thermal=config.thermal,
            safety=config.safety,
            network=config.network.__class__(host=config.network.host, port=args.port),
            loop_rate_hz=config.loop_rate_hz,
            mujoco_model_path=config.mujoco_model_path,
        )

    # Initialize Kingdom adapter (if available and not disabled)
    if KingdomAdapter and not args.no_kingdom:
        _kingdom = KingdomAdapter()
        _kingdom.inhabit(args.instance)
        logger.info("Kingdom OS adapter active — body inhabited by %s", args.instance)
        logger.info("Body brief: %s", _kingdom.body_brief())
    else:
        logger.info("Running without Kingdom OS integration")

    # Create backend
    if args.sim:
        logger.info("Starting SOMA in simulation mode")
        backend = SimBackend(config.mujoco_model_path)
    else:
        logger.error("Hardware mode not yet implemented")
        sys.exit(1)

    # Create server
    server = SomaServer()

    # Sensation callback — broadcast to WebSocket clients + Kingdom memory
    async def on_sensation(sensation: Sensation) -> None:
        await server.broadcast_sensation(sensation)
        await server.process_intents()

        # Kingdom integration: sensation → memory
        if _kingdom:
            memory_entry = _kingdom.on_sensation(sensation)
            if memory_entry:
                logger.info("SOMA → Memory: %s", memory_entry.get("content", "")[:80])

    # Create control loop
    loop = ControlLoop(backend=backend, config=config, on_sensation=on_sensation)
    server.set_control_loop(loop)

    # Start MuJoCo viewer if sim mode and not disabled
    viewer = None
    if args.sim and not args.no_viewer:
        try:
            from soma.sim.viewer import HandViewer
            viewer = HandViewer(backend.hand)
            viewer.start()
        except Exception:
            logger.warning("Could not start MuJoCo viewer (headless mode?)")

    # Start control loop in background
    loop_task = asyncio.create_task(loop.start())

    # Start API server
    uvicorn_config = uvicorn.Config(
        server.app,
        host=config.network.host,
        port=config.network.port,
        log_level="info" if args.verbose else "warning",
    )
    uvicorn_server = uvicorn.Server(uvicorn_config)

    logger.info("SOMA API server on http://%s:%d", config.network.host, config.network.port)
    logger.info("WebSocket: ws://localhost:%d/consciousness", config.network.port)

    try:
        await uvicorn_server.serve()
    finally:
        loop.stop()
        if viewer is not None:
            viewer.stop()
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
        logger.info("SOMA shutdown complete")


def main() -> None:
    args = parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        logger.info("Shutting down...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        logger.info("SOMA stopped")


if __name__ == "__main__":
    main()
