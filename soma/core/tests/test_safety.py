"""Tests for safety limit enforcement and watchdog."""

import time

import numpy as np

from soma.api.events import MotorCommand, MotorState, SensorState
from soma.bridge.safety import SafetyMonitor
from soma.config import SafetyConfig


def _make_motor_state(
    currents: list[float] | None = None,
    velocities: list[float] | None = None,
) -> MotorState:
    return MotorState(
        positions=np.zeros(16),
        velocities=np.array(velocities or [0.0] * 16),
        currents=np.array(currents or [0.0] * 16),
    )


def _make_sensor_state(temperatures: list[float] | None = None) -> SensorState:
    return SensorState(
        tactile=np.zeros((5, 16, 3)),
        temperatures=np.array(temperatures or [33.0] * 10),
        imu_quaternion=np.array([1.0, 0.0, 0.0, 0.0]),
        imu_acceleration=np.array([0.0, 0.0, 9.81]),
    )


def test_current_limit():
    safety = SafetyMonitor()
    # Motor 3 over current limit
    currents = [0.0] * 16
    currents[3] = 400.0
    motor_state = _make_motor_state(currents=currents)
    violations = safety.check_currents(motor_state)
    assert len(violations) == 1
    assert violations[0].category == "current"
    assert violations[0].motor_id == 3


def test_velocity_limit():
    safety = SafetyMonitor()
    vels = [0.0] * 16
    vels[7] = 3.0  # over 2 rad/s limit
    motor_state = _make_motor_state(velocities=vels)
    violations = safety.check_velocities(motor_state)
    assert len(violations) == 1
    assert violations[0].category == "velocity"
    assert violations[0].motor_id == 7


def test_temperature_limit():
    safety = SafetyMonitor()
    temps = [33.0] * 10
    temps[2] = 41.0  # over 40°C hard max
    sensor_state = _make_sensor_state(temperatures=temps)
    violations = safety.check_temperatures(sensor_state)
    assert len(violations) == 1
    assert violations[0].category == "temperature"


def test_no_violations():
    safety = SafetyMonitor()
    motor_state = _make_motor_state()
    sensor_state = _make_sensor_state()
    violations = safety.check_all(motor_state, sensor_state)
    assert len(violations) == 0


def test_watchdog_timeout():
    config = SafetyConfig(watchdog_timeout_s=0.01)  # very short
    safety = SafetyMonitor(config=config)
    time.sleep(0.02)  # let it expire
    violations = safety.check_watchdog()
    assert len(violations) == 1
    assert violations[0].category == "watchdog"
    assert safety.is_emergency


def test_watchdog_heartbeat():
    config = SafetyConfig(watchdog_timeout_s=0.1)
    safety = SafetyMonitor(config=config)
    safety.heartbeat()
    violations = safety.check_watchdog()
    assert len(violations) == 0
    assert not safety.is_emergency


def test_clamp_commands():
    safety = SafetyMonitor()
    commands = [
        MotorCommand(motor_id=0, position=1.0, max_current=500.0),
        MotorCommand(motor_id=1, position=0.5, max_current=200.0),
    ]
    clamped = safety.clamp_commands(commands)
    assert clamped[0].max_current == 350.0  # clamped
    assert clamped[1].max_current == 200.0  # unchanged


def test_clear_emergency():
    config = SafetyConfig(watchdog_timeout_s=0.01)
    safety = SafetyMonitor(config=config)
    time.sleep(0.02)
    safety.check_watchdog()
    assert safety.is_emergency
    safety.clear_emergency()
    assert not safety.is_emergency
