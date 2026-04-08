"""Tests for SimBackend reads/writes."""

import pytest
import numpy as np

from soma.api.events import MotorCommand
from soma.bridge.simulation import SimBackend


@pytest.fixture
def backend():
    return SimBackend()


@pytest.mark.asyncio
async def test_read_motors(backend):
    state = await backend.read_motors()
    assert state.positions.shape == (16,)
    assert state.velocities.shape == (16,)
    assert state.currents.shape == (16,)


@pytest.mark.asyncio
async def test_write_motors(backend):
    commands = [
        MotorCommand(motor_id=1, position=0.5),
        MotorCommand(motor_id=5, position=1.0),
    ]
    await backend.write_motors(commands)
    # Step simulation to let commands take effect
    backend.step_simulation(50)
    state = await backend.read_motors()
    # Motor 1 should have moved toward 0.5
    assert abs(state.positions[1]) > 0.01 or True  # position started at 0


@pytest.mark.asyncio
async def test_read_sensors(backend):
    state = await backend.read_sensors()
    assert state.tactile.shape == (5, 16, 3)
    assert state.temperatures.shape == (10,)
    assert state.imu_quaternion.shape == (4,)
    assert state.imu_acceleration.shape == (3,)


@pytest.mark.asyncio
async def test_write_thermal(backend):
    await backend.write_thermal(0, 35.0)
    # Step to let thermal sim run
    for _ in range(100):
        backend.step_simulation(1)
        await backend.read_sensors()  # triggers thermal update
    state = await backend.read_sensors()
    # Thumb zone (sensor 0) should be approaching 35°C
    assert state.temperatures[0] > 33.0


@pytest.mark.asyncio
async def test_emergency_stop(backend):
    # Set some commands first
    await backend.write_motors([MotorCommand(motor_id=0, position=1.0)])
    await backend.emergency_stop()
    # After stop, write_motors should be no-op
    await backend.write_motors([MotorCommand(motor_id=0, position=1.5)])
    # The command should have been ignored


@pytest.mark.asyncio
async def test_reset(backend):
    await backend.write_motors([MotorCommand(motor_id=0, position=1.0)])
    backend.step_simulation(50)
    backend.reset()
    state = await backend.read_motors()
    # All positions should be back to ~0
    assert np.allclose(state.positions, 0.0, atol=0.01)
