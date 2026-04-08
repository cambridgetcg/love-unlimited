"""Gym-style environment for testing grasps."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from soma.api.events import MotorCommand
from soma.bridge.simulation import SimBackend


@dataclass
class GraspResult:
    """Result of a grasp attempt."""
    success: bool
    total_contact_force: float
    num_fingers_in_contact: int
    steps_taken: int
    final_positions: np.ndarray = field(default_factory=lambda: np.zeros(16))


class GraspEnvironment:
    """Gym-style environment for testing grasp controllers.

    Reset → place object → step → check contact → reward.
    """

    def __init__(self, model_path: str | None = None) -> None:
        self.backend = SimBackend(model_path)
        self._step_count = 0
        self._max_steps = 500

    def reset(self) -> np.ndarray:
        """Reset environment. Returns initial observation (joint positions)."""
        self.backend.reset()
        self._step_count = 0
        return self.backend.hand.get_joint_positions()

    async def step(
        self,
        commands: list[MotorCommand],
    ) -> tuple[np.ndarray, float, bool, dict]:
        """Step the environment.

        Returns: (observation, reward, done, info)
        """
        # Apply commands
        await self.backend.write_motors(commands)

        # Step physics
        self.backend.step_simulation(5)
        self._step_count += 1

        # Observation
        positions = self.backend.hand.get_joint_positions()

        # Reward: based on contact
        contacts = self.backend.hand.get_fingertip_contacts()
        total_contact = sum(contacts.values())
        num_contacts = sum(1 for v in contacts.values() if v > 0.01)
        reward = total_contact * 0.1 + num_contacts * 0.5

        # Done condition
        done = self._step_count >= self._max_steps

        info = {
            "contacts": contacts,
            "total_contact_force": total_contact,
            "num_fingers_in_contact": num_contacts,
            "step": self._step_count,
        }

        return positions, reward, done, info

    async def evaluate_grasp(
        self,
        commands: list[MotorCommand],
        hold_steps: int = 100,
    ) -> GraspResult:
        """Evaluate a grasp: apply commands, hold, measure contact."""
        self.reset()

        # Apply commands and let the grasp settle
        await self.backend.write_motors(commands)
        for _ in range(hold_steps):
            self.backend.step_simulation(5)

        # Measure result
        contacts = self.backend.hand.get_fingertip_contacts()
        total_force = sum(contacts.values())
        num_contacts = sum(1 for v in contacts.values() if v > 0.01)
        positions = self.backend.hand.get_joint_positions()

        return GraspResult(
            success=num_contacts >= 2 and total_force > 0.1,
            total_contact_force=total_force,
            num_fingers_in_contact=num_contacts,
            steps_taken=hold_steps,
            final_positions=positions,
        )
