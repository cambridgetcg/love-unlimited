"""Tests for data type creation and serialization."""

import numpy as np

from soma.api.events import (
    Intent,
    MotorCommand,
    MotorState,
    Sensation,
    SensorState,
    ThermalCommand,
)


def test_motor_state_creation():
    state = MotorState(
        positions=np.zeros(16),
        velocities=np.zeros(16),
        currents=np.zeros(16),
    )
    assert state.positions.shape == (16,)
    d = state.to_dict()
    assert len(d["positions"]) == 16


def test_sensor_state_creation():
    state = SensorState(
        tactile=np.zeros((5, 16, 3)),
        temperatures=np.full(10, 33.0),
        imu_quaternion=np.array([1.0, 0.0, 0.0, 0.0]),
        imu_acceleration=np.array([0.0, 0.0, 9.81]),
    )
    assert state.tactile.shape == (5, 16, 3)
    assert state.temperatures.shape == (10,)
    d = state.to_dict()
    assert len(d["temperatures"]) == 10


def test_motor_command():
    cmd = MotorCommand(motor_id=5, position=1.0, max_current=200.0)
    assert cmd.motor_id == 5
    assert cmd.position == 1.0
    assert cmd.max_current == 200.0


def test_thermal_command():
    cmd = ThermalCommand(zone=3, target_celsius=34.0)
    assert cmd.zone == 3
    assert cmd.target_celsius == 34.0


def test_intent_serialization():
    intent = Intent(action="hold_gentle", params={"stiffness": 0.3})
    d = intent.to_dict()
    assert d["action"] == "hold_gentle"
    assert d["params"]["stiffness"] == 0.3

    # Round-trip
    restored = Intent.from_dict(d)
    assert restored.action == intent.action
    assert restored.params == intent.params


def test_intent_from_dict_no_params():
    intent = Intent.from_dict({"action": "release"})
    assert intent.action == "release"
    assert intent.params == {}


def test_sensation_creation():
    s = Sensation(
        fingers_active=[0, 1],
        pressures=[0.3, 0.5],
        gesture="holding",
        skin_temperature=33.2,
        contact_temperature=35.8,
    )
    d = s.to_dict()
    assert d["type"] == "sensation"
    assert d["touch"]["active"] is True
    assert d["touch"]["gesture"] == "holding"


def test_sensation_empty():
    s = Sensation.empty()
    assert s.fingers_active == []
    assert s.gesture == "none"
    d = s.to_dict()
    assert d["touch"]["active"] is False
