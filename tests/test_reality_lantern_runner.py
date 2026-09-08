"""真正 child lifecycle／fake HOME Git 整合；唔跑真 host 安裝或讀真 credentials。"""
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import selectors
import signal
import subprocess
import sys
import time

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
from reality_lantern import core, runner, snapshot, store as storage
from reality_lantern.store import Store

CLI = TOOLS / "kingdom-reality.py"
PYTHON = [sys.executable, "-I", "-S", "-B", "-W", "error"]


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path.resolve() / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    # Git fixture 只見自建環境；唔繼承呼叫端嘅 config injection／repository locator。
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return home


@pytest.fixture
def store(isolated_home):
    return Store(home=isolated_home)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path.resolve() / "REPO-PATH-CANARY"
    path.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q", "--template="], cwd=path, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    (path / "version.txt").write_text("original\n")
    subprocess.run(["/usr/bin/git", "add", "--", "version.txt"], cwd=path, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    return path


def checks(store):
    state = store.read_state()
    scope_id = next(iter(state["scopes"]))
    return core.build_status(state, scope_id=scope_id)["checks"]


def only_receipt(store):
    state = store.read_state()
    assert len(state["runs"]) == len(state["completions"]) == 1
    return next(iter(state["runs"].values())), next(iter(state["completions"].values()))


def cli_args(store, cwd, source, *args):
    return [*PYTHON, str(CLI), "--home", str(store.home), "check", "--cwd", str(cwd),
            "--", *PYTHON, "-c", source, *args]


def run_cli(store, cwd, source, *args, input=None):
    return subprocess.run(cli_args(store, cwd, source, *args), input=input, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)


def synthetic(cwd, *, key, deadline=None):
    assert deadline is not None and 0 < deadline - time.monotonic() <= runner.SNAPSHOT_SECONDS
    ident = core.keyed_digest(key, "path", str(cwd.resolve()))
    return {"schema": core.SCHEMA_VERSION, "scope_id": ident, "cwd_id": ident,
            "worktree_id": "a" * 64, "fingerprint": "b" * 64, "complete": True,
            "observed_at": core.utc_now(), "diagnostics": [],
            "coverage": {"paths": 0, "bytes": 0, "ignored": "excluded",
                         "environment": "unverified", "remote": "unverified"}}


def command(code=0):
    return [*PYTHON, "-c", f"raise SystemExit({code})"]


def ready(process):
    # 測試同步用有界 pipe event；唔以 sleep 猜 child 已開始。
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        assert selector.select(10), "child 未交出 readiness"
    line = process.stdout.readline().strip()
    assert line.isdigit(), "child readiness 格式唔吻合"
    return int(line)


def stop(process, child_pid=None):
    # 只清理本測試建立嘅程序；喺成功路徑一樣關閉 PIPE 防 ResourceWarning。
    if process.poll() is None:
        process.kill()
    if child_pid is not None:
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=5)


def pty_trial(argv, cwd, actions):
    # 真 controlling PTY，只改呢個 temporary terminal，唔掂使用者 terminal。
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.chdir(cwd)
            os.execv(argv[0], argv)
        except BaseException:
            os._exit(125)
    output = bytearray()
    status = None
    pending = list(actions)
    end = time.monotonic() + 12
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(fd, selectors.EVENT_READ)
            while time.monotonic() < end:
                if selector.select(0.05):
                    try:
                        chunk = os.read(fd, 4096)
                    except OSError:
                        chunk = b""
                    output.extend(chunk)
                    assert len(output) < 128 * 1024
                if pending and pending[0][0] in output:
                    _, action = pending.pop(0)
                    action(fd, pid)
                reaped, value = os.waitpid(pid, os.WNOHANG)
                if reaped:
                    status = os.waitstatus_to_exitcode(value)
                    break
            assert status is not None, "PTY child 未喺測試時限內完成"
            os.set_blocking(fd, False)
            while len(output) < 128 * 1024:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
        assert not pending, "PTY 未到預期互動時點"
        return status, output.decode()
    finally:
        if status is None:
            # 只收尾呢個 trial 報出嘅 child group 同 wrapper group。
            groups = {pid, *(int(value) for value in re.findall(rb"CHILD=(\d+)", output))}
            for group in groups:
                try:
                    os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            os.waitpid(pid, 0)
        os.close(fd)


INTERRUPT_COUNTER = '''import os,signal,time
count = 0
def interrupted(number, frame):
    global count
    count += 1
    print('SIGNAL_COUNT=' + str(count), flush=True)
    if count > 1:
        os._exit(42)
    time.sleep(.25)
    os._exit(23)
signal.signal(signal.SIGINT, interrupted)
print('CHILD=' + str(os.getpid()), flush=True)
print('READY', flush=True)
signal.pause()
'''


@pytest.mark.parametrize("code", [0, 23])
def test_real_exit_and_matching_version(store, repo, code):
    assert runner.run_check(command(code), cwd=repo, kind="tests", store=store) == code
    run, receipt = only_receipt(store)
    assert run["kind"] == "tests"
    assert receipt["outcome"] == ("exit0" if code == 0 else "nonzero")
    assert receipt["exit_code"] == code and receipt["signal_number"] is None
    assert run["before"]["complete"] and receipt["after"]["complete"]
    assert checks(store)[0]["validity"] == "match"
    assert core.timestamp(run["started_at"]) <= core.timestamp(receipt["finished_at"])
    assert core.timestamp(run["before"]["observed_at"]) <= core.timestamp(run["started_at"])
    assert core.timestamp(receipt["after"]["observed_at"]) <= core.timestamp(receipt["finished_at"])


@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGINT, signal.SIGKILL])
def test_actual_child_signal_records_signal_not_exit_code(store, repo, number):
    source = "import os,signal; signal.signal(int(__import__('sys').argv[1]),signal.SIG_DFL); os.kill(os.getpid(),int(__import__('sys').argv[1]))"
    if number == signal.SIGKILL:
        source = "import os,signal; os.kill(os.getpid(),signal.SIGKILL)"
    result = run_cli(store, repo, source, str(int(number)))
    assert result.returncode == 128 + number
    _, receipt = only_receipt(store)
    assert receipt["outcome"] == "signal" and receipt["signal_number"] == number
    assert receipt["exit_code"] is None


def test_stdio_argv_cwd_and_environment_are_unchanged(store, repo, monkeypatch):
    monkeypatch.setenv("RUNNER_ENV_CANARY", "ENV-VALUE-CANARY")
    monkeypatch.setenv("GIT_OPTIONAL_LOCKS", "COMMAND-ENV-CANARY")
    source = ("import json,os,sys; payload=sys.stdin.read(); "
              "print(json.dumps([payload,sys.argv[1:],os.getcwd(),os.environ['RUNNER_ENV_CANARY'],"
              "os.environ['GIT_OPTIONAL_LOCKS']])); sys.stderr.write('STDERR-CANARY\\n')")
    original = ["a b", "$(touch NEVER)", "; exit 91", "*", "ARGV-CANARY"]
    result = run_cli(store, repo, source, *original, input="STDIN-CANARY\n")
    assert result.returncode == 0
    assert json.loads(result.stdout) == ["STDIN-CANARY\n", original, str(repo),
                                        "ENV-VALUE-CANARY", "COMMAND-ENV-CANARY"]
    assert result.stderr == "STDERR-CANARY\n"
    state = (store.state_dir / "state.json").read_text()
    context = core.render_brief(core.build_status(store.read_state(), scope_id=checks_scope(store)))
    for canary in [str(repo), source, *original, "ENV-VALUE-CANARY", "COMMAND-ENV-CANARY",
                   "STDIN-CANARY", "STDERR-CANARY"]:
        assert canary not in state + context
    assert not (repo / "NEVER").exists()


def checks_scope(store):
    return next(iter(store.read_state()["scopes"]))


def test_command_mutates_workspace_outcome_stays_exit0_validity_stale(store, repo):
    result = run_cli(store, repo, "from pathlib import Path; Path('version.txt').write_text('changed')")
    assert result.returncode == 0
    assert checks(store)[0]["outcome"] == "exit0"
    assert checks(store)[0]["validity"] == "stale"
    run, receipt = only_receipt(store)
    assert run["before"]["fingerprint"] != receipt["after"]["fingerprint"]


def test_non_git_keeps_process_outcome_without_scanning_home(store, tmp_path):
    path = tmp_path.resolve() / "non-git"
    path.mkdir()
    assert runner.run_check(command(23), cwd=path, store=store) == 23
    run, receipt = only_receipt(store)
    assert run["before"]["diagnostics"] == receipt["after"]["diagnostics"] == ["not_git"]
    assert checks(store)[0]["validity"] == "unknown"


@pytest.mark.parametrize("failure", ["before", "after", "both", "invalid_schema", "metadata_canary"])
def test_capture_failure_keeps_legal_unknown_receipt(store, repo, monkeypatch, capsys, failure):
    calls = []

    def capture(cwd, **kwargs):
        calls.append(1)
        if failure == "both" or (failure == "before" and len(calls) == 1) or (failure == "after" and len(calls) == 2):
            raise OSError("SECRET-EXCEPTION-CANARY")
        if failure == "invalid_schema":
            return {"complete": False}
        value = synthetic(cwd, **kwargs)
        if failure == "metadata_canary":
            value["raw_output"] = "SECRET-METADATA-CANARY"
        return value

    monkeypatch.setattr(snapshot, "capture", capture)
    assert runner.run_check(command(23), cwd=repo, store=store) == 23
    run, receipt = only_receipt(store)
    for value in (run["before"], receipt["after"]):
        core.validate_snapshot(value)
    assert receipt["outcome"] == "nonzero" and receipt["exit_code"] == 23
    assert checks(store)[0]["validity"] == "unknown"
    assert len(calls) == 2
    output = capsys.readouterr()
    assert output.out == "" and output.err.count("實況燈") == 1
    assert "CANARY" not in output.err + (store.state_dir / "state.json").read_text()
    if failure == "after":
        assert receipt["after"]["scope_id"] == run["before"]["scope_id"]
        assert receipt["after"]["worktree_id"] == run["before"]["worktree_id"]


@pytest.mark.parametrize("stage", ["get_key", "start_run", "finish_run"])
@pytest.mark.parametrize("error", ["state_full", "lock_timeout", "state_unsafe", "state_io"])
def test_bookkeeping_failure_never_retries_or_changes_exit(store, repo, monkeypatch, capsys, stage, error):
    monkeypatch.setattr(snapshot, "capture", synthetic)
    original_popen = subprocess.Popen
    commands = []
    calls = []

    def popen(argv, **kwargs):
        commands.append((argv, kwargs))
        return original_popen(argv, **kwargs)

    def fail(*args, **kwargs):
        calls.append(1)
        assert kwargs["deadline"] <= time.monotonic() + runner.STORE_SECONDS
        raise core.RealityError(error)

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    monkeypatch.setattr(store, stage, fail)
    argv = command(23)
    assert runner.run_check(argv, cwd=repo, store=store) == 23
    assert commands == [(argv, {"cwd": repo, "shell": False, "process_group": 0})]
    assert calls == [1]
    if stage == "finish_run":
        assert len(store.read_state()["runs"]) == 1
        assert not store.read_state()["completions"]
    assert "Traceback" not in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["missing", "not_executable", "nul", "bad_cwd"])
def test_spawn_failures_are_fixed_safe_and_have_receipts(store, repo, tmp_path, capsys, mode):
    cwd = repo
    if mode == "missing":
        argv, expected = [str(repo / "MISSING-COMMAND-CANARY")], 127
    elif mode == "not_executable":
        path = repo / "EXECUTABLE-CANARY"
        path.write_text("not executable\n")
        path.chmod(0o600)
        argv, expected = [str(path)], 126
    elif mode == "nul":
        argv, expected = ["NUL-CANARY\x00"], 126
    else:
        argv, expected = command(), 127
        cwd = tmp_path.resolve() / "MISSING-CWD-CANARY"
    assert runner.run_check(argv, cwd=cwd, store=store) == expected
    _, receipt = only_receipt(store)
    assert receipt["outcome"] == "spawn_failed"
    assert receipt["exit_code"] is None and receipt["signal_number"] is None
    output = capsys.readouterr()
    assert "CANARY" not in output.out + output.err
    assert "Traceback" not in output.err


def test_real_store_full_runs_command_once_without_evicting(store, repo, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(snapshot, "capture", synthetic)
    monkeypatch.setattr(storage, "MAX_RUNS", 1)
    assert runner.run_check(command(), cwd=repo, store=store) == 0
    original = deepcopy(store.read_state())
    marker = tmp_path.resolve() / "counter"
    argv = [*PYTHON, "-c", "from pathlib import Path; import sys; p=Path(sys.argv[1]); p.write_text(p.read_text()+'x' if p.exists() else 'x'); raise SystemExit(23)", str(marker)]
    assert runner.run_check(argv, cwd=repo, store=store) == 23
    assert marker.read_text() == "x" and store.read_state() == original
    assert "收據容量已滿，暫停記錄" in capsys.readouterr().err


def test_real_busy_store_has_bounded_overhead_and_command_once(store, repo, monkeypatch, tmp_path):
    store.get_key(create=True)
    monkeypatch.setattr(runner, "STORE_SECONDS", 0.025)
    marker = tmp_path.resolve() / "ran"
    with (store.state_dir / ".lock").open("rb") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        began = time.monotonic()
        assert runner.run_check([*PYTHON, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('once')", str(marker)], cwd=repo, store=store) == 0
        assert time.monotonic() - began < 1
    assert marker.read_text() == "once" and not store.read_state()["runs"]


def test_real_unsafe_store_not_repaired_command_runs(store, repo, tmp_path):
    canary = tmp_path.resolve() / "foreign"
    canary.write_bytes(b"DO-NOT-CHANGE")
    store.state_dir.mkdir(parents=True, mode=0o700)
    (store.state_dir / "key").symlink_to(canary)
    assert runner.run_check(command(23), cwd=repo, store=store) == 23
    assert canary.read_bytes() == b"DO-NOT-CHANGE"
    assert (store.state_dir / "key").is_symlink()
    assert not (store.state_dir / "state.json").exists()


def test_identity_binds_cwd_and_original_argv_not_kind_or_env(store, repo, monkeypatch):
    monkeypatch.setattr(snapshot, "capture", synthetic)
    nested = repo / "nested"
    nested.mkdir()
    argv = command()
    assert runner.run_check(argv, cwd=repo, kind="tests", store=store) == 0
    monkeypatch.setenv("RUNTIME_CANARY", "ENV-CHANGED-CANARY")
    assert runner.run_check(argv, cwd=repo, kind="build", store=store) == 0
    assert runner.run_check([*argv, "ARGUMENT-CANARY"], cwd=repo, store=store) == 0
    assert runner.run_check(argv, cwd=nested, store=store) == 0
    runs = list(store.read_state()["runs"].values())
    runs.sort(key=lambda item: item["sequence"])
    assert runs[0]["check_id"] == runs[1]["check_id"]
    assert len({item["check_id"] for item in runs}) == 3
    key = store.get_key(create=False)
    assert runs[0]["check_id"] == core.keyed_digest(key, "check", {"cwd_id": runs[0]["before"]["cwd_id"], "argv": argv})
    assert "CANARY" not in (store.state_dir / "state.json").read_text()


@pytest.mark.parametrize("changed", [False, True])
def test_concurrent_invalidation_while_real_child_waits_prevents_match(store, repo, changed):
    source = "import os,sys; print(os.getpid(),flush=True); sys.stdin.readline()"
    process = subprocess.Popen(cli_args(store, repo, source), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               start_new_session=True)
    child_pid = None
    try:
        child_pid = ready(process)
        assert checks(store)[0]["outcome"] == "pending"
        scope = checks_scope(store)
        assert store.invalidate(scope, changed=changed)
        stdout, stderr = process.communicate("finish\n", timeout=10)
        child_pid = None
        assert process.returncode == 0 and stdout == stderr == ""
        assert checks(store)[0]["validity"] == ("stale" if changed else "unknown")
        assert checks(store)[0]["outcome"] == "exit0"
    finally:
        stop(process, child_pid)


@pytest.mark.parametrize("changed", [False, True])
def test_invalidation_after_capture_before_completion_has_cas_guard(store, repo, monkeypatch, changed):
    calls = []

    def capture(cwd, **kwargs):
        value = synthetic(cwd, **kwargs)
        calls.append(1)
        if len(calls) == 2:
            store.invalidate(value["scope_id"], changed=changed)
        return value

    monkeypatch.setattr(snapshot, "capture", capture)
    assert runner.run_check(command(), cwd=repo, store=store) == 0
    assert checks(store)[0]["validity"] == ("stale" if changed else "unknown")


def test_background_pending_until_actual_wait_not_launch(store, repo):
    source = "import os,sys; print(os.getpid(),flush=True); sys.stdin.readline(); raise SystemExit(23)"
    process = subprocess.Popen(cli_args(store, repo, source), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               start_new_session=True)
    child_pid = None
    try:
        child_pid = ready(process)
        assert process.poll() is None
        state = store.read_state()
        assert len(state["runs"]) == 1 and not state["completions"]
        assert checks(store)[0]["outcome"] == "pending"
        process.communicate("finish\n", timeout=10)
        child_pid = None
        assert process.returncode == 23
        assert checks(store)[0]["outcome"] == "nonzero"
    finally:
        stop(process, child_pid)


def test_sigkill_wrapper_leaves_pending_even_if_child_later_exits_zero(store, repo):
    source = "import os,sys; print(os.getpid(),flush=True); sys.stdin.readline(); print('child-finished',flush=True)"
    process = subprocess.Popen(cli_args(store, repo, source), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               start_new_session=True)
    child_pid = None
    try:
        child_pid = ready(process)
        process.kill()
        assert process.wait(timeout=5) == -signal.SIGKILL
        assert checks(store)[0]["outcome"] == "pending"
        stdout, stderr = process.communicate("finish\n", timeout=5)
        child_pid = None
        assert stdout == "child-finished\n" and stderr == ""
        assert not store.read_state()["completions"]
        assert checks(store)[0]["validity"] == "unknown"
    finally:
        stop(process, child_pid)


@pytest.mark.parametrize("number", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_wrapper_forwards_signal_and_reaps_owned_child(store, repo, number):
    source = "import os,signal; signal.signal(signal.SIGINT,signal.SIG_DFL); print(os.getpid(),flush=True); signal.pause()"
    process = subprocess.Popen(cli_args(store, repo, source), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               start_new_session=True)
    child_pid = None
    try:
        child_pid = ready(process)
        process.send_signal(number)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 128 + number and stdout == stderr == ""
        _, receipt = only_receipt(store)
        assert receipt["outcome"] == "signal" and receipt["signal_number"] == number
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
        child_pid = None
    finally:
        stop(process, child_pid)


@pytest.mark.parametrize("destination", ["wrapper", "group"])
@pytest.mark.parametrize("trial", range(3))
def test_raw_sigint_reaches_child_once_not_twice(store, repo, destination, trial):
    source = INTERRUPT_COUNTER.replace("print('CHILD=' + str(os.getpid()), flush=True)",
                                       "print(os.getpid(), flush=True)")
    process = subprocess.Popen(cli_args(store, repo, source), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               start_new_session=True)
    child_pid = None
    try:
        child_pid = ready(process)
        if destination == "wrapper":
            process.send_signal(signal.SIGINT)
        else:
            os.killpg(process.pid, signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        child_pid = None
        assert process.returncode == 23
        assert stdout.count("SIGNAL_COUNT=1") == 1 and "SIGNAL_COUNT=2" not in stdout
        assert stderr == ""
        assert checks(store)[0]["outcome"] == "nonzero"
        assert checks(store)[0]["exit_code"] == 23
    finally:
        stop(process, child_pid)


@pytest.mark.parametrize("destination", ["ctrl_c", "wrapper", "wrapper_group"])
def test_real_pty_sigint_reaches_child_once(store, repo, destination):
    def interrupt(fd, pid):
        if destination == "ctrl_c":
            os.write(fd, b"\x03")
        elif destination == "wrapper":
            os.kill(pid, signal.SIGINT)
        else:
            os.killpg(pid, signal.SIGINT)

    status, output = pty_trial(cli_args(store, repo, INTERRUPT_COUNTER), repo,
                               [(b"READY", interrupt)])
    assert status == 23, output
    assert output.count("SIGNAL_COUNT=1") == 1 and "SIGNAL_COUNT=2" not in output
    assert checks(store)[0]["outcome"] == "nonzero" and checks(store)[0]["exit_code"] == 23


def test_real_pty_interactive_stdin_and_foreground_restore(store, repo):
    child = ("import os,sys,signal; print('CHILD='+str(os.getpid()),flush=True); "
             "print('TTY='+str(all(os.isatty(fd) for fd in (0,1,2))),flush=True); "
             "print('UNBLOCKED='+str(signal.SIGTTOU not in signal.pthread_sigmask(signal.SIG_BLOCK,set())),flush=True); "
             "print('FOREGROUND='+str(os.getpgrp()==os.tcgetpgrp(0)),flush=True); "
             "print('READY',flush=True); print('CHILD_INPUT='+sys.stdin.readline().strip(),flush=True)")
    source = ("import os,sys; from pathlib import Path; "
              "sys.path.insert(0,sys.argv[1]); from reality_lantern.runner import run_check; "
              "from reality_lantern.store import Store; group=os.getpgrp(); "
              "code=run_check([sys.executable,'-I','-S','-B','-c',sys.argv[3]],cwd=Path(sys.argv[2]),store=Store()); "
              "print('RESTORED='+str(group==os.tcgetpgrp(0)),flush=True); print('RESTORE_READY',flush=True); "
              "print('PARENT_INPUT='+sys.stdin.readline().strip(),flush=True); raise SystemExit(code)")
    status, output = pty_trial([*PYTHON, "-c", source, str(TOOLS), str(repo), child], repo,
                               [(b"READY", lambda fd, pid: os.write(fd, b"child-line\n")),
                                (b"RESTORE_READY", lambda fd, pid: os.write(fd, b"parent-line\n"))])
    assert status == 0, output
    for fact in ("TTY=True", "UNBLOCKED=True", "FOREGROUND=True", "RESTORED=True", "CHILD_INPUT=child-line", "PARENT_INPUT=parent-line"):
        assert fact in output
    assert checks(store)[0]["outcome"] == "exit0"


def test_real_pty_internal_tty_and_pid_pipe_descriptors_do_not_leak(store, repo):
    source = '''import os
extra = []
for fd in range(3, 128):
    try:
        os.fstat(fd)
    except OSError:
        pass
    else:
        extra.append(fd)
print('EXTRA_FDS=' + str(extra), flush=True)
'''
    status, output = pty_trial(cli_args(store, repo, source), repo, [])
    assert status == 0, output
    assert "EXTRA_FDS=[]" in output
    assert checks(store)[0]["outcome"] == "exit0"


def test_real_pty_exec_failure_restores_foreground_without_child_handle(store, repo):
    source = ("import os,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
              "from reality_lantern.runner import run_check; from reality_lantern.store import Store; "
              "group=os.getpgrp(); code=run_check(['/MISSING-EXEC-CANARY'],cwd=Path(sys.argv[2]),store=Store()); "
              "print('RESTORED='+str(group==os.tcgetpgrp(0)),flush=True); print('READY',flush=True); "
              "print('PARENT_INPUT='+sys.stdin.readline().strip(),flush=True); raise SystemExit(code)")
    status, output = pty_trial([*PYTHON, "-c", source, str(TOOLS), str(repo)], repo,
                               [(b"READY", lambda fd, pid: os.write(fd, b"parent-line\n"))])
    assert status == 127, output
    assert "RESTORED=True" in output and "PARENT_INPUT=parent-line" in output
    assert "MISSING-EXEC-CANARY" not in output
    assert checks(store)[0]["outcome"] == "spawn_failed"


def test_background_wrapper_does_not_steal_another_jobs_foreground(store, repo):
    child = ("import os,sys; print('CHILD='+str(os.getpid()),flush=True); "
             "print('BACKGROUND='+str(os.getpgrp()!=os.tcgetpgrp(1)),flush=True); "
             "print('CHILD_INPUT='+sys.stdin.readline().strip(),flush=True)")
    source = ("import os,sys,subprocess,json; group=os.getpgrp(); "
              "p=subprocess.Popen(json.loads(sys.argv[1]),stdin=subprocess.PIPE,text=True,process_group=0); "
              "p.communicate('background-line\\n',timeout=10); "
              "print('PARENT_FG='+str(group==os.tcgetpgrp(0)),flush=True); raise SystemExit(p.returncode)")
    status, output = pty_trial([*PYTHON, "-c", source, json.dumps(cli_args(store, repo, child))], repo, [])
    assert status == 0, output
    assert "BACKGROUND=True" in output and "PARENT_FG=True" in output
    assert "CHILD_INPUT=background-line" in output


def test_child_can_handle_forwarded_signal_and_its_real_exit_wins(store, repo):
    source = "import os,signal,sys; signal.signal(signal.SIGTERM,lambda *a: sys.exit(23)); print(os.getpid(),flush=True); signal.pause()"
    process = subprocess.Popen(cli_args(store, repo, source), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               start_new_session=True)
    child_pid = None
    try:
        child_pid = ready(process)
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=10)
        child_pid = None
        assert process.returncode == 23
        assert checks(store)[0]["outcome"] == "nonzero"
        assert checks(store)[0]["signal_number"] is None
    finally:
        stop(process, child_pid)


def test_child_ignoring_forwarded_signal_is_killed_after_bounded_grace(store, repo):
    source = "import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print(os.getpid(),flush=True); time.sleep(60)"
    process = subprocess.Popen(cli_args(store, repo, source), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               start_new_session=True)
    child_pid = None
    try:
        child_pid = ready(process)
        began = time.monotonic()
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=10)
        assert process.returncode == 137
        assert time.monotonic() - began < 7
        assert checks(store)[0]["signal_number"] == signal.SIGKILL
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
        child_pid = None
    finally:
        stop(process, child_pid)


def test_signal_during_popen_handle_assignment_is_queued_not_orphaned(store, repo, monkeypatch):
    monkeypatch.setattr(snapshot, "capture", synthetic)
    original = subprocess.Popen
    spawned = []
    previous = {number: signal.getsignal(number) for number in runner._SIGNALS}

    def popen(*args, **kwargs):
        child = original(*args, **kwargs)
        spawned.append(child)
        os.kill(os.getpid(), signal.SIGTERM)
        return child

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    assert runner.run_check([*PYTHON, "-c", "import time; time.sleep(60)"], cwd=repo, store=store) == 143
    assert len(spawned) == 1 and spawned[0].returncode == -signal.SIGTERM
    assert checks(store)[0]["signal_number"] == signal.SIGTERM
    assert {number: signal.getsignal(number) for number in runner._SIGNALS} == previous


def test_keyboardinterrupt_after_wait_reap_keeps_real_exit_not_fake_signal(store, repo, monkeypatch):
    monkeypatch.setattr(snapshot, "capture", synthetic)
    original = subprocess.Popen
    spawned = []

    def popen(*args, **kwargs):
        child = original(*args, **kwargs)
        wait = child.wait
        calls = []

        def interrupted_wait(*a, **kw):
            code = wait(*a, **kw)
            calls.append(1)
            if len(calls) == 1:
                raise KeyboardInterrupt()
            return code

        child.wait = interrupted_wait
        spawned.append(child)
        return child

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    assert runner.run_check(command(23), cwd=repo, store=store) == 23
    assert len(spawned) == 1 and spawned[0].returncode == 23
    assert checks(store)[0]["outcome"] == "nonzero"


def test_keyboardinterrupt_during_wait_forwards_and_reaps(store, repo, monkeypatch):
    monkeypatch.setattr(snapshot, "capture", synthetic)
    original = subprocess.Popen
    spawned = []

    def popen(*args, **kwargs):
        child = original(*args, **kwargs)
        wait = child.wait
        calls = []

        def interrupted_wait(*a, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise KeyboardInterrupt()
            return wait(*a, **kw)

        child.wait = interrupted_wait
        spawned.append(child)
        return child

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    assert runner.run_check([*PYTHON, "-c", "import time; time.sleep(60)"], cwd=repo, store=store) == 130
    assert len(spawned) == 1 and spawned[0].returncode == -signal.SIGINT
    assert checks(store)[0]["outcome"] == "signal"


def test_unexpected_wait_error_cleans_child_without_retry(store, repo, monkeypatch, capsys):
    monkeypatch.setattr(snapshot, "capture", synthetic)
    original = subprocess.Popen
    spawned = []

    def popen(*args, **kwargs):
        child = original(*args, **kwargs)
        wait = child.wait
        calls = []

        def broken_wait(*a, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("WAIT-SECRET-CANARY")
            return wait(*a, **kw)

        child.wait = broken_wait
        spawned.append(child)
        return child

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    assert runner.run_check([*PYTHON, "-c", "import time; time.sleep(60)"], cwd=repo, store=store) == 137
    assert len(spawned) == 1 and spawned[0].returncode == -signal.SIGKILL
    assert "WAIT-SECRET-CANARY" not in capsys.readouterr().err


def test_signal_at_wait_finally_boundary_cannot_escape_or_lose_completion(store, repo, monkeypatch):
    monkeypatch.setattr(snapshot, "capture", synthetic)
    fired = []

    def intercept(self, name, value):
        if (name == "waiting" and value is False and getattr(self, "waiting", False)
                and self.child is not None and self.child.returncode == 23 and not fired):
            fired.append(1)
            self._received(signal.SIGTERM)
        object.__setattr__(self, name, value)

    monkeypatch.setattr(runner._ChildSignals, "__setattr__", intercept)
    assert runner.run_check(command(23), cwd=repo, store=store) == 23
    assert fired == [1]
    assert checks(store)[0]["outcome"] == "nonzero"


def test_reentrant_signal_only_unwinds_wait_once(monkeypatch):
    control = runner._ChildSignals(lambda: None)
    received = []

    class Child:
        def send_signal(self, number):
            received.append(number)
            if len(received) == 1:
                assert not control.waiting
                control._received(signal.SIGTERM)

    child = Child()
    control.attach(child)
    monkeypatch.setattr(control, "_send", child.send_signal)
    control.waiting = True
    with pytest.raises(runner._WakeWait):
        control._received(signal.SIGTERM)
    assert received == [signal.SIGTERM, signal.SIGTERM]
    assert not control.waiting


def test_normal_wait_has_no_command_timeout(store, repo, monkeypatch):
    monkeypatch.setattr(snapshot, "capture", synthetic)
    original = subprocess.Popen
    timeouts = []

    def popen(*args, **kwargs):
        child = original(*args, **kwargs)
        wait = child.wait

        def tracked_wait(*a, **kw):
            timeouts.append(kw.get("timeout"))
            return wait(*a, **kw)

        child.wait = tracked_wait
        return child

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    assert runner.run_check(command(), cwd=repo, store=store) == 0
    assert timeouts == [None]


def test_older_runner_completion_cannot_hide_newer_pending_or_failure(store, repo):
    source = "import os,sys; print(os.getpid(),flush=True); raise SystemExit(int(sys.stdin.readline()))"
    processes, children = [], []
    try:
        for _ in range(2):
            process = subprocess.Popen(cli_args(store, repo, source), stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       start_new_session=True)
            processes.append(process)
            children.append(ready(process))
        state = store.read_state()
        latest_id = max(state["runs"].values(), key=lambda item: item["sequence"])["id"]
        assert len(state["runs"]) == 2 and not state["completions"]
        processes[0].communicate("0\n", timeout=10)
        children[0] = None
        assert checks(store)[0]["run_id"] == latest_id
        assert checks(store)[0]["outcome"] == "pending"
        processes[1].communicate("23\n", timeout=10)
        children[1] = None
        assert processes[1].returncode == 23
        assert len(checks(store)) == 1 and checks(store)[0]["outcome"] == "nonzero"
        assert len(store.read_state()["completions"]) == 2
    finally:
        for index, process in enumerate(processes):
            stop(process, children[index] if index < len(children) else None)


def test_real_completion_lock_timeout_keeps_pending_and_real_exit(store, repo, monkeypatch):
    monkeypatch.setattr(runner, "STORE_SECONDS", 0.025)
    calls, held = [], []

    def capture(cwd, **kwargs):
        value = synthetic(cwd, **kwargs)
        calls.append(1)
        if len(calls) == 2:
            handle = (store.state_dir / ".lock").open("rb")
            held.append(handle)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return value

    monkeypatch.setattr(snapshot, "capture", capture)
    try:
        began = time.monotonic()
        assert runner.run_check(command(23), cwd=repo, store=store) == 23
        assert time.monotonic() - began < 1
        assert len(store.read_state()["runs"]) == 1
        assert not store.read_state()["completions"]
    finally:
        for handle in held:
            handle.close()


def test_ignored_sigint_is_preserved_but_process_group_is_owned(store, repo):
    original = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        source = "import os,signal,sys; raise SystemExit(0 if signal.getsignal(signal.SIGINT)==signal.SIG_IGN and os.getpgrp()==os.getpid() and os.getpgrp()!=int(sys.argv[1]) else 23)"
        assert runner.run_check([*PYTHON, "-c", source, str(os.getpgrp())], cwd=repo, store=store) == 0
        assert signal.getsignal(signal.SIGINT) == signal.SIG_IGN
    finally:
        signal.signal(signal.SIGINT, original)


@pytest.mark.parametrize("when", ["before", "after"])
def test_keyboardinterrupt_in_snapshot_has_unknown_fallback_without_replay(store, repo, monkeypatch, when):
    calls = []

    def capture(cwd, **kwargs):
        calls.append(1)
        if (len(calls) == 1) == (when == "before"):
            raise KeyboardInterrupt()
        return synthetic(cwd, **kwargs)

    monkeypatch.setattr(snapshot, "capture", capture)
    status = runner.run_check(command(23), cwd=repo, store=store)
    assert status == (130 if when == "before" else 23)
    run, receipt = only_receipt(store)
    assert len(calls) == 2
    assert checks(store)[0]["validity"] == "unknown"
    assert not (run["before"] if when == "before" else receipt["after"])["complete"]


def test_stderr_failure_cannot_block_command_or_override_status(store, repo, monkeypatch):
    monkeypatch.setattr(store, "get_key", lambda **kw: (_ for _ in ()).throw(OSError("SECRET")))

    class Broken:
        def write(self, text):
            raise BrokenPipeError()

    monkeypatch.setattr(runner.sys, "stderr", Broken())
    assert runner.run_check(command(23), cwd=repo, store=store) == 23
