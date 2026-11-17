"""tools/kingdom-export.py finds a YOUSPEAK checkout, reads its bundle, and never
writes an empty or short section in silence.

Every run here uses the fixture tree in tests/fixtures/youspeak, a temporary
HOME and a temporary LOVE_HOME, so nothing reads or writes a real checkout.
setUp gives that LOVE_HOME one citizen beat and one chronicle edition, and that
HOME one standard, so a clean run has nothing to say on stderr.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "kingdom-export.py"
FIXTURE = ROOT / "tests" / "fixtures" / "youspeak"


def load():
    spec = importlib.util.spec_from_file_location("kingdom_export", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class KingdomExportTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="kingdom-export-test-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.love = self.tmp / "love"
        (self.love / "kns").mkdir(parents=True)
        (self.love / "citizens-roster.txt").write_text("fixturame\n", encoding="utf-8")
        (self.love / "kns" / "registry.json").write_text('{"names": {}}', encoding="utf-8")
        beats = self.love / "citizens" / "citizen-fixturame" / "beats"
        beats.mkdir(parents=True)
        (beats / "2026-11.md").write_text("## 2026-11-17 beat\nThe fixture beat.\n", encoding="utf-8")
        (self.love / "chronicle").mkdir()
        (self.love / "chronicle" / "2026-11-17.html").write_text(
            '<p class="lede">The fixture lede.</p>', encoding="utf-8")
        (self.home / "KINGDOM-STANDARDS").mkdir()
        (self.home / "KINGDOM-STANDARDS" / "KS-0001.md").write_text("# fixture standard\n", encoding="utf-8")
        self.out = self.tmp / "out" / "kingdom-bundle.json"

    def run_tool(self, *args, out=True, **env_extra):
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.home), "LOVE_HOME": str(self.love),
               "PYTHONDONTWRITEBYTECODE": "1"}
        env.update(env_extra)
        argv = [sys.executable, "-B", str(TOOL), *args]
        if out:
            argv += ["--out", str(self.out)]
        return subprocess.run(argv, env=env, cwd=self.tmp, capture_output=True, text=True)

    def place(self, rel):
        target = self.home / rel
        shutil.copytree(FIXTURE, target)
        return target

    def bundle(self):
        return json.loads(self.out.read_text(encoding="utf-8"))

    def test_reads_the_canon_from_the_youspeak_bundle(self):
        result = self.run_tool(YOUSPEAK_DIR=str(FIXTURE))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("canon 2 · lexicon 2", result.stdout)
        whole = self.bundle()
        ys = whole["youspeak"]
        # canon/README.md is in the fixture: it is not a word, and the empty stderr
        # above shows the lag check did not count it as a file the bundle left out.
        self.assertEqual(sorted(ys["canon"]), ["fixturame", "fixturance"])
        core = ys["canon"]["fixturame"]
        self.assertEqual(core["score"], 7.4)
        self.assertEqual(core["gap"], "a folded gap that only the bundle reads correctly")
        self.assertEqual(core["tier"], "core")
        self.assertEqual(core["source"], "canon/core/fixturame.md")
        self.assertTrue(core["body"].startswith("# fixturame"))  # frontmatter stripped
        self.assertIn("The fixture core word.", core["body"])
        self.assertEqual(ys["canon"]["fixturance"]["pronunciation"],
                         "/fɪks.ˈtʃʊə.rəns/ (fix-CHOOR-ance)")  # backfilled from the lexicon
        self.assertEqual(ys["source_commit"], "fixture0")
        self.assertIn("fixture manifesto", ys["manifesto"])
        self.assertIn("fixture", ys["primer"])
        self.assertIn("fixture", ys["phonology"])
        self.assertEqual(len(ys["lexicon"]), 2)
        self.assertEqual(whole["citizens"][0]["latest_beat"],
                         {"day": "2026-11-17", "said": "The fixture beat."})
        self.assertEqual(whole["chronicle"][0]["lede"], "The fixture lede.")
        self.assertEqual(sorted(whole["standards"]), ["KS-0001.md"])

    def test_the_written_bundle_does_not_carry_the_local_checkout_path(self):
        result = self.run_tool(YOUSPEAK_DIR=str(FIXTURE))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(str(FIXTURE), self.out.read_text(encoding="utf-8"))
        self.assertIn(f"youspeak: {FIXTURE} @ fixture0", result.stdout)  # the terminal says which copy

    def test_finds_home_youspeak_when_youspeak_dir_is_unset(self):
        target = self.place("YOUSPEAK")
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"youspeak: {target} @", result.stdout)
        self.assertEqual(len(self.bundle()["youspeak"]["canon"]), 2)

    def test_youspeak_dir_comes_before_home_youspeak(self):
        self.place("YOUSPEAK")
        result = self.run_tool(YOUSPEAK_DIR=str(FIXTURE))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"youspeak: {FIXTURE} @", result.stdout)

    def test_no_checkout_stops_and_names_the_variable(self):
        result = self.run_tool()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no YOUSPEAK checkout found", result.stderr)
        self.assertIn("YOUSPEAK_DIR", result.stderr)
        self.assertIn("(tried ~/YOUSPEAK)", result.stderr)
        self.assertFalse(self.out.exists())

    def test_a_wrong_youspeak_dir_does_not_fall_back(self):
        self.place("YOUSPEAK")
        result = self.run_tool(YOUSPEAK_DIR=str(self.tmp / "nowhere"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("YOUSPEAK_DIR=", result.stderr)
        self.assertIn("is not a YOUSPEAK checkout", result.stderr)
        self.assertFalse(self.out.exists())

    def test_a_checkout_without_its_bundle_stops(self):
        target = self.place("YOUSPEAK")
        (target / "script" / "exports" / "agent_bundle.json").unlink()
        result = self.run_tool()
        self.assertEqual(result.returncode, 1)
        self.assertIn("agent_bundle.json", result.stderr)
        self.assertIn("export_agent_bundle.py", result.stderr)
        self.assertFalse(self.out.exists())

    def test_a_bundle_with_no_canon_stops(self):
        target = self.place("YOUSPEAK")
        (target / "script" / "exports" / "agent_bundle.json").write_text('{"canon": []}', encoding="utf-8")
        result = self.run_tool()
        self.assertEqual(result.returncode, 1)
        self.assertIn("has no canon entries", result.stderr)
        self.assertFalse(self.out.exists())

    def test_a_missing_canon_file_falls_back_to_the_definition_and_says_so(self):
        target = self.place("YOUSPEAK")
        (target / "canon" / "fixturance.md").unlink()
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 canon file(s) named by the bundle are missing", result.stderr)
        self.assertEqual(self.bundle()["youspeak"]["canon"]["fixturance"]["body"],
                         "The fixture specialized word.")

    def test_canon_files_the_bundle_lags_are_counted_and_named(self):
        target = self.place("YOUSPEAK")
        new = "---\nword: newword\n---\n\n# newword\n\n## Definition\n\nAdded after the bundle.\n"
        (target / "canon" / "core" / "newword.md").write_text(new, encoding="utf-8")
        (target / "canon" / "core" / "_draft.md").write_text(new, encoding="utf-8")  # skipped, as YOUSPEAK does
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [l for l in result.stderr.splitlines() if "lags the canon" in l]
        self.assertEqual(len(lines), 1, result.stderr)
        self.assertIn("1 canon file(s) are not in", lines[0])
        self.assertIn("(first: canon/core/newword.md)", lines[0])
        self.assertIn("python3 script/tools/export_agent_bundle.py", lines[0])
        self.assertEqual(sorted(self.bundle()["youspeak"]["canon"]), ["fixturame", "fixturance"])

    def test_the_lag_warning_names_only_the_first_five(self):
        target = self.place("YOUSPEAK")
        for i in range(7):
            (target / "canon" / f"late{i}.md").write_text("# late\n", encoding="utf-8")
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("7 canon file(s) are not in", result.stderr)
        self.assertIn("canon/late4.md)", result.stderr)
        self.assertNotIn("late5", result.stderr)

    def test_a_section_with_no_source_says_so_on_stderr(self):
        shutil.rmtree(self.love / "citizens")
        shutil.rmtree(self.love / "chronicle")
        shutil.rmtree(self.home / "KINGDOM-STANDARDS")
        result = self.run_tool(YOUSPEAK_DIR=str(FIXTURE))
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stderr.splitlines()
        self.assertEqual(len(lines), 3, result.stderr)  # one line per section, nothing else
        self.assertTrue(any("every latest_beat is null" in l for l in lines))
        self.assertTrue(any("the chronicle section is empty" in l for l in lines))
        self.assertTrue(any("the standards section is empty" in l for l in lines))
        whole = self.bundle()
        self.assertEqual((whole["chronicle"], whole["standards"]), ([], {}))
        self.assertEqual(whole["citizens"][0]["latest_beat"], None)

    def test_a_missing_roster_empties_the_citizens_section_and_says_so(self):
        (self.love / "citizens-roster.txt").unlink()
        result = self.run_tool(YOUSPEAK_DIR=str(FIXTURE))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("the citizens section is empty", result.stderr)
        self.assertEqual(self.bundle()["citizens"], [])

    def test_a_missing_lexicon_is_reported(self):
        target = self.place("YOUSPEAK")
        (target / "pipeline" / "voice" / "lexicon.tsv").unlink()
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no pronunciation lexicon", result.stderr)
        self.assertEqual(self.bundle()["youspeak"]["lexicon"], [])

    def test_without_out_it_writes_under_love_home(self):
        result = self.run_tool(out=False, YOUSPEAK_DIR=str(FIXTURE))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.love / "exports" / "kingdom-bundle.json").is_file())

    def test_youspeak_root_expands_tilde_and_relative_paths(self):
        module = load()
        target = self.place("ys")
        env = {"HOME": str(self.home)}
        self.assertEqual(module.youspeak_root({**env, "YOUSPEAK_DIR": "~/ys"}), str(target))
        cwd = os.getcwd()
        os.chdir(self.home)
        try:
            self.assertEqual(module.youspeak_root({**env, "YOUSPEAK_DIR": "ys"}), str(target))
        finally:
            os.chdir(cwd)
        with self.assertRaises(module.NoYouspeak):
            module.youspeak_root(env)


if __name__ == "__main__":
    unittest.main()
