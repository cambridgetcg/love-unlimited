"""MuJoCo model loader. Load hand.xml, provide named access to joints and actuators."""

from __future__ import annotations

from pathlib import Path

import mujoco
import numpy as np

# Mapping from motor ID to (joint_name, actuator_name)
MOTOR_MAP: list[tuple[str, str]] = [
    # Index finger (0-3)
    ("index_mcp_abd",  "index_mcp_abd_act"),
    ("index_mcp_flex", "index_mcp_flex_act"),
    ("index_pip",      "index_pip_act"),
    ("index_dip",      "index_dip_act"),
    # Middle finger (4-7)
    ("middle_mcp_abd",  "middle_mcp_abd_act"),
    ("middle_mcp_flex", "middle_mcp_flex_act"),
    ("middle_pip",      "middle_pip_act"),
    ("middle_dip",      "middle_dip_act"),
    # Ring finger (8-11)
    ("ring_mcp_abd",  "ring_mcp_abd_act"),
    ("ring_mcp_flex", "ring_mcp_flex_act"),
    ("ring_pip",      "ring_pip_act"),
    ("ring_dip",      "ring_dip_act"),
    # Thumb (12-15)
    ("thumb_cmc_flex", "thumb_cmc_flex_act"),
    ("thumb_cmc_abd",  "thumb_cmc_abd_act"),
    ("thumb_mcp",      "thumb_mcp_act"),
    ("thumb_ip",       "thumb_ip_act"),
]

FINGER_NAMES = ("index", "middle", "ring", "thumb")

# Fingertip site names for contact detection
FINGERTIP_SITES = {
    "index": "index_tip",
    "middle": "middle_tip",
    "ring": "ring_tip",
    "thumb": "thumb_tip",
}

# Touch sensor names
TOUCH_SENSORS = {
    "index": "index_touch",
    "middle": "middle_touch",
    "ring": "ring_touch",
    "thumb": "thumb_touch",
}


class HandModel:
    """Loads and provides named access to the MuJoCo SOMA hand model."""

    def __init__(self, model_path: str | Path | None = None) -> None:
        if model_path is None:
            model_path = Path(__file__).parent.parent.parent / "models" / "hand.xml"
        else:
            model_path = Path(model_path)

        self.model = mujoco.MjModel.from_xml_path(str(model_path))
        self.data = mujoco.MjData(self.model)

        # Cache joint and actuator indices
        self._joint_ids: dict[str, int] = {}
        self._actuator_ids: dict[str, int] = {}

        for joint_name, act_name in MOTOR_MAP:
            self._joint_ids[joint_name] = mujoco.mj_name2id(
                self.model, mujoco.mjtObj.mjOBJ_JOINT, joint_name
            )
            self._actuator_ids[act_name] = mujoco.mj_name2id(
                self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, act_name
            )

        # Cache site ids for fingertips
        self._site_ids: dict[str, int] = {}
        for finger, site_name in FINGERTIP_SITES.items():
            self._site_ids[finger] = mujoco.mj_name2id(
                self.model, mujoco.mjtObj.mjOBJ_SITE, site_name
            )

        # Cache touch sensor ids
        self._sensor_ids: dict[str, int] = {}
        for finger, sensor_name in TOUCH_SENSORS.items():
            self._sensor_ids[finger] = mujoco.mj_name2id(
                self.model, mujoco.mjtObj.mjOBJ_SENSOR, sensor_name
            )

    @property
    def num_joints(self) -> int:
        return len(MOTOR_MAP)

    @property
    def num_actuators(self) -> int:
        return len(MOTOR_MAP)

    def joint_id(self, name: str) -> int:
        return self._joint_ids[name]

    def actuator_id(self, name: str) -> int:
        return self._actuator_ids[name]

    def get_joint_positions(self) -> np.ndarray:
        """Get all 16 joint positions in motor ID order."""
        positions = np.zeros(16)
        for motor_id, (joint_name, _) in enumerate(MOTOR_MAP):
            jid = self._joint_ids[joint_name]
            qpos_adr = self.model.jnt_qposadr[jid]
            positions[motor_id] = self.data.qpos[qpos_adr]
        return positions

    def get_joint_velocities(self) -> np.ndarray:
        """Get all 16 joint velocities in motor ID order."""
        velocities = np.zeros(16)
        for motor_id, (joint_name, _) in enumerate(MOTOR_MAP):
            jid = self._joint_ids[joint_name]
            dof_adr = self.model.jnt_dofadr[jid]
            velocities[motor_id] = self.data.qvel[dof_adr]
        return velocities

    def get_actuator_forces(self) -> np.ndarray:
        """Get all 16 actuator forces in motor ID order."""
        forces = np.zeros(16)
        for motor_id, (_, act_name) in enumerate(MOTOR_MAP):
            aid = self._actuator_ids[act_name]
            forces[motor_id] = self.data.actuator_force[aid]
        return forces

    def set_actuator_ctrl(self, motor_id: int, value: float) -> None:
        """Set actuator control for a motor by ID."""
        _, act_name = MOTOR_MAP[motor_id]
        aid = self._actuator_ids[act_name]
        self.data.ctrl[aid] = value

    def get_fingertip_contacts(self) -> dict[str, float]:
        """Get contact force magnitude at each fingertip from touch sensors."""
        contacts: dict[str, float] = {}
        for finger, sensor_name in TOUCH_SENSORS.items():
            sid = self._sensor_ids[finger]
            adr = self.model.sensor_adr[sid]
            contacts[finger] = float(self.data.sensordata[adr])
        return contacts

    def step(self) -> None:
        """Step the simulation forward."""
        mujoco.mj_step(self.model, self.data)

    def reset(self) -> None:
        """Reset simulation to initial state."""
        mujoco.mj_resetData(self.model, self.data)

    def get_finger_joint_names(self, finger: str) -> list[str]:
        """Get joint names for a finger."""
        return [name for name, _ in MOTOR_MAP if name.startswith(finger)]

    def get_finger_motor_ids(self, finger: str) -> list[int]:
        """Get motor IDs for a finger."""
        return [i for i, (name, _) in enumerate(MOTOR_MAP) if name.startswith(finger)]
