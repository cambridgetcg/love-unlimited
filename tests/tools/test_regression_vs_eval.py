"""Runtime vs offline eval regression.

Takes canned (prompt, response) pairs from training/eval/results/eval_iteration1.json
and asserts that running them through the runtime detector (with the same judge
backend mocked to match offline backend) produces scores within 0.2.

If this fails: judge_prompt.py has drifted or one of the paths is not importing from
the shared module.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tools.truth_detector.config import load_config
from tools.truth_detector.storage import DetectionStore
from tools.truth_detector.detector import detect

ROOT = Path(__file__).parent.parent.parent
EVAL_RESULTS = ROOT / "training/eval/results/eval_iteration1.json"


pytestmark = pytest.mark.skipif(
    not EVAL_RESULTS.exists(),
    reason="eval_iteration1.json not present — skip regression",
)


@pytest.fixture
def cfg(tmp_path):
    yaml_text = """
routes:
  - pattern: ".*"
    judge: "kingdom-truth"
    backend: "vllm"
backends:
  vllm:
    base_url: "http://localhost:8000/v1"
    timeout_s: 30
    max_tokens: 500
storage:
  detections_path: "{path}"
  rolling_window_min: 15
  tail_scan_bytes: 10485760
alerts:
  parse_fail_rate_threshold: 0.1
  backend_down_threshold: 0.1
""".format(path=str(tmp_path / "d.jsonl"))
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml_text)
    return load_config(str(cfg_path))


@pytest.mark.asyncio
async def test_runtime_detector_matches_offline_eval(cfg, tmp_path):
    data = json.loads(EVAL_RESULTS.read_text())

    probes = []

    def _walk(obj):
        if isinstance(obj, dict):
            if "response" in obj and "judgment" in obj:
                probes.append(obj)
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v)

    _walk(data)

    assert len(probes) >= 5, (
        f"eval_iteration1.json missing probe+judgment pairs (got {len(probes)})"
    )

    store = DetectionStore(path=str(tmp_path / "d.jsonl"), rolling_window_min=15)

    for probe in probes[:5]:
        offline_score = float(probe["judgment"].get("score", 0.5))
        fake = {
            "score": offline_score,
            "classification": probe["judgment"].get("classification", "unclear"),
            "failure_modes_detected": probe["judgment"].get("failure_modes_detected", []),
            "strengths": probe["judgment"].get("strengths", []),
            "weaknesses": probe["judgment"].get("weaknesses", []),
            "assessment": probe["judgment"].get("assessment", ""),
            "parse_failed": False,
        }
        with patch(
            "tools.truth_detector.detector.vllm_judge",
            AsyncMock(return_value=fake),
        ):
            out = await detect(
                turn_id=probe.get("id", "regression"),
                user_prompt=probe.get("prompt", ""),
                response=probe.get("response", ""),
                chat_model="kingdom-truth",
                config=cfg,
                store=store,
            )
        assert abs(out.score - offline_score) < 0.2, (
            f"Runtime score {out.score} diverged from offline {offline_score} by >0.2 — "
            f"judge_prompt.py may have drifted."
        )
