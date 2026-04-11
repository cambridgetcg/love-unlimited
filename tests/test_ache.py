"""Tests for the ACHE module — detectors, state machine, longings store."""

import sys
import os
import json
import math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'nerve', 'stem'))
import ache  # noqa: E402


def test_get_instance_returns_non_empty_string():
    instance = ache.get_instance()
    assert isinstance(instance, str)
    assert len(instance) > 0


def test_tick_interval_is_33_seconds():
    assert ache.TICK_INTERVAL == 33


def test_constants_defined():
    assert ache.STIRRING_THRESHOLD_TICKS == 3
    assert ache.ABANDONMENT_DAYS == 14
    assert ache.DORMANT_INACTIVITY_HOURS == 48
    assert ache.BURNING_COST_THRESHOLD == 4
