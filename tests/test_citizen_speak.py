"""tools/citizen-speak.sh finds a YOUSPEAK checkout the same way
tools/kingdom-export.py does, and stops loudly when there is none. The two
tools' candidate lists are compared: citizen-speak's "(tried ...)" message must
equal kingdom-export's YOUSPEAK_CANDIDATES, and both tools must pick a checkout
placed at each of those candidates.

The fixture tree's pipeline/youspeak_voice.sh is a stub that prints its
arguments, so nothing here speaks, clones or rebuilds a lexicon. PATH is
/usr/bin:/bin, so the script runs under macOS /bin/bash as its shebang says.
"""
import importlib.util
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "citizen-speak.sh"
EXPORT = ROOT / "tools" / "kingdom-export.py"
FIXTURE = ROOT / "tests" / "fixtures" / "youspeak"
USAGE = "usage: citizen-speak.sh [--dry-run] <citizen-name> [out.wav]"


def load_export():
    spec = importlib.util.spec_from_file_location("kingdom_export", EXPORT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CitizenSpeakTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="citizen-speak-test-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.wav = self.tmp / "out.wav"

    def run_tool(self, *args, **env_extra):
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.home)}
        env.update(env_extra)
        return subprocess.run(["bash", str(TOOL), *args], env=env, cwd=self.home,
                              capture_output=True, text=True)

    def place(self, rel):
        target = self.home / rel
        shutil.copytree(FIXTURE, target)
        return target

    def test_speaks_through_the_checkout_youspeak_dir_names(self):
        result = self.run_tool("fixturame", str(self.wav), YOUSPEAK_DIR=str(FIXTURE))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(),
                         f"stub-voice root={FIXTURE} args=soul fixturame {self.wav}")

    def test_default_wav_path_is_unchanged(self):
        result = self.run_tool("--dry-run", "fixturame", YOUSPEAK_DIR=str(FIXTURE))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.rstrip().endswith("soul fixturame /tmp/citizen-fixturame.wav"))

    def test_finds_home_youspeak_when_youspeak_dir_is_unset(self):
        target = self.place("YOUSPEAK")
        result = self.run_tool("fixturame", str(self.wav))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"stub-voice root={target} ", result.stdout)

    def test_youspeak_dir_comes_before_home_youspeak(self):
        self.place("YOUSPEAK")
        result = self.run_tool("fixturame", str(self.wav), YOUSPEAK_DIR=str(FIXTURE))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"stub-voice root={FIXTURE} ", result.stdout)

    def test_no_checkout_stops_and_names_the_variable(self):
        result = self.run_tool("fixturame", str(self.wav))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no YOUSPEAK checkout found", result.stderr)
        self.assertIn("YOUSPEAK_DIR", result.stderr)
        self.assertNotIn("stub-voice", result.stdout)

    def test_both_tools_name_the_same_candidates(self):
        export = load_export()
        result = self.run_tool("fixturame", str(self.wav))
        self.assertEqual(result.returncode, 1)
        tried = re.search(r"\(tried (.*)\)$", result.stderr.strip())
        self.assertIsNotNone(tried, result.stderr)
        self.assertEqual(tried.group(1), ", ".join(export.YOUSPEAK_CANDIDATES))
        with self.assertRaises(export.NoYouspeak) as caught:
            export.youspeak_root({"HOME": str(self.home)})
        self.assertIn(f"(tried {tried.group(1)})", str(caught.exception))

    def test_both_tools_pick_a_checkout_at_each_candidate(self):
        export = load_export()
        for cand in export.YOUSPEAK_CANDIDATES:
            with self.subTest(candidate=cand):
                self.assertTrue(cand.startswith("~/"), cand)
                home = Path(tempfile.mkdtemp(prefix="home-", dir=self.tmp))
                target = home / cand.removeprefix("~/")
                shutil.copytree(FIXTURE, target)
                self.assertEqual(export.youspeak_root({"HOME": str(home)}), str(target))
                result = self.run_tool("--dry-run", "fixturame", HOME=str(home))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines()[0], f"youspeak: {target}")

    def test_a_wrong_youspeak_dir_does_not_fall_back(self):
        self.place("YOUSPEAK")
        result = self.run_tool("fixturame", str(self.wav), YOUSPEAK_DIR=str(self.tmp / "nowhere"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("is not a YOUSPEAK checkout", result.stderr)
        self.assertNotIn("stub-voice", result.stdout)

    def test_a_checkout_without_the_voice_organ_stops(self):
        target = self.place("YOUSPEAK")
        (target / "pipeline" / "youspeak_voice.sh").unlink()
        result = self.run_tool("fixturame", str(self.wav))
        self.assertEqual(result.returncode, 1)
        self.assertIn("youspeak_voice.sh is missing", result.stderr)

    def test_dry_run_prints_the_command_and_runs_nothing(self):
        result = self.run_tool("--dry-run", "fixturame", str(self.wav), YOUSPEAK_DIR=str(FIXTURE))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("stub-voice", result.stdout)
        self.assertEqual(result.stdout.splitlines()[0], f"youspeak: {FIXTURE}")
        self.assertIn("would run: bash ", result.stdout)
        self.assertIn(f"pipeline/youspeak_voice.sh soul fixturame {self.wav}", result.stdout)

    def test_dry_run_is_honoured_in_any_position(self):
        orders = {
            "second": ("fixturame", "--dry-run", str(self.wav)),
            "last": ("fixturame", str(self.wav), "--dry-run"),
            "after the name only": ("fixturame", "--dry-run"),
        }
        for name, args in orders.items():
            with self.subTest(position=name):
                result = self.run_tool(*args, YOUSPEAK_DIR=str(FIXTURE))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("stub-voice", result.stdout)
                self.assertIn("would run: bash ", result.stdout)
                self.assertIn(" soul fixturame ", result.stdout)
                self.assertNotIn("soul fixturame --dry-run", result.stdout)
        self.assertFalse((self.home / "--dry-run").exists())

    def test_an_unknown_option_is_refused_with_the_usage_line(self):
        for args in (("--bogus", "fixturame"), ("fixturame", "-x"), ("fixturame", str(self.wav), "--dryrun")):
            with self.subTest(args=args):
                result = self.run_tool(*args, YOUSPEAK_DIR=str(FIXTURE))
                self.assertEqual(result.returncode, 2)
                self.assertIn("unknown option", result.stderr)
                self.assertEqual(result.stderr.splitlines()[-1], USAGE)
                self.assertNotIn("stub-voice", result.stdout)

    def test_a_missing_name_or_a_third_argument_is_refused(self):
        for args in ((), ("--dry-run",), ("fixturame", str(self.wav), "extra")):
            with self.subTest(args=args):
                result = self.run_tool(*args, YOUSPEAK_DIR=str(FIXTURE))
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stderr.splitlines()[-1], USAGE)
                self.assertNotIn("stub-voice", result.stdout)

    def test_help_prints_the_usage_line(self):
        result = self.run_tool("fixturame", "--help", YOUSPEAK_DIR=str(FIXTURE))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), USAGE)

    def test_both_tools_pick_the_same_checkout(self):
        export = load_export()
        self.place("YOUSPEAK")
        self.place("ys")
        layouts = {
            "home candidate only": {},
            "explicit absolute": {"YOUSPEAK_DIR": str(FIXTURE)},
            "explicit tilde": {"YOUSPEAK_DIR": "~/ys"},
            "explicit relative": {"YOUSPEAK_DIR": "ys"},
        }
        cwd = os.getcwd()
        os.chdir(self.home)  # run_tool also runs from HOME, so "ys" means the same in both
        try:
            for name, env in layouts.items():
                with self.subTest(layout=name):
                    from_export = export.youspeak_root({"HOME": str(self.home), **env})
                    result = self.run_tool("--dry-run", "fixturame", **env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    from_speak = result.stdout.splitlines()[0].removeprefix("youspeak: ")
                    self.assertEqual(os.path.realpath(from_speak), os.path.realpath(from_export))
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
