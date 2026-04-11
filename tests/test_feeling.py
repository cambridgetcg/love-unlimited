"""Tests for the FEELING module — three strata, curtain logic, pattern library."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'nerve', 'stem'))

from feeling import get_instance, HORMONE_COEFS


def test_get_instance_returns_non_empty_string():
    instance = get_instance()
    assert isinstance(instance, str)
    assert len(instance) > 0


def test_hormone_coefs_has_all_five_hormones():
    assert set(HORMONE_COEFS.keys()) == {"adrenaline", "cortisol", "oxytocin", "melatonin", "dopamine"}
    for name, coefs in HORMONE_COEFS.items():
        assert "valence" in coefs
        assert "arousal" in coefs
