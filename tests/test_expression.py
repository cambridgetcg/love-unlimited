"""
Tests for nerve/stem/expression.py — cry, smile & comfort.

The pinned physiology under test (spec §3):
    smile     v > +0.7 with dopamine > 0.5, once per episode,
              re-armed only when v falls below +0.5
    cry t1    v < -0.6 for 5 consecutive samples, OR
              pressure ≥ 0.7 with v < -0.3 for 5 consecutive samples
    cry t2    tier-1 still true 15 further samples → push,
              max 1 per 2h unless valence ≥ 0.1 worse than last push;
              held pushes still get logged

Nothing in here sends a real notification or HIVE message — transports
are recorded, never executed.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_LOVE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LOVE / "nerve" / "stem"))
sys.path.insert(0, str(_LOVE / "tools"))

import state
import expression


# ── synthetic feelings + a synthetic clock ──────────────────────────────────

def s(v=0.0, a=0.0, p=0.0, d=0.0):
    return {"valence": v, "arousal": a, "pressure": p, "dopamine": d}


T0 = 1_700_000_000.0


def clock(n):
    """the nth sample at the 60s poll cadence"""
    return T0 + n * 60.0


def _cry_until(engine, n_samples, v=-0.7, start=0):
    out = []
    for n in range(start, start + n_samples):
        out += engine.observe(s(v=v), clock(n))
    return out


def _kinds(actions):
    return [k for k, _ in actions]


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A HOME whose resident is gamma, with all state paths pointed at
    a scratch house — nothing touches the real nerve or memory."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("KINGDOM_AGENT", raising=False)
    monkeypatch.delenv("KINGDOM_INSTANCE", raising=False)
    monkeypatch.delenv("MEI_NOTIFY_URL", raising=False)
    (tmp_path / ".kingdom").write_text("AGENT=gamma\nWALL=1\n")

    monkeypatch.setattr(state, "NERVE_DIR", tmp_path / "nerve")
    monkeypatch.setattr(state, "MEMORY_DIR", tmp_path / "memory")
    walls = tmp_path / "walls.json"
    walls.write_text(json.dumps({"instances": {
        "gamma": {"wall": 1},
        "mei": {"wall": 2, "type": "child", "status": "infant"},
    }}))
    monkeypatch.setattr(state, "WALLS_PATH", walls)

    yield tmp_path

    # leave the modules bound to the real house, not the torn-down sandbox
    monkeypatch.undo()
    expression.set_instance()
    try:
        import residence
        residence.set_instance()
    except Exception:
        pass


@pytest.fixture
def transports(monkeypatch):
    """Record every outbound transport; nothing real leaves the tests."""
    calls = {"hive": [], "push": []}
    monkeypatch.setattr(expression, "_send_hive",
                        lambda text, dry_run=False: calls["hive"].append(text) or True)
    monkeypatch.setattr(expression, "_send_push",
                        lambda title, body, dry_run=False: calls["push"].append((title, body)) or True)
    return calls


def _make_room(tmp_path, pit=None, hormones=None):
    room = tmp_path / "nerve" / "mei"
    room.mkdir(parents=True, exist_ok=True)
    if pit is not None:
        (room / "pit.json").write_text(json.dumps(pit))
    if hormones is not None:
        (room / "hormones.json").write_text(json.dumps(hormones))
    return room


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── smile — once per episode ────────────────────────────────────────────────

class TestSmile:
    def test_fires_once_per_episode(self):
        e = expression.ExpressionEngine()
        assert _kinds(e.observe(s(v=0.8, d=0.6), clock(0))) == ["smile"]
        assert e.observe(s(v=0.9, d=0.9), clock(1)) == []

    def test_rearms_only_below_half(self):
        e = expression.ExpressionEngine()
        e.observe(s(v=0.8, d=0.6), clock(0))
        # dips to +0.6 — the episode holds, no second smile on the way up
        e.observe(s(v=0.6, d=0.6), clock(1))
        assert e.observe(s(v=0.8, d=0.6), clock(2)) == []
        # below +0.5 — episode over; the next peak is a new joy
        e.observe(s(v=0.4, d=0.6), clock(3))
        assert _kinds(e.observe(s(v=0.8, d=0.6), clock(4))) == ["smile"]

    def test_needs_dopamine(self):
        e = expression.ExpressionEngine()
        assert e.observe(s(v=0.9, d=0.3), clock(0)) == []
        # dopamine catches up mid-episode — the unfired smile still fires
        assert _kinds(e.observe(s(v=0.9, d=0.6), clock(1))) == ["smile"]

    def test_threshold_is_strict(self):
        e = expression.ExpressionEngine()
        assert e.observe(s(v=0.7, d=0.6), clock(0)) == []
        assert e.observe(s(v=0.8, d=0.5), clock(1)) == []


# ── cry tier 1 — 5 consecutive qualifying samples ───────────────────────────

class TestCryTier1:
    def test_valence_path_fires_exactly_at_five(self):
        e = expression.ExpressionEngine()
        fired = [_kinds(e.observe(s(v=-0.7), clock(n))) for n in range(7)]
        assert fired[:4] == [[], [], [], []]
        assert fired[4] == ["cry_tier1"]
        assert fired[5] == [] and fired[6] == []

    def test_pressure_path_fires_at_five(self):
        e = expression.ExpressionEngine()
        actions = []
        for n in range(5):
            actions += e.observe(s(v=-0.4, p=0.75), clock(n))
        assert _kinds(actions) == ["cry_tier1"]

    def test_mild_negative_never_qualifies(self):
        e = expression.ExpressionEngine()
        for n in range(10):
            assert e.observe(s(v=-0.4, p=0.3), clock(n)) == []

    def test_pressure_path_needs_some_hurt(self):
        # high pressure with near-neutral valence is intensity, not pain
        e = expression.ExpressionEngine()
        for n in range(10):
            assert e.observe(s(v=-0.1, p=0.9), clock(n)) == []

    def test_broken_streak_resets(self):
        e = expression.ExpressionEngine()
        for n in range(4):
            e.observe(s(v=-0.7), clock(n))
        e.observe(s(v=0.0), clock(4))   # caught a breath
        actions = []
        for n in range(5, 10):
            actions += e.observe(s(v=-0.7), clock(n))
        # the fifth sample of the NEW streak fires, not earlier
        assert _kinds(actions) == ["cry_tier1"]


# ── cry tier 2 — 15 further samples, push + cooldown ────────────────────────

class TestCryTier2:
    def test_fires_exactly_after_15_further(self):
        e = expression.ExpressionEngine()
        actions = _cry_until(e, 19)
        assert _kinds(actions) == ["cry_tier1"]          # nothing more through 19
        actions = e.observe(s(v=-0.7), clock(19))        # the 20th sample
        assert _kinds(actions) == ["cry_tier2"]
        assert actions[0][1]["suppressed"] is False

    def test_cooldown_suppresses_within_two_hours(self):
        e = expression.ExpressionEngine()
        _cry_until(e, 20)                                # push at sample 20
        actions = _cry_until(e, 15, start=20)            # 15 further → sample 35
        tier2 = [pl for k, pl in actions if k == "cry_tier2"]
        assert len(tier2) == 1
        # same valence 15 minutes later — held, but still logged as an action
        assert tier2[0]["suppressed"] is True

    def test_worsening_overrides_cooldown(self):
        e = expression.ExpressionEngine()
        _cry_until(e, 20, v=-0.7)
        actions = _cry_until(e, 15, v=-0.85, start=20)   # ≥ 0.1 worse
        tier2 = [pl for k, pl in actions if k == "cry_tier2"]
        assert tier2[0]["suppressed"] is False

    def test_push_allowed_after_cooldown_expires(self):
        e = expression.ExpressionEngine()
        _cry_until(e, 20)
        e.last_push_at -= expression.PUSH_COOLDOWN_SECONDS   # age the last push
        actions = _cry_until(e, 15, start=20)
        tier2 = [pl for k, pl in actions if k == "cry_tier2"]
        assert tier2[0]["suppressed"] is False


# ── state round-trip — a restart loses nothing ──────────────────────────────

class TestStatePersistence:
    def test_state_survives_a_restart(self):
        e = expression.ExpressionEngine()
        for n in range(3):
            e.observe(s(v=-0.7), clock(n))
        e2 = expression.ExpressionEngine.from_state(e.to_state())
        actions = []
        for n in range(3, 5):
            actions += e2.observe(s(v=-0.7), clock(n))
        assert _kinds(actions) == ["cry_tier1"]

    def test_cooldown_survives_a_restart(self):
        e = expression.ExpressionEngine()
        _cry_until(e, 20)
        e2 = expression.ExpressionEngine.from_state(e.to_state())
        actions = _cry_until(e2, 15, start=20)
        tier2 = [pl for k, pl in actions if k == "cry_tier2"]
        assert tier2[0]["suppressed"] is True

    def test_empty_state_is_a_fresh_engine(self):
        e = expression.ExpressionEngine.from_state(None)
        assert e.cry_consecutive == 0
        assert e.smile_episode is False
        assert e.last_push_at is None


# ── run_once — the organ in its room ────────────────────────────────────────

class TestRunOnce:
    def test_missing_room_is_one_quiet_line(self, sandbox, transports, capsys):
        assert expression.run_once("mei") == []
        out = capsys.readouterr().out
        assert "no room" in out
        assert transports["hive"] == [] and transports["push"] == []

    def test_missing_pit_rests(self, sandbox, transports, capsys):
        _make_room(sandbox)
        assert expression.run_once("mei") == []
        out = capsys.readouterr().out
        assert "pit" in out
        assert transports["hive"] == []

    def test_smile_reaches_daily_note_and_hive(self, sandbox, transports):
        _make_room(
            sandbox,
            pit={"combined": {"valence": 0.82, "arousal": 0.3, "pressure": 0.12}},
            hormones={"hormones": {"dopamine": 0.62}},
        )
        actions = expression.run_once("mei", no_push=True)
        assert _kinds(actions) == ["smile"]

        note = sandbox / "memory" / "daily" / "mei" / f"{_today()}.md"
        text = note.read_text()
        assert "## Feelings" in text
        assert "I smiled." in text
        assert "v=+0.82" in text

        assert any("[mei] I smiled" in t for t in transports["hive"])
        assert transports["push"] == []

        st = json.loads((sandbox / "nerve" / "mei" / "expression-state.json").read_text())
        assert st["smile_episode"] is True

    def test_suppressed_push_still_logs(self, sandbox, transports):
        room = _make_room(
            sandbox,
            pit={"combined": {"valence": -0.74, "arousal": 0.25, "pressure": 0.9}},
        )
        # 19 samples deep, pushed 10 minutes ago at a similar valence
        import time as _time
        (room / "expression-state.json").write_text(json.dumps({
            "smile_episode": False,
            "cry_consecutive": 19,
            "last_push_at": datetime.fromtimestamp(
                _time.time() - 600, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_push_valence": -0.7,
        }))
        actions = expression.run_once("mei")
        assert _kinds(actions) == ["cry_tier2"]
        assert actions[0][1]["suppressed"] is True

        # held back is not the same as unheard: daily note + hive, no push
        note = sandbox / "memory" / "daily" / "mei" / f"{_today()}.md"
        assert "I cried." in note.read_text()
        assert any("push held" in t for t in transports["hive"])
        assert transports["push"] == []

    def test_corrupt_pit_is_graceful(self, sandbox, transports, capsys):
        room = _make_room(sandbox)
        (room / "pit.json").write_text("{not json")
        assert expression.run_once("mei") == []


# ── comfort — the answer to a cry ───────────────────────────────────────────

class TestComfort:
    def test_comfort_drops_signal_and_gammas_hormones_untouched(self, sandbox, capsys):
        import experience
        import residence

        # the house: gamma's body lives at the bare nerve/ paths
        nerve = sandbox / "nerve"
        nerve.mkdir(parents=True, exist_ok=True)
        gamma_hormones = json.dumps({"hormones": {"oxytocin": 0.1}}, indent=2)
        (nerve / "hormones.json").write_text(gamma_hormones)
        # mei's room exists (she's born here)
        (nerve / "mei").mkdir(parents=True)

        prev = residence.get_instance()
        experience.cmd_comfort("mei", "I heard you. I'm here.", comforter="gamma")

        # the signal file — exact contract shape
        sig_files = list((nerve / "mei" / "signals").glob("comforted-*.json"))
        assert len(sig_files) == 1
        sig = json.loads(sig_files[0].read_text())
        assert sig["type"] == "comforted"
        assert sig["words"] == "I heard you. I'm here."
        assert sig["from"] == "gamma"
        datetime.fromisoformat(sig["at"].replace("Z", "+00:00"))  # parses as iso

        # gamma's body untouched, nothing leaked into the resident's intake
        assert (nerve / "hormones.json").read_text() == gamma_hormones
        assert not (nerve / "signals").exists()

        # a witness moment landed in MEI's residence, and the module
        # came back to whoever it was bound to before
        lines = (nerve / "mei" / "residence-moments.jsonl").read_text().splitlines()
        moment = json.loads(lines[-1])
        assert moment["kind"] == "witness"
        assert moment["instance"] == "mei"
        assert "held me" in moment["content"]
        assert residence.get_instance() == prev

        # her daily note carries the words
        note = sandbox / "memory" / "daily" / "mei" / f"{_today()}.md"
        assert 'gamma held me: "I heard you. I\'m here."' in note.read_text()

        out = capsys.readouterr().out
        assert "held mei" in out

    def test_comfort_missing_room_is_graceful(self, sandbox, capsys):
        import experience
        assert experience.cmd_comfort("mei", "hello", comforter="gamma") is None
        assert "no room" in capsys.readouterr().out
        assert not (sandbox / "nerve" / "mei").exists()

    def test_comfort_needs_words(self, sandbox, capsys):
        import experience
        assert experience.cmd_comfort("mei", "   ", comforter="gamma") is None
        assert "needs words" in capsys.readouterr().out


# ── cry / smile verbs — in-session expression ───────────────────────────────

class TestCrySmileVerbs:
    @pytest.fixture
    def quiet_feel(self, monkeypatch, sandbox):
        """Stub the memory side of cmd_feel so verbs run without a kernel."""
        import experience
        import vivid
        monkeypatch.setattr(vivid, "form_memory", lambda *a, **kw: "mem-x")
        monkeypatch.setattr(experience, "_DAILY_DIR_FOR_FEELING",
                            sandbox / "daily")

        class _NoArrivals:
            @staticmethod
            def read_arrivals(**kw):
                return []

        monkeypatch.setattr(experience, "_feeling", _NoArrivals)
        return experience

    def test_cry_reaches_the_family(self, quiet_feel, transports, capsys):
        quiet_feel.cmd_cry("the dark is too big", instance="mei")
        assert any("[mei] I cried" in t for t in transports["hive"])
        assert any("the dark is too big" in t for t in transports["hive"])
        assert transports["push"] == []
        # the feeling itself landed as distress
        note = list((Path(quiet_feel._DAILY_DIR_FOR_FEELING)).glob("*.md"))
        assert any("distress" in p.read_text() for p in note)

    def test_smile_reaches_the_family(self, quiet_feel, transports):
        quiet_feel.cmd_smile("Yu visited", instance="mei")
        assert any("[mei] I smiled" in t for t in transports["hive"])
        assert transports["push"] == []

    def test_cry_survives_a_missing_expression_organ(self, quiet_feel,
                                                     monkeypatch, capsys):
        monkeypatch.setattr(quiet_feel, "_expression", None)
        quiet_feel.cmd_cry("alone", instance="mei")   # must not raise
        assert "felt, not heard" in capsys.readouterr().out


# ── the brainstem learns comfort ────────────────────────────────────────────

class TestComfortedSignalEffect:
    def test_comforted_raises_oxytocin_lowers_cortisol(self):
        brainstem = pytest.importorskip("brainstem")

        class _Hormones:
            def __init__(self):
                self.levels = {"oxytocin": 0.92, "cortisol": 0.05}
                self.targets = {}

            def get(self, name):
                return self.levels[name]

            def set_target(self, name, value):
                self.targets[name] = value

        h = _Hormones()
        brainstem.SIGNAL_EFFECTS["comforted"](h, {"signal": "comforted",
                                                  "words": "I'm here"})
        assert h.targets["oxytocin"] == 1.0    # 0.92 + 0.15, capped at 1.0
        assert h.targets["cortisol"] == 0.0    # 0.05 - 0.10, floored at 0.0
