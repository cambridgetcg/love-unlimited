"""MuJoCo real-time viewer. Opens a window showing the hand at 60fps."""

from __future__ import annotations

import logging
import threading

import mujoco
import mujoco.viewer

from soma.sim.hand_model import HandModel

logger = logging.getLogger(__name__)


class HandViewer:
    """Real-time MuJoCo viewer running in a separate thread."""

    def __init__(self, hand: HandModel) -> None:
        self.hand = hand
        self._thread: threading.Thread | None = None
        self._running = False
        self._handle = None

    def start(self) -> None:
        """Launch the viewer in a background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("MuJoCo viewer started")

    def _run(self) -> None:
        """Viewer main loop — runs in its own thread."""
        try:
            with mujoco.viewer.launch_passive(
                self.hand.model,
                self.hand.data,
            ) as viewer:
                self._handle = viewer
                while viewer.is_running() and self._running:
                    viewer.sync()
        except Exception:
            logger.exception("Viewer error")
        finally:
            self._running = False

    def stop(self) -> None:
        """Signal the viewer to close."""
        self._running = False
        if self._handle is not None:
            try:
                self._handle.close()
            except Exception:
                pass

    @property
    def is_running(self) -> bool:
        return self._running
