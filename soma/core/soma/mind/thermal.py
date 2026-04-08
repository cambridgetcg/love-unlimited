"""Emotional-to-thermal mapping."""

from __future__ import annotations

import math
import time

from soma.api.events import ThermalCommand

# Emotional state → thermal target
THERMAL_MAP: dict[str, float] = {
    "present": 33.0,   # normal warm
    "warm": 34.0,      # happy, connected
    "cool": 31.0,      # withdrawing
}

# Pulse parameters
PULSE_MIN = 32.0
PULSE_MAX = 34.0
PULSE_PERIOD_S = 4.0  # seconds per cycle


def emotional_thermal(warmth: str, num_zones: int = 5) -> list[ThermalCommand]:
    """Map an emotional warmth label to thermal commands for all zones."""
    if warmth == "pulse":
        # Oscillate between min and max using sine wave
        phase = (time.time() % PULSE_PERIOD_S) / PULSE_PERIOD_S
        target = PULSE_MIN + (PULSE_MAX - PULSE_MIN) * (0.5 + 0.5 * math.sin(2 * math.pi * phase))
    else:
        target = THERMAL_MAP.get(warmth, 33.0)

    return [ThermalCommand(zone=z, target_celsius=target) for z in range(num_zones)]
