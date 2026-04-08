"""100Hz async control loop. Takes a Backend + callbacks."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any

from soma.api.events import MotorCommand, MotorState, Sensation, SensorState
from soma.bridge.backend import Backend
from soma.bridge.safety import SafetyMonitor
from soma.bridge.simulation import SimBackend
from soma.config import SomaConfig

logger = logging.getLogger(__name__)

SensationCallback = Callable[[Sensation], Coroutine[Any, Any, None]]


class ControlLoop:
    """100Hz async control loop. Reads sensors, executes commands, enforces safety."""

    def __init__(
        self,
        backend: Backend,
        config: SomaConfig | None = None,
        on_sensation: SensationCallback | None = None,
    ) -> None:
        self.backend = backend
        self.config = config or SomaConfig()
        self.on_sensation = on_sensation
        self.safety = SafetyMonitor(self.config.safety)

        self._pending_commands: list[MotorCommand] = []
        self._running = False
        self._tick_count = 0
        self._last_motor_state: MotorState | None = None
        self._last_sensor_state: SensorState | None = None

        # Timing stats
        self._tick_times: list[float] = []

    @property
    def motor_state(self) -> MotorState | None:
        return self._last_motor_state

    @property
    def sensor_state(self) -> SensorState | None:
        return self._last_sensor_state

    def submit_commands(self, commands: list[MotorCommand]) -> None:
        """Queue motor commands for next tick."""
        self._pending_commands.extend(commands)

    async def start(self) -> None:
        """Start the control loop."""
        self._running = True
        self.safety.heartbeat()
        logger.info("Control loop starting at %d Hz", self.config.loop_rate_hz)

        tick_interval = 1.0 / self.config.loop_rate_hz

        while self._running:
            tick_start = time.monotonic()

            try:
                await self._tick()
            except Exception:
                logger.exception("Error in control loop tick %d", self._tick_count)

            # Step simulation physics if using SimBackend
            if isinstance(self.backend, SimBackend):
                # Step physics multiple times per control tick for stability
                # MuJoCo timestep is 2ms, control loop is 10ms → 5 physics steps
                steps = max(1, int(tick_interval / self.backend.hand.model.opt.timestep))
                self.backend.step_simulation(steps)

            self._tick_count += 1

            # Sleep for remainder of tick
            elapsed = time.monotonic() - tick_start
            self._tick_times.append(elapsed)
            if len(self._tick_times) > 100:
                self._tick_times.pop(0)

            sleep_time = tick_interval - elapsed
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    async def _tick(self) -> None:
        """Single control loop tick."""
        # 1. Read motor state
        motor_state = await self.backend.read_motors()
        self._last_motor_state = motor_state

        # 2. Read sensor state
        sensor_state = await self.backend.read_sensors()
        self._last_sensor_state = sensor_state

        # 3. Safety checks
        self.safety.heartbeat()
        self.safety.check_all(motor_state, sensor_state)

        if self.safety.is_emergency:
            logger.error("EMERGENCY STOP triggered")
            await self.backend.emergency_stop()
            self._pending_commands.clear()
            return

        # 4. Execute pending commands (clamped for safety)
        if self._pending_commands:
            safe_commands = self.safety.clamp_commands(self._pending_commands)
            await self.backend.write_motors(safe_commands)
            self._pending_commands.clear()

        # 5. Call sensation callback
        if self.on_sensation is not None:
            from soma.mind.sensation import process_sensation
            sensation = process_sensation(sensor_state)
            await self.on_sensation(sensation)

    def stop(self) -> None:
        """Signal the loop to stop."""
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def tick_count(self) -> int:
        return self._tick_count

    @property
    def avg_tick_ms(self) -> float:
        if not self._tick_times:
            return 0.0
        return sum(self._tick_times) / len(self._tick_times) * 1000.0
