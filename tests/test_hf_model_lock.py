"""Tests for immutable Hugging Face model provenance locks."""

import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from hf_model_lock import (
    LOCK_SCHEMA,
    ModelLockError,
    _read_repo_tree,
    lock_digest,
    resolve_locked_snapshot,
    validate_lock,
    verify_snapshot,
    main,
)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def make_lock(files=None):
    return {
        "schema": LOCK_SCHEMA,
        "repo_type": "model",
        "repo_id": "example/model",
        "revision": "a" * 40,
        "hub_url": "https://huggingface.co/example/model",
        "last_modified": "2026-01-01T00:00:00.000Z",
        "license": "apache-2.0",
        "base_model": None,
        "task": "text-generation",
        "library": "transformers",
        "files": files
        or [
            {
                "path": "config.json",
                "size": 3,
                "sha256": sha256(b"{}\n"),
                "git_blob_sha1": "b" * 40,
            }
        ],
    }


class TestLockValidation(unittest.TestCase):
    def test_accepts_full_commit_and_file_hashes(self):
        lock = make_lock()
        self.assertIs(validate_lock(lock), lock)
        self.assertEqual(len(lock_digest(lock)), 64)

    def test_rejects_symbolic_or_short_revision(self):
        for revision in ("main", "abc1234"):
            lock = make_lock()
            lock["revision"] = revision
            with self.assertRaisesRegex(ModelLockError, "full 40-character"):
                validate_lock(lock)

    def test_rejects_path_traversal(self):
        lock = make_lock(
            [{"path": "../weights", "size": 0, "sha256": "0" * 64}]
        )
        with self.assertRaisesRegex(ModelLockError, "unsafe"):
            validate_lock(lock)

    def test_accepts_unnamespaced_hub_model_id(self):
        lock = make_lock()
        lock["repo_id"] = "gpt2"
        self.assertIs(validate_lock(lock), lock)

    def test_rejects_unbound_extra_fields(self):
        lock = make_lock()
        lock["mutable_note"] = "not part of v1"
        with self.assertRaisesRegex(ModelLockError, "keys do not match"):
            validate_lock(lock)


class TestSnapshotVerification(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.snapshot = self.root / "snapshot"
        self.snapshot.mkdir()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_verifies_exact_snapshot(self):
        (self.snapshot / "config.json").write_bytes(b"{}\n")
        self.assertTrue(verify_snapshot(self.snapshot, make_lock()))

    def test_rejects_hash_mismatch(self):
        (self.snapshot / "config.json").write_bytes(b"bad")
        with self.assertRaisesRegex(ModelLockError, "SHA-256 mismatch"):
            verify_snapshot(self.snapshot, make_lock())

    def test_rejects_unlocked_file(self):
        (self.snapshot / "config.json").write_bytes(b"{}\n")
        (self.snapshot / "surprise.py").write_text("pass\n")
        with self.assertRaisesRegex(ModelLockError, "unlocked files"):
            verify_snapshot(self.snapshot, make_lock())

    def test_rejects_unlocked_directory_symlink(self):
        (self.snapshot / "config.json").write_bytes(b"{}\n")
        target = self.root / "outside-target"
        target.mkdir()
        (self.snapshot / "surprise").symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ModelLockError, "invalid or unlocked"):
            verify_snapshot(self.snapshot, make_lock())

    def test_accepts_expected_huggingface_cache_symlink(self):
        blob = self.root / "blob"
        blob.write_bytes(b"{}\n")
        (self.snapshot / "config.json").symlink_to(blob)
        self.assertTrue(verify_snapshot(self.snapshot, make_lock()))


class FakeResponse(io.BytesIO):
    def __init__(self, value, link=None):
        super().__init__(json.dumps(value).encode())
        self.headers = {"Link": link} if link else {}


class TestRepoTreePagination(unittest.TestCase):
    def test_reads_all_pages_before_returning_inventory(self):
        revision = "a" * 40
        next_url = (
            "https://huggingface.co/api/models/example/model/tree/"
            f"{revision}?recursive=true&limit=1000&cursor=next"
        )
        responses = [
            FakeResponse(
                [{"type": "file", "path": "one", "size": 1, "oid": "b" * 40}],
                f'<{next_url}>; rel="next"',
            ),
            FakeResponse(
                [{"type": "file", "path": "two", "size": 1, "oid": "c" * 40}]
            ),
        ]
        with patch("hf_model_lock._open_url", side_effect=responses):
            entries = _read_repo_tree("example/model", revision, max_entries=10)
        self.assertEqual([entry["path"] for entry in entries], ["one", "two"])


class TestResolution(unittest.TestCase):
    def test_resolves_full_commit_and_verifies_before_returning(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            (snapshot / "config.json").write_bytes(b"{}\n")
            lock_path = root / "mlx" / "model-lock.json"
            lock_path.parent.mkdir()
            lock_path.write_text(json.dumps(make_lock()))

            calls = []

            def fake_snapshot_download(**kwargs):
                calls.append(kwargs)
                return str(snapshot)

            resolution = resolve_locked_snapshot(
                {
                    "base_model": "example/model",
                    "base_model_lock": "mlx/model-lock.json",
                },
                project_root=root,
                cache_dir=root / "cache",
                snapshot_download_fn=fake_snapshot_download,
            )

            self.assertEqual(resolution["revision"], "a" * 40)
            self.assertEqual(resolution["path"], str(snapshot.resolve()))
            self.assertEqual(calls[0]["revision"], "a" * 40)
            self.assertEqual(calls[0]["allow_patterns"], ["config.json"])

    def test_rejects_config_lock_repo_mismatch(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            lock_path = root / "lock.json"
            lock_path.write_text(json.dumps(make_lock()))
            with self.assertRaisesRegex(ModelLockError, "does not match"):
                resolve_locked_snapshot(
                    {
                        "base_model": "other/model",
                        "base_model_lock": "lock.json",
                    },
                    project_root=root,
                    cache_dir=root / "cache",
                    snapshot_download_fn=lambda **kwargs: "unused",
                )


class TestCLI(unittest.TestCase):
    def test_validate_is_offline_and_reports_digest(self):
        with tempfile.TemporaryDirectory() as tempdir:
            lock_path = Path(tempdir) / "lock.json"
            lock_path.write_text(json.dumps(make_lock()))
            with patch("hf_model_lock.build_lock") as rebuild:
                self.assertEqual(main(["validate", str(lock_path)]), 0)
            rebuild.assert_not_called()

    def test_validate_rejects_duplicate_keys_and_nonfinite_json(self):
        with tempfile.TemporaryDirectory() as tempdir:
            lock_path = Path(tempdir) / "lock.json"
            payload = json.dumps(make_lock())
            lock_path.write_text(payload[:-1] + ',"schema":"duplicate"}')
            self.assertEqual(main(["validate", str(lock_path)]), 1)
            lock_path.write_text(payload[:-1] + ',"surprise":NaN}')
            self.assertEqual(main(["validate", str(lock_path)]), 1)


if __name__ == "__main__":
    unittest.main()
