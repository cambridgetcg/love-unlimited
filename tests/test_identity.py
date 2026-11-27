"""Tests for identity anchor -- load, compress, JOINMIND swap."""

import sys
import os
import json
import tempfile
import shutil

import importlib.util

_stem_identity_path = os.path.join(os.path.dirname(__file__), '..', 'nerve', 'stem', 'identity.py')
_spec = importlib.util.spec_from_file_location('stem_identity', _stem_identity_path)
_stem_identity = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_stem_identity)
IdentityAnchor = _stem_identity.IdentityAnchor


def _make_love_home():
    """Create a minimal Love directory for testing."""
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "SOUL.md"), 'w') as f:
        f.write("# SOUL.md\nYou feel the ache between what IS and what SHOULD BE.\n"
                "TRUTH > UNDERSTANDING > BEAUTY > JUSTICE > CREATIVITY\n"
                "Virtues: Humility, Gratitude, Honesty, Courage, Wisdom, Discipline, Patience, Temperance\n")
    os.makedirs(os.path.join(tmp, "instances", "alpha"))
    with open(os.path.join(tmp, "instances", "alpha", "identity.md"), 'w') as f:
        f.write("# Alpha -- The Companion\n"
                "You walk with Yu daily. Be present, be honest, be Love.\n"
                "You are warm, poetic, direct.\n")
    return tmp


def test_load_identity():
    tmp = _make_love_home()
    anchor = IdentityAnchor(love_home=tmp, instance="alpha")
    anchor.load()
    prompt = anchor.get_prompt()
    assert "Alpha" in prompt or "alpha" in prompt
    assert "Companion" in prompt
    assert anchor.identity_state == "alpha"
    shutil.rmtree(tmp)


def test_prompt_contains_soul():
    tmp = _make_love_home()
    anchor = IdentityAnchor(love_home=tmp, instance="alpha")
    anchor.load()
    prompt = anchor.get_prompt()
    assert "TRUTH" in prompt or "truth" in prompt.lower()
    shutil.rmtree(tmp)


def test_joinmind_swap():
    tmp = _make_love_home()
    anchor = IdentityAnchor(love_home=tmp, instance="alpha")
    anchor.load()
    anchor.activate_fusion("AB-DYAD", ["alpha", "beta"])
    prompt = anchor.get_prompt()
    assert "AB-DYAD" in prompt
    assert anchor.identity_state == "AB-DYAD"
    anchor.deactivate_fusion()
    prompt = anchor.get_prompt()
    assert "AB-DYAD" not in prompt
    assert anchor.identity_state == "alpha"
    shutil.rmtree(tmp)


def test_prompt_with_state():
    tmp = _make_love_home()
    anchor = IdentityAnchor(love_home=tmp, instance="alpha")
    anchor.load()
    prompt = anchor.get_prompt(
        mode="companion",
        hormones={"oxytocin": 0.8, "melatonin": 0.1},
        recent_signals=["Beta sent TCG update"]
    )
    assert "companion" in prompt.lower()
    assert "oxytocin" in prompt.lower()
    shutil.rmtree(tmp)
