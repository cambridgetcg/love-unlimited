"""真 temporary 檔案反證 nofollow／bounds／race；唔碰 host secrets。"""
import os
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from coedit_radar import api
from coedit_radar.core import MAX_FILE, end_time
from coedit_radar import observer


@pytest.fixture
def desk(tmp_path, monkeypatch):
    home = tmp_path.resolve() / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    file = tmp_path.resolve() / "normal.txt"
    file.write_text("hello\n")
    return home, file


def sample(file):
    return observer.observe_file(str(file), key=b"k" * 32, deadline=end_time())


@pytest.mark.parametrize("name", [".env", ".env.prod", "credentials.json", "auth.json", "cookies.txt",
                                     "id_ed25519", ".claude/settings.json", ".codex/config.toml",
                                     ".aws/config", "Library/Keychains/login.keychain-db", "token.json.bak"])
def test_secret_open_never_called(desk, monkeypatch, name):
    home, file = desk
    secret = file.parent / name
    def forbidden(*args, **kwargs):
        pytest.fail("secret path 連 open 都唔應調用")
    monkeypatch.setattr(os, "open", forbidden)
    assert sample(secret)["reason"] == "secret_path"
    assert api.observe(home=home, path=secret, session="s")["reason"] == "secret_path"
    assert api.compare(home=home, path=secret, session="s")["reason"] == "secret_path"


@pytest.mark.parametrize("name", ["file.pdf", "file.ipynb", "file.png", "file.db"])
def test_unsupported_extension_no_open(desk, monkeypatch, name):
    _, file = desk
    monkeypatch.setattr(os, "open", lambda *a, **k: pytest.fail("unsupported path 唔應 open"))
    assert sample(file.with_name(name))["status"] == "unknown"


@pytest.mark.parametrize("data", [b"hello\x00world", b"\xff", b"hello\x7f", b"%PDF-1.7\nhello"])
def test_binary_pdf_magic_unknown(desk, data):
    _, file = desk
    file.write_bytes(data)
    assert sample(file)["status"] == "unknown"


def test_regular_symlink_hardlink_fifo_directory(desk):
    _, file = desk
    link = file.with_name("link")
    link.symlink_to(file)
    assert sample(link)["status"] == "unknown"
    hard = file.with_name("hard")
    os.link(file, hard)
    assert sample(file)["reason"] == "unsafe_file"
    hard.unlink()
    pipe = file.with_name("pipe")
    os.mkfifo(pipe)
    assert sample(pipe)["reason"] == "unsafe_file"
    assert sample(file.parent)["status"] == "unknown"


def test_ancestor_symlink_not_followed(desk):
    _, file = desk
    alias = file.parent / "alias"
    alias.symlink_to(file.parent, target_is_directory=True)
    assert sample(alias / file.name)["status"] == "unknown"


def test_exact_file_byte_cap(desk):
    _, file = desk
    file.write_bytes(b"x" * MAX_FILE)
    value = sample(file)
    assert value["status"] == "observed"
    assert value["size"] == MAX_FILE
    assert not observer.read_bounds_supported(value)
    with file.open("ab") as handle:
        handle.write(b"x")
    assert sample(file)["reason"] == "file_too_large"


@pytest.mark.parametrize("data,expected", [(b"x\n" * 2000, True), (b"x\n" * 2001, False),
                                             (b"x" * 2000, True), (b"x" * 2001, False),
                                             (b"", False), (b"x\r\n", False)])
def test_implicit_read_bounds(desk, data, expected):
    _, file = desk
    file.write_bytes(data)
    assert observer.read_bounds_supported(sample(file)) is expected


@pytest.mark.parametrize("data,lines,trailing_lf", [
    (b"", 0, False), (b"\n", 1, True), (b"x", 1, False), (b"x\n\n", 2, True),
    ("狀態：完成。\n註記：模擬另一個 writer 已更新。\n".encode("utf-8"), 2, True),
    ("狀態：完成。\n註記：模擬另一個 writer 已更新。".encode("utf-8"), 2, False),
])
def test_trailing_lf_is_ephemeral_observation_metadata(desk, data, lines, trailing_lf):
    _, file = desk
    file.write_bytes(data)
    observed = sample(file)
    assert observed["status"] == "observed"
    assert observed["lines"] == lines
    assert observed["trailing_lf"] is trailing_lf


@pytest.mark.parametrize("lines", [1, 1999, 2000, 2001])
@pytest.mark.parametrize("trailing_lf", [False, True])
@pytest.mark.parametrize("extra_line", [0, 1])
def test_read_response_physical_and_terminal_line_bounds(desk, lines, trailing_lf, extra_line):
    _, file = desk
    data = b"\n".join([b"x"] * lines) + (b"\n" if trailing_lf else b"")
    file.write_bytes(data)
    observed = sample(file)
    reported = lines + extra_line
    response = {"type": "text", "file": {"filePath": str(file), "content": "CANARY",
                "numLines": reported, "startLine": 1, "totalLines": reported}}
    assert observed["lines"] == lines
    assert observed["trailing_lf"] is trailing_lf
    expected = reported <= 2000 and (extra_line == 0 or trailing_lf)
    assert observer.read_response_supported(response, path=str(file), lines=observed["lines"],
                                            trailing_lf=observed["trailing_lf"]) is expected


@pytest.mark.parametrize("inputs", [{"file_path": "/a", "offset": 0}, {"file_path": "/a", "limit": 2000},
                                     {"file_path": "/a", "pages": "1"}, {"path": "/a"}, {}, None])
def test_explicit_offset_limit_never_become_full(inputs):
    assert observer.read_request_supported(inputs) is False


def test_mutation_during_read_unknown(desk, monkeypatch):
    _, file = desk
    original = os.read
    fired = False
    def race(fd, size):
        nonlocal fired
        data = original(fd, size)
        if not fired:
            fired = True
            file.write_text("raced\n")
        return data
    monkeypatch.setattr(os, "read", race)
    assert sample(file)["reason"] == "file_race"


def test_same_content_replacement_during_read_unknown(desk, monkeypatch):
    _, file = desk
    replacement = file.with_name("replacement")
    replacement.write_bytes(file.read_bytes())
    original = os.read
    fired = False
    def race(fd, size):
        nonlocal fired
        data = original(fd, size)
        if not fired:
            fired = True
            replacement.replace(file)
        return data
    monkeypatch.setattr(os, "read", race)
    assert sample(file)["reason"] == "file_race"


def test_ancestor_swap_during_read_unknown(desk, monkeypatch):
    _, file = desk
    directory = file.parent / "nested"
    directory.mkdir()
    target = directory / "text"
    target.write_text("hello")
    original = os.read
    fired = False
    def race(fd, size):
        nonlocal fired
        data = original(fd, size)
        if not fired:
            fired = True
            directory.rename(file.parent / "moved")
            directory.mkdir()
            (directory / "text").write_text("hello")
        return data
    monkeypatch.setattr(os, "read", race)
    assert sample(target)["reason"] == "file_race"


def test_same_size_same_mtime_uses_ctime(desk):
    home, file = desk
    api.observe(home=home, path=file, session="s")
    before = file.stat()
    file.write_text("world\n")
    os.utime(file, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert api.compare(home=home, path=file, session="s")["status"] == "changed"


def test_missing_ancestor_is_not_proven_deletion(desk):
    _, file = desk
    assert sample(file.parent / "absent" / "file")["reason"] == "file_unavailable"


def test_read_response_shape_does_not_inspect_content():
    class Canary(str):
        def __eq__(self, other):
            pytest.fail("唔應比較輸出文字")
        def __len__(self):
            pytest.fail("唔應計輸出文字長度")
        def encode(self, *args, **kwargs):
            pytest.fail("唔應 hash 輸出文字")
    value = {"type": "text", "file": {"filePath": "/a", "content": Canary("CANARY"),
             "numLines": 1, "startLine": 1, "totalLines": 1}}
    assert observer.read_response_supported(value, path="/a", lines=1)
    value["file"].update(numLines=2, totalLines=2)
    assert observer.read_response_supported(value, path="/a", lines=1, trailing_lf=True)
    assert not observer.read_response_supported(value, path="/a", lines=1)
    assert not observer.read_response_supported(value, path="/a", lines=1, trailing_lf=1)
    value["file"]["content"] = []
    assert not observer.read_response_supported(value, path="/a", lines=1, trailing_lf=True)
