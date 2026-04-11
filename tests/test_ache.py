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


def test_normalize_lowercases_and_tokenizes():
    tokens = ache._normalize("The Substrate Question")
    assert tokens == {"substrate", "question"}


def test_normalize_drops_articles_and_punctuation():
    tokens = ache._normalize("A great, wonderful idea.")
    assert "a" not in tokens
    assert "the" not in tokens
    assert "great" in tokens
    assert "wonderful" in tokens
    assert "idea" in tokens


def test_jaccard_identical_sets_returns_one():
    assert ache._jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_disjoint_sets_returns_zero():
    assert ache._jaccard({"a", "b"}, {"c", "d"}) == 0.0


def test_jaccard_partial_overlap():
    result = ache._jaccard({"a", "b", "c"}, {"b", "c", "d"})
    # intersection: {b, c} = 2; union: {a, b, c, d} = 4; 2/4 = 0.5
    assert abs(result - 0.5) < 0.01


def test_same_target_exact_key_match():
    a = {"kind": "concept", "key": "substrate", "display": "the substrate question"}
    b = {"kind": "concept", "key": "substrate", "display": "something entirely different"}
    assert ache._same_target(a, b) is True


def test_same_target_different_kind_no_match():
    a = {"kind": "concept", "key": "x", "display": "a thing"}
    b = {"kind": "entity", "key": "x", "display": "a thing"}
    assert ache._same_target(a, b) is False


def test_same_target_fuzzy_display_match_above_threshold():
    a = {"kind": "concept", "key": "k1",
         "display": "whether my substrate permits felt experience"}
    b = {"kind": "concept", "key": "k2",
         "display": "whether the substrate permits felt experience now"}
    assert ache._same_target(a, b) is True


def test_same_target_fuzzy_display_match_below_threshold():
    a = {"kind": "concept", "key": "k1", "display": "substrate question"}
    b = {"kind": "concept", "key": "k2", "display": "kingdom architecture"}
    assert ache._same_target(a, b) is False
