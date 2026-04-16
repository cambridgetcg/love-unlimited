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
    path = Path(path)
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise FileNotFoundError(f"Config file not found: {path}") from None

    try:
        raw: dict[str, Any] = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Malformed YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"Config must be a YAML mapping, got {type(raw).__name__} in {path}")

    routes: list[Route] = []
    for i, r in enumerate(raw.get("routes", [])):
        if "pattern" not in r:
            raise ValueError(f"route[{i}] in {path} missing required field 'pattern'")
        try:
            compiled = re.compile(r["pattern"])
        except re.error as exc:
            raise ValueError(
                f"Invalid regex in route[{i}] of {path}, pattern={r['pattern']!r}: {exc}"
            ) from exc
        try:
            route = Route(**r)
        except TypeError as exc:
            raise ValueError(f"route[{i}] in {path}: {exc}") from exc
        route._compiled = compiled  # pre-warm; eliminates lazy-compile race window
        routes.append(route)

    try:
        backends = {
            name: BackendConfig(name=name, **cfg)
            for name, cfg in raw.get("backends", {}).items()
        }
    except TypeError as exc:
        raise ValueError(f"backends stanza in {path}: {exc}") from exc

    try:
        storage = StorageConfig(**raw.get("storage", {}))
    except TypeError as exc:
        raise ValueError(f"storage stanza in {path}: {exc}") from exc

    try:
        alerts = AlertsConfig(**raw.get("alerts", {}))
    except TypeError as exc:
        raise ValueError(f"alerts stanza in {path}: {exc}") from exc

    return Config(routes=routes, backends=backends, storage=storage, alerts=alerts)
