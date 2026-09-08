"""只包住明示命令一次；收據失敗唔擋工作，唔錄 argv／環境／程序輸出。"""
from __future__ import annotations

from contextlib import AbstractContextManager
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .core import SCHEMA_VERSION, RealityError, keyed_digest, utc_now, validate_snapshot
from .store import Store

SNAPSHOT_SECONDS = 2.0
STORE_SECONDS = 0.5
INTERRUPT_GRACE_SECONDS = 2.0
_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class _Warning:
    def __init__(self):
        self.reported = False

    def __call__(self, code=None):
        if not self.reported:
            self.reported = True
            _stderr("實況燈｜收據容量已滿，暫停記錄；原命令唔會重跑。\n"
                    if code == "state_full" else "實況燈｜收據或版本觀察未完整；唔當已驗證。\n")


def _stderr(text):
    try:
        sys.stderr.write(text)
        sys.stderr.flush()
    except Exception:
        # Broken pipe／關閉 stderr 都唔可以令原命令重跑或改 exit status。
        pass


class _Terminal(AbstractContextManager):
    """只移交自己已持有嘅 foreground tty；唔搶其他 job 嘅 terminal。"""
    def __init__(self, warn):
        self.warn = warn
        self.fd = None
        self.group = os.getpgrp()
        self.child = None
        self.report = None
        self.failed_pid = None

    def __enter__(self):
        try:
            fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY | os.O_CLOEXEC)
        except OSError:
            return self
        try:
            if os.tcgetpgrp(fd) == self.group:
                self.report = os.pipe()
                os.set_blocking(self.report[0], False)
                self.fd = fd
                fd = None
        except OSError:
            self.warn()
            if self.report is not None:
                for item in self.report:
                    os.close(item)
                self.report = None
        finally:
            if fd is not None:
                os.close(fd)
        return self

    @staticmethod
    def _foreground(fd, group):
        # Background group 亦要安全還 tty；只暫時 block SIGTTOU，原 mask 一定還原。
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTTOU})
        try:
            os.tcsetpgrp(fd, group)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)

    def child_setup(self):
        # Popen 已 setpgid，exec 前先交 tty，唔留 stdin read → SIGTTIN 嘅 race。
        # 呢個窄 preexec 只做 tty／signal-mask syscalls；CLI 本身係單 thread。
        os.write(self.report[1], str(os.getpid()).encode("ascii"))
        self._foreground(self.fd, os.getpgrp())
        os.set_inheritable(self.fd, False)
        os.set_inheritable(self.report[1], False)

    def options(self):
        result = {"process_group": 0}
        if self.fd is not None:
            result.update(preexec_fn=self.child_setup, pass_fds=(self.fd, self.report[1]))
        return result

    def restore(self):
        if self.fd is not None:
            try:
                if self.child is None and self.failed_pid is None:
                    # exec 失敗時 Popen 唔回 handle；只信自己 pipe 報返嘅 PID，唔猜其他 group。
                    data = os.read(self.report[0], 32)
                    if data.isdigit():
                        self.failed_pid = int(data)
                group = self.child.pid if self.child is not None else self.failed_pid
                if group is not None and os.tcgetpgrp(self.fd) == group:
                    self._foreground(self.fd, self.group)
            except BlockingIOError:
                pass
            except OSError:
                self.warn()

    def __exit__(self, *exc):
        try:
            self.restore()
        finally:
            if self.fd is not None:
                os.close(self.fd)
            if self.report is not None:
                for fd in self.report:
                    os.close(fd)


class _WakeWait(BaseException):
    """只喺已持有 child handle 嘅 wait 範圍打斷；唔打斷 Popen。"""


class _ChildSignals(AbstractContextManager):
    def __init__(self, warn):
        self.warn = warn
        self.child = None
        self.previous = {}
        self.queued = []
        self.waiting = False
        self.interrupted_at = None

    def __enter__(self):
        try:
            for number in _SIGNALS:
                previous = signal.getsignal(number)
                # Shell background job 嘅 SIG_IGN 要保留，唔偷偷改 job-control 語義。
                if previous != signal.SIG_IGN:
                    signal.signal(number, self._received)
                    self.previous[number] = previous
        except (ValueError, OSError):
            # 非 main thread 冇得裝 handlers；仍然只跑一次，唔阻工作。
            self.warn()
        return self

    def __exit__(self, *exc):
        self.waiting = False
        for number, previous in self.previous.items():
            signal.signal(number, previous)

    def _send(self, number):
        try:
            if self.child.poll() is None:
                # 呢個 group 由本次 Popen 建立，唔向 wrapper／外來工作嘅 group 發 signal。
                try:
                    os.killpg(self.child.pid, number)
                except ProcessLookupError:
                    self.child.send_signal(number)
        except OSError:
            # Child 可以啱啱退出；真正 outcome 仍然由 wait 決定。
            pass

    def _received(self, number, frame=None):
        # 先關 wake flag；重入 signal 唔再拋 exception，免得打斷第一次 unwind。
        wake, self.waiting = self.waiting, False
        if self.child is not None and self.interrupted_at is None:
            self.interrupted_at = time.monotonic()
        if self.child is None:
            if number not in self.queued:
                self.queued.append(number)
        else:
            self._send(number)
        if wake:
            raise _WakeWait()

    def attach(self, child):
        self.child = child
        queued, self.queued = self.queued, []
        for number in queued:
            # Grace 由真正開始轉發計，唔將之前 snapshot 開銷扣落 child。
            self._received(number)

    def wait(self):
        # 平常係 blocking wait，冇週期掃描／command timeout。收到中斷先有界收尾。
        while True:
            try:
                try:
                    self.waiting = True
                    timeout = None
                    if self.interrupted_at is not None:
                        timeout = max(0.0, self.interrupted_at + INTERRUPT_GRACE_SECONDS - time.monotonic())
                    return self.child.wait(timeout=timeout)
                finally:
                    self.waiting = False
            except _WakeWait:
                # 外層亦接住 wait 已返回、但 finally 未關 flag 嗰一刻嘅 signal。
                pass
            except KeyboardInterrupt:
                # 包括 wait 已經 reap、但 Python 喺回傳前收到 KeyboardInterrupt 嘅 race。
                self._received(signal.SIGINT)
            except subprocess.TimeoutExpired:
                break
        return self.cleanup()

    def cleanup(self):
        # 只清理本次擁有嘅 group；有 detach 嘅 descendants 唔追，leader 仍然要 reap。
        # SIGKILL 後 blocking reap；OS scheduling／不可中斷 kernel IO 冇硬時限保證。
        self.waiting = False
        self._send(signal.SIGKILL)
        while True:
            try:
                return self.child.wait()
            except KeyboardInterrupt:
                self._send(signal.SIGKILL)


def _unknown(cwd, key, previous=None):
    if previous is None:
        try:
            canonical = os.path.realpath(cwd)
        except (OSError, ValueError, RuntimeError):
            canonical = os.path.abspath(cwd)
        cwd_id = scope_id = keyed_digest(key, "path", canonical)
        worktree_id = None
    else:
        cwd_id, scope_id, worktree_id = (previous[name] for name in ("cwd_id", "scope_id", "worktree_id"))
    return {"schema": SCHEMA_VERSION, "scope_id": scope_id, "worktree_id": worktree_id,
            "cwd_id": cwd_id, "fingerprint": None, "complete": False,
            "observed_at": utc_now(), "diagnostics": ["snapshot_failed"],
            "coverage": {"paths": 0, "bytes": 0, "ignored": "excluded",
                         "environment": "unverified", "remote": "unverified"}}


def _capture(cwd, key, signals, warn, previous=None):
    try:
        # Import／capture／schema 失敗都降級合法 unknown，唔丟失可保存嘅 outcome。
        from .snapshot import capture
        value = capture(cwd, key=key, deadline=time.monotonic() + SNAPSHOT_SECONDS)
        validate_snapshot(value)
        return value
    except KeyboardInterrupt:
        signals._received(signal.SIGINT)
    except Exception:
        pass
    warn()
    return _unknown(cwd, key, previous)


def run_check(argv: list[str], *, cwd: Path, kind: str = "check", store: Store) -> int:
    """回 CLI status：exit 原值、signal 128+N、找唔到命令 127、其他 spawn failure 126。

    保留 stdio／環境；child 有獨立 owned process group。Foreground tty 喺 exec 前移交、
    wait 後還原，Ctrl-C 只到 child group；wrapper-targeted SIGINT/TERM/HUP 先轉發。
    轉發後兩秒仍未退出就 SIGKILL owned group 同 reap leader。唔追 detach descendants。
    只 main thread 裝 handlers；foreground preexec seam 只支援單-thread CLI，唔代理 Ctrl-Z。
    started_at／finished_at 由 Store 記 pending／completion 時點，包住真正 spawn/wait；
    後者包含 after capture 時間，唔冒認高精度 child duration。
    """
    warn = _Warning()
    key = before = run = None
    with _ChildSignals(warn) as signals, _Terminal(warn) as terminal:
        try:
            key = store.get_key(create=True, deadline=time.monotonic() + STORE_SECONDS)
            if type(key) is not bytes or len(key) != 32:
                raise ValueError()
            before = _capture(cwd, key, signals, warn)
            check_id = keyed_digest(key, "check", {"cwd_id": before["cwd_id"], "argv": argv})
            run = store.start_run(kind=kind, check_id=check_id, before=before,
                                  deadline=time.monotonic() + STORE_SECONDS)
        except KeyboardInterrupt:
            signals._received(signal.SIGINT)
            warn()
        except RealityError as exc:
            warn(exc.code)
        except Exception:
            warn()

        outcome, exit_code, signal_number = "spawn_failed", None, None
        status = 126
        try:
            # 冇 shell expansion、PIPE 或 env 改寫；隔離 group 防 terminal signal 雙重轉發。
            child = subprocess.Popen(argv, cwd=cwd, shell=False, **terminal.options())
        except FileNotFoundError:
            status = 127
            _stderr("實況燈｜原命令未能啟動。\n")
        except Exception:
            _stderr("實況燈｜原命令未能啟動。\n")
        else:
            terminal.child = child
            signals.attach(child)
            try:
                code = signals.wait()
            except Exception:
                warn()
                # wait bookkeeping 出錯唔留 child／唔重跑；只接收 cleanup 真正 wait 結果。
                code = signals.cleanup()
            if code < 0:
                outcome, signal_number = "signal", -code
                status = 128 + signal_number
            else:
                outcome, exit_code = ("exit0" if code == 0 else "nonzero"), code
                status = code

        terminal.restore()
        if run is not None:
            try:
                after = _capture(cwd, key, signals, warn, previous=before)
                # finish_run 自己喺鎖內核對開始 generation；唔自行 observe 復活 stale。
                store.finish_run(run["id"], outcome=outcome, exit_code=exit_code,
                                 signal_number=signal_number, after=after,
                                 deadline=time.monotonic() + STORE_SECONDS)
            except KeyboardInterrupt:
                signals._received(signal.SIGINT)
                warn()
            except RealityError as exc:
                warn(exc.code)
            except Exception:
                warn()
        return status
