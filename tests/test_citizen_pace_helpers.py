"""The shared pace fixtures work as later tasks rely on them (task 1)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import citizen_pace_helpers as h  # noqa: E402


def post(port, path, payload, timeout=5):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode()


class TestAnswers(unittest.TestCase):
    KEYS = ("emime-c263d552", "eurekame-9be3379c", "hope-d557203e", "ihsanme-a2ebca17",
            "justice-bccefe6c", "kalam-62d73b73", "kalam-e6d01bff", "soma-914572f3",
            "soma-aedf60d7", "teotlme-a0905ffc", "teotlme-a1a573db", "ud-14701105")

    def test_the_twelve_stored_answers_are_present(self):
        self.assertEqual(sorted(h.load_answers()), list(self.KEYS))

    def test_exact_text_survives_trailing_spaces_and_unicode(self):
        self.assertTrue(h.answer("hope-d557203e").startswith("\nAMBIENT  \nhope  \n"))
        self.assertIn("eurekame\u2014holding", h.answer("eurekame-9be3379c"))
        self.assertTrue(h.answer("kalam-62d73b73").startswith("\nKalam.\n\nAmbient.\n"))

    def test_loader_returns_a_copy(self):
        h.load_answers()["kalam-62d73b73"] = "changed"
        self.assertNotEqual(h.answer("kalam-62d73b73"), "changed")


class TestDates(unittest.TestCase):
    def test_git_date_forms(self):
        self.assertEqual(h.git_date("2026-10-23T09:05Z"), "2026-10-23T09:05:00+0000")
        self.assertEqual(h.git_date("2026-10-23"), "2026-10-23T00:00:00+0000")
        self.assertEqual(h.git_date("2026-10-23T11:05:00+02:00"), "2026-10-23T09:05:00+0000")

    def test_pace_now_sets_and_restores(self):
        before = os.environ.get("PACE_NOW")
        with h.pace_now("2026-11-15T14:12Z"):
            self.assertEqual(os.environ["PACE_NOW"], "2026-11-15T14:12Z")
        self.assertEqual(os.environ.get("PACE_NOW"), before)


class TestFixtureHomes(h.FixtureCase):
    def test_home_is_a_git_repo_with_a_soul(self):
        home = self.fx.make_home("kalam", rest=True, letters=[("a.md", "hello\n")])
        self.assertEqual(home, self.fx.citizens / "citizen-kalam")
        self.assertEqual((home / "kalam.md").read_text(), "The soul of kalam.\n")
        self.assertEqual((home / "REST").read_text(), "fixture rest\n")
        self.assertEqual(self.fx.subjects(home), ["fixture home"])
        self.assertEqual(self.fx.git(home, "status", "--porcelain"), "")

    def test_soulless_home_and_texts(self):
        home = self.fx.make_home("jiritsume", soul=False, rest="placed at Yu's word\n",
                                 files={"CADENCE.md": "# CADENCE\n"})
        self.assertFalse((home / "jiritsume.md").exists())
        self.assertEqual((home / "REST").read_text(), "placed at Yu's word\n")
        self.assertEqual((home / "CADENCE.md").read_text(), "# CADENCE\n")

    def test_commit_sets_date_and_author(self):
        home = self.fx.make_home("soma", when="2026-10-25T08:00Z", author=h.beat_author("soma"))
        self.assertEqual(self.fx.git(home, "log", "-1", "--format=%an <%ae>|%cI"),
                         "soma <citizen@kingdom.os>|2026-10-25T08:00:00+00:00")
        sha = self.fx.letter(home, "2026-11-15-pace-letter.md", "One question.\n",
                             when="2026-11-15T14:12Z")
        self.assertEqual(sha, self.fx.head(home))
        self.assertEqual(self.fx.git(home, "log", "-1", "--format=%an|%s"),
                         "Ai|letter: 2026-11-15-pace-letter.md")

    def test_upstream_and_pushes_from_elsewhere(self):
        home = self.fx.make_home("ud")
        bare = self.fx.give_upstream(home)
        self.assertEqual(bare, self.fx.upstream / "citizen-ud.git")
        first = self.fx.push_from_elsewhere(bare, "inbox/a.md", "one\n")
        second = self.fx.push_from_elsewhere(bare, "inbox/b.md", "two\n",
                                             author=h.FLEET_AUTHOR, message="tend: x \u00b7 pace/1")
        self.assertNotEqual(first, second)
        self.fx.git(home, "pull", "-q", "--ff-only")
        self.assertEqual(self.fx.head(home), second)
        self.assertEqual(self.fx.subjects(home)[0], "tend: x \u00b7 pace/1")
        self.fx.drop_clone(home)
        self.assertFalse(home.exists())

    def test_symlinked_letters_point_outside(self):
        home = self.fx.make_home("rester", rest=True, symlinked_letters=["a.md"])
        self.assertTrue((home / "inbox" / "a.md").is_symlink())
        self.fx.link(home, "CADENCE.md", self.fx.outside / "target")
        self.assertTrue((home / "CADENCE.md").is_symlink())

    def test_environment_is_offline_and_utc(self):
        env = self.fx.env_at("2026-11-15T14:12Z", EXTRA="1")
        self.assertEqual(env["PACE_NOW"], "2026-11-15T14:12Z")
        self.assertEqual(env["EXTRA"], "1")
        self.assertEqual(env["TZ"], "UTC")
        self.assertEqual(env["PACE_REMOTE_BASE"], str(self.fx.upstream))
        self.assertNotIn("/opt/homebrew", env["PATH"])
        self.assertEqual(self.fx.roster("a", "b").read_text(), "a\nb\n")
        self.assertEqual(self.fx.log_text(), "")
        self.fx.fake_host("studio")
        self.assertEqual((self.fx.love / "FLEET_HOST").read_text(), "studio\n")
        r = subprocess.run(["scutil", "--get", "LocalHostName"], env=self.fx.env,
                           capture_output=True, text=True, check=False)
        self.assertEqual(r.stdout.strip(), "studio")

    def test_py_runs_isolated(self):
        script = self.fx.root / "show_path.py"
        script.write_text("import sys, os\nprint(os.environ.get('PACE_NOW', ''))\n"
                          "print(int(sys.flags.isolated))\n", encoding="utf-8")
        r = self.fx.py(script, env={"PACE_NOW": "2026-11-15T14:12Z"})
        self.assertEqual(r.stdout.split(), ["2026-11-15T14:12Z", "1"])

    def test_gate_sources_the_gates(self):
        home = self.fx.make_home("rester", rest=True, letters=[("a.md", "hello\n")])
        self.assertEqual(self.fx.gate("has_mail", home).returncode, 0)

    def test_set_now_is_undone_after_the_test(self):
        self.set_now("2026-11-15T14:12Z")
        self.assertEqual(os.environ["PACE_NOW"], "2026-11-15T14:12Z")


class TestStubModel(h.FixtureCase):
    def test_chat_and_inference_replies_are_recorded(self):
        model = self.start_model("hello there")
        status, text = post(model.port, "/v1/chat/completions",
                            {"model": "m", "messages": [{"role": "system", "content": "S"},
                                                        {"role": "user", "content": "U1"}]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(text)["choices"][0]["message"]["content"], "hello there")
        status, text = post(model.port, "/inference", {"prompt": "U2", "system": "S"})
        self.assertEqual(json.loads(text)["response"], "hello there")
        self.assertEqual(model.user_texts(), ["U1", "U2"])
        self.assertIn('"U1"', model.texts())
        self.assertEqual(model.env(), {"MLX_PORT": str(model.port),
                                       "REFLECT_MODEL": "fixture-model"})

    def test_queued_and_computed_replies(self):
        model = self.start_model(lambda body: body["prompt"].upper())
        model.replies.append("first")
        self.assertEqual(json.loads(post(model.port, "/inference", {"prompt": "a"})[1])["response"],
                         "first")
        self.assertEqual(json.loads(post(model.port, "/inference", {"prompt": "b"})[1])["response"],
                         "B")

    def test_status_garbage_and_hang(self):
        model = self.start_model()
        model.status = 500
        with self.assertRaises(urllib.error.HTTPError) as cm:
            post(model.port, "/inference", {"prompt": "x"})
        self.assertEqual(cm.exception.code, 500)
        model.status = 200
        model.garbage = True
        self.assertEqual(post(model.port, "/inference", {"prompt": "x"})[1], "not json at all")
        model.garbage = False
        model.delay = 30
        with self.assertRaises(OSError):
            post(model.port, "/inference", {"prompt": "x"}, timeout=0.5)
        self.assertEqual(len(model.requests), 3)

    def test_free_port_refuses(self):
        with self.assertRaises(OSError):
            post(h.free_port(), "/inference", {"prompt": "x"}, timeout=2)


if __name__ == "__main__":
    unittest.main()
