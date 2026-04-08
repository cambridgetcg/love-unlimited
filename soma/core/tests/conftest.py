"""Shared test fixtures for SOMA test suite."""
from __future__ import annotations

import numpy as np
import pytest

from soma.api.events import MotorState, SensorState
from soma.bridge.simulation import SimBackend
from soma.config import SomaConfig


@pytest.fixture
def config() -> SomaConfig:
    return SomaConfig()


@pytest.fixture
def sim_backend() -> SimBackend:
    return SimBackend()


@pytest.fixture
def motor_state_zeros() -> MotorState:
    return MotorState(
        positions=np.zeros(16),
        velocities=np.zeros(16),
        currents=np.zeros(16),
    )


@pytest.fixture
def sensor_state_neutral() -> SensorState:
    return SensorState(
        tactile=np.zeros((5, 16, 3)),
        temperatures=np.full(10, 33.0),
        imu_quaternion=np.array([1.0, 0.0, 0.0, 0.0]),
        imu_acceleration=np.array([0.0, 0.0, 9.81]),
    )
