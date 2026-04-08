"""Safety module. Current, temperature, velocity limits. Watchdog. Runs every tick."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from soma.api.events import MotorCommand, MotorState, SensorState
from soma.config import SafetyConfig

logger = logging.getLogger(__name__)


@dataclass
class SafetyViolation:
    """Record of a safety limit being hit."""
    category: str  # "current", "temperature", "velocity", "watchdog"
    motor_id: int | None
    value: float
    limit: float
    message: str


@dataclass
class SafetyMonitor:
    """Enforces safety limits every control loop tick."""

    config: SafetyConfig = field(default_factory=SafetyConfig)
    _last_heartbeat: float = field(default_factory=time.time)
    _violations: list[SafetyViolation] = field(default_factory=list)
    _emergency: bool = False

    def heartbeat(self) -> None:
        """Call every tick to prevent watchdog timeout."""
        self._last_heartbeat = time.time()

    def check_all(
        self,
        motor_state: MotorState,
        sensor_state: SensorState,
    ) -> list[SafetyViolation]:
        """Run all safety checks. Returns list of violations."""
        violations: list[SafetyViolation] = []
        violations.extend(self.check_currents(motor_state))
        violations.extend(self.check_velocities(motor_state))
        violations.extend(self.check_temperatures(sensor_state))
        violations.extend(self.check_watchdog())
        self._violations = violations
        return violations

    def check_currents(self, motor_state: MotorState) -> list[SafetyViolation]:
        """Check motor currents against limit (350mA)."""
        violations = []
        for i, current in enumerate(motor_state.currents):
            if abs(current) > self.config.current_limit_ma:
                v = SafetyViolation(
                    category="current",
                    motor_id=i,
                    value=float(current),
                    limit=self.config.current_limit_ma,
                    message=f"Motor {i} current {current:.0f}mA exceeds limit {self.config.current_limit_ma}mA",
                )
                violations.append(v)
                logger.warning(v.message)
        return violations

    def check_velocities(self, motor_state: MotorState) -> list[SafetyViolation]:
        """Check joint velocities against limit (2 rad/s)."""
        violations = []
        for i, vel in enumerate(motor_state.velocities):
            if abs(vel) > self.config.velocity_limit_rad_s:
                v = SafetyViolation(
                    category="velocity",
                    motor_id=i,
                    value=float(vel),
                    limit=self.config.velocity_limit_rad_s,
                    message=f"Motor {i} velocity {vel:.2f} rad/s exceeds limit {self.config.velocity_limit_rad_s} rad/s",
                )
                violations.append(v)
                logger.warning(v.message)
        return violations

    def check_temperatures(self, sensor_state: SensorState) -> list[SafetyViolation]:
        """Check temperatures against hard max (40°C)."""
        violations = []
        for i, temp in enumerate(sensor_state.temperatures):
            if temp > self.config.temperature_hard_max_c:
                v = SafetyViolation(
                    category="temperature",
                    motor_id=None,
                    value=float(temp),
                    limit=self.config.temperature_hard_max_c,
                    message=f"Temperature sensor {i} at {temp:.1f}°C exceeds hard max {self.config.temperature_hard_max_c}°C",
                )
                violations.append(v)
                logger.warning(v.message)
        return violations

    def check_watchdog(self) -> list[SafetyViolation]:
        """Check if heartbeat has timed out."""
        elapsed = time.time() - self._last_heartbeat
        if elapsed > self.config.watchdog_timeout_s:
            v = SafetyViolation(
                category="watchdog",
                motor_id=None,
                value=elapsed,
                limit=self.config.watchdog_timeout_s,
                message=f"Watchdog timeout: {elapsed:.1f}s since last heartbeat",
            )
            logger.error(v.message)
            self._emergency = True
            return [v]
        return []

    def clamp_commands(self, commands: list[MotorCommand]) -> list[MotorCommand]:
        """Clamp motor commands to safe values."""
        clamped = []
        for cmd in commands:
            max_current = min(cmd.max_current, self.config.current_limit_ma)
            clamped.append(MotorCommand(
                motor_id=cmd.motor_id,
                position=cmd.position,
                max_current=max_current,
            ))
        return clamped

    @property
    def is_emergency(self) -> bool:
        return self._emergency

    def clear_emergency(self) -> None:
        self._emergency = False
        self._last_heartbeat = time.time()

    @property
    def violations(self) -> list[SafetyViolation]:
        return self._violations
