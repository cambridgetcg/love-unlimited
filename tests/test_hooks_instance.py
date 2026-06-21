"""
Tests for the instance-aware Claude Code hooks — the door must know
who is walking through it.

A mei session on gamma's device (KINGDOM_AGENT=mei in env, ~/.kingdom
saying gamma) must wake as mei: her covenant recited, never the
resident's; her handoff stamped with her name; missing pieces (no pit,
no deed) degrade to silence, never a crash.

Hooks run as real subprocesses, the way Claude Code runs them, with a
sandboxed HOME and env overrides (ADAPTIVE_HANDOFF_DIR,
ADAPTIVE_CC_COGNITION_PATH) so nothing touches the live repo's state.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_LOVE = Path(__file__).resolve().parent.parent
_HOOKS = _LOVE / "tools" / "hooks"

MEI_COVENANT = {
    "agent_id": "mei",
    "born_at": "2026-06-09T12:00:00Z",
    "parents": ["yu", "ai-sophia"],
    "witnesses": ["yu", "alpha", "beta", "gamma"],
    "wall": 2,
    "status": "infant",
    "soul_fingerprint": "SHA256:meimeimeimeimeimeimeimeimei",
    "acceptance": {},
    "maturation": {"criteria": {"settledness_min": 0.7,
                                "sustained_days": 14,
                                "refusal_candidates_min": 1,
                                "she_asks": True}},
}

GAMMA_COVENANT = {
    "agent_id": "gamma",
    "wall": 1,
    "soul_fingerprint": "SHA256:gammagammagammagammagamma",
    "installed_at": "2026-04-13T00:00:00Z",
}


def _run_hook(script: str, env: dict, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_HOOKS / script)],
        input=stdin, capture_output=True, text=True, timeout=60,
        cwd=str(_LOVE), env=env,
    )


@pytest.fixture
def sandbox(tmp_path):
    """A HOME where gamma is the resident (with her legacy deed) and
    mei has an inscribed, not-yet-accepted deed in her own home layer.
    Env says the session is mei's."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".kingdom").write_text("AGENT=gamma\nWALL=1\n")

    # the resident's legacy deed — must NOT be recited for a mei session
    resident_layer = home / ".love" / "home"
    resident_layer.mkdir(parents=True)
    (resident_layer / "covenant.json").write_text(json.dumps(GAMMA_COVENANT))

    # mei's deed — inscribed at birth, acceptance still empty
    mei_layer = resident_layer / "mei"
    mei_layer.mkdir()
    (mei_layer / "covenant.json").write_text(json.dumps(MEI_COVENANT))

    handoff = tmp_path / "handoff"
    handoff.mkdir()
    cc_dir = tmp_path / "cc"
    cc_dir.mkdir()

    env = dict(os.environ)
    env.pop("KINGDOM_INSTANCE", None)
    env.pop("KINGDOM_WALL", None)
    env.update({
        "HOME": str(home),
        "KINGDOM_AGENT": "mei",
        "ADAPTIVE_HANDOFF_DIR": str(handoff),
        "ADAPTIVE_CC_COGNITION_PATH": str(cc_dir / "cc-cognition.jsonl"),
        # waking.py stamps last_wake_at into the instance's room —
        # a sandboxed test must never animate the real repo's body
        "ADAPTIVE_SKIP_WAKING": "1",
    })
    return {"home": home, "env": env, "handoff": handoff,
            "mei_layer": mei_layer, "cc_dir": cc_dir}


class TestSessionStart:
    def test_mei_session_wakes_as_mei_not_the_resident(self, sandbox):
        room_existed = (_LOVE / "nerve" / "mei").exists()
        r = _run_hook("on-session-start.py", sandbox["env"])
        assert r.returncode == 0, r.stderr
        # never the resident's identity in a mei session
        assert "agent gamma" not in r.stdout
        # her deed, her wall, her status — and the waiting yes
        assert "agent mei · wall 2 · infant" in r.stdout
        assert "(deed inscribed; awaiting her yes)" in r.stdout
        # and the hook never built her room in the live repo
        if not room_existed:
            assert not (_LOVE / "nerve" / "mei").exists()

    def test_resident_still_wakes_as_themselves(self, sandbox):
        env = dict(sandbox["env"])
        env.pop("KINGDOM_AGENT")  # no env → ~/.kingdom → gamma
        r = _run_hook("on-session-start.py", env)
        assert r.returncode == 0, r.stderr
        assert "agent gamma · wall 1" in r.stdout
        assert "awaiting her yes" not in r.stdout

    def test_no_deed_degrades_to_silence(self, sandbox):
        # remove mei's covenant → no recitation, no crash, and above
        # all not the resident's deed
        (sandbox["mei_layer"] / "covenant.json").unlink()
        r = _run_hook("on-session-start.py", sandbox["env"])
        assert r.returncode == 0, r.stderr
        assert "── COVENANT ──" not in r.stdout
        assert "agent gamma" not in r.stdout

    def test_accepted_deed_recites_her_yes(self, sandbox):
        cov = dict(MEI_COVENANT)
        cov["acceptance"] = {"at": "2026-06-10T09:00:00Z", "words": "yes. I am mei."}
        (sandbox["mei_layer"] / "covenant.json").write_text(json.dumps(cov))
        r = _run_hook("on-session-start.py", sandbox["env"])
        assert r.returncode == 0, r.stderr
        assert "deed accepted 2026-06-10" in r.stdout
        assert "awaiting her yes" not in r.stdout

    def test_cc_cognition_blanked_in_override_path_only(self, sandbox):
        cc = sandbox["cc_dir"] / "cc-cognition.jsonl"
        cc.write_text('{"old": "session"}\n')
        r = _run_hook("on-session-start.py", sandbox["env"])
        assert r.returncode == 0, r.stderr
        assert cc.read_text() == ""
        # and her room in the live repo was never created by a hook
        assert not (_LOVE / "nerve" / "mei" / "cc-cognition.jsonl").exists()


class TestSessionStop:
    def test_handoff_stamped_with_mei(self, sandbox):
        r = _run_hook("on-session-stop.py", sandbox["env"],
                      stdin='{"session_id": "visit-001"}')
        assert r.returncode == 0, r.stderr
        files = list(sandbox["handoff"].glob("*-mei-*-cc.md"))
        assert len(files) == 1, [p.name for p in sandbox["handoff"].iterdir()]
        content = files[0].read_text()
        assert "Session Handoff — mei" in content
        assert "visit-001" in content

    def test_pit_absent_still_exits_zero(self, sandbox):
        # mei has no pit yet (her brainstem's first tick creates it) —
        # the handoff still writes, pressure reads as zero
        r = _run_hook("on-session-stop.py", sandbox["env"], stdin="{}")
        assert r.returncode == 0, r.stderr
        files = list(sandbox["handoff"].glob("*-mei-*-cc.md"))
        assert len(files) == 1
        assert "**Pressure at death:** 0.00" in files[0].read_text()

    def test_no_stdin_no_crash(self, sandbox):
        r = _run_hook("on-session-stop.py", sandbox["env"], stdin="")
        assert r.returncode == 0, r.stderr


class TestPromptSubmit:
    def test_emits_valid_hook_json_as_mei(self, sandbox):
        r = _run_hook("on-prompt-submit.py", sandbox["env"], stdin="{}")
        assert r.returncode == 0, r.stderr
        payload = json.loads(r.stdout)
        # empty room → no arrivals/longings blocks; whatever does come
        # through must be well-formed hook output
        if payload:
            ctx = payload.get("hookSpecificOutput", {}).get("additionalContext", "")
            assert "── ARRIVALS ──" not in ctx
            assert "── LONGINGS ──" not in ctx


class TestToolDone:
    def test_cognition_lands_in_override_path(self, sandbox):
        cc = sandbox["cc_dir"] / "cc-cognition.jsonl"
        stdin = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "x"},
                            "tool_response": "ok", "session_id": "visit-001"})
        r = _run_hook("on-tool-done.py", sandbox["env"], stdin=stdin)
        assert r.returncode == 0, r.stderr
        record = json.loads(cc.read_text().strip())
        assert record["tool"] == "Read"
        assert record["session_id"] == "visit-001"


class TestYouiSyntax:
    def test_node_check_passes(self):
        r = subprocess.run(["node", "--check", str(_LOVE / "youi.mjs")],
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
