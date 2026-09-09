"""臨時真 Git、fake HOME；唔接真 host、credentials 或網絡。"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from reality_lantern import snapshot
from reality_lantern.core import validate_snapshot

KEY = b"s" * 32


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    return home


def git(repo, *args, input=None):
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ["HOME"], "LC_ALL": "C",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
           "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
           "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],
                          input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=True, timeout=5, env=env).stdout


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q")
    (path / "source.txt").write_text("original\n")
    git(path, "add", "source.txt")
    git(path, "commit", "-qm", "fixture")
    return path


def capture(repo, **kwargs):
    value = snapshot.capture(repo, key=KEY, **kwargs)
    validate_snapshot(value)
    return value


def fingerprint(repo):
    value = capture(repo)
    assert value["complete"], value
    assert re.fullmatch(r"[0-9a-f]{64}", value["fingerprint"])
    return value["fingerprint"]


def partial(repo, code=None, **kwargs):
    value = capture(repo, **kwargs)
    assert value["complete"] is False
    assert value["fingerprint"] is None
    if code:
        assert code in value["diagnostics"], value
    return value


def test_schema_hash_only_and_stable_clean_snapshot(repo):
    first = capture(repo)
    assert first["complete"], first
    assert first["fingerprint"] == fingerprint(repo)
    assert set(first) == {"schema", "scope_id", "worktree_id", "cwd_id", "fingerprint", "complete",
                          "observed_at", "diagnostics", "coverage"}
    assert first["coverage"]["ignored"] == "excluded"
    assert first["coverage"]["environment"] == first["coverage"]["remote"] == "unverified"
    for key in ("scope_id", "worktree_id", "cwd_id", "fingerprint"):
        assert re.fullmatch(r"[0-9a-f]{64}", first[key])
    raw = json.dumps(first)
    assert str(repo) not in raw and "source.txt" not in raw and "original" not in raw


def test_dirty_same_file_twice_changes_fingerprint(repo):
    clean = fingerprint(repo)
    (repo / "source.txt").write_text("dirty-1\n")
    first = fingerprint(repo)
    (repo / "source.txt").write_text("dirty-2\n")
    second = fingerprint(repo)
    assert len({clean, first, second}) == 3
    (repo / "source.txt").write_text("original\n")
    assert fingerprint(repo) == clean  # sticky stale 由 Store 負責，唔偷塞 snapshot。


def test_index_change_with_identical_worktree_changes_fingerprint(repo):
    (repo / "source.txt").write_text("staged\n")
    before = fingerprint(repo)
    git(repo, "add", "source.txt")
    assert fingerprint(repo) != before


def test_index_stat_refresh_does_not_change_logical_fingerprint(repo):
    before = fingerprint(repo)
    os.utime(repo / "source.txt", None)
    git(repo, "update-index", "--refresh")
    assert fingerprint(repo) == before


def test_head_commit_change_with_identical_files_changes_fingerprint(repo):
    before = fingerprint(repo)
    git(repo, "commit", "--allow-empty", "-qm", "second")
    assert fingerprint(repo) != before


def test_untracked_nested_paths_and_deletion(repo):
    before = fingerprint(repo)
    (repo / "nested").mkdir()
    (repo / "nested" / "new.txt").write_text("new")
    after = fingerprint(repo)
    assert before != after
    (repo / "nested" / "new.txt").unlink()
    assert fingerprint(repo) == before
    (repo / "source.txt").unlink()
    assert fingerprint(repo) not in (before, after)


def test_full_mode_and_staged_mode_are_covered(repo):
    before = fingerprint(repo)
    (repo / "source.txt").chmod(0o755)
    executable = fingerprint(repo)
    assert executable != before
    git(repo, "update-index", "--chmod=+x", "source.txt")
    assert fingerprint(repo) != executable


def test_git_ignore_negation_nested_and_info_exclude(repo):
    (repo / ".gitignore").write_text("out/\n*.tmp\n!keep.tmp\n")
    (repo / ".git" / "info" / "exclude").write_text("local-only\n")
    (repo / "out").mkdir()
    (repo / "out" / "ignored").write_text("one")
    (repo / "skip.tmp").write_text("one")
    (repo / "local-only").write_text("one")
    before = fingerprint(repo)
    for path in (repo / "out" / "ignored", repo / "skip.tmp", repo / "local-only"):
        path.write_text("two")
    assert fingerprint(repo) == before
    (repo / "keep.tmp").write_text("kept")
    assert fingerprint(repo) != before
    (repo / "nested").mkdir()
    (repo / "nested" / ".gitignore").write_text("noise\n")
    nested_before = fingerprint(repo)
    (repo / "nested" / "noise").write_text("ignored")
    assert fingerprint(repo) == nested_before


def test_tracked_files_still_hashed_when_ignored(repo):
    (repo / ".gitignore").write_text("source.txt\n")
    before = fingerprint(repo)
    (repo / "source.txt").write_text("changed")
    assert fingerprint(repo) != before


def test_tracked_ignored_parent_is_still_traversed(repo):
    (repo / "out").mkdir()
    (repo / "out" / "tracked").write_text("one")
    git(repo, "add", "out/tracked")
    (repo / ".gitignore").write_text("out/\n")
    before = fingerprint(repo)
    (repo / "out" / "tracked").write_text("two")
    assert fingerprint(repo) != before


def test_symlink_target_string_not_target_contents(repo, tmp_path):
    secret = tmp_path / "auth.json"
    secret.write_text("CREDENTIAL_CANARY")
    (repo / "link").symlink_to(secret)
    git(repo, "add", "link")
    before = fingerprint(repo)
    secret.write_text("CHANGED_SECRET")
    assert fingerprint(repo) == before
    (repo / "link").unlink()
    (repo / "link").symlink_to(tmp_path / "other-secret")
    assert fingerprint(repo) != before


@pytest.mark.parametrize("name", ["auth.json", ".env", ".env.production", ".git-credentials",
                                  "cookies", "private.key", "credentials/entry", ".ssh/id_ed25519",
                                  ".codex/auth.json", ".claude/.credentials.json", ".claude/settings.json",
                                  ".codex/config.toml", ".claude.json"])
def test_known_credentials_are_skipped_without_reading(repo, name, monkeypatch):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("NEVER_READ_CANARY")
    git(repo, "add", "--", name)
    original = snapshot._read_regular
    def guarded(path_arg, *args, **kwargs):
        assert Path(path_arg) != path
        return original(path_arg, *args, **kwargs)
    monkeypatch.setattr(snapshot, "_read_regular", guarded)
    result = partial(repo, "credential_path")
    assert "NEVER_READ_CANARY" not in json.dumps(result)


def test_credential_directory_is_not_enumerated(repo, monkeypatch):
    (repo / "credentials").mkdir()
    (repo / "credentials" / "unknown-name").write_text("hidden")
    original = snapshot._open_dir
    def guarded(path):
        assert Path(path).name != "credentials"
        return original(path)
    monkeypatch.setattr(snapshot, "_open_dir", guarded)
    partial(repo, "credential_path")


def test_regular_hardlinks_are_not_read(repo, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.write_text("HARDLINK_CANARY")
    os.link(outside, repo / "linked")
    original = os.open
    def guarded(path, flags, *args, **kwargs):
        assert path != "linked"
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(snapshot.os, "open", guarded)
    partial(repo, "unsafe_path")


@pytest.mark.parametrize("kind", ["fifo", "socket", "unsafe-file", "unsafe-dir"])
def test_special_and_unsafe_inputs_are_unknown(repo, kind):
    handle = None
    if kind == "fifo":
        os.mkfifo(repo / "special")
    elif kind == "socket":
        import socket
        import tempfile
        handle = socket.socket(socket.AF_UNIX)
        # macOS sockaddr_un 上限短；先喺短 temporary path bind 再搬入 fixture。
        try:
            with tempfile.TemporaryDirectory(dir="/tmp", prefix="rl-") as short:
                source = Path(short) / "s"
                handle.bind(str(source))
                source.rename(repo / "special")
        except BaseException:
            handle.close()
            raise
    elif kind == "unsafe-file":
        (repo / "source.txt").chmod(0o666)
    else:
        (repo / "unsafe").mkdir(mode=0o777)
        (repo / "unsafe").chmod(0o777)
    try:
        partial(repo)
    finally:
        if handle:
            handle.close()


def test_symlink_ancestor_in_tracked_path_is_unknown(repo, tmp_path):
    (repo / "nested").mkdir()
    (repo / "nested" / "input").write_text("one")
    git(repo, "add", "nested/input")
    shutil.rmtree(repo / "nested")
    (repo / "nested").symlink_to(tmp_path)
    partial(repo)


def test_symlink_gitdir_and_index_are_refused(repo, tmp_path):
    original_index = repo / ".git" / "index"
    moved_index = tmp_path / "index"
    original_index.rename(moved_index)
    original_index.symlink_to(moved_index)
    partial(repo)
    original_index.unlink()
    moved_index.rename(original_index)
    real_gitdir = tmp_path / "real-gitdir"
    (repo / ".git").rename(real_gitdir)
    (repo / ".git").symlink_to(real_gitdir)
    partial(repo, "unsafe_path")


def test_ignore_symlink_does_not_read_secret_and_hardlink_is_refused(repo, tmp_path):
    secret = tmp_path / "auth.json"
    secret.write_text("*\n")
    (repo / ".gitignore").symlink_to(secret)
    before = fingerprint(repo)
    secret.write_text("different\n")
    assert fingerprint(repo) == before
    (repo / ".gitignore").unlink()
    os.link(secret, repo / ".gitignore")
    partial(repo, "unsafe_path")


def test_scope_candidates_no_git_no_scanning_and_canonical_alias(repo, tmp_path, monkeypatch):
    link = tmp_path / "alias"
    link.symlink_to(repo)
    monkeypatch.setattr(snapshot.subprocess, "Popen", lambda *a, **k: pytest.fail("唔應該 spawn Git"))
    monkeypatch.setattr(snapshot.os, "scandir", lambda *a, **k: pytest.fail("唔應該掃目錄"))
    candidates = snapshot.scope_candidates(link / "missing" / "file", key=KEY)
    expected = [snapshot._digest(KEY, "path", str(path))
                for path in (repo / "missing" / "file", repo / "missing", repo)]
    assert candidates == expected


def test_nonrepo_home_is_not_scanned(isolated, monkeypatch):
    (isolated / "auth.json").write_text("NEVER_READ")
    monkeypatch.setattr(snapshot.os, "scandir", lambda *a, **k: pytest.fail("非 repo 唔掃 HOME"))
    monkeypatch.setattr(snapshot.subprocess, "Popen", lambda *a, **k: pytest.fail("非 repo 唔 spawn"))
    result = partial(isolated, "not_git")
    assert result["worktree_id"] is None
    assert result["scope_id"] == result["cwd_id"]
    assert result["coverage"]["paths"] == 0


def test_worktrees_and_nested_cwd_use_distinct_correct_identity(repo, tmp_path):
    worktree = tmp_path / "other-tree"
    git(repo, "worktree", "add", "-qb", "other", str(worktree))
    first, other = capture(repo), capture(worktree)
    assert first["complete"] and other["complete"], (first, other)
    assert first["worktree_id"] != other["worktree_id"]
    assert first["scope_id"] != other["scope_id"]
    (repo / "nested").mkdir()
    nested = capture(repo / "nested")
    assert nested["worktree_id"] == first["worktree_id"]
    assert nested["scope_id"] == first["scope_id"]
    assert nested["cwd_id"] != first["cwd_id"]


def test_replaced_repository_identity_is_not_borrowed(repo, tmp_path):
    first = capture(repo)
    (repo / ".git").rename(tmp_path / "old-git")
    git(repo, "init", "-q")
    git(repo, "add", "source.txt")
    git(repo, "commit", "-qm", "fixture")
    after = capture(repo)
    assert after["complete"], after
    assert first["scope_id"] == after["scope_id"]
    assert first["worktree_id"] != after["worktree_id"]


@pytest.mark.parametrize("mutation", ["same-file", "new-file", "deletion", "mode", "index", "head", "ignore"])
def test_midscan_mutation_never_reports_complete(repo, monkeypatch, mutation):
    original = snapshot._read_content
    changed = False
    def mutate(*args):
        nonlocal changed
        result = original(*args)
        if not changed:
            changed = True
            if mutation == "same-file":
                (repo / "source.txt").write_text("mutated!\n")
            elif mutation == "new-file":
                (repo / "new").write_text("new")
            elif mutation == "deletion":
                (repo / "source.txt").unlink()
            elif mutation == "mode":
                (repo / "source.txt").chmod(0o755)
            elif mutation == "index":
                git(repo, "update-index", "--chmod=+x", "source.txt")
            elif mutation == "head":
                git(repo, "commit", "--allow-empty", "-qm", "racing")
            else:
                (repo / ".gitignore").write_text("*\n")
        return result
    monkeypatch.setattr(snapshot, "_read_content", mutate)
    partial(repo, "snapshot_race")


def test_final_component_swap_to_symlink_is_not_followed(repo, tmp_path, monkeypatch):
    outside = tmp_path / "secret"
    outside.write_text("CANARY")
    original = snapshot.os.open
    changed = False
    def swap(path, flags, *args, **kwargs):
        nonlocal changed
        if path == "source.txt" and not changed:
            changed = True
            (repo / "source.txt").unlink()
            (repo / "source.txt").symlink_to(outside)
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(snapshot.os, "open", swap)
    partial(repo)
    assert changed


@pytest.mark.parametrize("option", ["--assume-unchanged", "--skip-worktree"])
def test_unsupported_index_flags_are_unknown(repo, option):
    git(repo, "update-index", option, "source.txt")
    partial(repo, "unsupported_index")


def test_split_and_v4_index_are_unknown(repo):
    git(repo, "update-index", "--split-index")
    partial(repo, "unsupported_index")
    git(repo, "update-index", "--no-split-index")
    git(repo, "update-index", "--index-version", "4")
    partial(repo, "unsupported_index")


def test_submodule_index_is_unknown_without_recursion(repo):
    oid = git(repo, "rev-parse", "HEAD").strip().decode()
    git(repo, "update-index", "--add", "--cacheinfo", "160000," + oid + ",nested")
    partial(repo, "unsupported_submodule")


def test_corrupt_index_and_busy_index_are_unknown(repo):
    (repo / ".git" / "index.lock").write_bytes(b"")
    partial(repo, "snapshot_incomplete")
    (repo / ".git" / "index.lock").unlink()
    (repo / ".git" / "index").write_bytes(b"CANARY")
    partial(repo, "unsupported_index")


def test_unborn_head_and_packed_refs(repo, tmp_path):
    unborn = tmp_path / "unborn"
    unborn.mkdir()
    git(unborn, "init", "-q")
    assert capture(unborn)["complete"]
    git(repo, "pack-refs", "--all")
    before = fingerprint(repo)
    git(repo, "commit", "--allow-empty", "-qm", "later")
    assert fingerprint(repo) != before


def test_path_byte_deadline_and_git_output_caps(repo, monkeypatch):
    monkeypatch.setattr(snapshot, "MAX_PATHS", 0)
    partial(repo, "path_limit")
    monkeypatch.setattr(snapshot, "MAX_PATHS", 10_000)
    monkeypatch.setattr(snapshot, "MAX_BYTES", 16)
    result = partial(repo, "byte_limit")
    assert result["coverage"]["bytes"] <= 16
    monkeypatch.setattr(snapshot, "MAX_BYTES", 64 * 1024 * 1024)
    partial(repo, "deadline_exceeded", deadline=time.monotonic() - 1)
    (repo / ".gitignore").write_text("ignored\n")
    (repo / "ignored").write_text("nothing")
    monkeypatch.setattr(snapshot, "MAX_GIT_OUTPUT", 1)
    partial(repo, "git_output_limit")


def test_caller_cannot_extend_two_second_policy(repo, monkeypatch):
    monkeypatch.setattr(snapshot, "MAX_SECONDS", 0)
    partial(repo, "deadline_exceeded", deadline=time.monotonic() + 100)


def test_many_ignored_children_are_not_walked(repo, monkeypatch):
    (repo / ".gitignore").write_text("output/\n")
    (repo / "output").mkdir()
    for number in range(50):
        (repo / "output" / str(number)).write_text("skip")
    monkeypatch.setattr(snapshot, "MAX_PATHS", 5)
    assert capture(repo)["complete"]


def test_malicious_config_environment_hooks_and_filters_never_execute(repo, tmp_path, monkeypatch):
    marker = tmp_path / "EXECUTED"
    script = tmp_path / "evil"
    script.write_text("#!/bin/sh\nprintf unsafe > '" + str(marker) + "'\n")
    script.chmod(0o755)
    # include 目標根本唔係 config；若真 Git 讀到原 config，必然失敗。
    poison = tmp_path / "auth.json"
    poison.write_text('{"credential": "DO_NOT_READ_CONFIG_CANARY"}')
    with (repo / ".git" / "config").open("a") as handle:
        handle.write(f'\n[include]\n path = {poison}\n[core]\n fsmonitor = {script}\n'
                     f' hooksPath = {tmp_path}\n excludesFile = {poison}\n'
                     f'[filter "evil"]\n clean = {script}\n smudge = {script}\n required = true\n'
                     '[remote "origin"]\n promisor = true\n url = ext::false\n')
    (repo / ".gitattributes").write_text("* filter=evil\n")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(script))
    monkeypatch.setenv("GIT_EXEC_PATH", str(tmp_path))
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", str(script))
    monkeypatch.setenv("GIT_ASKPASS", str(script))
    monkeypatch.setenv("PATH", str(tmp_path))
    before = capture(repo)
    assert before["complete"], before
    assert not marker.exists()
    assert "CANARY" not in json.dumps(before)
    assert not list(repo.glob(".git/*.lock"))


def test_git_helper_timeout_output_cap_and_children_reaped(tmp_path, monkeypatch):
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    snapshot._make_shadow(shadow, None)
    real_popen = subprocess.Popen
    children = []
    def spawn(*args, **kwargs):
        process = real_popen([sys.executable, "-B", "-c", "import time; time.sleep(30)"], **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr(snapshot.subprocess, "Popen", spawn)
    start = time.monotonic()
    with pytest.raises(snapshot._Incomplete, match="deadline"):
        snapshot._git([], shadow=shadow, budget=snapshot._Budget(start + 0.1), payload=b"one\0")
    assert time.monotonic() - start < 1
    assert all(child.poll() is not None for child in children)
    assert all(child.stdin.closed and child.stdout.closed and child.stderr.closed for child in children)


def test_temporary_shadows_removed_on_success_and_error(repo, tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    fingerprint(repo)
    assert not list(tmp_path.glob("reality-snapshot-*"))
    monkeypatch.setattr(snapshot, "_read_content", lambda *a: (_ for _ in ()).throw(OSError("RAW_SECRET_CANARY")))
    result = partial(repo, "read_failed")
    assert "RAW_SECRET_CANARY" not in json.dumps(result)
    assert not list(tmp_path.glob("reality-snapshot-*"))


def test_true_64_mib_cap_refuses_sparse_input_before_content_read(repo, monkeypatch):
    with (repo / "large").open("wb") as stream:
        stream.truncate(64 * 1024 * 1024 + 1)
    original = snapshot.os.open
    def guarded(path, flags, *args, **kwargs):
        assert path != "large"
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(snapshot.os, "open", guarded)
    result = partial(repo, "byte_limit")
    assert result["coverage"]["bytes"] < 4096


def test_true_10k_cap_stops_directory_enumeration(repo):
    for number in range(10_001):
        (repo / f"n{number}").touch()
    result = partial(repo, "path_limit")
    assert result["coverage"]["paths"] == 10_000


def test_total_read_cap_includes_multiple_files_and_metadata(repo, monkeypatch):
    (repo / "a").write_bytes(b"a" * 600)
    (repo / "b").write_bytes(b"b" * 600)
    monkeypatch.setattr(snapshot, "MAX_BYTES", 1500)
    result = partial(repo, "byte_limit")
    assert result["coverage"]["bytes"] <= 1500


def test_large_manifest_is_folded_within_core_json_limit(repo):
    for number in range(800):
        (repo / (f"n{number:04d}-" + "x" * 180)).touch()
    git(repo, "add", ".")
    first = fingerprint(repo)
    (repo / ("n0000-" + "x" * 180)).write_text("changed")
    assert fingerprint(repo) != first


@pytest.mark.parametrize("name", [b"bad-\xff", b"line\nname"])
def test_unusual_filename_is_hashed_losslessly(repo, name):
    import errno
    path = os.fsencode(repo) + b"/" + name
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    except OSError as exc:
        if exc.errno == errno.EILSEQ:
            pytest.skip("呢個 filesystem 唔支援 non-UTF8 filename")
        raise
    os.close(fd)
    first = fingerprint(repo)
    fd = os.open(path, os.O_WRONLY)
    try:
        os.write(fd, b"changed")
    finally:
        os.close(fd)
    assert fingerprint(repo) != first


def test_device_keys_do_not_share_identity_or_fingerprint(repo):
    first = capture(repo)
    other = snapshot.capture(repo, key=b"z" * 32)
    validate_snapshot(other)
    assert other["complete"]
    for field in ("cwd_id", "scope_id", "worktree_id", "fingerprint"):
        assert first[field] != other[field]


def test_snapshot_helpers_leave_original_git_metadata_untouched(repo):
    paths = [repo / ".git" / name for name in ("index", "HEAD", "config")]
    before = [(path.read_bytes(), snapshot._metadata(path.stat())) for path in paths]
    fingerprint(repo)
    after = [(path.read_bytes(), snapshot._metadata(path.stat())) for path in paths]
    assert before == after


def test_index_path_bombs_are_rejected_before_parent_expansion():
    for path in (b"a" * 4097, b"a/" * 129 + b"end", b"../secret", b".git/config", b"a//b"):
        with pytest.raises(snapshot._Incomplete, match="unsupported_index"):
            snapshot._valid_path(path)


def test_git_stderr_limit_also_kills_and_reaps(repo, monkeypatch):
    original = subprocess.Popen
    children = []
    def noisy(*args, **kwargs):
        process = original([sys.executable, "-B", "-c",
                            "import os,time; os.write(2,b'CANARY'*2000); time.sleep(30)"], **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr(snapshot.subprocess, "Popen", noisy)
    monkeypatch.setattr(snapshot, "MAX_GIT_OUTPUT", 32)
    result = partial(repo, "git_output_limit")
    assert "CANARY" not in json.dumps(result)
    assert children and all(child.poll() is not None for child in children)
    assert all(child.stdin.closed and child.stdout.closed and child.stderr.closed for child in children)


def test_git_spawn_failure_has_fixed_code(repo, monkeypatch):
    def fail(*args, **kwargs):
        raise FileNotFoundError("RAW_CANARY")
    monkeypatch.setattr(snapshot.subprocess, "Popen", fail)
    result = partial(repo, "read_failed")
    assert "RAW_CANARY" not in json.dumps(result)


def test_unknown_internal_diagnostic_cannot_escape(repo, monkeypatch):
    def fail(*args, **kwargs):
        raise snapshot._Incomplete("RAW_CANARY")
    monkeypatch.setattr(snapshot, "_read_content", fail)
    result = partial(repo, "snapshot_failed")
    assert "RAW_CANARY" not in json.dumps(result)


def test_cleanup_failure_cannot_leave_complete_true(repo, monkeypatch):
    original = snapshot.tempfile.TemporaryDirectory
    instances = []
    class FailsAfterCleanup(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.outer = not instances
            instances.append(self)
        def __exit__(self, *args):
            result = super().__exit__(*args)
            if self.outer:
                raise OSError("CLEANUP_CANARY")
            return result
    monkeypatch.setattr(snapshot.tempfile, "TemporaryDirectory", FailsAfterCleanup)
    result = partial(repo, "read_failed")
    assert "CANARY" not in json.dumps(result)


def test_mutation_during_manifest_hashing_is_detected(repo, monkeypatch):
    original = snapshot._fold
    def mutate(key, domain, records, budget):
        value = original(key, domain, records, budget)
        if domain == "file-entry":
            (repo / "source.txt").write_text("late change")
        return value
    monkeypatch.setattr(snapshot, "_fold", mutate)
    partial(repo, "snapshot_race")


def test_mutation_during_final_git_guard_is_detected(repo, monkeypatch):
    original = snapshot._git_state
    count = 0
    def mutate(*args):
        nonlocal count
        value = original(*args)
        count += 1
        if count == 2:
            (repo / "source.txt").write_text("late change")
        return value
    monkeypatch.setattr(snapshot, "_git_state", mutate)
    partial(repo, "snapshot_race")


def test_reftable_and_fake_git_marker_are_not_reported_complete(repo, tmp_path):
    (repo / ".git" / "reftable").mkdir()
    partial(repo, "unsupported")
    fake = tmp_path / "fake"
    (fake / ".git").mkdir(parents=True)
    (fake / ".git" / "HEAD").write_text("ref: refs/heads/absent\n")
    partial(fake, "read_failed")


def test_sha256_index_and_detached_head(repo, tmp_path):
    path = tmp_path / "sha256"
    path.mkdir()
    git(path, "init", "--object-format=sha256", "-q")
    (path / "source").write_text("one")
    git(path, "add", "source")
    git(path, "commit", "-qm", "fixture")
    before = fingerprint(path)
    git(path, "checkout", "--detach", "-q")
    assert fingerprint(path) != before


def test_ancestor_swapped_between_lstat_and_open_is_not_followed(repo, tmp_path, monkeypatch):
    (repo / "nested").mkdir()
    (repo / "nested" / "source").write_text("one")
    original = snapshot._read_regular
    changed = False
    def swap(path, *args, **kwargs):
        nonlocal changed
        if Path(path) == repo / "nested" / "source" and not changed:
            changed = True
            (repo / "nested").rename(repo / "old-nested")
            (repo / "nested").symlink_to(tmp_path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(snapshot, "_read_regular", swap)
    partial(repo, "read_failed")
    assert changed


@pytest.mark.parametrize("kind", ["repo", "worktree"])
def test_scope_candidates_stop_at_nearest_nested_boundary(repo, kind, monkeypatch):
    nested = repo / "nested"
    if kind == "repo":
        nested.mkdir()
        git(nested, "init", "-q")
    else:
        git(repo, "worktree", "add", "-qb", "nested", str(nested))
    monkeypatch.setattr(snapshot.subprocess, "Popen", lambda *a, **k: pytest.fail("scope 唔 spawn"))
    monkeypatch.setattr(snapshot.os, "scandir", lambda *a, **k: pytest.fail("scope 唔 enumerate"))
    monkeypatch.setattr(snapshot, "_read_regular", lambda *a, **k: pytest.fail("scope 唔讀內容"))
    values = snapshot.scope_candidates(nested / "new-file", key=KEY)
    assert values == [snapshot._digest(KEY, "path", str(path)) for path in (nested / "new-file", nested)]
    assert snapshot._digest(KEY, "path", str(repo)) not in values


@pytest.mark.parametrize("kind", ["symlink", "fifo", "unsafe", "hardlink"])
def test_scope_candidates_unsafe_marker_never_borrows_parent(repo, tmp_path, kind):
    nested = repo / "nested"
    nested.mkdir()
    marker = nested / ".git"
    if kind == "symlink":
        marker.symlink_to(repo / ".git")
    elif kind == "fifo":
        os.mkfifo(marker)
    elif kind == "unsafe":
        marker.mkdir()
        marker.chmod(0o777)
    else:
        target = tmp_path / "marker"
        target.write_text("gitdir: nowhere\n")
        os.link(target, marker)
    assert snapshot.scope_candidates(nested / "file", key=KEY) == []


def test_scope_locator_work_is_bounded_and_existing_file_maps_to_root(repo, monkeypatch):
    values = snapshot.scope_candidates(repo / "source.txt", key=KEY)
    assert values[-1] == capture(repo)["scope_id"]
    monkeypatch.setattr(snapshot, "MAX_SCOPE_DEPTH", 1)
    assert snapshot.scope_candidates(repo / "missing" / "file", key=KEY) == []
    monkeypatch.setattr(snapshot, "MAX_SCOPE_DEPTH", 128)
    monkeypatch.setattr(snapshot, "MAX_SCOPE_SECONDS", 0)
    assert snapshot.scope_candidates(repo, key=KEY) == []


@pytest.mark.parametrize("ignorecase", ["false", "true"])
def test_ignorecase_negation_never_drops_potential_input(repo, ignorecase):
    git(repo, "config", "core.ignorecase", ignorecase)
    (repo / ".gitignore").write_text("*.tmp\n!KEEP.TMP\n")
    (repo / "keep.tmp").write_text("one")
    actual = git(repo, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
    if ignorecase == "true":
        assert b"keep.tmp" in actual
    first = fingerprint(repo)
    (repo / "keep.tmp").write_text("two")
    # 唔讀 config，兩種 boolean 語義嘅 nonignored union 都納入；容許保守 over-coverage。
    assert fingerprint(repo) != first


def test_case_alias_ignore_file_negation_follows_actual_filesystem(repo):
    (repo / ".gitignore").write_text("*.tmp\n")
    (repo / "nested").mkdir()
    (repo / "nested" / ".GITIGNORE").write_text("!keep.tmp\n")
    if not (repo / "nested" / ".gitignore").exists():
        pytest.skip("只適用 case-insensitive filesystem")
    (repo / "nested" / "keep.tmp").write_text("one")
    actual = git(repo, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
    assert b"nested/keep.tmp" in actual
    first = fingerprint(repo)
    (repo / "nested" / "keep.tmp").write_text("two")
    assert fingerprint(repo) != first


def test_case_alias_git_directory_never_exposes_config(repo, monkeypatch):
    (repo / ".git").rename(repo / ".GIT")
    original = snapshot._read_regular
    def guarded(path, *args, **kwargs):
        assert Path(path).name != "config"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(snapshot, "_read_regular", guarded)
    partial(repo)


def test_discovery_root_swap_closes_already_open_common_fd(repo, tmp_path, monkeypatch):
    original = snapshot._open_dir
    opened = []
    root_opens = 0
    def swapped(path):
        nonlocal root_opens
        if Path(path) == repo:
            root_opens += 1
            if root_opens == 2:
                repo.rename(tmp_path / "moved")
                repo.symlink_to(tmp_path)
        fd = original(path)
        opened.append(fd)
        return fd
    monkeypatch.setattr(snapshot, "_open_dir", swapped)
    partial(repo, "read_failed")
    assert root_opens == 2
    for fd in set(opened):
        with pytest.raises(OSError):
            os.fstat(fd)


def test_path_cap_counts_union_across_both_scan_passes(repo, monkeypatch):
    (repo / "a").write_text("one")
    monkeypatch.setattr(snapshot, "MAX_PATHS", 2)
    original = snapshot._read_content
    changed = False
    def mutate(*args):
        nonlocal changed
        value = original(*args)
        if not changed:
            changed = True
            (repo / "a").unlink()
            (repo / "b").write_text("two")
        return value
    monkeypatch.setattr(snapshot, "_read_content", mutate)
    result = partial(repo, "path_limit")
    assert result["coverage"]["paths"] == 2
