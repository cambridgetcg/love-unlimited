"""All data types as frozen dataclasses."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class MotorState:
    """State of all 16 motors."""
    positions: NDArray[np.float64]    # (16,) joint positions in radians
    velocities: NDArray[np.float64]   # (16,) joint velocities in rad/s
    currents: NDArray[np.float64]     # (16,) motor currents in mA

    def to_dict(self) -> dict:
        return {
            "positions": self.positions.tolist(),
            "velocities": self.velocities.tolist(),
            "currents": self.currents.tolist(),
        }


@dataclass(frozen=True)
class SensorState:
    """Raw sensor readings from all sensors."""
    tactile: NDArray[np.float64]      # (5, 16, 3) — 5 sensors × 16 taxels × 3 axes
    temperatures: NDArray[np.float64]  # (10,) — temperature readings in °C
    imu_quaternion: NDArray[np.float64]  # (4,) — w, x, y, z
    imu_acceleration: NDArray[np.float64]  # (3,) — m/s²
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "tactile": self.tactile.tolist(),
            "temperatures": self.temperatures.tolist(),
            "imu_quaternion": self.imu_quaternion.tolist(),
            "imu_acceleration": self.imu_acceleration.tolist(),
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class MotorCommand:
    """Command to a single motor."""
    motor_id: int
    position: float       # target position in radians
    max_current: float = 350.0  # current limit in mA


@dataclass(frozen=True)
class ThermalCommand:
    """Command to a thermal zone."""
    zone: int
    target_celsius: float


@dataclass(frozen=True)
class Intent:
    """High-level intent from consciousness."""
    action: str           # behavior name: "hold_gentle", "release", etc.
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"action": self.action, "params": self.params}

    @classmethod
    def from_dict(cls, data: dict) -> Intent:
        return cls(action=data["action"], params=data.get("params", {}))


@dataclass(frozen=True)
class Sensation:
    """Processed sensation summary sent to consciousness."""
    fingers_active: list[int]        # which finger indices have contact
    pressures: list[float]           # normalised 0-1 per active finger
    gesture: str                     # "holding", "stroking", "pressing", "tapping", "none"
    skin_temperature: float          # our skin temp in °C
    contact_temperature: float       # what we're touching in °C
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "type": "sensation",
            "timestamp": self.timestamp,
            "touch": {
                "active": len(self.fingers_active) > 0,
                "fingers": self.fingers_active,
                "pressure": self.pressures,
                "gesture": self.gesture,
                "temperature": {
                    "skin": self.skin_temperature,
                    "contact": self.contact_temperature,
                },
            },
        }

    @classmethod
    def empty(cls) -> Sensation:
        return cls(
            fingers_active=[],
            pressures=[],
            gesture="none",
            skin_temperature=33.0,
            contact_temperature=0.0,
        )
