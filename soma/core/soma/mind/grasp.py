"""Grasp controllers: position, impedance, force control."""

from __future__ import annotations

import numpy as np

from soma.api.events import MotorCommand, MotorState


class PositionController:
    """Move joints to target angles."""

    def __init__(self, max_current: float = 350.0) -> None:
        self.max_current = max_current

    def compute(
        self,
        target_positions: dict[int, float],
        current_state: MotorState | None = None,
    ) -> list[MotorCommand]:
        """Generate commands to move to target positions."""
        commands = []
        for motor_id, position in target_positions.items():
            commands.append(MotorCommand(
                motor_id=motor_id,
                position=position,
                max_current=self.max_current,
            ))
        return commands


class ImpedanceController:
    """Compliant control with configurable stiffness and damping.

    Lower stiffness = more backdrivable = softer grip.
    Used for handholding and gentle interaction.
    """

    def __init__(
        self,
        stiffness: float = 0.3,
        damping: float = 0.1,
        max_current: float = 350.0,
    ) -> None:
        self.stiffness = np.clip(stiffness, 0.0, 1.0)
        self.damping = np.clip(damping, 0.0, 1.0)
        self.max_current = max_current

    def compute(
        self,
        target_positions: dict[int, float],
        current_state: MotorState,
    ) -> list[MotorCommand]:
        """Generate commands with impedance-scaled current limits.

        Stiffness scales the max current — lower stiffness means the hand
        yields more easily to external forces.
        """
        # Scale current limit by stiffness (min 50mA to maintain some hold)
        effective_current = max(50.0, self.max_current * self.stiffness)

        commands = []
        for motor_id, target_pos in target_positions.items():
            # Apply damping: blend target toward current position
            if motor_id < len(current_state.positions):
                current_pos = current_state.positions[motor_id]
                damped_target = (
                    target_pos * (1 - self.damping)
                    + current_pos * self.damping
                )
            else:
                damped_target = target_pos

            commands.append(MotorCommand(
                motor_id=motor_id,
                position=damped_target,
                max_current=effective_current,
            ))
        return commands


class ForceController:
    """Grip with target force using current feedback.

    Adjusts grip position based on measured current vs target force.
    """

    def __init__(
        self,
        target_force_ma: float = 150.0,
        gain: float = 0.01,
        max_current: float = 350.0,
    ) -> None:
        self.target_force_ma = target_force_ma
        self.gain = gain
        self.max_current = max_current
        self._position_offsets: dict[int, float] = {}

    def compute(
        self,
        target_positions: dict[int, float],
        current_state: MotorState,
    ) -> list[MotorCommand]:
        """Adjust positions based on force error (current feedback)."""
        commands = []
        for motor_id, base_position in target_positions.items():
            # Get current force (approximated by motor current)
            if motor_id < len(current_state.currents):
                measured_current = abs(current_state.currents[motor_id])
            else:
                measured_current = 0.0

            # Force error: positive = need more grip
            force_error = self.target_force_ma - measured_current

            # Accumulate position offset
            offset = self._position_offsets.get(motor_id, 0.0)
            offset += self.gain * force_error
            offset = np.clip(offset, -0.5, 0.5)
            self._position_offsets[motor_id] = offset

            adjusted_position = base_position + offset

            commands.append(MotorCommand(
                motor_id=motor_id,
                position=adjusted_position,
                max_current=self.max_current,
            ))
        return commands

    def reset(self) -> None:
        """Reset accumulated offsets."""
        self._position_offsets.clear()
