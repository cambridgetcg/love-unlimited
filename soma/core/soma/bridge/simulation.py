"""SimBackend implementing Backend protocol. Wraps MuJoCo."""

from __future__ import annotations

import time

import numpy as np

from soma.api.events import MotorCommand, MotorState, SensorState
from soma.sim.hand_model import HandModel


class SimBackend:
    """MuJoCo simulation backend — same API as HardwareBackend."""

    def __init__(self, model_path: str | None = None) -> None:
        self.hand = HandModel(model_path)
        self._thermal_targets: dict[int, float] = {}
        self._simulated_temps = np.full(10, 33.0)  # start at 33°C
        self._stopped = False

    async def read_motors(self) -> MotorState:
        positions = self.hand.get_joint_positions()
        velocities = self.hand.get_joint_velocities()
        # Convert actuator forces to approximate current in mA
        # XL330: ~1.2 mA per mN·m, force here is in N·m
        forces = self.hand.get_actuator_forces()
        currents = np.abs(forces) * 1000.0  # rough mA approximation
        return MotorState(
            positions=positions,
            velocities=velocities,
            currents=currents,
        )

    async def write_motors(self, commands: list[MotorCommand]) -> None:
        if self._stopped:
            return
        for cmd in commands:
            self.hand.set_actuator_ctrl(cmd.motor_id, cmd.position)

    async def read_sensors(self) -> SensorState:
        # Generate synthetic tactile data from MuJoCo contact forces
        tactile = np.zeros((5, 16, 3))
        contacts = self.hand.get_fingertip_contacts()

        finger_to_sensor = {"index": 0, "middle": 1, "ring": 2, "thumb": 3}
        for finger, force in contacts.items():
            if finger in finger_to_sensor:
                sensor_idx = finger_to_sensor[finger]
                if force > 0:
                    # Spread contact force across central taxels
                    for taxel in range(4, 12):
                        tactile[sensor_idx, taxel, 2] = force / 8.0  # normal force
                        tactile[sensor_idx, taxel, 0] = force / 16.0  # small shear

        # Simulate thermal dynamics
        for zone, target in self._thermal_targets.items():
            zone_temps = self._get_zone_temp_indices(zone)
            for idx in zone_temps:
                if idx < len(self._simulated_temps):
                    current = self._simulated_temps[idx]
                    # Simple first-order thermal response
                    self._simulated_temps[idx] = current + (target - current) * 0.01

        # IMU: hand is mounted on desk, gravity points down
        imu_quat = np.array([1.0, 0.0, 0.0, 0.0])
        imu_accel = np.array([0.0, 0.0, 9.81])

        return SensorState(
            tactile=tactile,
            temperatures=self._simulated_temps.copy(),
            imu_quaternion=imu_quat,
            imu_acceleration=imu_accel,
            timestamp=time.time(),
        )

    async def write_thermal(self, zone: int, target_c: float) -> None:
        self._thermal_targets[zone] = target_c

    async def emergency_stop(self) -> None:
        self._stopped = True
        # Set all actuators to current position (no movement)
        for motor_id in range(16):
            positions = self.hand.get_joint_positions()
            self.hand.set_actuator_ctrl(motor_id, float(positions[motor_id]))

    def step_simulation(self, n_steps: int = 1) -> None:
        """Step MuJoCo forward. Called by the control loop."""
        for _ in range(n_steps):
            self.hand.step()

    def reset(self) -> None:
        """Reset simulation state."""
        self.hand.reset()
        self._stopped = False
        self._thermal_targets.clear()
        self._simulated_temps = np.full(10, 33.0)

    @staticmethod
    def _get_zone_temp_indices(zone: int) -> list[int]:
        """Map thermal zone to temperature sensor indices."""
        match zone:
            case 0:
                return [0]           # thumb
            case 1:
                return [1, 2]        # index + middle
            case 2:
                return [3, 4]        # ring + pinky
            case 3:
                return [5, 6, 7]     # palm
            case 4:
                return [8, 9]        # dorsum
            case _:
                return []
