"""Tests for sensation processing — contact detection from data."""

import numpy as np

from soma.api.events import SensorState
from soma.mind.sensation import process_sensation


def _make_sensor_state(
    tactile: np.ndarray | None = None,
    temperatures: np.ndarray | None = None,
) -> SensorState:
    if tactile is None:
        tactile = np.zeros((5, 16, 3))
    if temperatures is None:
        temperatures = np.full(10, 33.0)
    return SensorState(
        tactile=tactile,
        temperatures=temperatures,
        imu_quaternion=np.array([1.0, 0.0, 0.0, 0.0]),
        imu_acceleration=np.array([0.0, 0.0, 9.81]),
    )


def test_no_contact():
    state = _make_sensor_state()
    sensation = process_sensation(state)
    assert sensation.fingers_active == []
    assert sensation.gesture == "none"


def test_single_finger_contact():
    tactile = np.zeros((5, 16, 3))
    # Apply force to index finger (sensor 1), normal axis (z=2)
    tactile[1, 8, 2] = 1.0
    state = _make_sensor_state(tactile=tactile)
    sensation = process_sensation(state)
    assert 1 in sensation.fingers_active
    assert len(sensation.pressures) > 0
    assert sensation.pressures[0] > 0


def test_multi_finger_holding():
    tactile = np.zeros((5, 16, 3))
    # Apply moderate force to thumb, index, middle
    for sensor_idx in [0, 1, 2]:
        for taxel in range(4, 12):
            tactile[sensor_idx, taxel, 2] = 0.5
    state = _make_sensor_state(tactile=tactile)
    sensation = process_sensation(state)
    assert len(sensation.fingers_active) >= 3
    assert sensation.gesture == "holding"


def test_pressing_gesture():
    tactile = np.zeros((5, 16, 3))
    # High force on one finger
    for taxel in range(16):
        tactile[0, taxel, 2] = 2.0
    state = _make_sensor_state(tactile=tactile)
    sensation = process_sensation(state)
    assert sensation.gesture == "pressing"


def test_temperature_reading():
    temps = np.array([34.0, 34.5, 33.5, 33.0, 33.0, 33.0, 33.0, 33.0, 33.0, 33.0])
    state = _make_sensor_state(temperatures=temps)
    sensation = process_sensation(state)
    assert 33.0 <= sensation.skin_temperature <= 35.0


def test_contact_temperature():
    tactile = np.zeros((5, 16, 3))
    tactile[0, 8, 2] = 1.0  # thumb contact
    temps = np.array([36.0, 33.0, 33.0, 33.0, 33.0, 33.0, 33.0, 33.0, 33.0, 33.0])
    state = _make_sensor_state(tactile=tactile, temperatures=temps)
    sensation = process_sensation(state)
    assert sensation.contact_temperature > 0
