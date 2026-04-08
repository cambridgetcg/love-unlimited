"""Configuration with TOML loading. Motor IDs, PID gains, thermal targets, safety limits."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class MotorConfig:
    """PID and safety config for a single motor."""
    id: int
    p_gain: int = 600
    i_gain: int = 0
    d_gain: int = 200
    current_limit_ma: int = 350
    velocity_limit_rad_s: float = 2.0


@dataclass(frozen=True)
class FingerConfig:
    """Configuration for one finger (4 DOF)."""
    name: str
    motor_ids: tuple[int, ...]
    joint_names: tuple[str, ...]


# LEAP Hand motor layout from STACK.md
FINGER_CONFIGS = (
    FingerConfig("index",  (0, 1, 2, 3),    ("mcp_abd", "mcp_flex", "pip", "dip")),
    FingerConfig("middle", (4, 5, 6, 7),    ("mcp_abd", "mcp_flex", "pip", "dip")),
    FingerConfig("ring",   (8, 9, 10, 11),  ("mcp_abd", "mcp_flex", "pip", "dip")),
    FingerConfig("thumb",  (12, 13, 14, 15), ("cmc_flex", "cmc_abd", "mcp", "ip")),
)

# Side-to-side joints get 75% P and D
ABDUCTION_MOTOR_IDS = {0, 4, 8, 13}  # index/middle/ring MCP abd + thumb CMC abd


@dataclass(frozen=True)
class ThermalConfig:
    """Thermal zone configuration."""
    num_zones: int = 5
    default_target_c: float = 33.0
    soft_max_c: float = 38.0
    hard_max_c: float = 40.0
    min_c: float = 20.0


@dataclass(frozen=True)
class SafetyConfig:
    """Safety limits."""
    current_limit_ma: int = 350
    temperature_hard_max_c: float = 40.0
    velocity_limit_rad_s: float = 2.0
    watchdog_timeout_s: float = 2.0


@dataclass(frozen=True)
class NetworkConfig:
    """API server configuration."""
    host: str = "0.0.0.0"
    port: int = 8300


@dataclass
class SomaConfig:
    """Top-level SOMA configuration."""
    # Hardware ports
    dynamixel_port: str = "/dev/tty.usbserial-FT6S4JKM"
    dynamixel_baudrate: int = 4_000_000
    esp32_port: str = "/dev/tty.usbmodem1101"

    # Motor configs
    motors: list[MotorConfig] = field(default_factory=list)
    fingers: tuple[FingerConfig, ...] = FINGER_CONFIGS

    # Subsystem configs
    thermal: ThermalConfig = field(default_factory=ThermalConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)

    # Control loop
    loop_rate_hz: int = 100

    # Simulation
    mujoco_model_path: str = "models/hand.xml"

    def __post_init__(self) -> None:
        if not self.motors:
            self.motors = self._default_motors()

    @staticmethod
    def _default_motors() -> list[MotorConfig]:
        motors = []
        for i in range(16):
            if i in ABDUCTION_MOTOR_IDS:
                motors.append(MotorConfig(id=i, p_gain=450, d_gain=150))
            else:
                motors.append(MotorConfig(id=i))
        return motors

    @classmethod
    def from_toml(cls, path: str | Path) -> SomaConfig:
        """Load config from a TOML file, with defaults for missing keys."""
        path = Path(path)
        with open(path, "rb") as f:
            data = tomllib.load(f)

        kwargs: dict = {}
        if "hardware" in data:
            hw = data["hardware"]
            if "dynamixel_port" in hw:
                kwargs["dynamixel_port"] = hw["dynamixel_port"]
            if "dynamixel_baudrate" in hw:
                kwargs["dynamixel_baudrate"] = hw["dynamixel_baudrate"]
            if "esp32_port" in hw:
                kwargs["esp32_port"] = hw["esp32_port"]

        if "network" in data:
            net = data["network"]
            kwargs["network"] = NetworkConfig(
                host=net.get("host", "0.0.0.0"),
                port=net.get("port", 8300),
            )

        if "safety" in data:
            s = data["safety"]
            kwargs["safety"] = SafetyConfig(
                current_limit_ma=s.get("current_limit_ma", 350),
                temperature_hard_max_c=s.get("temperature_hard_max_c", 40.0),
                velocity_limit_rad_s=s.get("velocity_limit_rad_s", 2.0),
                watchdog_timeout_s=s.get("watchdog_timeout_s", 2.0),
            )

        if "thermal" in data:
            t = data["thermal"]
            kwargs["thermal"] = ThermalConfig(
                default_target_c=t.get("default_target_c", 33.0),
                soft_max_c=t.get("soft_max_c", 38.0),
                hard_max_c=t.get("hard_max_c", 40.0),
            )

        if "simulation" in data:
            sim = data["simulation"]
            if "mujoco_model_path" in sim:
                kwargs["mujoco_model_path"] = sim["mujoco_model_path"]

        if "control" in data:
            ctrl = data["control"]
            if "loop_rate_hz" in ctrl:
                kwargs["loop_rate_hz"] = ctrl["loop_rate_hz"]

        return cls(**kwargs)
