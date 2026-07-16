import importlib.util
import io
import json
import math
import os
import sqlite3
import stat
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "outreach_store.py"
SPEC = importlib.util.spec_from_file_location("outreach_store", MODULE_PATH)
store = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(store)
sys.path.insert(0, str(MODULE_PATH.parent))
sys.modules.setdefault("outreach_store", store)
import relations_pipeline as pipeline  # noqa: E402


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / "private" / "outreach.sqlite3"
    connection = store.connect(path)
    yield connection, path
    connection.close()


def add_contact(connection, contact_id="helia", recipient="maintainer@example.test"):
    store.add_contact(
        connection,
        contact_id,
        "Helia",
        recipient,
        "email",
        "content-addressed-storage",
        "https://ipfs.github.io/helia/",
    )
    store.set_readiness(connection, contact_id, "ready", "focused adapter tests passed")


def draft(connection, contact_id="helia", body="A small tested adapter."):
    return store.draft_message(connection, contact_id, "Interop example", body)


def approve(connection, message_id):
    work_id = ready_work(connection, message_id)
    store.transition_message(
        connection,
        message_id,
        "draft",
        "reviewed",
        "message_reviewed",
        {"reviewed_by": "nuance+crucible+vigil"},
        work_id,
    )
    store.transition_message(
        connection,
        message_id,
        "reviewed",
        "awaiting_approval",
        "approval_requested",
        work_id=work_id,
    )
    expected_hash = store.require_message(connection, message_id)["content_hash"]
    return store.approve_message(
        connection, message_id, "yu", 24, expected_hash, work_id
    )


def ready_work(connection, message_id):
    pipeline.ensure_schema(connection)
    message = store.require_message(connection, message_id)
    work_id = pipeline.create_work(
        connection,
        kind="outreach",
        title="Test outreach artifact",
        objective="Exercise the approval-bound message workflow.",
        done_when="The exact reviewed snapshot can be approved once.",
        owner_role="loom",
        next_action="Record the fixture evidence.",
        contact_id=message["contact_id"],
        source_ref="test:fixture",
    )
    pipeline.link_message(connection, work_id, message_id)
    pipeline.advance_work(connection, work_id, "research", "loom")
    pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="source",
        reference="test:source",
        claim="Fixture context is explicit.",
        result="info",
        added_by="loom",
    )
    pipeline.advance_work(connection, work_id, "planned", "loom")
    pipeline.advance_work(connection, work_id, "building", "loom")
    pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="artifact",
        reference="test:artifact",
        claim="The test message is the reviewed artifact.",
        result="pass",
        added_by="builder",
        artifact_hash="a" * 64,
    )
    pipeline.advance_work(connection, work_id, "verifying", "loom")
    pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="test",
        reference="test:approval-flow",
        claim="The offline fixture passed.",
        result="pass",
        added_by="vigil",
    )
    pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="contact_basis",
        reference="test:public-channel",
        claim="One relevant test gesture is in scope.",
        result="pass",
        added_by="vigil",
    )
    pipeline.advance_work(connection, work_id, "review", "loom")
    for role in sorted(pipeline.REVIEW_ROLES):
        pipeline.add_review(
            connection,
            work_id,
            role=role,
            verdict="pass",
            summary="Current fixture snapshot reviewed.",
        )
    pipeline.advance_work(connection, work_id, "ready", "loom")
    return work_id


def message_state(connection, message_id):
    return connection.execute(
        "SELECT state FROM messages WHERE id=?", (message_id,)
    ).fetchone()[0]


def test_owner_only_database_permissions(ledger):
    _, path = ledger

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_legacy_unbound_approval_migrates_fail_closed(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE contacts (
          id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT,maturity TEXT,priority INTEGER,
          project_url TEXT,public_channel TEXT,fit TEXT,first_gesture TEXT,readiness_gate TEXT,
          recipient TEXT,channel TEXT,endpoint_fingerprint TEXT,
          readiness_status TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL
        );
        CREATE TABLE messages (
          id TEXT PRIMARY KEY,contact_id TEXT NOT NULL,channel TEXT NOT NULL,recipient TEXT NOT NULL,
          endpoint_fingerprint TEXT NOT NULL,subject TEXT NOT NULL,body TEXT NOT NULL,
          content_hash TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL
        );
        CREATE TABLE approvals (
          id TEXT PRIMARY KEY,message_id TEXT NOT NULL,channel TEXT NOT NULL,recipient TEXT NOT NULL,
          content_hash TEXT NOT NULL,approved_by TEXT NOT NULL,approved_at TEXT NOT NULL,
          expires_at TEXT NOT NULL,consumed_at TEXT,revoked_at TEXT
        );
        INSERT INTO contacts VALUES(
          'legacy','Legacy',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,
          'legacy@example.test','email','fingerprint','ready','research','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z'
        );
        INSERT INTO messages VALUES(
          'msg-legacy','legacy','email','legacy@example.test','fingerprint','Subject','Body',
          'hash','approved','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z'
        );
        INSERT INTO approvals VALUES(
          'approval-legacy','msg-legacy','email','legacy@example.test','hash','yu',
          '2026-01-01T00:00:00Z','2099-01-01T00:00:00Z',NULL,NULL
        );
        """
    )
    legacy.commit()
    legacy.close()

    migrated = store.connect(path)
    try:
        approval = migrated.execute(
            "SELECT work_id,work_snapshot_hash,revoked_at FROM approvals"
        ).fetchone()
        assert approval["work_id"] is None
        assert approval["work_snapshot_hash"] is None
        assert approval["revoked_at"]
        assert store.require_message(migrated, "msg-legacy")["state"] == "draft"
        assert migrated.execute(
            "SELECT 1 FROM outreach_meta WHERE key='pipeline_bound_approvals_v1'"
        ).fetchone()
        with pytest.raises(store.OutreachError, match="expected approved"):
            store.export_message(migrated, "msg-legacy")
    finally:
        migrated.close()


def test_export_requires_exact_single_use_approval(ledger):
    connection, _ = ledger
    add_contact(connection)
    message_id = draft(connection)

    with pytest.raises(store.OutreachError, match="expected approved"):
        store.export_message(connection, message_id)

    approval_id = approve(connection, message_id)
    exported = store.export_message(connection, message_id)

    assert approval_id.startswith("approval-")
    assert exported == {
        "message_id": message_id,
        "channel": "email",
        "recipient": "maintainer@example.test",
        "subject": "Interop example",
        "body": "A small tested adapter.",
        "content_hash": exported["content_hash"],
    }
    assert len(exported["content_hash"]) == 64
    assert message_state(connection, message_id) == "exported"

    with pytest.raises(store.OutreachError, match="expected approved"):
        store.export_message(connection, message_id)


def test_build_first_readiness_gate_blocks_drafting(ledger):
    connection, _ = ledger
    store.add_contact(
        connection,
        "blocked",
        "Blocked Protocol",
        "maintainer@example.test",
        "email",
        "protocol",
        "https://example.test",
    )

    with pytest.raises(store.OutreachError, match="readiness is pending"):
        store.draft_message(connection, "blocked", "Too soon", "No artifact yet")
    with pytest.raises(store.OutreachError, match="requires concrete"):
        store.set_readiness(connection, "blocked", "ready", "")

    store.set_readiness(connection, "blocked", "ready", "fixture passes")
    assert store.draft_message(connection, "blocked", "Now ready", "Artifact attached")


def test_readiness_regression_revokes_approval_and_blocks_export(ledger):
    connection, _ = ledger
    add_contact(connection)
    message_id = draft(connection)
    approve(connection, message_id)

    store.set_readiness(connection, "helia", "blocked", "adapter regression")

    assert message_state(connection, message_id) == "reviewed"
    assert connection.execute(
        "SELECT revoked_at FROM approvals WHERE message_id=?", (message_id,)
    ).fetchone()[0]
    work_id = connection.execute(
        "SELECT work_id FROM approvals WHERE message_id=?", (message_id,)
    ).fetchone()[0]
    with pytest.raises(store.OutreachError, match="readiness is blocked"):
        store.approve_message(connection, message_id, "yu", 24, "0" * 64, work_id)
    with pytest.raises(store.OutreachError, match="expected approved"):
        store.export_message(connection, message_id)


def test_revision_changes_hash_and_revokes_approval(ledger):
    connection, _ = ledger
    add_contact(connection)
    message_id = draft(connection)
    approve(connection, message_id)
    before = store.require_message(connection, message_id)["content_hash"]

    store.revise_message(connection, message_id, None, "A corrected, sourced adapter.")

    after = store.require_message(connection, message_id)["content_hash"]
    approval = connection.execute(
        "SELECT revoked_at FROM approvals WHERE message_id=?", (message_id,)
    ).fetchone()
    assert before != after
    assert message_state(connection, message_id) == "draft"
    assert approval["revoked_at"]
    with pytest.raises(store.OutreachError, match="expected approved"):
        store.export_message(connection, message_id)


def test_expired_approval_fails_closed(ledger):
    connection, _ = ledger
    add_contact(connection)
    message_id = draft(connection)
    approval_id = approve(connection, message_id)
    connection.execute(
        "UPDATE approvals SET expires_at='2000-01-01T00:00:00Z' WHERE id=?",
        (approval_id,),
    )
    connection.commit()

    with pytest.raises(store.OutreachError, match="approval expired"):
        store.export_message(connection, message_id)


def test_do_not_contact_cancels_pending_and_is_a_hard_gate(ledger):
    connection, _ = ledger
    add_contact(connection)
    first = draft(connection)
    approve(connection, first)

    store.suppress_contact(connection, "helia", "declined")

    assert store.require_contact(connection, "helia")["state"] == "do_not_contact"
    assert message_state(connection, first) == "cancelled"
    assert connection.execute(
        "SELECT COUNT(*) FROM approvals WHERE revoked_at IS NOT NULL"
    ).fetchone()[0] == 1
    with pytest.raises(store.OutreachError, match="do_not_contact"):
        store.draft_message(connection, "helia", "No", "Do not send")
    with pytest.raises(store.OutreachError, match="do_not_contact"):
        store.export_message(connection, first)


def test_reply_closes_the_open_gesture_and_allows_a_new_one(ledger):
    connection, _ = ledger
    add_contact(connection)
    first = draft(connection)
    approve(connection, first)
    store.export_message(connection, first)
    store.transition_message(
        connection,
        first,
        "exported",
        "sent",
        "message_marked_sent",
        {"provider_id": "manual:test-receipt"},
    )
    with pytest.raises(store.OutreachError, match="open sent gesture"):
        draft(connection, body="An unsolicited follow-up that must be blocked.")

    store.record_reply(connection, first, "manual:inbound:test:1:42")

    assert message_state(connection, first) == "replied"
    assert draft(connection, body="A reply-aware next gesture.")


def test_only_one_open_gesture_per_recipient(ledger):
    connection, _ = ledger
    add_contact(connection)
    draft(connection)

    with pytest.raises(store.OutreachError, match="open draft gesture"):
        draft(connection, body="No parallel sequence")


def test_events_are_append_only(ledger):
    connection, _ = ledger
    add_contact(connection)

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute("UPDATE events SET event_type='rewritten'")
    connection.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute("DELETE FROM events")
    connection.rollback()


def test_public_seed_is_idempotent_and_never_overwrites_private_endpoint(ledger, tmp_path):
    connection, _ = ledger
    seed_file = tmp_path / "targets.json"
    seed_file.write_text(
        json.dumps(
            {
                "targets": [
                    {
                        "id": "helia",
                        "name": "IPFS / Helia",
                        "kind": "content-addressed-storage",
                        "maturity": "established",
                        "priority": 2,
                        "project_url": "https://ipfs.github.io/helia/",
                        "public_channel": "https://discuss.ipfs.tech/",
                        "fit": "CID and BlockStore overlap",
                        "first_gesture": "Build an adapter",
                        "readiness_gate": "Tests pass",
                    }
                ]
            }
        )
    )

    assert store.seed_contacts(connection, seed_file) == 1
    store.add_contact(
        connection,
        "helia",
        None,
        "maintainer@example.test",
        "email",
        None,
        None,
    )
    assert store.seed_contacts(connection, seed_file) == 1

    contact = store.require_contact(connection, "helia")
    assert contact["recipient"] == "maintainer@example.test"
    assert contact["public_channel"] == "https://discuss.ipfs.tech/"
    assert contact["maturity"] == "established"
    assert contact["priority"] == 2
    assert contact["readiness_status"] == "pending"
    assert connection.execute(
        "SELECT COUNT(*) FROM contacts WHERE id='helia'"
    ).fetchone()[0] == 1


def test_blocked_seed_revokes_existing_approval(ledger, tmp_path):
    connection, _ = ledger
    add_contact(connection)
    message_id = draft(connection)
    approve(connection, message_id)
    seed_file = tmp_path / "blocked-target.json"
    seed_file.write_text(
        json.dumps(
            {
                "targets": [
                    {
                        "id": "helia",
                        "name": "Helia",
                        "readiness_gate": "BLOCKED: adapter regression",
                    }
                ]
            }
        )
    )

    store.seed_contacts(connection, seed_file)

    assert store.require_contact(connection, "helia")["readiness_status"] == "blocked"
    assert message_state(connection, message_id) == "reviewed"
    assert connection.execute(
        "SELECT revoked_at FROM approvals WHERE message_id=?", (message_id,)
    ).fetchone()[0]
    store.set_readiness(connection, "helia", "ready", "regression repaired")
    with pytest.raises(store.OutreachError, match="expected approved"):
        store.export_message(connection, message_id)


def test_list_is_redacted_and_preview_does_not_mutate(ledger, capsys):
    connection, path = ledger
    add_contact(connection)
    message_id = draft(connection)
    connection.close()

    assert store.run(["contact", "list"], path=path) == 0
    listing = capsys.readouterr().out
    assert "maintainer@example.test" not in listing

    assert store.run(["message", "preview", message_id], path=path) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["recipient"] == "m***@example.test"
    assert preview["state"] == "draft"

    reopened = store.connect(path)
    try:
        assert message_state(reopened, message_id) == "draft"
        assert reopened.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='message_exported'"
        ).fetchone()[0] == 0
    finally:
        reopened.close()


def test_url_recipient_mask_never_leaks_path_query_or_userinfo():
    value = "HTTPS://user:pass@forum.example/private/@alice?token=not-a-secret"
    masked = store.mask_recipient(value)

    assert masked == "https://forum.example/…"
    assert "alice" not in masked
    assert "token" not in masked
    assert "user" not in masked
    assert store.endpoint_fingerprint(
        "forum", "HTTPS://Forum.Example:443"
    ) == store.endpoint_fingerprint("FORUM", "https://forum.example/")


def test_private_recipient_can_come_from_stdin(ledger, monkeypatch):
    connection, path = ledger
    connection.close()
    monkeypatch.setattr(store.sys, "stdin", io.StringIO("private@example.test\n"))

    assert store.run(
        [
            "contact",
            "add",
            "--id",
            "private",
            "--name",
            "Private Contact",
            "--recipient-stdin",
            "--channel",
            "email",
        ],
        path=path,
    ) == 0

    reopened = store.connect(path)
    try:
        assert store.require_contact(reopened, "private")["recipient"] == "private@example.test"
    finally:
        reopened.close()


def test_pause_blocks_new_messages_and_dnc_cannot_be_reopened(ledger):
    connection, _ = ledger
    add_contact(connection)
    store.set_contact_state(connection, "helia", "paused", "waiting")
    with pytest.raises(store.OutreachError, match="paused"):
        draft(connection)

    store.set_contact_state(connection, "helia", "active", "reply received")
    assert draft(connection)
    store.suppress_contact(connection, "helia", "declined")
    with pytest.raises(store.OutreachError, match="hard gate"):
        store.set_contact_state(connection, "helia", "active", "try to reopen")


def test_reply_never_reopens_do_not_contact(ledger):
    connection, _ = ledger
    add_contact(connection)
    message_id = draft(connection)
    approve(connection, message_id)
    store.export_message(connection, message_id)
    store.transition_message(
        connection,
        message_id,
        "exported",
        "sent",
        "message_marked_sent",
        {"provider_id": "manual:test-receipt"},
    )
    store.suppress_contact(connection, "helia", "opted out")

    store.record_reply(connection, message_id, "manual:inbound:test:1:43")

    assert store.require_contact(connection, "helia")["state"] == "do_not_contact"
    assert message_state(connection, message_id) == "replied"


def test_endpoint_suppression_applies_across_contact_ids(ledger):
    connection, _ = ledger
    add_contact(connection, "first", "same@example.test")
    add_contact(connection, "second", "SAME@example.test")
    pending = draft(connection, "second")

    store.suppress_contact(connection, "first", "recipient declined")

    assert store.require_contact(connection, "second")["state"] == "do_not_contact"
    assert message_state(connection, pending) == "cancelled"
    with pytest.raises(store.OutreachError, match="suppressed"):
        store.add_contact(
            connection,
            "third",
            "Third",
            "same@example.test",
            "email",
            "project",
            "https://example.test/third",
        )
    with pytest.raises(store.OutreachError, match="suppressed"):
        store.add_contact(
            connection,
            "channel-alias",
            "Channel Alias",
            "same@example.test",
            "smtp",
            "project",
            "https://example.test/channel-alias",
        )
    with pytest.raises(store.OutreachError, match="bare address"):
        store.add_contact(
            connection,
            "mailto-alias",
            "Mailto Alias",
            "mailto:same@example.test",
            "email",
            "project",
            "https://example.test/mailto-alias",
        )
    with pytest.raises(store.OutreachError, match="bare address"):
        store.add_contact(
            connection,
            "display-alias",
            "Display Alias",
            "Same Person <same@example.test>",
            "smtp",
            "project",
            "https://example.test/display-alias",
        )


def test_url_suppression_normalizes_scheme_and_host_case(ledger):
    connection, _ = ledger
    store.add_contact(
        connection,
        "forum-first",
        "Forum First",
        "https://Forum.Example/project",
        "forum",
        "project",
        "https://example.test",
    )
    store.suppress_contact(connection, "forum-first", "declined")

    with pytest.raises(store.OutreachError, match="suppressed"):
        store.add_contact(
            connection,
            "forum-second",
            "Forum Second",
            "HTTPS://forum.example/project",
            "forum",
            "project",
            "https://example.test",
        )
    with pytest.raises(store.OutreachError, match="must not contain userinfo"):
        store.add_contact(
            connection,
            "userinfo",
            "Unsafe URL",
            "https://user:password@forum.example/project",
            "forum",
            "project",
            "https://example.test",
        )


def test_connect_rebuilds_suppression_tombstone_from_existing_dnc(ledger):
    connection, path = ledger
    add_contact(connection)
    store.suppress_contact(connection, "helia", "declined")
    connection.execute("DELETE FROM suppressions")
    connection.commit()
    connection.close()

    reopened = store.connect(path)
    try:
        with pytest.raises(store.OutreachError, match="suppressed"):
            store.add_contact(
                reopened,
                "renamed",
                "Renamed Contact",
                "MAINTAINER@example.test",
                "email",
                "project",
                "https://example.test/renamed",
            )
    finally:
        reopened.close()


def test_fingerprint_migration_preserves_legacy_dnc_across_endpoint_aliases(ledger):
    connection, path = ledger
    now = store.iso()
    legacy_rows = [
        (
            "legacy-url",
            "Legacy URL",
            "https://Forum.Example/project",
            "forum",
            "old-url-fingerprint",
        ),
        (
            "legacy-mailto",
            "Legacy Mailto",
            "mailto:mailbox@example.test?subject=hello",
            "email",
            "old-mailto-fingerprint",
        ),
        (
            "legacy-display",
            "Legacy Display",
            "Same Person <display@example.test> (friend)",
            "smtp",
            "old-display-fingerprint",
        ),
        (
            "legacy-userinfo-url",
            "Legacy Userinfo URL",
            "https://user:pass@Forum.Example/private",
            "forum",
            "old-userinfo-fingerprint",
        ),
    ]
    for contact_id, name, recipient, channel, fingerprint in legacy_rows:
        connection.execute(
            """INSERT INTO contacts(
               id,name,recipient,channel,endpoint_fingerprint,readiness_status,
               state,created_at,updated_at) VALUES(?,?,?,?,?,'blocked',
               'do_not_contact',?,?)""",
            (contact_id, name, recipient, channel, fingerprint, now, now),
        )
        connection.execute(
            "INSERT INTO suppressions(endpoint_fingerprint,created_at,reason) VALUES(?,?,?)",
            (fingerprint, now, "legacy suppression"),
        )
    connection.execute("DELETE FROM outreach_meta WHERE key='endpoint_fingerprint_v2'")
    connection.commit()
    connection.close()

    reopened = store.connect(path)
    try:
        attempts = [
            ("new-url", "https://forum.example/project", "web"),
            ("new-mailto", "mailbox@example.test", "smtp"),
            ("new-display", "display@example.test", "email"),
            ("new-userinfo", "https://forum.example/private", "web"),
        ]
        for contact_id, recipient, channel in attempts:
            with pytest.raises(store.OutreachError, match="suppressed"):
                store.add_contact(
                    reopened,
                    contact_id,
                    "Replacement",
                    recipient,
                    channel,
                    "project",
                    "https://example.test",
                )
    finally:
        reopened.close()


def test_email_endpoint_rejects_urlish_or_display_representations():
    for recipient in (
        "mailto:same@example.test",
        "Same Person <same@example.test>",
        "same@example.test/path",
    ):
        with pytest.raises(store.OutreachError, match="bare address"):
            store.normalize_endpoint("email", recipient)


def test_endpoint_change_retargets_snapshot_and_revokes_approval(ledger, capsys):
    connection, path = ledger
    add_contact(connection)
    message_id = draft(connection)
    approve(connection, message_id)
    old_hash = store.require_message(connection, message_id)["content_hash"]

    store.add_contact(
        connection,
        "helia",
        None,
        "new@example.test",
        "email",
        None,
        None,
    )

    message = store.require_message(connection, message_id)
    assert message["state"] == "draft"
    assert message["recipient"] == "new@example.test"
    assert message["content_hash"] != old_hash
    assert connection.execute(
        "SELECT revoked_at FROM approvals WHERE message_id=?", (message_id,)
    ).fetchone()[0]

    connection.close()
    assert store.run(
        ["message", "preview", message_id, "--show-recipient"], path=path
    ) == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["recipient"] == "new@example.test"


def test_concurrent_export_consumes_approval_once(ledger):
    connection, path = ledger
    add_contact(connection)
    message_id = draft(connection)
    approve(connection, message_id)
    connection.close()
    barrier = threading.Barrier(2)

    def attempt_export():
        worker = store.connect(path)
        try:
            barrier.wait(timeout=5)
            try:
                store.export_message(worker, message_id)
                return "exported"
            except store.OutreachError:
                return "rejected"
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt_export(), range(2)))

    assert sorted(results) == ["exported", "rejected"]


def test_unsafe_override_parent_is_rejected_without_chmod(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    os.chmod(shared, 0o755)

    with pytest.raises(store.OutreachError, match="owner-only"):
        store.connect(shared / "outreach.sqlite3")

    assert stat.S_IMODE(shared.stat().st_mode) == 0o755
    assert not (shared / "outreach.sqlite3").exists()


def test_non_finite_approval_expiry_is_rejected(ledger):
    connection, _ = ledger
    add_contact(connection)
    message_id = draft(connection)
    with pytest.raises(store.OutreachError, match="approval expiry"):
        store.approve_message(
            connection,
            message_id,
            "yu",
            math.nan,
            store.require_message(connection, message_id)["content_hash"],
            "work-test",
        )


def test_approval_requires_the_hash_from_the_reviewed_snapshot(ledger):
    connection, _ = ledger
    add_contact(connection)
    message_id = draft(connection)
    work_id = ready_work(connection, message_id)
    store.transition_message(
        connection,
        message_id,
        "draft",
        "reviewed",
        "message_reviewed",
        work_id=work_id,
    )
    store.transition_message(
        connection,
        message_id,
        "reviewed",
        "awaiting_approval",
        "approval_requested",
        work_id=work_id,
    )

    with pytest.raises(store.OutreachError, match="preview again"):
        store.approve_message(connection, message_id, "yu", 24, "0" * 64, work_id)

    current_hash = store.require_message(connection, message_id)["content_hash"]
    assert store.approve_message(
        connection, message_id, "yu", 24, current_hash, work_id
    )


def test_retarget_cannot_collide_with_another_open_gesture(ledger):
    connection, _ = ledger
    add_contact(connection, "first", "first@example.test")
    add_contact(connection, "second", "second@example.test")
    draft(connection, "first")
    draft(connection, "second")

    with pytest.raises(store.OutreachError, match="open draft gesture"):
        store.add_contact(
            connection,
            "first",
            None,
            "second@example.test",
            "email",
            None,
            None,
        )

    assert store.require_contact(connection, "first")["recipient"] == "first@example.test"
    assert store.require_contact(connection, "second")["recipient"] == "second@example.test"


def test_unresolved_sent_message_blocks_channel_hopping(ledger):
    connection, _ = ledger
    add_contact(connection)
    message_id = draft(connection)
    approve(connection, message_id)
    store.export_message(connection, message_id)
    store.transition_message(
        connection,
        message_id,
        "exported",
        "sent",
        "message_marked_sent",
        {"provider_id": "manual:sent-no-reply"},
    )

    with pytest.raises(store.OutreachError, match="sent message is unresolved"):
        store.add_contact(
            connection,
            "helia",
            None,
            "another-channel@example.test",
            "email",
            None,
            None,
        )


def test_core_rejects_forged_transitions_and_multiline_fields(ledger):
    connection, _ = ledger
    add_contact(connection)
    with pytest.raises(store.OutreachError, match="single line"):
        store.draft_message(connection, "helia", "Subject\nBcc: other", "Body")
    message_id = draft(connection)
    with pytest.raises(store.OutreachError, match="invalid message transition"):
        store.transition_message(
            connection,
            message_id,
            "draft",
            "sent",
            "message_marked_sent",
            {"provider_id": "manual:forged"},
        )


def test_only_yu_assertion_can_create_operator_approval(ledger):
    connection, _ = ledger
    add_contact(connection)
    message_id = draft(connection)
    work_id = ready_work(connection, message_id)
    store.transition_message(
        connection,
        message_id,
        "draft",
        "reviewed",
        "message_reviewed",
        work_id=work_id,
    )
    store.transition_message(
        connection,
        message_id,
        "reviewed",
        "awaiting_approval",
        "approval_requested",
        work_id=work_id,
    )
    digest = store.require_message(connection, message_id)["content_hash"]

    with pytest.raises(store.OutreachError, match="--by yu"):
        store.approve_message(
            connection, message_id, "agent", 24, digest, work_id
        )


def test_delivery_and_reply_references_cannot_be_reused(ledger):
    connection, _ = ledger
    add_contact(connection, "first", "first@example.test")
    add_contact(connection, "second", "second@example.test")
    first = draft(connection, "first")
    second = draft(connection, "second")
    approve(connection, first)
    approve(connection, second)
    store.export_message(connection, first)
    store.export_message(connection, second)
    store.transition_message(
        connection,
        first,
        "exported",
        "sent",
        "message_marked_sent",
        {"provider_id": "manual:unique-delivery"},
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.transition_message(
            connection,
            second,
            "exported",
            "sent",
            "message_marked_sent",
            {"provider_id": "manual:unique-delivery"},
        )
    store.transition_message(
        connection,
        second,
        "exported",
        "sent",
        "message_marked_sent",
        {"provider_id": "manual:second-delivery"},
    )
    store.record_reply(connection, first, "manual:inbound:test:7:100")
    with pytest.raises(sqlite3.IntegrityError):
        store.record_reply(connection, second, "manual:inbound:test:7:100")
