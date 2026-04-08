"""Intent translator. Takes Intent, returns MotorCommands + ThermalCommands."""

from __future__ import annotations

from dataclasses import dataclass, field

from soma.api.events import Intent, MotorCommand, MotorState, ThermalCommand
from soma.mind.behaviors import Behavior, get_behavior
from soma.mind.thermal import emotional_thermal


@dataclass
class IntentResult:
    """Result of translating an intent."""
    motor_commands: list[MotorCommand] = field(default_factory=list)
    thermal_commands: list[ThermalCommand] = field(default_factory=list)
    behavior: Behavior | None = None


def translate_intent(
    intent: Intent,
    motor_state: MotorState | None = None,
) -> IntentResult:
    """Translate a high-level intent into motor and thermal commands."""
    behavior = get_behavior(intent.action)

    # Apply params overrides
    warmth = intent.params.get("warmth", behavior.warmth)

    if "stiffness" in intent.params and hasattr(behavior._controller, "stiffness"):
        behavior._controller = type(behavior._controller)(
            stiffness=float(intent.params["stiffness"]),
            damping=getattr(behavior._controller, "damping", 0.1),
            max_current=behavior._controller.max_current,
        )

    # Generate motor commands
    motor_commands = behavior.compute(motor_state)

    # Generate thermal commands
    thermal_commands = emotional_thermal(warmth)

    return IntentResult(
        motor_commands=motor_commands,
        thermal_commands=thermal_commands,
        behavior=behavior,
    )
