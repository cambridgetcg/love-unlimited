import json
import sqlite3
import sys
from pathlib import Path

import pytest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import outreach_store as outreach  # noqa: E402
import relations_pipeline as pipeline  # noqa: E402


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / "private" / "outreach.sqlite3"
    connection = pipeline.connect(path)
    yield connection, path
    connection.close()


def create_work(connection, kind="development", contact_id=None):
    return pipeline.create_work(
        connection,
        kind=kind,
        title="Helia adapter",
        objective="Store ADDS payload blocks through Helia without plaintext disclosure.",
        done_when="Round-trip, tamper, bounded-read, and disclosure fixtures pass.",
        owner_role="loom",
        next_action="Collect primary sources and map the interface boundary.",
        contact_id=contact_id,
        source_ref="local:intake:test",
    )


def advance_to_review(connection, work_id):
    pipeline.advance_work(connection, work_id, "research", "loom")
    pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="source",
        reference="https://example.test/official-doc",
        claim="The source defines the current public interface.",
        result="info",
        added_by="loom",
    )
    pipeline.advance_work(connection, work_id, "planned", "loom")
    pipeline.advance_work(connection, work_id, "building", "loom")
    pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="commit",
        reference="git:abc123",
        claim="The adapter implementation is fixed to this commit.",
        result="pass",
        added_by="builder",
        artifact_hash="a" * 64,
    )
    pipeline.advance_work(connection, work_id, "verifying", "loom")
    pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="test",
        reference="pytest:test-adapter",
        claim="Round-trip and tamper fixtures pass.",
        result="pass",
        added_by="vigil",
    )
    pipeline.advance_work(connection, work_id, "review", "loom")


def pass_reviews(connection, work_id):
    for role in sorted(pipeline.REVIEW_ROLES):
        pipeline.add_review(
            connection,
            work_id,
            role=role,
            verdict="pass",
            summary=f"{role} reviewed the current evidence snapshot.",
        )


def add_contact(connection):
    outreach.add_contact(
        connection,
        "helia",
        "Helia",
        "https://discuss.example.test/helia",
        "forum",
        "content-addressed-storage",
        "https://example.test/helia",
    )
    outreach.set_readiness(
        connection, "helia", "ready", "adapter fixtures passed at abc123"
    )


def external_work_at_review(connection):
    add_contact(connection)
    message_id = outreach.draft_message(
        connection,
        "helia",
        "A small tested adapter",
        "Here is the artifact and one question.",
    )
    work_id = create_work(connection, kind="outreach", contact_id="helia")
    pipeline.link_message(connection, work_id, message_id)
    advance_to_review(connection, work_id)
    return work_id, message_id


def test_internal_work_requires_evidence_and_accepted_handoffs(ledger):
    connection, _ = ledger
    work_id = create_work(connection)

    pipeline.advance_work(connection, work_id, "research", "loom")
    with pytest.raises(pipeline.PipelineError, match="source or context"):
        pipeline.advance_work(connection, work_id, "planned", "loom")
    pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="source",
        reference="https://example.test/spec",
        claim="The current interface is documented.",
        result="info",
        added_by="loom",
    )
    pipeline.advance_work(connection, work_id, "planned", "loom")

    handoff_id = pipeline.offer_handoff(
        connection,
        work_id,
        from_role="loom",
        to_role="builder",
        summary="Research complete; no protocol claim exceeds the source.",
        next_action="Implement the bounded adapter fixture.",
    )
    with pytest.raises(pipeline.PipelineError, match="must be accepted"):
        pipeline.advance_work(connection, work_id, "building", "loom")
    with pytest.raises(pipeline.PipelineError, match="accepted by builder"):
        pipeline.accept_handoff(connection, handoff_id, "someone-else")

    pipeline.accept_handoff(connection, handoff_id, "builder")
    assert pipeline.require_work(connection, work_id)["owner_role"] == "builder"
    pipeline.advance_work(connection, work_id, "building", "builder")
    with pytest.raises(pipeline.PipelineError, match="artifact, commit, or patch"):
        pipeline.advance_work(connection, work_id, "verifying", "builder")


def test_reviews_are_bound_to_the_current_evidence_snapshot(ledger):
    connection, _ = ledger
    work_id = create_work(connection)
    advance_to_review(connection, work_id)
    pass_reviews(connection, work_id)
    reviewed_hash = pipeline.work_digest(connection, work_id)

    pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="audit",
        reference="audit:second-pass",
        claim="A later audit changed the evidence bundle.",
        result="pass",
        added_by="vigil",
    )

    assert pipeline.work_digest(connection, work_id) != reviewed_hash
    with pytest.raises(pipeline.PipelineError, match="pass reviews from"):
        pipeline.advance_work(connection, work_id, "ready", "loom")
    pass_reviews(connection, work_id)
    pipeline.advance_work(connection, work_id, "ready", "loom")
    pipeline.advance_work(connection, work_id, "done", "loom")
    assert pipeline.require_work(connection, work_id)["state"] == "done"


def test_external_message_requires_basis_reviews_and_delivery_receipts(ledger):
    connection, path = ledger
    add_contact(connection)
    message_id = outreach.draft_message(
        connection, "helia", "A small tested adapter", "Here is the artifact and one question."
    )
    work_id = create_work(connection, kind="outreach", contact_id="helia")
    pipeline.link_message(connection, work_id, message_id)
    advance_to_review(connection, work_id)
    pass_reviews(connection, work_id)

    with pytest.raises(pipeline.PipelineError, match="contact_basis"):
        pipeline.advance_work(connection, work_id, "ready", "loom")

    pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="contact_basis",
        reference="https://discuss.example.test/helia",
        claim="This public project forum accepts one relevant technical post.",
        result="pass",
        added_by="vigil",
    )
    pass_reviews(connection, work_id)
    pipeline.advance_work(connection, work_id, "ready", "loom")

    connection.close()
    assert outreach.run(
        ["message", "review", message_id, "--work-id", work_id], path=path
    ) == 0
    assert outreach.run(
        ["message", "request-approval", message_id, "--work-id", work_id],
        path=path,
    ) == 0

    reopened = pipeline.connect(path)
    try:
        content_hash = outreach.require_message(reopened, message_id)["content_hash"]
    finally:
        reopened.close()
    assert outreach.run(
        [
            "message",
            "approve",
            message_id,
            "--work-id",
            work_id,
            "--by",
            "yu",
            "--content-hash",
            content_hash,
        ],
        path=path,
    ) == 0

    connection = pipeline.connect(path)
    approval = connection.execute(
        "SELECT work_id,work_snapshot_hash FROM approvals WHERE message_id=?",
        (message_id,),
    ).fetchone()
    assert approval["work_id"] == work_id
    assert approval["work_snapshot_hash"] == pipeline.work_digest(connection, work_id)
    pipeline.block_work(connection, work_id, "delivery paused for a final check", "loom")
    with pytest.raises(pipeline.PipelineError, match="expected ready"):
        outreach.export_message(connection, message_id)
    pipeline.resume_work(connection, work_id, "loom")
    outreach.export_message(connection, message_id)
    with pytest.raises(outreach.OutreachError, match="delivery receipt"):
        outreach.transition_message(
            connection, message_id, "exported", "sent", "message_marked_sent"
        )
    outreach.transition_message(
        connection,
        message_id,
        "exported",
        "sent",
        "message_marked_sent",
        {"provider_id": "manual:forum-post-42"},
    )
    with pytest.raises(outreach.OutreachError, match="inbound receipt"):
        outreach.record_reply(connection, message_id, "")
    outreach.suppress_contact(connection, "helia", "reply requested no further contact")
    intake_result = pipeline.ingest_mail_document(
        connection,
        {
            "account": "test",
            "found": 1,
            "next_uid": 42,
            "uidvalidity": 9,
            "emails": [
                {
                    "uid": "42",
                    "message_id_hash": "d" * 64,
                    "from_hash": "e" * 64,
                    "subject_hash": "f" * 64,
                    "subject_length": 12,
                    "date": None,
                }
            ],
        },
    )
    with pytest.raises(outreach.OutreachError, match="needs_action"):
        outreach.record_reply(
            connection,
            message_id,
            "imap:test:9:42",
            intake_result["created"][0],
        )
    pipeline.classify_mail(
        connection,
        intake_result["created"][0],
        outcome="needs_action",
        reason="Authenticated inbound item is the reply to the recorded gesture.",
        by="tithe",
    )
    with pytest.raises(outreach.OutreachError, match="UIDVALIDITY:UID"):
        outreach.record_reply(
            connection,
            message_id,
            "imap:test:09:042",
            intake_result["created"][0],
        )
    outreach.record_reply(
        connection,
        message_id,
        "imap:test:9:42",
        intake_result["created"][0],
    )
    assert outreach.require_contact(connection, "helia")["state"] == "do_not_contact"
    assert connection.execute(
        "SELECT mail_work_id FROM reply_receipts WHERE message_id=?", (message_id,)
    ).fetchone()["mail_work_id"] == intake_result["created"][0]
    original = outreach.require_message(connection, message_id)
    connection.execute(
        """INSERT INTO messages(
           id,contact_id,channel,recipient,endpoint_fingerprint,subject,body,
           content_hash,state,created_at,updated_at)
           VALUES('msg-second-reply-fixture',?,?,?,?,?,?,?,'sent',?,?)""",
        (
            original["contact_id"],
            original["channel"],
            original["recipient"],
            original["endpoint_fingerprint"],
            "Second fixture",
            "Second fixture body",
            "0" * 64,
            outreach.iso(),
            outreach.iso(),
        ),
    )
    connection.commit()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO reply_receipts(
               receipt_ref,message_id,mail_work_id,recorded_at) VALUES(?,?,?,?)""",
            (
                "manual:second-reference",
                "msg-second-reply-fixture",
                intake_result["created"][0],
                outreach.iso(),
            ),
        )
    connection.rollback()
    pipeline.advance_work(connection, work_id, "done", "loom")


def test_append_only_pipeline_records_and_block_resume(ledger):
    connection, _ = ledger
    work_id = create_work(connection)
    evidence_id = pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="context",
        reference="local:context",
        claim="The task boundary is recorded.",
        result="info",
        added_by="loom",
    )

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(
            "UPDATE work_evidence SET claim='rewritten' WHERE id=?", (evidence_id,)
        )
    connection.rollback()

    pipeline.block_work(connection, work_id, "waiting for upstream decision", "loom")
    assert pipeline.require_work(connection, work_id)["state"] == "blocked"
    pipeline.resume_work(connection, work_id, "loom")
    assert pipeline.require_work(connection, work_id)["state"] == "intake"


def test_latest_failed_evidence_supersedes_older_passes(ledger):
    connection, _ = ledger
    work_id, _ = external_work_at_review(connection)
    pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="test",
        reference="test:regression",
        claim="A regression is currently failing.",
        result="fail",
        added_by="vigil",
    )
    pass_reviews(connection, work_id)
    shown = pipeline.show_work(connection, work_id)
    assert [row["seq"] for row in shown["evidence"]] == sorted(
        row["seq"] for row in shown["evidence"]
    )
    assert [
        row for row in shown["evidence"] if row["evidence_type"] == "test"
    ][-1]["result"] == "fail"
    with pytest.raises(pipeline.PipelineError, match="verification evidence"):
        pipeline.advance_work(connection, work_id, "ready", "loom")

    pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="test",
        reference="test:regression-fixed",
        claim="The regression now passes.",
        result="pass",
        added_by="vigil",
    )
    pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="contact_basis",
        reference="https://discuss.example.test/helia",
        claim="One relevant public-forum gesture is in scope.",
        result="pass",
        added_by="vigil",
    )
    pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="contact_basis",
        reference="local:basis-revoked",
        claim="The endpoint assessment is no longer current.",
        result="fail",
        added_by="vigil",
    )
    pass_reviews(connection, work_id)
    with pytest.raises(pipeline.PipelineError, match="current passing Vigil"):
        pipeline.advance_work(connection, work_id, "ready", "loom")


def test_readiness_and_message_revision_require_explicit_reopen(ledger):
    connection, _ = ledger
    work_id, message_id = external_work_at_review(connection)
    pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="contact_basis",
        reference="https://discuss.example.test/helia",
        claim="One relevant public-forum gesture is in scope.",
        result="pass",
        added_by="vigil",
    )
    pass_reviews(connection, work_id)
    pipeline.advance_work(connection, work_id, "ready", "loom")
    original_hash = pipeline.work_digest(connection, work_id)

    outreach.set_readiness(connection, "helia", "blocked", "artifact regression")
    outreach.set_readiness(connection, "helia", "ready", "regression repaired")
    assert pipeline.work_digest(connection, work_id) != original_hash
    with pytest.raises(pipeline.PipelineError, match="pass reviews from"):
        pipeline.assert_work_ready_for_message(connection, work_id, message_id)

    pipeline.reopen_work(connection, work_id, "loom", "Review repaired readiness")
    pass_reviews(connection, work_id)
    pipeline.advance_work(connection, work_id, "ready", "loom")
    outreach.revise_message(
        connection, message_id, None, "A corrected artifact and one question."
    )
    with pytest.raises(pipeline.PipelineError, match="pass reviews from"):
        pipeline.assert_work_ready_for_message(connection, work_id, message_id)
    pipeline.reopen_work(connection, work_id, "loom", "Review revised message")
    pass_reviews(connection, work_id)
    pipeline.advance_work(connection, work_id, "ready", "loom")
    assert pipeline.assert_work_ready_for_message(connection, work_id, message_id)


def test_message_links_and_handoff_acceptance_fail_closed(ledger):
    connection, _ = ledger
    add_contact(connection)
    message_id = outreach.draft_message(connection, "helia", "Subject", "Body")
    internal_work = create_work(connection, kind="development")
    with pytest.raises(pipeline.PipelineError, match="only email or outreach"):
        pipeline.link_message(connection, internal_work, message_id)

    external_work = create_work(connection, kind="outreach", contact_id="helia")
    outreach.cancel_message(connection, message_id, "draft withdrawn")
    with pytest.raises(pipeline.PipelineError, match="only a draft"):
        pipeline.link_message(connection, external_work, message_id)

    handoff_id = pipeline.offer_handoff(
        connection,
        internal_work,
        from_role="loom",
        to_role="builder",
        summary="Implementation can start.",
        next_action="Build the fixture.",
    )
    pipeline.cancel_work(connection, internal_work, "scope withdrawn", "loom")
    with pytest.raises(pipeline.PipelineError, match="cannot accept"):
        pipeline.accept_handoff(connection, handoff_id, "builder")


def test_contact_basis_is_bound_to_the_exact_endpoint(ledger):
    connection, _ = ledger
    work_id, _ = external_work_at_review(connection)
    pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="contact_basis",
        reference="https://discuss.example.test/helia",
        claim="One public-forum gesture is in scope.",
        result="pass",
        added_by="vigil",
    )
    outreach.add_contact(
        connection,
        "helia",
        None,
        "maintainer@example.test",
        "email",
        None,
        None,
    )
    pass_reviews(connection, work_id)

    with pytest.raises(pipeline.PipelineError, match="current passing Vigil"):
        pipeline.advance_work(connection, work_id, "ready", "loom")

    pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="contact_basis",
        reference="local:verified-email-basis",
        claim="The exact email endpoint is valid for one requested gesture.",
        result="pass",
        added_by="vigil",
    )
    pass_reviews(connection, work_id)
    pipeline.advance_work(connection, work_id, "ready", "loom")


def test_single_gesture_basis_is_consumed_even_if_export_is_cancelled(ledger):
    connection, _ = ledger
    work_id, first_message = external_work_at_review(connection)
    pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="contact_basis",
        reference="https://discuss.example.test/helia",
        claim="One public-forum gesture is in scope.",
        result="pass",
        added_by="vigil",
    )
    pass_reviews(connection, work_id)
    pipeline.advance_work(connection, work_id, "ready", "loom")
    outreach.transition_message(
        connection,
        first_message,
        "draft",
        "reviewed",
        "message_reviewed",
        work_id=work_id,
    )
    outreach.transition_message(
        connection,
        first_message,
        "reviewed",
        "awaiting_approval",
        "approval_requested",
        work_id=work_id,
    )
    digest = outreach.require_message(connection, first_message)["content_hash"]
    outreach.approve_message(
        connection, first_message, "yu", 24, digest, work_id
    )
    outreach.export_message(connection, first_message)
    outreach.cancel_message(connection, first_message, "packet discarded before send")

    pipeline.reopen_work(connection, work_id, "loom", "Prepare a different gesture")
    second_message = outreach.draft_message(
        connection,
        "helia",
        "A different artifact question",
        "This is a distinct proposed gesture.",
    )
    pipeline.link_message(connection, work_id, second_message)
    pass_reviews(connection, work_id)
    with pytest.raises(pipeline.PipelineError, match="already consumed"):
        pipeline.advance_work(connection, work_id, "ready", "loom")

    pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="contact_basis",
        reference="https://discuss.example.test/helia/new-assessment",
        claim="A new single gesture is separately assessed.",
        result="pass",
        added_by="vigil",
    )
    pass_reviews(connection, work_id)
    pipeline.advance_work(connection, work_id, "ready", "loom")


def test_blocked_ready_work_cannot_hide_new_failing_evidence(ledger):
    connection, _ = ledger
    work_id = create_work(connection)
    advance_to_review(connection, work_id)
    pass_reviews(connection, work_id)
    pipeline.advance_work(connection, work_id, "ready", "loom")
    pipeline.block_work(connection, work_id, "new regression under investigation", "loom")
    pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="test",
        reference="test:new-regression",
        claim="The latest verification currently fails.",
        result="fail",
        added_by="vigil",
    )
    pipeline.resume_work(connection, work_id, "loom")
    assert pipeline.require_work(connection, work_id)["state"] == "review"
    pass_reviews(connection, work_id)
    with pytest.raises(pipeline.PipelineError, match="verification evidence"):
        pipeline.advance_work(connection, work_id, "ready", "loom")
    with pytest.raises(pipeline.PipelineError, match="next state must be ready"):
        pipeline.advance_work(connection, work_id, "done", "loom")


def test_hashed_mail_intake_is_idempotent_and_contains_no_raw_mail(ledger):
    connection, _ = ledger
    document = {
        "account": "test-inbox",
        "found": 1,
        "next_uid": 42,
        "uidvalidity": 678,
        "emails": [
            {
                "uid": "42",
                "message_id_hash": "a" * 64,
                "from_hash": "b" * 64,
                "subject_hash": "c" * 64,
                "subject_length": 17,
                "date": "2026-07-16T12:34:56Z",
            }
        ],
    }

    first = pipeline.ingest_mail_document(connection, document)
    second = pipeline.ingest_mail_document(connection, document)

    assert len(first["created"]) == 1
    assert first["existing"] == []
    assert first["next_uid"] == 42
    assert first["needs_review"] == []
    assert second == {
        "account": "test-inbox",
        "uidvalidity": 678,
        "next_uid": 42,
        "created": [],
        "existing": first["created"],
        "needs_review": [],
    }
    work = pipeline.require_work(connection, first["created"][0])
    assert (work["kind"], work["state"], work["owner_role"]) == (
        "email",
        "intake",
        "tithe",
    )
    assert work["source_ref"] == "imap:test-inbox:678:42"
    intake = connection.execute("SELECT * FROM mail_intake").fetchone()
    assert intake["message_id_hash"] == "a" * 64
    assert intake["from_hash"] == "b" * 64
    assert intake["subject_hash"] == "c" * 64
    shown = pipeline.show_work(connection, first["created"][0])
    assert shown["mail_intake"]["subject_length"] == 17
    assert shown["mail_intake"]["from_hash"] == "b" * 64


def test_mail_intake_rejects_raw_or_changed_identity_metadata(ledger):
    connection, _ = ledger
    document = {
        "account": "test",
        "found": 1,
        "next_uid": 9,
        "uidvalidity": 7,
        "emails": [
            {
                "uid": "9",
                "message_id_hash": "a" * 64,
                "from_hash": "b" * 64,
                "subject_hash": "c" * 64,
                "subject_length": 6,
                "date": None,
            }
        ],
    }
    raw = {**document, "emails": [{**document["emails"][0], "subject": "secret"}]}
    with pytest.raises(pipeline.PipelineError, match="refuses raw"):
        pipeline.ingest_mail_document(connection, raw)

    pipeline.ingest_mail_document(connection, document)
    changed = {
        **document,
        "emails": [{**document["emails"][0], "subject_hash": "d" * 64}],
    }
    with pytest.raises(pipeline.PipelineError, match="different metadata"):
        pipeline.ingest_mail_document(connection, changed)

    ahead = {**document, "next_uid": 10, "uidvalidity": 8}
    with pytest.raises(pipeline.PipelineError, match="final ingested UID"):
        pipeline.ingest_mail_document(connection, ahead)


def test_mail_classification_has_a_no_action_path_and_an_action_path(ledger):
    connection, _ = ledger
    base = {
        "account": "test",
        "found": 1,
        "next_uid": 11,
        "uidvalidity": 8,
        "emails": [
            {
                "uid": "11",
                "message_id_hash": "1" * 64,
                "from_hash": "2" * 64,
                "subject_hash": "3" * 64,
                "subject_length": 4,
                "date": None,
            }
        ],
    }
    no_action = pipeline.ingest_mail_document(connection, base)["created"][0]
    pipeline.classify_mail(
        connection,
        no_action,
        outcome="no_action",
        reason="Informational receipt; no response or state change requested.",
        by="tithe",
    )
    assert pipeline.require_work(connection, no_action)["state"] == "done"
    assert pipeline.show_work(connection, no_action)["mail_disposition"]["outcome"] == "no_action"

    action_doc = {
        **base,
        "next_uid": 12,
        "emails": [
            {
                **base["emails"][0],
                "uid": "12",
                "message_id_hash": "4" * 64,
            }
        ],
    }
    needs_action = pipeline.ingest_mail_document(connection, action_doc)["created"][0]
    pipeline.classify_mail(
        connection,
        needs_action,
        outcome="needs_action",
        reason="The authenticated message needs an operator decision before any reply.",
        by="tithe",
    )
    assert pipeline.require_work(connection, needs_action)["state"] == "research"


def test_mail_replay_is_blocked_for_operator_review_and_batch_uids_are_deduplicated(ledger):
    connection, _ = ledger
    item = {
        "uid": "5",
        "message_id_hash": "a" * 64,
        "from_hash": "b" * 64,
        "subject_hash": "c" * 64,
        "subject_length": 5,
        "date": None,
    }
    first = pipeline.ingest_mail_document(
        connection,
        {
            "account": "TEST",
            "found": 2,
            "next_uid": 5,
            "uidvalidity": 1,
            "emails": [item, dict(item)],
        },
    )
    assert len(first["created"]) == 1
    assert first["account"] == "test"

    replay_item = {**item, "uid": "6"}
    replay = pipeline.ingest_mail_document(
        connection,
        {
            "account": "test",
            "found": 1,
            "next_uid": 6,
            "uidvalidity": 2,
            "emails": [replay_item],
        },
    )
    assert replay["needs_review"] == replay["created"]
    work = pipeline.require_work(connection, replay["created"][0])
    assert (work["state"], work["resume_state"]) == ("blocked", "intake")


def test_redacted_packet_contains_only_coordination_fields(ledger, capsys):
    _, path = ledger
    connection = pipeline.connect(path)
    work_id = create_work(connection)
    connection.close()

    assert pipeline.run(["packet", work_id], path=path) == 0
    packet = json.loads(capsys.readouterr().out)
    assert set(packet) == {"work_id", "state", "to_role"}


def test_snapshot_sequence_survives_vacuum(ledger):
    connection, _ = ledger
    work_id = create_work(connection)
    pipeline.add_evidence(
        connection,
        work_id,
        evidence_type="context",
        reference="local:stable-order",
        claim="The evidence order has a permanent application sequence.",
        result="info",
        added_by="loom",
    )
    before = pipeline.work_digest(connection, work_id)

    connection.execute("VACUUM")

    assert pipeline.work_digest(connection, work_id) == before


def test_interrupted_sequence_migration_repairs_all_append_only_tables(tmp_path):
    connection = outreach.connect(tmp_path / "private" / "outreach.sqlite3")
    connection.executescript(
        """
        CREATE TABLE work_items (
          id TEXT PRIMARY KEY,kind TEXT NOT NULL,title TEXT NOT NULL,
          objective TEXT NOT NULL,done_when TEXT NOT NULL,state TEXT NOT NULL,
          resume_state TEXT,blocked_snapshot_hash TEXT,owner_role TEXT NOT NULL,
          contact_id TEXT,message_id TEXT,source_ref TEXT,next_action TEXT NOT NULL,
          blocked_reason TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL
        );
        CREATE TABLE work_evidence (
          id TEXT PRIMARY KEY,seq INTEGER,work_id TEXT NOT NULL,
          evidence_type TEXT NOT NULL,reference TEXT NOT NULL,claim TEXT NOT NULL,
          artifact_hash TEXT,endpoint_fingerprint TEXT,basis_scope TEXT,
          result TEXT NOT NULL,added_by TEXT NOT NULL,created_at TEXT NOT NULL
        );
        CREATE TABLE work_reviews (
          id TEXT PRIMARY KEY,seq INTEGER,work_id TEXT NOT NULL,role TEXT NOT NULL,
          verdict TEXT NOT NULL,summary TEXT NOT NULL,snapshot_hash TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE work_handoffs (
          id TEXT PRIMARY KEY,seq INTEGER,work_id TEXT NOT NULL,
          from_role TEXT NOT NULL,to_role TEXT NOT NULL,summary TEXT NOT NULL,
          next_action TEXT NOT NULL,snapshot_hash TEXT NOT NULL,created_at TEXT NOT NULL
        );
        INSERT INTO work_items VALUES(
          'work-interrupted','development','Interrupted migration','Repair order',
          'Newer failures stay newer','review',NULL,NULL,'loom',NULL,NULL,NULL,
          'Repair sequence',NULL,'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z'
        );
        INSERT INTO work_evidence VALUES(
          'ev-old',1,'work-interrupted','test','test:old','Old pass',NULL,NULL,NULL,
          'pass','vigil','2026-01-01T00:00:00Z'
        );
        INSERT INTO work_evidence VALUES(
          'ev-new',NULL,'work-interrupted','test','test:new','New failure',NULL,NULL,NULL,
          'fail','vigil','2026-01-02T00:00:00Z'
        );
        INSERT INTO work_reviews VALUES(
          'review-old',1,'work-interrupted','nuance','pass','Old review','old-hash',
          '2026-01-01T00:00:00Z'
        );
        INSERT INTO work_reviews VALUES(
          'review-new',NULL,'work-interrupted','nuance','block','New review','new-hash',
          '2026-01-02T00:00:00Z'
        );
        INSERT INTO work_handoffs VALUES(
          'handoff-old',1,'work-interrupted','loom','builder','Old handoff','Build',
          'old-hash','2026-01-01T00:00:00Z'
        );
        INSERT INTO work_handoffs VALUES(
          'handoff-new',NULL,'work-interrupted','loom','vigil','New handoff','Verify',
          'new-hash','2026-01-02T00:00:00Z'
        );
        """
    )
    connection.commit()

    pipeline.ensure_schema(connection)

    for table in ("work_evidence", "work_reviews", "work_handoffs"):
        assert [
            row["seq"]
            for row in connection.execute(
                f"SELECT seq FROM {table} ORDER BY rowid"
            ).fetchall()
        ] == [1, 2]
    latest = pipeline._latest_evidence(
        connection, "work-interrupted", {"test"}
    )
    assert latest["id"] == "ev-new"
    assert latest["result"] == "fail"
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(
            "UPDATE work_evidence SET result='pass' WHERE id='ev-new'"
        )
    connection.rollback()
    connection.close()
