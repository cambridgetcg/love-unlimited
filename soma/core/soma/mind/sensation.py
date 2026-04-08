"""Sensation processing. Raw SensorState → Sensation summary."""

from __future__ import annotations

import numpy as np

from soma.api.events import Sensation, SensorState

# Finger names mapped to tactile sensor indices
FINGER_SENSOR_MAP = {
    0: "thumb",
    1: "index",
    2: "middle",
    3: "ring",
    4: "pinky",
}

# Pressure threshold for contact detection (normalised)
CONTACT_THRESHOLD = 0.01

# Maximum expected force for normalisation
MAX_FORCE = 5.0


def process_sensation(sensor_state: SensorState) -> Sensation:
    """Convert raw sensor data into a felt sensation summary."""
    fingers_active: list[int] = []
    pressures: list[float] = []

    # Process tactile data per finger
    for sensor_idx in range(5):
        # Sum normal forces (z-axis) across all taxels for this finger
        finger_tactile = sensor_state.tactile[sensor_idx]  # (16, 3)
        normal_forces = finger_tactile[:, 2]  # z-axis = normal force
        total_force = float(np.sum(np.abs(normal_forces)))

        # Normalise to 0-1
        normalised = min(total_force / MAX_FORCE, 1.0)

        if normalised > CONTACT_THRESHOLD:
            fingers_active.append(sensor_idx)
            pressures.append(round(normalised, 3))

    # Detect gesture
    gesture = _detect_gesture(sensor_state, fingers_active, pressures)

    # Skin temperature: average of fingertip sensors (indices 0-4)
    skin_temp = float(np.mean(sensor_state.temperatures[:5]))

    # Contact temperature estimate: if touching something, infer from fingertip temps
    if fingers_active:
        active_temps = [sensor_state.temperatures[i] for i in fingers_active if i < 5]
        contact_temp = float(np.mean(active_temps)) if active_temps else 0.0
    else:
        contact_temp = 0.0

    return Sensation(
        fingers_active=fingers_active,
        pressures=pressures,
        gesture=gesture,
        skin_temperature=round(skin_temp, 1),
        contact_temperature=round(contact_temp, 1),
    )


def _detect_gesture(
    sensor_state: SensorState,
    fingers_active: list[int],
    pressures: list[float],
) -> str:
    """Detect gesture from contact pattern."""
    if not fingers_active:
        return "none"

    num_fingers = len(fingers_active)
    avg_pressure = sum(pressures) / len(pressures) if pressures else 0.0

    # Multiple fingers with sustained moderate pressure → holding
    if num_fingers >= 3 and avg_pressure > 0.05:
        return "holding"

    # High pressure on few fingers → pressing
    if avg_pressure > 0.3:
        return "pressing"

    # Light pressure → could be stroking or tapping
    if num_fingers <= 2 and avg_pressure < 0.15:
        return "tapping"

    # Default for moderate contact
    if num_fingers >= 2:
        return "holding"

    return "tapping"
