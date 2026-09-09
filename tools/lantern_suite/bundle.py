"""Deterministic working-tree bundle；只帶 standalone verifier 嘅逐檔 allowlist。"""
from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path
import re
import tarfile


def bootstrap():
    path = Path(__file__).resolve().parents[1] / "lantern-bootstrap.py"
    spec = importlib.util.spec_from_file_location("_kingdom_lantern_trusted_bootstrap", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def allowlist():
    return tuple(sorted(bootstrap().BUNDLE_FILES))


def build(*, repo_root, output, base_revision=None):
    """版本由 working-tree bytes 決定；base_revision 只係明示 metadata，唔執行 git。"""
    boot = bootstrap()
    repo, target = Path(repo_root), Path(output)
    boot.require(repo.is_absolute() and target.is_absolute(), "bundle_unsafe_path")
    boot.require(base_revision is None or (type(base_revision) is str and
                 re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", base_revision) is not None), "bundle_invalid_revision")
    content, files, total = {}, [], 0
    for name in sorted(boot.BUNDLE_FILES):
        data = boot.safe_read(repo / name)
        try:
            text = data.decode("utf-8")
        except UnicodeError:
            raise boot.BundleError("bundle_nontext_source") from None
        # 有界 lexical guard，唔係通用 secret scanner。Unicode token包括combining marks；
        # quotes／空白／EOF都可結束HOME根。排除regex／template語法，唔自攔policy source。
        personal = re.search(r"/(?:Users|home)/" r"""[^/\s\\'"`<>{}\[\]()*?|:;=]+(?=/|[\s'"`]|$)""", text)
        boot.require("\x00" not in text and personal is None, "bundle_personal_source")
        content[name] = data
        total += len(data)
        boot.require(total <= boot.MAX_TOTAL, "bundle_total_limit")
        files.append({"path": name, "sha256": boot.sha(data), "size": len(data), "mode": boot.file_mode(name)})
    manifest = {"schema": boot.SCHEMA, "content_digest": boot.sha(boot.canonical(files)),
                "base_revision": base_revision, "files": files}
    content["MANIFEST.json"] = boot.canonical(manifest)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in sorted(content.items()):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o600 if name == "MANIFEST.json" else boot.file_mode(name)
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(data))
    data = stream.getvalue()
    digest = boot.sha(data)
    boot.verify_bytes(data, expected_sha256=digest)
    # 全部讀完／封包後再核對每份 source；writer 未 freeze 就可能明確拒絕。
    for name in boot.BUNDLE_FILES:
        boot.require(boot.safe_read(repo / name) == content[name], "bundle_source_race")
    # 唔覆寫既有 artifact，亦唔跟 output symlink。Publish 失敗唔猜有無 durable。
    with boot.directory(target.parent) as fd:
        boot._put(fd, target.name, data, 0o600)
    return {"ok": True, "action": "build", "sha256": digest,
            "content_digest": manifest["content_digest"], "base_revision": base_revision,
            "file_count": len(files), "bytes": len(data)}


def verify(bundle, *, expected_sha256):
    return bootstrap().verify(bundle, expected_sha256=expected_sha256)


def stage(bundle, *, expected_sha256, home):
    return bootstrap().stage(bundle, expected_sha256=expected_sha256, home=home)


def install(bundle, *, expected_sha256, home):
    return bootstrap().install(bundle, expected_sha256=expected_sha256, home=home)
