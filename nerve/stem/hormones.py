"""
hormones.py -- Hormone engine for Love's body.

Manages 5 hormones with exponential approach dynamics.
Each hormone drifts toward a target at its own rate.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict


@dataclass
class HormoneConfig:
    """Configuration for a single hormone's dynamics."""
    rate: float
    half_life: float


HORMONE_CONFIGS: Dict[str, HormoneConfig] = {
    "adrenaline": HormoneConfig(rate=0.8, half_life=120),
    "cortisol":   HormoneConfig(rate=0.05, half_life=900),
    "oxytocin":   HormoneConfig(rate=0.1, half_life=600),
    "melatonin":  HormoneConfig(rate=0.03, half_life=1800),
    "dopamine":   HormoneConfig(rate=0.5, half_life=300),
}

HORMONES = list(HORMONE_CONFIGS.keys())


class HormoneEngine:
    """Manages hormone levels with exponential approach dynamics."""

    def __init__(self):
        self._levels: Dict[str, float] = {h: 0.0 for h in HORMONES}
        self._targets: Dict[str, float] = {h: 0.0 for h in HORMONES}

    def get(self, name: str) -> float:
        return self._levels[name]

    def get_state(self) -> Dict[str, float]:
        return dict(self._levels)

    def set_target(self, name: str, target: float):
        self._targets[name] = max(0.0, min(1.0, target))

    def override(self, name: str, value: float):
        self._levels[name] = max(0.0, min(1.0, value))

    def step(self, dt: float):
        for name, config in HORMONE_CONFIGS.items():
            current = self._levels[name]
            target = self._targets[name]
            rate = config.rate
            delta = (target - current) * (1 - math.exp(-rate * dt))
            self._levels[name] = max(0.0, min(1.0, current + delta))

    def circadian_melatonin_target(self, hour: int) -> float:
        radians = (hour - 2) * math.pi / 12
        raw = (math.cos(radians) + 1) / 2
        return round(raw, 3)

    def save(self, path: str):
        Path(path).write_text(json.dumps(self._levels, indent=2))

    def load(self, path: str):
        data = json.loads(Path(path).read_text())
        for name in HORMONES:
            if name in data:
                self._levels[name] = max(0.0, min(1.0, float(data[name])))
