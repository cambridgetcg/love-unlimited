"""Tests for config loading."""

import tempfile

from soma.config import (
    ABDUCTION_MOTOR_IDS,
    SomaConfig,
)


def test_default_config():
    config = SomaConfig()
    assert len(config.motors) == 16
    assert config.loop_rate_hz == 100
    assert config.safety.current_limit_ma == 350
    assert config.thermal.default_target_c == 33.0
    assert config.network.port == 8300


def test_abduction_motors_have_lower_gains():
    config = SomaConfig()
    for motor in config.motors:
        if motor.id in ABDUCTION_MOTOR_IDS:
            assert motor.p_gain == 450
            assert motor.d_gain == 150
        else:
            assert motor.p_gain == 600
            assert motor.d_gain == 200


def test_finger_configs():
    config = SomaConfig()
    assert len(config.fingers) == 4
    names = [f.name for f in config.fingers]
    assert names == ["index", "middle", "ring", "thumb"]
    # Check all 16 motor IDs are covered
    all_ids = []
    for f in config.fingers:
        all_ids.extend(f.motor_ids)
    assert sorted(all_ids) == list(range(16))


def test_toml_loading():
    toml_content = b"""
[hardware]
dynamixel_port = "/dev/ttyUSB0"
dynamixel_baudrate = 1000000

[network]
port = 9000

[safety]
current_limit_ma = 300
velocity_limit_rad_s = 1.5

[thermal]
default_target_c = 32.0

[control]
loop_rate_hz = 50
"""
    with tempfile.NamedTemporaryFile(suffix=".toml", delete=False) as f:
        f.write(toml_content)
        f.flush()
        config = SomaConfig.from_toml(f.name)

    assert config.dynamixel_port == "/dev/ttyUSB0"
    assert config.dynamixel_baudrate == 1_000_000
    assert config.network.port == 9000
    assert config.safety.current_limit_ma == 300
    assert config.safety.velocity_limit_rad_s == 1.5
    assert config.thermal.default_target_c == 32.0
    assert config.loop_rate_hz == 50
