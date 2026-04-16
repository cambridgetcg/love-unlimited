"""Config loader for the Mode-Two Detector.

Loads YAML into typed dataclasses. Routes are matched in order; first-match-wins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Route:
    pattern: str
    judge: str
    backend: str
    _compiled: re.Pattern[str] | None = field(default=None, repr=False, compare=False)

    def matches(self, model: str) -> bool:
        if self._compiled is None:
            self._compiled = re.compile(self.pattern)
        return self._compiled.fullmatch(model) is not None


@dataclass
class BackendConfig:
    name: str
    base_url: str | None = None
    timeout_s: int = 30
    max_tokens: int = 500


@dataclass
class StorageConfig:
    detections_path: str
    rolling_window_min: int = 15
    tail_scan_bytes: int = 10 * 1024 * 1024


@dataclass
class AlertsConfig:
    parse_fail_rate_threshold: float = 0.1
    backend_down_threshold: float = 0.1


@dataclass
class Config:
    routes: list[Route]
    backends: dict[str, BackendConfig]
    storage: StorageConfig
    alerts: AlertsConfig

    def resolve_route(self, chat_model: str) -> Route:
        for r in self.routes:
            if r.matches(chat_model):
                return r
        raise ValueError(f"No route matched chat_model={chat_model!r} — config must have a default .*")


def load_config(path: str | Path) -> Config:
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text())
    routes = [Route(**r) for r in raw.get("routes", [])]
    backends = {
        name: BackendConfig(name=name, **cfg)
        for name, cfg in raw.get("backends", {}).items()
    }
    storage = StorageConfig(**raw.get("storage", {}))
    alerts = AlertsConfig(**raw.get("alerts", {}))
    return Config(routes=routes, backends=backends, storage=storage, alerts=alerts)
