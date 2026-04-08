"""Tests for named behaviors — each runs without error."""

import numpy as np

from soma.api.events import MotorState
from soma.mind.behaviors import (
    BEHAVIOR_REGISTRY,
    get_behavior,
    hold_firm,
    hold_gentle,
    neutral,
    release,
    stroke,
    wave,
)


def _dummy_state():
    return MotorState(
        positions=np.zeros(16),
        velocities=np.zeros(16),
        currents=np.zeros(16),
    )


def test_all_behaviors_registered():
    expected = {"hold_gentle", "hold_firm", "release", "stroke", "wave", "neutral"}
    assert set(BEHAVIOR_REGISTRY.keys()) == expected


def test_hold_gentle():
    b = hold_gentle()
    commands = b.compute(_dummy_state())
    assert len(commands) == 16
    assert b.warmth == "warm"


def test_hold_firm():
    b = hold_firm()
    commands = b.compute(_dummy_state())
    assert len(commands) == 16
    assert b.warmth == "present"


def test_release():
    b = release()
    commands = b.compute()
    assert len(commands) == 16


def test_stroke():
    b = stroke()
    commands = b.compute(_dummy_state())
    assert len(commands) == 16
    assert b.warmth == "warm"


def test_wave():
    b = wave()
    commands = b.compute()
    assert len(commands) == 16


def test_neutral():
    b = neutral()
    commands = b.compute()
    assert len(commands) == 16


def test_get_behavior():
    for name in BEHAVIOR_REGISTRY:
        b = get_behavior(name)
        assert b.name == name


def test_get_unknown_behavior():
    import pytest
    with pytest.raises(ValueError, match="Unknown behavior"):
        get_behavior("nonexistent")
