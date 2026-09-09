"""Bundle verifier adversarial tests；只用自建 bytes／tmp source／fake HOME。"""
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from lantern_suite import bundle


@pytest.fixture
def boot():
    return bundle.bootstrap()


@pytest.fixture
def desk(tmp_path, monkeypatch, boot):
    home, source = tmp_path / "home", tmp_path / "source"
    home.mkdir(mode=0o700)
    source.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    for name in boot.BUNDLE_FILES:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    return home, source


def minimal(boot):
    content = {name: b"# fixture\n" for name in boot.BUNDLE_FILES}
    files = [{"path": name, "sha256": boot.sha(data), "size": len(data), "mode": boot.file_mode(name)}
             for name, data in sorted(content.items())]
    content["MANIFEST.json"] = boot.canonical({"schema": boot.SCHEMA, "files": files,
        "content_digest": boot.sha(boot.canonical(files)), "base_revision": None})
    return content


def archive(boot, content, *, extra=(), overrides=None):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, data in [*sorted(content.items()), *extra]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o600 if name == "MANIFEST.json" else boot.file_mode(name)
            for field, value in (overrides or {}).get(name, {}).items():
                setattr(info, field, value)
            tar.addfile(info, io.BytesIO(data))
    return stream.getvalue()


def verify(boot, data):
    return boot.verify_bytes(data, expected_sha256=boot.sha(data))


def test_minimal_manifest_and_deterministic_build(desk, boot, tmp_path):
    value, files = verify(boot, archive(boot, minimal(boot)))
    assert len(files) == len(boot.BUNDLE_FILES) + 1
    first = bundle.build(repo_root=desk[1], output=tmp_path / "first.tar", base_revision="a" * 40)
    second = bundle.build(repo_root=desk[1], output=tmp_path / "second.tar", base_revision="a" * 40)
    assert first == second
    assert (tmp_path / "first.tar").read_bytes() == (tmp_path / "second.tar").read_bytes()
    assert first["file_count"] == len(boot.BUNDLE_FILES)
    assert first["sha256"] != first["content_digest"]
    assert bundle.verify(tmp_path / "first.tar", expected_sha256=first["sha256"])["ok"]


def test_base_revision_only_metadata_workingtree_digest(desk, boot, tmp_path):
    a = bundle.build(repo_root=desk[1], output=tmp_path / "a.tar", base_revision="a" * 40)
    b = bundle.build(repo_root=desk[1], output=tmp_path / "b.tar", base_revision="b" * 40)
    assert a["content_digest"] == b["content_digest"] and a["sha256"] != b["sha256"]
    path = desk[1] / "tools/lantern_suite/skill/SKILL.md"
    path.write_bytes(path.read_bytes() + b"\nfixture bytes\n")
    c = bundle.build(repo_root=desk[1], output=tmp_path / "c.tar", base_revision="a" * 40)
    assert c["content_digest"] != a["content_digest"]


def test_base_revision_change_keeps_existing_full_manifest_conflict(desk, boot, tmp_path):
    first, second = tmp_path / "a.tar", tmp_path / "b.tar"
    a = bundle.build(repo_root=desk[1], output=first, base_revision="a" * 40)
    b = bundle.build(repo_root=desk[1], output=second, base_revision="b" * 40)
    assert a["content_digest"] == b["content_digest"] and a["sha256"] != b["sha256"]
    root = Path(boot.stage(first, expected_sha256=a["sha256"], home=desk[0])["root"])
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    with pytest.raises(boot.BundleError, match="^bundle_root_conflict$"):
        boot.stage(second, expected_sha256=b["sha256"], home=desk[0])
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


def test_allowlist_excludes_foreign_state_home_tests_and_boot(desk, boot, tmp_path):
    for name in ("foreign.py", "memory/key", ".claude/settings.json", "CLAUDE.md", "tests/test_private.py", "tools/lantern_suite/untracked.py"):
        path = desk[1] / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("PRIVATE_SENTINEL")
    report = bundle.build(repo_root=desk[1], output=tmp_path / "bundle.tar")
    data = (tmp_path / "bundle.tar").read_bytes()
    assert b"PRIVATE_SENTINEL" not in data
    assert "tools/lantern_suite/advisory.py" in boot.BUNDLE_FILES
    assert "tools/lantern_suite/advisory.mjs" in boot.BUNDLE_FILES
    assert len(set(boot.BUNDLE_FILES)) == len(boot.BUNDLE_FILES)
    assert report["ok"]


@pytest.mark.parametrize("case", ["missing", "symlink", "hardlink", "personal", "nontext", "too_large"])
def test_build_refuses_unsafe_source_and_no_output(desk, boot, tmp_path, case):
    target = desk[1] / "tools/coedit_radar/api.py"
    if case == "missing":
        target.unlink()
    elif case in ("symlink", "hardlink"):
        other = desk[1] / "other"
        other.write_text("pass\n")
        target.unlink()
        target.symlink_to(other) if case == "symlink" else os.link(other, target)
    elif case == "personal":
        target.write_text("# /Users/private-person/private-file\n")
    elif case == "nontext":
        target.write_bytes(b"\xff\x00")
    else:
        target.write_bytes(b"x" * (boot.MAX_FILE + 1))
    output = tmp_path / "output.tar"
    with pytest.raises(Exception):
        bundle.build(repo_root=desk[1], output=output)
    assert not output.exists() and list(desk[0].iterdir()) == []


@pytest.mark.parametrize("root", ["/Users/PRIVATE-SENTINEL", "/home/PRIVATE-SENTINEL",
                                  "/Users/測試帳戶", "/home/測試帳戶", "/Users/privé"])
@pytest.mark.parametrize("ending", ["\n", "", "'", '"', "`", "/work\n"])
def test_build_private_home_tokens_refused_before_publish(desk, boot, monkeypatch, tmp_path, root, ending):
    # 全60檔真runtime副本；只改guide，唔用不完整／假code掩蓋filter缺口。
    guide = desk[1] / "tools/lantern_suite/PORTABLE.md"
    guide.write_bytes(guide.read_bytes() + ("\nLocal home: " + root + ending).encode("utf-8"))
    monkeypatch.setattr(bundle, "bootstrap", lambda: boot)
    monkeypatch.setattr(boot, "_put", lambda *_, **__: pytest.fail("私人路徑唔准到publication"))
    output = tmp_path / "output.tar"
    with pytest.raises(boot.BundleError, match="^bundle_personal_source$"):
        bundle.build(repo_root=desk[1], output=output)
    assert not output.exists() and list(desk[0].iterdir()) == []


def test_build_lexical_policy_does_not_reject_its_own_syntax(desk, boot, tmp_path):
    guide = desk[1] / "tools/lantern_suite/PORTABLE.md"
    guide.write_bytes(guide.read_bytes() + b'\nPolicy examples: /Users/[A-Za-z0-9_.-]+/ and /home/[^/]+\n')
    output = tmp_path / "baseline.tar"
    report = bundle.build(repo_root=desk[1], output=output)
    assert report["ok"] and report["file_count"] == len(boot.BUNDLE_FILES) == 60
    assert bundle.verify(output, expected_sha256=report["sha256"])["ok"]
    assert list(desk[0].iterdir()) == []


def test_build_source_race_rejected(desk, boot, monkeypatch, tmp_path):
    original = boot.safe_read
    count = 0
    def raced(path, *args, **kwargs):
        nonlocal count
        count += 1
        data = original(path, *args, **kwargs)
        return data + b"changed" if count > len(boot.BUNDLE_FILES) else data
    monkeypatch.setattr(boot, "safe_read", raced)
    monkeypatch.setattr(bundle, "bootstrap", lambda: boot)
    with pytest.raises(boot.BundleError, match="bundle_source_race"):
        bundle.build(repo_root=desk[1], output=tmp_path / "bundle.tar")
    assert not (tmp_path / "bundle.tar").exists()


def test_output_existing_or_link_never_clobbered(desk, boot, tmp_path):
    output = tmp_path / "bundle.tar"
    output.write_text("ORIGINAL")
    with pytest.raises(FileExistsError):
        bundle.build(repo_root=desk[1], output=output)
    assert output.read_text() == "ORIGINAL"
    link = tmp_path / "link.tar"
    link.symlink_to(output)
    with pytest.raises(FileExistsError):
        bundle.build(repo_root=desk[1], output=link)
    assert output.read_text() == "ORIGINAL"


@pytest.mark.parametrize("expected", [None, "", "a" * 63, "A" * 64, "g" * 64, "0" * 64])
def test_expected_sha_required_not_embedded_checksum(boot, expected):
    data = archive(boot, minimal(boot))
    with pytest.raises(boot.BundleError):
        boot.verify_bytes(data, expected_sha256=expected)


@pytest.mark.parametrize("name", ["../escape", "/absolute", "tools/../escape", "tools//evil", "extra.py", "tools/lantern_suite/api.py"])
def test_extra_traversal_absolute_and_duplicate_rejected(boot, name):
    data = archive(boot, minimal(boot), extra=[(name, b"pass")])
    with pytest.raises(boot.BundleError, match="bundle_entry_refused"):
        verify(boot, data)


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.FIFOTYPE,
                                   tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.XHDTYPE, tarfile.GNUTYPE_LONGNAME])
def test_links_and_special_even_allowlisted_name_rejected(boot, kind):
    name = "tools/lantern_suite/api.py"
    data = archive(boot, minimal(boot), overrides={name: {"type": kind, "linkname": "target"}})
    with pytest.raises(boot.BundleError):
        verify(boot, data)


@pytest.mark.parametrize("field,value", [("mode", 0o777), ("mode", 0o644), ("uid", 1), ("gid", 1),
                                         ("mtime", 1), ("uname", "private"), ("gname", "private")])
def test_tar_metadata_must_be_canonical(boot, field, value):
    data = archive(boot, minimal(boot), overrides={"tools/lantern_suite/api.py": {field: value}})
    with pytest.raises(boot.BundleError, match="bundle_noncanonical_header"):
        verify(boot, data)


@pytest.mark.parametrize("case", ["file_tamper", "missing", "extra_manifest", "duplicate_json", "digest", "mode", "size", "path", "missing_manifest"])
def test_manifest_completeness_and_hashes_even_with_new_archive_checksum(boot, case):
    content = minimal(boot)
    value = json.loads(content["MANIFEST.json"])
    if case == "file_tamper":
        content["tools/lantern_suite/api.py"] = b"EVIL"
    elif case == "missing":
        del content["tools/lantern_suite/api.py"]
    elif case == "missing_manifest":
        del content["MANIFEST.json"]
    elif case == "duplicate_json":
        content["MANIFEST.json"] = content["MANIFEST.json"][:-1] + b',"schema":"duplicate"}'
    else:
        if case == "extra_manifest":
            value["private"] = "PRIVATE"
        elif case == "digest":
            value["content_digest"] = "0" * 64
        elif case == "mode":
            value["files"][0]["mode"] = 0o777
        elif case == "size":
            value["files"][0]["size"] += 1
        else:
            value["files"][0]["path"] = "../evil"
        content["MANIFEST.json"] = boot.canonical(value)
    with pytest.raises(boot.BundleError):
        verify(boot, archive(boot, content))


@pytest.mark.parametrize("case", ["large_archive", "large_file", "large_manifest", "total", "members", "trailing", "second_archive", "truncated", "padding"])
def test_resource_limits_terminators_and_hidden_payload(boot, case):
    content = minimal(boot)
    if case == "large_file":
        content["tools/lantern_suite/api.py"] = b"x" * (boot.MAX_FILE + 1)
    elif case == "large_manifest":
        content["MANIFEST.json"] = b"x" * (boot.MAX_MANIFEST + 1)
    data = archive(boot, content)
    if case == "large_archive":
        data += bytes(boot.MAX_ARCHIVE)
    elif case == "total":
        boot.MAX_TOTAL = 10
    elif case == "members":
        boot.MAX_MEMBERS = 1
    elif case == "trailing":
        data += b"evil" + bytes(508)
    elif case == "second_archive":
        data += data
    elif case == "truncated":
        data = data[:-1]
    elif case == "padding":
        length = len(content["MANIFEST.json"])
        data = data[:512 + length] + b"x" + data[513 + length:]
    with pytest.raises(boot.BundleError):
        verify(boot, data)


def test_bad_archive_never_copies_or_executes(desk, boot, monkeypatch, tmp_path):
    output = tmp_path / "bad.tar"
    output.write_bytes(archive(boot, minimal(boot)))
    def forbidden(*args, **kwargs):
        pytest.fail("verify 前唔准 exec")
    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(boot.BundleError, match="bundle_sha_mismatch"):
        boot.install(output, expected_sha256="0" * 64, home=desk[0])
    assert list(desk[0].iterdir()) == []


def test_stage_copies_declared_modes_and_never_executes(desk, boot, monkeypatch, tmp_path):
    output = tmp_path / "minimal.tar"
    data = archive(boot, minimal(boot))
    output.write_bytes(data)
    monkeypatch.setattr(subprocess, "run", lambda *_, **__: pytest.fail("stage 唔准 exec"))
    result = boot.stage(output, expected_sha256=boot.sha(data), home=desk[0])
    root = Path(result["root"])
    assert root == desk[0] / ".local/share/kingdom-lanterns" / result["content_digest"]
    for path in root.rglob("*"):
        expected = 0o700 if path.is_dir() else (0o600 if path.name == "MANIFEST.json" else boot.file_mode(str(path.relative_to(root))))
        assert stat.S_IMODE(path.stat().st_mode) == expected
    assert boot.stage(output, expected_sha256=boot.sha(data), home=desk[0]) == result
    assert not (desk[0] / ".local/bin").exists()


@pytest.mark.parametrize("case", ["bytes", "mode", "extra", "symlink", "hardlink", "root_link"])
def test_existing_root_drift_refused_no_silent_upgrade(desk, boot, tmp_path, case):
    output = tmp_path / "bundle.tar"
    data = archive(boot, minimal(boot))
    output.write_bytes(data)
    result = boot.stage(output, expected_sha256=boot.sha(data), home=desk[0])
    root = Path(result["root"])
    file = root / "tools/lantern_suite/api.py"
    if case == "bytes":
        file.write_text("FOREIGN")
    elif case == "mode":
        file.chmod(0o644)
    elif case == "extra":
        (root / "extra.py").write_text("FOREIGN")
    elif case in ("symlink", "hardlink"):
        other = tmp_path / "foreign"
        other.write_text("FOREIGN")
        file.unlink()
        file.symlink_to(other) if case == "symlink" else os.link(other, file)
    else:
        moved = root.with_name("moved")
        root.rename(moved)
        root.symlink_to(moved)
    with pytest.raises(Exception):
        boot.stage(output, expected_sha256=boot.sha(data), home=desk[0])
    if case == "bytes":
        assert file.read_text() == "FOREIGN"


def test_fresh_standalone_e2e_source_deleted_permanent_commands(desk, boot, tmp_path):
    home, source = desk
    # Git 只用自建 source；bundle 版本唔依賴有 commit。
    subprocess.run(["git", "init", "-q", str(source)], check=True, env={**os.environ, "HOME": str(home)})
    output = tmp_path / "bundle.tar"
    built = bundle.build(repo_root=source, output=output)
    trusted = tmp_path / "trusted-bootstrap.py"
    shutil.copyfile(source / "tools/lantern-bootstrap.py", trusted)
    shutil.rmtree(source)
    child = subprocess.run([sys.executable, "-I", "-S", "-B", str(trusted), "install", "--bundle", str(output),
                            "--expected-sha256", built["sha256"], "--home", str(home)],
                           cwd=home, env={**os.environ, "HOME": str(home)}, capture_output=True, text=True, timeout=60)
    assert child.returncode == 0, child.stdout + child.stderr
    report = json.loads(child.stdout)
    assert report["ok"] and report["activation"] == "unverified"
    permanent = Path(report["root"])
    assert permanent.is_dir()
    output.unlink()
    trusted.unlink()
    for name in ("quota", "reality", "friction", "coedit", "lanterns"):
        for command in (("status", "--json"), ("doctor", "--json")):
            child = subprocess.run([str(home / ".local/bin" / ("kingdom-" + name)), *command],
                                   cwd=home, env={**os.environ, "HOME": str(home)},
                                   capture_output=True, text=True, timeout=15)
            assert child.returncode == 0, (name, child.stdout, child.stderr)
            value = json.loads(child.stdout)
            if command[0] == "doctor":
                assert value["installed"] and value["activation"] == "unverified"
    child = subprocess.run([str(home / ".local/bin/kingdom-lanterns"), "uninstall", "--json"],
                           cwd=home, env={**os.environ, "HOME": str(home)}, capture_output=True, text=True, timeout=15)
    assert child.returncode == 0 and json.loads(child.stdout)["ok"]
    assert permanent.is_dir()
    assert not any((home / ".local/bin" / ("kingdom-" + name)).exists()
                   for name in ("quota", "reality", "friction", "coedit", "lanterns"))


def test_existing_source_install_refuses_bundle_relocation(desk, boot, tmp_path):
    from quota_lantern import install as quota
    home, source = desk
    assert quota.install(home=home, repo_root=source, dry_run=False)["ok"]
    wrapper = home / ".local/bin/kingdom-quota"
    before = wrapper.read_bytes()
    output = tmp_path / "bundle.tar"
    built = bundle.build(repo_root=source, output=output)
    result = boot.install(output, expected_sha256=built["sha256"], home=home)
    assert not result["ok"] and wrapper.read_bytes() == before
    assert quota.doctor(home=home, repo_root=source)["installed"]
    assert not (home / ".local/bin/kingdom-reality").exists()


@pytest.mark.parametrize("preflight", ["ok", "refused", "unverified"])
def test_bootstrap_verified_root_preflight_before_owned_install(desk, boot, monkeypatch, tmp_path, preflight):
    data = archive(boot, minimal(boot))
    output = tmp_path / "bundle.tar"
    output.write_bytes(data)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        root = Path(command[4]).parents[1]
        assert (root / "MANIFEST.json").is_file()  # 必須先完成永久stage。
        assert command[:4] == [sys.executable, "-I", "-S", "-B"]
        assert kwargs["env"]["HOME"] == str(desk[0])
        assert command[5:9] == ["--home", str(desk[0]), "install", "--json"]
        assert not (desk[0] / ".local/state").exists()
        dry_run = len(calls) == 1
        value = {"ok": preflight != "refused" or not dry_run, "action": "install",
                 "dry_run": dry_run and preflight != "unverified"}
        return subprocess.CompletedProcess(command, 0 if value["ok"] else 1, boot.canonical(value), b"")
    monkeypatch.setattr(subprocess, "run", run)
    if preflight == "unverified":
        with pytest.raises(boot.BundleError, match="^bundle_install_unverified$"):
            boot.install(output, expected_sha256=boot.sha(data), home=desk[0])
    else:
        result = boot.install(output, expected_sha256=boot.sha(data), home=desk[0])
        assert result["ok"] is (preflight == "ok")
    assert calls[0][9:] == ["--dry-run"]
    assert len(calls) == (2 if preflight == "ok" else 1)
    if preflight == "ok":
        assert calls[1] == calls[0][:-1]
    assert not (desk[0] / ".local/bin").exists()


def suite_cli(home, *args):
    return subprocess.run([sys.executable, "-I", "-S", "-B", str(ROOT / "tools/kingdom-lanterns.py"),
                           "--home", str(home), *args], cwd=home, capture_output=True, text=True,
                          env={**os.environ, "HOME": str(home)}, timeout=60)


def test_suite_bundle_install_owned_permanent_root(desk, boot, tmp_path):
    home, source = desk
    output = tmp_path / "bundle.tar"
    built = bundle.build(repo_root=source, output=output)
    args = ("bundle", "install", "--bundle", str(output), "--expected-sha256", built["sha256"])
    child = suite_cli(home, *args)
    assert child.returncode == 0, child.stdout + child.stderr
    report = json.loads(child.stdout)
    assert report["ok"] and report["action"] == "install" and report["activation"] == "unverified"
    permanent = home / ".local/share/kingdom-lanterns" / built["content_digest"]
    assert Path(report["root"]) == permanent and permanent.is_dir()
    assert all(item["state"] == "created" for item in report["installation"]["products"].values())
    for name in ("quota", "reality", "friction", "coedit", "lanterns"):
        wrapper = home / ".local/bin" / ("kingdom-" + name)
        assert str(permanent) in wrapper.read_text()
    assert suite_cli(home, "doctor", "--json").returncode == 1  # 原source唔冒認permanent安裝。
    child = subprocess.run([str(home / ".local/bin/kingdom-lanterns"), "doctor", "--json"],
                           cwd=home, env={**os.environ, "HOME": str(home)}, capture_output=True, text=True, timeout=15)
    assert child.returncode == 0 and json.loads(child.stdout)["installed"]


def test_suite_bundle_bad_digest_no_write_or_exec(desk, boot, monkeypatch, tmp_path, capsys):
    from lantern_suite.cli import main
    output = tmp_path / "bundle.tar"
    output.write_bytes(archive(boot, minimal(boot)))
    monkeypatch.setattr(subprocess, "run", lambda *_, **__: pytest.fail("未驗bundle唔准exec"))
    args = ["--home", str(desk[0]), "bundle", "install", "--bundle", str(output), "--expected-sha256", "0" * 64]
    result = main(args)
    assert result == 1 and json.loads(capsys.readouterr().out)["ok"] is False
    assert list(desk[0].iterdir()) == []


def test_suite_bundle_install_refuses_existing_source_relocation(desk, boot, tmp_path):
    from quota_lantern import install as quota
    home, source = desk
    assert quota.install(home=home, repo_root=source, dry_run=False)["ok"]
    # bootstrap可stage，但所有原產品 artifacts 必須byte-for-byte不變。
    before = {path.relative_to(home): path.read_bytes() for path in home.rglob("*") if path.is_file()}
    output = tmp_path / "bundle.tar"
    built = bundle.build(repo_root=source, output=output)
    child = suite_cli(home, "bundle", "install", "--bundle", str(output), "--expected-sha256", built["sha256"])
    assert child.returncode == 1, child.stdout + child.stderr
    report = json.loads(child.stdout)
    assert not report["ok"] and not report["installation"]["ok"]
    assert report["installation"]["dry_run"] is True
    assert not (home / ".local/state/lantern-suite").exists()
    assert all((home / name).read_bytes() == data for name, data in before.items())
    assert quota.doctor(home=home, repo_root=source)["installed"]
    assert not (home / ".local/bin/kingdom-reality").exists()


def test_payload_tamper_after_valid_sha_never_copies_or_executes(desk, boot, monkeypatch, tmp_path):
    content = minimal(boot)
    content["tools/lantern_suite/api.py"] = b"PRIVATE_EXECUTION"
    data = archive(boot, content)
    path = tmp_path / "tampered.tar"
    path.write_bytes(data)
    monkeypatch.setattr(subprocess, "run", lambda *_, **__: pytest.fail("manifest 全驗前唔准 exec"))
    with pytest.raises(boot.BundleError, match="bundle_file_mismatch"):
        boot.install(path, expected_sha256=boot.sha(data), home=desk[0])
    assert list(desk[0].iterdir()) == []


@pytest.mark.parametrize("phase", ["before_copy", "after_copy", "before_rename", "after_rename"])
def test_copy_or_publish_failure_never_executes_or_claims_success(desk, boot, monkeypatch, tmp_path, phase):
    data = archive(boot, minimal(boot))
    path = tmp_path / "minimal.tar"
    path.write_bytes(data)
    original_put, original_rename = boot._put, boot.os.rename
    def put(*args, **kwargs):
        if phase == "before_copy":
            raise OSError("PRIVATE_COPY_ERROR")
        original_put(*args, **kwargs)
        if phase == "after_copy":
            raise OSError("PRIVATE_COPY_ERROR")
    def rename(*args, **kwargs):
        if phase == "before_rename":
            raise OSError("PRIVATE_PUBLISH_ERROR")
        original_rename(*args, **kwargs)
        if phase == "after_rename":
            raise OSError("PRIVATE_PUBLISH_ERROR")
    with monkeypatch.context() as patch:
        patch.setattr(boot, "_put", put)
        patch.setattr(boot.os, "rename", rename)
        patch.setattr(subprocess, "run", lambda *_, **__: pytest.fail("copy 失敗唔准 exec"))
        with pytest.raises(OSError):
            boot.install(path, expected_sha256=boot.sha(data), home=desk[0])
    assert not (desk[0] / ".local/bin").exists()
    if phase == "after_rename":
        assert boot.stage(path, expected_sha256=boot.sha(data), home=desk[0])["ok"]
    else:
        parent = desk[0] / ".local/share/kingdom-lanterns"
        assert any(item.name.startswith(".staging-") for item in parent.iterdir())


def test_home_may_be_safe_755_but_payload_remains_private(desk, boot, tmp_path):
    data = archive(boot, minimal(boot))
    path = tmp_path / "bundle.tar"
    path.write_bytes(data)
    desk[0].chmod(0o755)
    result = boot.stage(path, expected_sha256=boot.sha(data), home=desk[0])
    assert stat.S_IMODE(desk[0].stat().st_mode) == 0o755
    assert stat.S_IMODE(Path(result["root"]).stat().st_mode) == 0o700


def test_unsafe_parent_refuses_copy_without_chmod_or_symlink_traversal(desk, boot, tmp_path):
    data = archive(boot, minimal(boot))
    path = tmp_path / "bundle.tar"
    path.write_bytes(data)
    foreign = tmp_path / "foreign"
    foreign.mkdir(mode=0o700)
    (desk[0] / ".local").symlink_to(foreign)
    with pytest.raises(Exception):
        boot.stage(path, expected_sha256=boot.sha(data), home=desk[0])
    assert list(foreign.iterdir()) == []
