"""Tests for the hormone engine -- exponential decay, target calculation, clamping."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'body', 'mind'))

from hormones import HormoneEngine, HORMONE_CONFIGS


def test_initial_state():
    engine = HormoneEngine()
    state = engine.get_state()
    assert set(state.keys()) == {"adrenaline", "cortisol", "oxytocin", "melatonin", "dopamine"}
    for v in state.values():
        assert v == 0.0


def test_set_target_and_step():
    engine = HormoneEngine()
    engine.set_target("adrenaline", 1.0)
    engine.step(dt=2.0)
    assert engine.get("adrenaline") > 0.3
    assert engine.get("adrenaline") < 1.0


def test_decay_toward_zero():
    engine = HormoneEngine()
    engine.set_target("adrenaline", 1.0)
    engine.step(dt=10.0)
    high = engine.get("adrenaline")
    engine.set_target("adrenaline", 0.0)
    engine.step(dt=10.0)
    assert engine.get("adrenaline") < high


def test_clamping():
    engine = HormoneEngine()
    engine.set_target("cortisol", 5.0)
    engine.step(dt=1000.0)
    assert engine.get("cortisol") <= 1.0
    engine.set_target("cortisol", -1.0)
    engine.step(dt=1000.0)
    assert engine.get("cortisol") >= 0.0


def test_override():
    engine = HormoneEngine()
    engine.override("oxytocin", 0.75)
    assert engine.get("oxytocin") == 0.75


def test_cortisol_slow_buildup():
    engine = HormoneEngine()
    engine.set_target("cortisol", 1.0)
    engine.step(dt=2.0)
    cortisol_2s = engine.get("cortisol")
    assert cortisol_2s < 0.3
    engine_fast = HormoneEngine()
    engine_fast.set_target("adrenaline", 1.0)
    engine_fast.step(dt=2.0)
    adrenaline_2s = engine_fast.get("adrenaline")
    assert adrenaline_2s > cortisol_2s


def test_melatonin_circadian():
    engine = HormoneEngine()
    target = engine.circadian_melatonin_target(hour=23)
    assert target > 0.6
    target = engine.circadian_melatonin_target(hour=10)
    assert target < 0.3


def test_load_and_save():
    import json
    import tempfile
    engine = HormoneEngine()
    engine.override("dopamine", 0.5)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        path = f.name
        engine.save(path)
    engine2 = HormoneEngine()
    engine2.load(path)
    assert engine2.get("dopamine") == 0.5
    os.unlink(path)
