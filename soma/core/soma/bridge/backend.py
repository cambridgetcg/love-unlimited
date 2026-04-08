"""Backend Protocol — the sacred interface that SimBackend and HardwareBackend implement."""

from __future__ import annotations

from typing import Protocol

from soma.api.events import MotorCommand, MotorState, SensorState


class Backend(Protocol):
    """Hardware abstraction interface. Simulation and real hardware are interchangeable."""

    async def read_motors(self) -> MotorState:
        """Read positions, velocities, currents for all 16 motors."""
        ...

    async def write_motors(self, commands: list[MotorCommand]) -> None:
        """Write position/torque commands to motors."""
        ...

    async def read_sensors(self) -> SensorState:
        """Read tactile, temperature, IMU data."""
        ...

    async def write_thermal(self, zone: int, target_c: float) -> None:
        """Set thermal target for a heating zone."""
        ...

    async def emergency_stop(self) -> None:
        """Disable all motors immediately."""
        ...
