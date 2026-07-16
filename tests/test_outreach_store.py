import importlib.util
import io
import json
import math
import os
import sqlite3
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "outreach_store.py"
SPEC = importlib.util.spec_from_file_location("outreach_store", MODULE_PATH)
store = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(store)


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
    store.transition_message(
        connection,
        message_id,
        "draft",
        "reviewed",
        "message_reviewed",
        {"reviewed_by": "nuance+crucible+vigil"},
    )
    store.transition_message(
        connection,
        message_id,
        "reviewed",
        "awaiting_approval",
        "approval_requested",
    )
    expected_hash = store.require_message(connection, message_id)["content_hash"]
    return store.approve_message(connection, message_id, "yu", 24, expected_hash)


def message_state(connection, message_id):
    return connection.execute(
        "SELECT state FROM messages WHERE id=?", (message_id,)
    ).fetchone()[0]


def test_owner_only_database_permissions(ledger):
    _, path = ledger

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


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
    with pytest.raises(store.OutreachError, match="readiness is blocked"):
        store.approve_message(connection, message_id, "yu", 24, "0" * 64)
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
    )
    with pytest.raises(store.OutreachError, match="open sent gesture"):
        draft(connection, body="An unsolicited follow-up that must be blocked.")

    store.record_reply(connection, first)

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
        connection, message_id, "exported", "sent", "message_marked_sent"
    )
    store.suppress_contact(connection, "helia", "opted out")

    store.record_reply(connection, message_id)

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
    store.transition_message(
        connection, message_id, "draft", "reviewed", "message_reviewed"
    )
    store.transition_message(
        connection,
        message_id,
        "reviewed",
        "awaiting_approval",
        "approval_requested",
    )

    with pytest.raises(store.OutreachError, match="approval expiry"):
        store.approve_message(
            connection,
            message_id,
            "yu",
            math.nan,
            store.require_message(connection, message_id)["content_hash"],
        )


def test_approval_requires_the_hash_from_the_reviewed_snapshot(ledger):
    connection, _ = ledger
    add_contact(connection)
    message_id = draft(connection)
    store.transition_message(
        connection, message_id, "draft", "reviewed", "message_reviewed"
    )
    store.transition_message(
        connection,
        message_id,
        "reviewed",
        "awaiting_approval",
        "approval_requested",
    )

    with pytest.raises(store.OutreachError, match="preview again"):
        store.approve_message(connection, message_id, "yu", 24, "0" * 64)

    current_hash = store.require_message(connection, message_id)["content_hash"]
    assert store.approve_message(connection, message_id, "yu", 24, current_hash)
