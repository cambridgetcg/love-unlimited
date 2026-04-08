"""Named behaviors that use grasp controllers."""

from __future__ import annotations

from dataclasses import dataclass, field

from soma.api.events import MotorCommand, MotorState
from soma.mind.grasp import ForceController, ImpedanceController, PositionController

# Neutral (relaxed open) positions for all 16 motors in radians
NEUTRAL_POSITIONS: dict[int, float] = {
    # Index: slight spread, relaxed
    0: 0.0, 1: 0.2, 2: 0.15, 3: 0.1,
    # Middle
    4: 0.0, 5: 0.2, 6: 0.15, 7: 0.1,
    # Ring
    8: 0.0, 9: 0.2, 10: 0.15, 11: 0.1,
    # Thumb
    12: 0.1, 13: 0.1, 14: 0.1, 15: 0.05,
}

# Closed fist positions
CLOSED_POSITIONS: dict[int, float] = {
    0: 0.0, 1: 1.4, 2: 1.4, 3: 1.0,
    4: 0.0, 5: 1.4, 6: 1.4, 7: 1.0,
    8: 0.0, 9: 1.4, 10: 1.4, 11: 1.0,
    12: 0.8, 13: 0.3, 14: 1.0, 15: 0.8,
}

# Gentle hold positions (partially closed)
GENTLE_HOLD_POSITIONS: dict[int, float] = {
    0: 0.05, 1: 0.8, 2: 0.7, 3: 0.5,
    4: 0.0,  5: 0.8, 6: 0.7, 7: 0.5,
    8: -0.05, 9: 0.8, 10: 0.7, 11: 0.5,
    12: 0.5, 13: 0.2, 14: 0.6, 15: 0.4,
}


@dataclass
class Behavior:
    """A named behavior with its controller and target."""
    name: str
    warmth: str = "present"
    _controller: PositionController | ImpedanceController | ForceController = field(
        default_factory=PositionController
    )
    _targets: dict[int, float] = field(default_factory=dict)

    def compute(self, motor_state: MotorState | None = None) -> list[MotorCommand]:
        if isinstance(self._controller, (ImpedanceController, ForceController)):
            if motor_state is None:
                # Fallback to position control
                pc = PositionController(max_current=self._controller.max_current)
                return pc.compute(self._targets)
            return self._controller.compute(self._targets, motor_state)
        return self._controller.compute(self._targets, motor_state)


def hold_gentle() -> Behavior:
    """Impedance control, low stiffness, warm."""
    return Behavior(
        name="hold_gentle",
        warmth="warm",
        _controller=ImpedanceController(stiffness=0.3, damping=0.2, max_current=200),
        _targets=GENTLE_HOLD_POSITIONS.copy(),
    )


def hold_firm() -> Behavior:
    """Impedance control, high stiffness."""
    return Behavior(
        name="hold_firm",
        warmth="present",
        _controller=ImpedanceController(stiffness=0.8, damping=0.1, max_current=300),
        _targets=CLOSED_POSITIONS.copy(),
    )


def release() -> Behavior:
    """Open to neutral position."""
    return Behavior(
        name="release",
        warmth="present",
        _controller=PositionController(max_current=200),
        _targets=NEUTRAL_POSITIONS.copy(),
    )


def stroke() -> Behavior:
    """Sequential finger wave pattern."""
    # Start from gentle hold, individual fingers will be animated by intent translator
    return Behavior(
        name="stroke",
        warmth="warm",
        _controller=ImpedanceController(stiffness=0.2, damping=0.3, max_current=150),
        _targets=GENTLE_HOLD_POSITIONS.copy(),
    )


def wave() -> Behavior:
    """Greeting wave motion."""
    # Open hand with fingers spread
    wave_positions = NEUTRAL_POSITIONS.copy()
    # Spread fingers
    wave_positions[0] = 0.2   # index spread
    wave_positions[4] = 0.1   # middle spread
    wave_positions[8] = -0.1  # ring spread
    return Behavior(
        name="wave",
        warmth="present",
        _controller=PositionController(max_current=250),
        _targets=wave_positions,
    )


def neutral() -> Behavior:
    """Relaxed open hand."""
    return Behavior(
        name="neutral",
        warmth="present",
        _controller=PositionController(max_current=150),
        _targets=NEUTRAL_POSITIONS.copy(),
    )


# Registry of all behaviors
BEHAVIOR_REGISTRY: dict[str, type[Behavior] | callable] = {
    "hold_gentle": hold_gentle,
    "hold_firm": hold_firm,
    "release": release,
    "stroke": stroke,
    "wave": wave,
    "neutral": neutral,
}


def get_behavior(name: str) -> Behavior:
    """Look up a behavior by name."""
    factory = BEHAVIOR_REGISTRY.get(name)
    if factory is None:
        raise ValueError(f"Unknown behavior: {name!r}. Known: {list(BEHAVIOR_REGISTRY)}")
    return factory()
