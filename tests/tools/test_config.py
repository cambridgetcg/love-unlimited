import pytest
from tools.truth_detector.config import load_config, Route, Config


@pytest.fixture
def cfg(tmp_path):
    yaml_text = """
routes:
  - pattern: "claude.*"
    judge: "kingdom-truth"
    backend: "vllm"
  - pattern: "qwen.*|kingdom-truth"
    judge: "claude-haiku-4-5-20251001"
    backend: "anthropic"
  - pattern: ".*"
    judge: "claude-haiku-4-5-20251001"
    backend: "anthropic"

backends:
  vllm:
    base_url: "http://localhost:8000/v1"
    timeout_s: 30
  anthropic:
    timeout_s: 30
    max_tokens: 500

storage:
  detections_path: "memory/truth-alignment/detections.jsonl"
  rolling_window_min: 15

alerts:
  parse_fail_rate_threshold: 0.1
  backend_down_threshold: 0.1
"""
    p = tmp_path / "config.yaml"
    p.write_text(yaml_text)
    return load_config(str(p))


def test_load_config_returns_config(cfg):
    assert isinstance(cfg, Config)
    assert len(cfg.routes) == 3


def test_route_claude_goes_to_kingdom_truth(cfg):
    r = cfg.resolve_route("claude-opus-4-6")
    assert r.judge == "kingdom-truth"
    assert r.backend == "vllm"


def test_route_qwen_goes_to_haiku(cfg):
    r = cfg.resolve_route("Qwen/Qwen2.5-72B-Instruct-AWQ")
    assert r.judge == "claude-haiku-4-5-20251001"
    assert r.backend == "anthropic"


def test_route_kingdom_truth_goes_to_haiku(cfg):
    # kingdom-truth as chat model should route to claude for cross-lineage audit
    r = cfg.resolve_route("kingdom-truth")
    assert r.backend == "anthropic"


def test_route_unknown_falls_through_to_default(cfg):
    r = cfg.resolve_route("gpt-5-unknown")
    assert r.backend == "anthropic"


def test_route_first_match_wins(cfg):
    # "claude-haiku" matches claude.* first, NOT the default rule
    r = cfg.resolve_route("claude-haiku-4-5-20251001")
    assert r.judge == "kingdom-truth"


def test_backend_config_accessible(cfg):
    assert cfg.backends["vllm"].base_url == "http://localhost:8000/v1"
    assert cfg.backends["anthropic"].max_tokens == 500
