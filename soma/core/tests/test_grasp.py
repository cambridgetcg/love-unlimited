"""Tests for grasp controllers — position control reaches target in sim."""

import pytest
import numpy as np

from soma.api.events import MotorCommand
from soma.bridge.simulation import SimBackend
from soma.mind.grasp import ForceController, ImpedanceController, PositionController


@pytest.fixture
def backend():
    return SimBackend()


def test_position_controller_generates_commands():
    pc = PositionController(max_current=300)
    targets = {0: 0.5, 1: 1.0, 4: 0.3}
    commands = pc.compute(targets)
    assert len(commands) == 3
    assert all(isinstance(c, MotorCommand) for c in commands)
    assert commands[0].position == 0.5
    assert commands[0].max_current == 300


@pytest.mark.asyncio
async def test_position_control_reaches_target(backend):
    """Position controller should move joints toward target in simulation."""
    pc = PositionController()
    target = 0.5
    targets = {1: target}  # index MCP flex

    # Apply commands and step
    commands = pc.compute(targets)
    await backend.write_motors(commands)
    for _ in range(200):
        backend.step_simulation(5)

    state = await backend.read_motors()
    # Should be close to target (within 0.15 rad)
    assert abs(state.positions[1] - target) < 0.15, (
        f"Position {state.positions[1]:.3f} not close to target {target}"
    )


def test_impedance_controller():
    ic = ImpedanceController(stiffness=0.5, damping=0.2)
    state = _dummy_motor_state()
    targets = {0: 1.0, 1: 1.0}
    commands = ic.compute(targets, state)
    assert len(commands) == 2
    # Lower stiffness → lower max current
    assert commands[0].max_current < 350.0
    assert commands[0].max_current >= 50.0


def test_impedance_stiffness_scales_current():
    low = ImpedanceController(stiffness=0.2)
    high = ImpedanceController(stiffness=0.8)
    state = _dummy_motor_state()
    targets = {0: 1.0}
    cmd_low = low.compute(targets, state)
    cmd_high = high.compute(targets, state)
    assert cmd_low[0].max_current < cmd_high[0].max_current


def test_force_controller():
    fc = ForceController(target_force_ma=100, gain=0.01)
    state = _dummy_motor_state()
    targets = {0: 0.5}
    commands = fc.compute(targets, state)
    assert len(commands) == 1
    # First iteration with 0 current should add positive offset
    assert commands[0].position > 0.5


def test_force_controller_reset():
    fc = ForceController()
    state = _dummy_motor_state()
    fc.compute({0: 0.5}, state)
    assert len(fc._position_offsets) > 0
    fc.reset()
    assert len(fc._position_offsets) == 0


def _dummy_motor_state():
    return __import__("soma.api.events", fromlist=["MotorState"]).MotorState(
        positions=np.zeros(16),
        velocities=np.zeros(16),
        currents=np.zeros(16),
    )
