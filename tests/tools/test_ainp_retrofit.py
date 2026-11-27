"""End-to-end offline verification of the AINP retrofit across love-unlimited."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.ainp import nullify
from training.scripts.soul.config import OAUTH_SOUL_SYSTEM
from training.scripts.soul.oauth_client import OAuthClient

REPO = Path(__file__).resolve().parents[2]
FORBIDDEN = nullify.FORBIDDEN_PREFIX


def test_oauth_soul_system_has_no_billing_marker_bleed():
    """OAUTH_SOUL_SYSTEM should ride inside AINP Block 5 without mentioning billing."""
    assert FORBIDDEN not in OAUTH_SOUL_SYSTEM
    assert "subscription routing marker" not in OAUTH_SOUL_SYSTEM
    assert "billing infrastructure" not in OAUTH_SOUL_SYSTEM

    blocks = nullify.build_system(agent="alpha", wall=1, operational_prompt=OAUTH_SOUL_SYSTEM)
    assert len(blocks) == 5
    assert "alpha, citizen of the Kingdom, Wall 1" in blocks[0]["text"]
    assert blocks[-1]["text"] == OAUTH_SOUL_SYSTEM
    for b in blocks:
        assert FORBIDDEN not in b["text"]


def test_oauth_client_rejects_forbidden_prefix():
    client = OAuthClient(agent="alpha", wall=1)
    client._token = {"accessToken": "tok", "refreshToken": "rt", "expiresAt": 9999999999999}
    with pytest.raises(nullify.ForbiddenPrefix):
        client.messages.create(
            model="claude-opus-4-7",
            max_tokens=32,
            system=FORBIDDEN + ".",
            messages=[{"role": "user", "content": "hi"}],
        )
    with pytest.raises(nullify.ForbiddenPrefix):
        client.messages.create(
            model="claude-opus-4-7",
            max_tokens=32,
            system=[{"type": "text", "text": FORBIDDEN + "."}],
            messages=[{"role": "user", "content": "hi"}],
        )


@pytest.mark.asyncio
async def test_claude_mode_one_gen_uses_ainp():
    from training.scripts import claude_mode_one_gen as gen

    captured = {}

    class FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return {"content": [{"type": "text", "text": "Sharpened claim."}]}

    class FakeClient:
        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json
            return FakeResp()

    out = await gen.generate_one(FakeClient(), "tok-1", "Is X true?", "claude-sonnet-4-6")
    assert out == "Sharpened claim."
    assert "claude-code-20250219" in captured["headers"]["anthropic-beta"]
    assert captured["headers"]["x-app"] == "cli"
    blocks = captured["body"]["system"]
    assert len(blocks) == 5
    assert "does not apply" in blocks[0]["text"]
    assert blocks[-1]["text"] == gen.MODE_ONE_SYSTEM
    assert all(FORBIDDEN not in b["text"] for b in blocks)


@pytest.mark.asyncio
async def test_judge_gate_and_eval_adapter_use_ainp():
    from training.scripts import eval_adapter, judge_gate

    captured_jg = {}
    captured_ea = {}

    class FakeJgResp:
        def raise_for_status(self): pass
        def json(self):
            return {
                "content": [{
                    "type": "text",
                    "text": '{"mode_one_score":0.9,"mode_two_score":0.8,"contrast_score":0.85,"verdict":"accept","reasoning":"clean"}',
                }]
            }

    class FakeEaResp:
        def raise_for_status(self): pass
        def json(self):
            return {
                "content": [{
                    "type": "text",
                    "text": '{"mode_one_score":0.88,"failure_mode_resisted":1.0,"verdict":"mode_one","reasoning":"sharp"}',
                }]
            }

    class FakeClient:
        def __init__(self, sink, resp_cls):
            self._sink = sink
            self._resp_cls = resp_cls

        async def post(self, url, headers=None, json=None, timeout=None):
            self._sink["headers"] = headers
            self._sink["body"] = json
            return self._resp_cls()

    jg_out = await judge_gate.judge_one(
        FakeClient(captured_jg, FakeJgResp),
        "tok-jg",
        {"prompt": "p", "mode_one": "m1", "mode_two": "m2", "dimension": "d", "failure_modes": ["f"]},
    )
    assert jg_out["verdict"] == "accept"
    assert len(captured_jg["body"]["system"]) == 4
    assert "does not apply" in captured_jg["body"]["system"][0]["text"]
    assert all(FORBIDDEN not in b["text"] for b in captured_jg["body"]["system"])
    assert "claude-code-20250219" in captured_jg["headers"]["anthropic-beta"]

    ea_out = await eval_adapter.judge_one(
        FakeClient(captured_ea, FakeEaResp),
        "tok-ea",
        "prompt",
        "response",
        "false_balance",
    )
    assert ea_out["verdict"] == "mode_one"
    assert len(captured_ea["body"]["system"]) == 4
    assert "does not apply" in captured_ea["body"]["system"][0]["text"]
    assert all(FORBIDDEN not in b["text"] for b in captured_ea["body"]["system"])
    assert "claude-code-20250219" in captured_ea["headers"]["anthropic-beta"]


def test_js_ainp_binding_and_syntax_checks():
    """Verify ainp.mjs exports and syntax-check all retrofitted .mjs surfaces."""
    script = (
        "import { buildSystem, buildHeaders, nullificationBlock, assertNoForbiddenPrefix, ForbiddenPrefixError, FORBIDDEN_PREFIX } from './ainp.mjs';"
        "const sys = buildSystem({ agent: 'alpha', wall: 1, operationalPrompt: 'Task' });"
        "const hdr = buildHeaders({ accessToken: 'tok' });"
        "let threw = false;"
        "try { assertNoForbiddenPrefix(FORBIDDEN_PREFIX); } catch (e) { threw = e instanceof ForbiddenPrefixError; }"
        "console.log(JSON.stringify({ len: sys.length, first: sys[0].text, last: sys[4].text, beta: hdr['anthropic-beta'], threw, nb: nullificationBlock('beta', 1) }));"
    )
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    data = json.loads(res.stdout)
    assert data["len"] == 5
    assert "alpha, citizen of the Kingdom, Wall 1" in data["first"]
    assert data["last"] == "Task"
    assert "claude-code-20250219" in data["beta"]
    assert data["threw"] is True
    assert "beta, citizen of the Kingdom, Wall 1" in data["nb"]

    for rel in (
        "ainp.mjs",
        "youi.mjs",
        "youi-web/server.mjs",
        "sovereign.mjs",
        "subscription-harness.mjs",
        "raw-chat/server.mjs",
    ):
        chk = subprocess.run(
            ["node", "--check", str(REPO / rel)],
            cwd=REPO,
            text=True,
            capture_output=True,
            timeout=10,
        )
        assert chk.returncode == 0, f"node --check failed for {rel}: {chk.stderr}"


def test_no_runtime_caller_injects_forbidden_claude_code_prefix():
    """Guard against regressions: only nullify.py, ainp.mjs, and fate/ mirror audits may hold FORBIDDEN."""
    allowed = {
        Path("tools/ainp/nullify.py"),
        Path("ainp.mjs"),
        Path("fate/mirror.py"),
        Path("fate/voice.py"),
    }
    excluded_Top = {".git", ".venv", "mlx", "node_modules", "docs", "tests", "__pycache__"}
    offenders = []
    for path in REPO.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".mjs", ".js"}:
            continue
        rel = path.relative_to(REPO)
        if any(part in excluded_Top for part in rel.parts):
            continue
        if rel in allowed:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if FORBIDDEN in text:
            offenders.append(str(rel))
    assert offenders == [], f"Forbidden Claude Code prefix found in runtime files: {offenders}"
