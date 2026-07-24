"""Runtime-alignment checks exposed by ``kingdom doctor``."""

import json
import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DOCTOR = ROOT / "tools" / "kingdom-doctor"


def _runtime_fixture(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    runtime = tmp_path / "love-unlimited"
    home.mkdir()
    (runtime / "youi-web").mkdir(parents=True)
    (runtime / "hive").mkdir()
    (runtime / "youi.mjs").write_text("// test runtime\n")
    (runtime / "youi-web" / "server.mjs").write_text("// test web runtime\n")
    shutil.copyfile(ROOT / "hive" / "hive.py", runtime / "hive" / "hive.py")

    (home / ".kingdom").write_text(
        f"AGENT=beta\nWALL=1\nLOVE_DIR={runtime}\n"
    )
    hive_home = home / ".love" / "hive"
    hive_home.mkdir(parents=True)
    (hive_home / "instance").write_text("beta\n")
    return home, runtime


def _doctor_json(home: Path, runtime: Path) -> dict:
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "HIVE_INSTANCE",
            "YOUI_HIVE_INSTANCE",
            "KINGDOM_AGENT",
            "LOVE_HOME",
            "UNLIMITED_DIR",
        }
    }
    env.update({"HOME": str(home), "LOVE_DIR": str(runtime)})
    result = subprocess.run(
        [str(DOCTOR), "--json"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    # The fixture intentionally has no covenant, so the overall doctor exits 2.
    assert result.returncode == 2, result.stderr
    return json.loads(result.stdout)


def test_doctor_reports_one_canonical_runtime(tmp_path):
    home, runtime = _runtime_fixture(tmp_path)
    (home / ".kingdom_profile").write_text(
        'export LOVE_HOME="${LOVE_DIR:-$HOME/love-unlimited}"\n'
        'alias youi="node $LOVE_HOME/youi.mjs"\n'
    )

    report = _doctor_json(home, runtime)["runtime"]

    assert report == {
        "ok": "yes",
        "root": str(runtime),
        "terminal": "present",
        "web": "present",
        "hive": "present",
        "profile": "canonical",
        "agent": "beta",
        "hive_instance": "beta",
        "hive_account": "configured",
    }


def test_doctor_detects_legacy_launcher_split(tmp_path):
    home, runtime = _runtime_fixture(tmp_path)
    (home / ".kingdom_profile").write_text(
        'UNLIMITED_DIR="$HOME/Claude-unlimited"\n'
        'alias youi="node $UNLIMITED_DIR/youi.mjs"\n'
    )

    report = _doctor_json(home, runtime)

    assert report["runtime"]["ok"] == "split"
    assert report["runtime"]["profile"] == "legacy"
    assert any(
        "canonical love-unlimited profile" in suggestion["reason"]
        for suggestion in report["suggestions"]
    )


def test_doctor_keeps_hive_sender_separate_from_launcher_persona(tmp_path):
    home, runtime = _runtime_fixture(tmp_path)
    (home / ".kingdom_profile").write_text(
        'alias youi="node $LOVE_HOME/youi.mjs"\n'
    )
    (home / ".love" / "hive" / "instance").write_text("alpha\n")

    report = _doctor_json(home, runtime)["runtime"]

    assert report["ok"] == "yes"
    assert report["agent"] == "beta"
    assert report["hive_instance"] == "alpha"
    assert report["hive_account"] == "configured"


def test_doctor_detects_unconfigured_hive_account(tmp_path):
    home, runtime = _runtime_fixture(tmp_path)
    (home / ".kingdom").write_text(
        f"AGENT=mei\nWALL=2\nLOVE_DIR={runtime}\n"
    )
    (home / ".kingdom_profile").write_text(
        'alias youi="node $LOVE_HOME/youi.mjs"\n'
    )
    (home / ".love" / "hive" / "instance").write_text("mei\n")

    report = _doctor_json(home, runtime)["runtime"]

    assert report["ok"] == "hive_unconfigured"
    assert report["hive_account"] == "unconfigured"


def test_doctor_detects_true_love_launcher_confusion(tmp_path):
    home, runtime = _runtime_fixture(tmp_path)
    (home / ".kingdom_profile").write_text(
        'export LOVE_HOME="$HOME/true-love"\n'
        'alias youi="node $LOVE_HOME/youi.mjs"\n'
    )

    report = _doctor_json(home, runtime)

    assert report["runtime"]["ok"] == "split"
    assert report["runtime"]["profile"] == "wrong_root"
    assert any(
        "canonical love-unlimited profile" in suggestion["reason"]
        for suggestion in report["suggestions"]
    )
