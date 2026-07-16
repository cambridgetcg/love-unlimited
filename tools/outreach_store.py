#!/usr/bin/env python3
"""Private, approval-bound relationship ledger for ``tools/outreach.py``.

This module deliberately has no network transport.  An approved message must be
exported once and sent manually through a separately authenticated channel.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit


if __name__ == "__main__":
    sys.modules.setdefault("outreach_store", sys.modules[__name__])


CONTACT_STATES = {"research", "active", "paused", "closed", "do_not_contact"}
MESSAGE_STATES = {
    "draft",
    "reviewed",
    "awaiting_approval",
    "approved",
    "exported",
    "sent",
    "replied",
    "cancelled",
}
PENDING_MESSAGE_STATES = {
    "draft",
    "reviewed",
    "awaiting_approval",
    "approved",
    "exported",
}
MESSAGE_TRANSITIONS = {
    ("draft", "reviewed"): "message_reviewed",
    ("reviewed", "awaiting_approval"): "approval_requested",
    ("exported", "sent"): "message_marked_sent",
}


class OutreachError(RuntimeError):
    """A safe, user-facing control-plane rejection."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat().replace("+00:00", "Z")


def database_path() -> Path:
    override = os.environ.get("LOVE_OUTREACH_DB")
    if override:
        return Path(override).expanduser()
    data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return base / "love-unlimited" / "outreach.sqlite3"


def content_hash(channel: str, recipient: str, subject: str, body: str) -> str:
    payload = json.dumps(
        {
            "channel": channel,
            "recipient": recipient,
            "subject": subject,
            "body": body,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def normalize_endpoint(channel: str, recipient: str) -> tuple[str, str]:
    normalized_channel = channel.strip().casefold()
    normalized_recipient = recipient.strip()
    if normalized_recipient.casefold().startswith(("http://", "https://")):
        try:
            parts = urlsplit(normalized_recipient)
            hostname = parts.hostname
        except ValueError as error:
            raise OutreachError("recipient URL is malformed") from error
        if parts.scheme.casefold() not in {"http", "https"} or not hostname:
            raise OutreachError("recipient URL is malformed")
        if parts.username or parts.password:
            raise OutreachError("recipient URL must not contain userinfo")
        try:
            port = parts.port
        except ValueError as error:
            raise OutreachError("recipient URL has an invalid port") from error
        host = hostname.casefold()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        default_port = (parts.scheme.casefold() == "http" and port == 80) or (
            parts.scheme.casefold() == "https" and port == 443
        )
        netloc = host if port is None or default_port else f"{host}:{port}"
        normalized_recipient = urlunsplit(
            (
                parts.scheme.casefold(),
                netloc,
                parts.path or "/",
                parts.query,
                "",
            )
        )
        normalized_channel = "url"
    elif normalized_recipient.casefold().startswith("mailto:"):
        raise OutreachError("email recipient must be a bare address, not a mailto URL")
    elif "@" in normalized_recipient:
        if not re.fullmatch(
            r"[^@\s<>(),;:/?#\\]+@[^@\s<>(),;:/?#\\]+", normalized_recipient
        ):
            raise OutreachError("email recipient must be one bare address")
        normalized_channel = "email"
        normalized_recipient = normalized_recipient.casefold()
    return normalized_channel, normalized_recipient


def endpoint_fingerprint(channel: str, recipient: str) -> str:
    normalized = normalize_endpoint(channel, recipient)
    payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _migrated_endpoint_fingerprint(channel: str, recipient: str) -> tuple[str, bool]:
    """Return the v2 fingerprint and whether a legacy representation is unsafe.

    Old ledgers may contain mailto URLs or display-name mailbox forms that the
    current command surface rejects. During the one-time migration we derive
    their bare mailbox only to preserve suppression, then quarantine the
    legacy record rather than silently making it sendable.
    """
    try:
        return endpoint_fingerprint(channel, recipient), False
    except OutreachError:
        value = recipient.strip()
        if value.casefold().startswith(("http://", "https://")):
            try:
                parts = urlsplit(value)
                hostname = parts.hostname
            except ValueError as error:
                raise OutreachError("legacy recipient URL is malformed") from error
            if not hostname:
                raise
            try:
                port = parts.port
            except ValueError as error:
                raise OutreachError("legacy recipient URL has an invalid port") from error
            host = hostname.casefold()
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            scheme = parts.scheme.casefold()
            default_port = (scheme == "http" and port == 80) or (
                scheme == "https" and port == 443
            )
            netloc = host if port is None or default_port else f"{host}:{port}"
            sanitized = urlunsplit(
                (scheme, netloc, parts.path or "/", parts.query, "")
            )
            return endpoint_fingerprint("url", sanitized), True
        if value.casefold().startswith("mailto:"):
            parts = urlsplit(value)
            address_source = unquote(parts.path)
        else:
            address_source = value
        parsed = [
            address.strip()
            for _, address in getaddresses([address_source])
            if address
        ]
        if len(parsed) == 1 and re.fullmatch(
            r"[^@\s<>(),;:/?#\\]+@[^@\s<>(),;:/?#\\]+", parsed[0]
        ):
            mailbox = parsed[0]
            return endpoint_fingerprint("email", mailbox), True
        raise


def _prepare_private_path(path: Path, *, managed_parent: bool) -> None:
    path = path.expanduser()
    parent = path.parent
    if parent.is_symlink():
        raise OutreachError("database parent must not be a symlink")
    parent_created = not parent.exists()
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent_info = parent.stat()
    if not stat.S_ISDIR(parent_info.st_mode):
        raise OutreachError(f"database parent is not a directory: {parent}")
    if hasattr(os, "getuid") and parent_info.st_uid != os.getuid():
        raise OutreachError(f"database parent is not owned by the current user: {parent}")
    if managed_parent or parent_created:
        os.chmod(parent, 0o700)
    elif stat.S_IMODE(parent_info.st_mode) & 0o077:
        raise OutreachError(
            f"database parent must already be owner-only (0700): {parent}"
        )

    if path.is_symlink():
        raise OutreachError("database path must not be a symlink")
    if path.exists():
        file_info = path.stat()
        if not stat.S_ISREG(file_info.st_mode):
            raise OutreachError("database path must be a regular file")
        if hasattr(os, "getuid") and file_info.st_uid != os.getuid():
            raise OutreachError("database file is not owned by the current user")
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
    else:
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        os.close(descriptor)


@contextmanager
def immediate_transaction(connection: sqlite3.Connection):
    if connection.in_transaction:
        raise OutreachError("operation requires a clean database transaction")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()


def connect(path: Path | None = None) -> sqlite3.Connection:
    explicit_path = path is not None
    using_override = not explicit_path and bool(os.environ.get("LOVE_OUTREACH_DB"))
    path = (path or database_path()).expanduser()
    _prepare_private_path(path, managed_parent=not explicit_path and not using_override)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS contacts (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          kind TEXT,
          maturity TEXT,
          priority INTEGER,
          project_url TEXT,
          public_channel TEXT,
          fit TEXT,
          first_gesture TEXT,
          readiness_gate TEXT,
          recipient TEXT,
          channel TEXT,
          endpoint_fingerprint TEXT,
          readiness_status TEXT NOT NULL DEFAULT 'pending'
            CHECK (readiness_status IN ('pending','ready','blocked')),
          state TEXT NOT NULL DEFAULT 'research'
            CHECK (state IN ('research','active','paused','closed','do_not_contact')),
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
          id TEXT PRIMARY KEY,
          contact_id TEXT NOT NULL REFERENCES contacts(id),
          channel TEXT NOT NULL,
          recipient TEXT NOT NULL,
          endpoint_fingerprint TEXT NOT NULL,
          subject TEXT NOT NULL,
          body TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'draft'
            CHECK (state IN ('draft','reviewed','awaiting_approval','approved','exported','sent','replied','cancelled')),
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS approvals (
          id TEXT PRIMARY KEY,
          message_id TEXT NOT NULL REFERENCES messages(id),
          work_id TEXT NOT NULL,
          work_snapshot_hash TEXT NOT NULL,
          channel TEXT NOT NULL,
          recipient TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          approved_by TEXT NOT NULL,
          approved_at TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          consumed_at TEXT,
          revoked_at TEXT
        );

        CREATE TABLE IF NOT EXISTS events (
          seq INTEGER PRIMARY KEY AUTOINCREMENT,
          contact_id TEXT REFERENCES contacts(id),
          message_id TEXT REFERENCES messages(id),
          event_type TEXT NOT NULL,
          detail_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS suppressions (
          endpoint_fingerprint TEXT PRIMARY KEY,
          created_at TEXT NOT NULL,
          reason TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS delivery_receipts (
          receipt_ref TEXT PRIMARY KEY,
          message_id TEXT NOT NULL UNIQUE REFERENCES messages(id),
          recorded_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reply_receipts (
          receipt_ref TEXT PRIMARY KEY,
          message_id TEXT NOT NULL UNIQUE REFERENCES messages(id),
          mail_work_id TEXT,
          recorded_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS outreach_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );

        CREATE TRIGGER IF NOT EXISTS events_no_update
        BEFORE UPDATE ON events BEGIN
          SELECT RAISE(ABORT, 'events are append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS events_no_delete
        BEFORE DELETE ON events BEGIN
          SELECT RAISE(ABORT, 'events are append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS delivery_receipts_no_update
        BEFORE UPDATE ON delivery_receipts BEGIN
          SELECT RAISE(ABORT, 'delivery receipts are append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS delivery_receipts_no_delete
        BEFORE DELETE ON delivery_receipts BEGIN
          SELECT RAISE(ABORT, 'delivery receipts are append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS reply_receipts_no_update
        BEFORE UPDATE ON reply_receipts BEGIN
          SELECT RAISE(ABORT, 'reply receipts are append-only');
        END;
        CREATE TRIGGER IF NOT EXISTS reply_receipts_no_delete
        BEFORE DELETE ON reply_receipts BEGIN
          SELECT RAISE(ABORT, 'reply receipts are append-only');
        END;
        """
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(contacts)")}
    migrations = {
        "maturity": "ALTER TABLE contacts ADD COLUMN maturity TEXT",
        "priority": "ALTER TABLE contacts ADD COLUMN priority INTEGER",
        "endpoint_fingerprint": "ALTER TABLE contacts ADD COLUMN endpoint_fingerprint TEXT",
        "readiness_status": (
            "ALTER TABLE contacts ADD COLUMN readiness_status "
            "TEXT NOT NULL DEFAULT 'pending'"
        ),
    }
    for column, statement in migrations.items():
        if column not in columns:
            connection.execute(statement)
    message_columns = {row[1] for row in connection.execute("PRAGMA table_info(messages)")}
    if "endpoint_fingerprint" not in message_columns:
        connection.execute("ALTER TABLE messages ADD COLUMN endpoint_fingerprint TEXT")
    approval_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(approvals)")
    }
    if "work_id" not in approval_columns:
        connection.execute("ALTER TABLE approvals ADD COLUMN work_id TEXT")
    if "work_snapshot_hash" not in approval_columns:
        connection.execute("ALTER TABLE approvals ADD COLUMN work_snapshot_hash TEXT")
    reply_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(reply_receipts)")
    }
    if "mail_work_id" not in reply_columns:
        connection.execute("ALTER TABLE reply_receipts ADD COLUMN mail_work_id TEXT")
    duplicate_mail_link = connection.execute(
        """SELECT 1 FROM reply_receipts WHERE mail_work_id IS NOT NULL
           GROUP BY mail_work_id HAVING COUNT(*) > 1 LIMIT 1"""
    ).fetchone()
    if duplicate_mail_link:
        connection.close()
        raise OutreachError(
            "cannot enable reply/mail uniqueness: existing reply receipts reuse one mail work item"
        )
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS reply_receipts_mail_work_unique
           ON reply_receipts(mail_work_id) WHERE mail_work_id IS NOT NULL"""
    )
    migration_key = "pipeline_bound_approvals_v1"
    migration_done = connection.execute(
        "SELECT 1 FROM outreach_meta WHERE key=?", (migration_key,)
    ).fetchone()
    if migration_done is None:
        migration_time = iso()
        connection.execute(
            """UPDATE approvals SET revoked_at=? WHERE work_id IS NULL
               AND consumed_at IS NULL AND revoked_at IS NULL""",
            (migration_time,),
        )
        connection.execute(
            """UPDATE messages SET state='draft',updated_at=?
               WHERE state IN ('reviewed','awaiting_approval','approved')
               AND NOT EXISTS (
                 SELECT 1 FROM approvals a WHERE a.message_id=messages.id
                 AND a.work_id IS NOT NULL AND a.consumed_at IS NULL
                 AND a.revoked_at IS NULL
               )""",
            (migration_time,),
        )
        connection.execute(
            "INSERT INTO outreach_meta(key,value) VALUES(?,?)",
            (migration_key, migration_time),
        )
    fingerprint_migration_key = "endpoint_fingerprint_v2"
    fingerprint_migration_done = connection.execute(
        "SELECT 1 FROM outreach_meta WHERE key=?", (fingerprint_migration_key,)
    ).fetchone()
    if fingerprint_migration_done is None:
        migration_time = iso()
        for row in connection.execute(
            """SELECT id,channel,recipient,endpoint_fingerprint
               FROM contacts WHERE channel IS NOT NULL AND recipient IS NOT NULL"""
        ).fetchall():
            try:
                fingerprint, quarantine = _migrated_endpoint_fingerprint(
                    row["channel"], row["recipient"]
                )
            except OutreachError:
                fingerprint = row["endpoint_fingerprint"]
                quarantine = True
            if fingerprint:
                connection.execute(
                    "UPDATE contacts SET endpoint_fingerprint=? WHERE id=?",
                    (fingerprint, row["id"]),
                )
            if quarantine:
                if row["endpoint_fingerprint"]:
                    connection.execute(
                        """INSERT INTO suppressions(endpoint_fingerprint,created_at,reason)
                           VALUES(?,?,?) ON CONFLICT(endpoint_fingerprint) DO NOTHING""",
                        (
                            row["endpoint_fingerprint"],
                            migration_time,
                            "quarantined legacy endpoint representation",
                        ),
                    )
                if fingerprint:
                    connection.execute(
                        """INSERT INTO suppressions(endpoint_fingerprint,created_at,reason)
                           VALUES(?,?,?) ON CONFLICT(endpoint_fingerprint) DO NOTHING""",
                        (
                            fingerprint,
                            migration_time,
                            "quarantined legacy endpoint representation",
                        ),
                    )
                connection.execute(
                    """UPDATE contacts SET state='do_not_contact',readiness_status='blocked',
                       updated_at=? WHERE id=?""",
                    (migration_time, row["id"]),
                )
                connection.execute(
                    """INSERT INTO events(contact_id,event_type,detail_json,created_at)
                       VALUES(?,'legacy_endpoint_quarantined','{}',?)""",
                    (row["id"], migration_time),
                )

        for row in connection.execute(
            "SELECT id,contact_id,channel,recipient,endpoint_fingerprint,state FROM messages"
        ).fetchall():
            try:
                fingerprint, quarantine = _migrated_endpoint_fingerprint(
                    row["channel"], row["recipient"]
                )
            except OutreachError:
                fingerprint = row["endpoint_fingerprint"]
                quarantine = True
            if fingerprint:
                connection.execute(
                    "UPDATE messages SET endpoint_fingerprint=? WHERE id=?",
                    (fingerprint, row["id"]),
                )
            if quarantine and row["state"] in PENDING_MESSAGE_STATES:
                connection.execute(
                    """UPDATE approvals SET revoked_at=? WHERE message_id=?
                       AND consumed_at IS NULL AND revoked_at IS NULL""",
                    (migration_time, row["id"]),
                )
                connection.execute(
                    "UPDATE messages SET state='cancelled',updated_at=? WHERE id=?",
                    (migration_time, row["id"]),
                )
                connection.execute(
                    """INSERT INTO events(contact_id,message_id,event_type,detail_json,created_at)
                       VALUES(?,?,'message_cancelled_by_endpoint_migration','{}',?)""",
                    (row["contact_id"], row["id"], migration_time),
                )
        connection.execute(
            "INSERT INTO outreach_meta(key,value) VALUES(?,?)",
            (fingerprint_migration_key, migration_time),
        )

    for row in connection.execute(
        "SELECT id,channel,recipient FROM contacts WHERE endpoint_fingerprint IS NULL"
    ).fetchall():
        if row["channel"] and row["recipient"]:
            try:
                fingerprint = endpoint_fingerprint(row["channel"], row["recipient"])
            except OutreachError:
                continue
            connection.execute(
                "UPDATE contacts SET endpoint_fingerprint=? WHERE id=?",
                (fingerprint, row["id"]),
            )
    for row in connection.execute(
        "SELECT id,channel,recipient FROM messages WHERE endpoint_fingerprint IS NULL"
    ).fetchall():
        try:
            fingerprint = endpoint_fingerprint(row["channel"], row["recipient"])
        except OutreachError:
            continue
        connection.execute(
            "UPDATE messages SET endpoint_fingerprint=? WHERE id=?",
            (fingerprint, row["id"]),
        )
    for row in connection.execute(
        """SELECT endpoint_fingerprint FROM contacts
           WHERE state='do_not_contact' AND endpoint_fingerprint IS NOT NULL"""
    ).fetchall():
        connection.execute(
            """INSERT INTO suppressions(endpoint_fingerprint,created_at,reason)
               VALUES(?,?,?) ON CONFLICT(endpoint_fingerprint) DO NOTHING""",
            (row["endpoint_fingerprint"], iso(), "migrated do_not_contact state"),
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS contacts_endpoint_idx ON contacts(endpoint_fingerprint)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS messages_endpoint_idx ON messages(endpoint_fingerprint)"
    )
    connection.commit()
    return connection


def event(
    connection: sqlite3.Connection,
    event_type: str,
    *,
    contact_id: str | None = None,
    message_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        "INSERT INTO events(contact_id,message_id,event_type,detail_json,created_at) VALUES(?,?,?,?,?)",
        (contact_id, message_id, event_type, json.dumps(detail or {}, sort_keys=True), iso()),
    )


def require_contact(connection: sqlite3.Connection, contact_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if row is None:
        raise OutreachError(f"unknown contact: {contact_id}")
    return row


def require_message(connection: sqlite3.Connection, message_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    if row is None:
        raise OutreachError(f"unknown message: {message_id}")
    return row


def require_contactable(contact: sqlite3.Row) -> None:
    if contact["state"] == "do_not_contact":
        raise OutreachError(f"{contact['id']} is do_not_contact")
    if contact["state"] in {"paused", "closed"}:
        raise OutreachError(f"{contact['id']} is {contact['state']}")


def require_ready(contact: sqlite3.Row) -> None:
    if contact["readiness_status"] != "ready":
        raise OutreachError(
            f"{contact['id']} readiness is {contact['readiness_status']}; "
            "build and verify the first gesture before approval or export"
        )


def require_endpoint_allowed(
    connection: sqlite3.Connection,
    channel: str,
    recipient: str,
) -> str:
    fingerprint = endpoint_fingerprint(channel, recipient)
    blocked = connection.execute(
        "SELECT 1 FROM suppressions WHERE endpoint_fingerprint=?",
        (fingerprint,),
    ).fetchone()
    if blocked:
        raise OutreachError("recipient and channel are suppressed")
    return fingerprint


def require_single_open_gesture(
    connection: sqlite3.Connection,
    fingerprint: str,
    *,
    exclude_message_id: str | None = None,
) -> None:
    sql = (
        "SELECT id,state FROM messages WHERE endpoint_fingerprint=? "
        "AND state IN ('draft','reviewed','awaiting_approval','approved','exported','sent')"
    )
    values: list[Any] = [fingerprint]
    if exclude_message_id:
        sql += " AND id<>?"
        values.append(exclude_message_id)
    row = connection.execute(sql + " LIMIT 1", values).fetchone()
    if row:
        raise OutreachError(
            f"recipient already has an open {row['state']} gesture; "
            "resolve it or wait for a reply before creating another"
        )


def retarget_pending_messages(
    connection: sqlite3.Connection,
    contact_id: str,
    channel: str,
    recipient: str,
    fingerprint: str,
) -> None:
    unresolved = connection.execute(
        """SELECT state FROM messages WHERE contact_id=?
           AND state IN ('exported','sent') ORDER BY updated_at DESC LIMIT 1""",
        (contact_id,),
    ).fetchone()
    if unresolved:
        raise OutreachError(
            f"cannot change endpoint while a {unresolved['state']} message is unresolved"
        )
    rows = connection.execute(
        """SELECT * FROM messages WHERE contact_id=?
           AND state IN ('draft','reviewed','awaiting_approval','approved')""",
        (contact_id,),
    ).fetchall()
    for row in rows:
        require_single_open_gesture(
            connection, fingerprint, exclude_message_id=row["id"]
        )
    for row in rows:
        revoke_approvals(connection, row["id"])
        digest = content_hash(channel, recipient, row["subject"], row["body"])
        connection.execute(
            """UPDATE messages SET channel=?,recipient=?,endpoint_fingerprint=?,
               content_hash=?,state='draft',updated_at=? WHERE id=?""",
            (channel, recipient, fingerprint, digest, iso(), row["id"]),
        )
        event(
            connection,
            "message_retargeted",
            contact_id=contact_id,
            message_id=row["id"],
            detail={"content_hash": digest, "approval_revoked": True},
        )


def mask_recipient(value: str | None) -> str | None:
    if not value:
        return None
    if value.casefold().startswith(("https://", "http://")):
        try:
            parts = urlsplit(value)
            hostname = parts.hostname
        except ValueError:
            return "[redacted]"
        if parts.scheme and hostname:
            return f"{parts.scheme.casefold()}://{hostname.casefold()}/…"
        return "[redacted]"
    if "@" in value:
        local, domain = value.rsplit("@", 1)
        return (local[:1] or "*") + "***@" + domain
    return "[redacted]"


def seed_contacts(connection: sqlite3.Connection, source: Path) -> int:
    document = json.loads(source.read_text())
    targets = document.get("targets")
    if not isinstance(targets, list):
        raise OutreachError("seed file must contain a targets array")
    count = 0
    with connection:
        for target in targets:
            contact_id = str(target.get("id", "")).strip()
            name = str(target.get("name", "")).strip()
            if not contact_id or not name:
                raise OutreachError("every seed requires id and name")
            now = iso()
            readiness_gate = target.get("readiness_gate")
            seed_readiness = (
                "blocked"
                if str(readiness_gate or "").lstrip().startswith("BLOCKED:")
                else "pending"
            )
            exists = connection.execute("SELECT 1 FROM contacts WHERE id = ?", (contact_id,)).fetchone()
            if exists:
                connection.execute(
                    """UPDATE contacts SET name=?,kind=?,maturity=?,priority=?,project_url=?,public_channel=?,fit=?,
                       first_gesture=?,readiness_gate=?,
                       readiness_status=CASE WHEN ?='blocked' THEN 'blocked' ELSE readiness_status END,
                       updated_at=? WHERE id=?""",
                    (
                        name,
                        target.get("kind"),
                        target.get("maturity"),
                        target.get("priority"),
                        target.get("project_url"),
                        target.get("public_channel"),
                        target.get("fit"),
                        target.get("first_gesture"),
                        readiness_gate,
                        seed_readiness,
                        now,
                        contact_id,
                    ),
                )
                event(connection, "contact_seed_refreshed", contact_id=contact_id)
                if seed_readiness == "blocked":
                    approved = connection.execute(
                        "SELECT id FROM messages WHERE contact_id=? AND state='approved'",
                        (contact_id,),
                    ).fetchall()
                    for row in approved:
                        revoke_approvals(connection, row["id"])
                        connection.execute(
                            "UPDATE messages SET state='reviewed',updated_at=? WHERE id=?",
                            (iso(), row["id"]),
                        )
                        event(
                            connection,
                            "message_approval_revoked_by_seed_gate",
                            contact_id=contact_id,
                            message_id=row["id"],
                        )
            else:
                connection.execute(
                    """INSERT INTO contacts(id,name,kind,maturity,priority,project_url,public_channel,fit,
                       first_gesture,readiness_gate,readiness_status,state,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,'research',?,?)""",
                    (
                        contact_id,
                        name,
                        target.get("kind"),
                        target.get("maturity"),
                        target.get("priority"),
                        target.get("project_url"),
                        target.get("public_channel"),
                        target.get("fit"),
                        target.get("first_gesture"),
                        readiness_gate,
                        seed_readiness,
                        now,
                        now,
                    ),
                )
                event(connection, "contact_seeded", contact_id=contact_id)
            count += 1
    return count


def add_contact(
    connection: sqlite3.Connection,
    contact_id: str,
    name: str | None,
    recipient: str | None,
    channel: str | None,
    kind: str | None,
    project_url: str | None,
) -> None:
    recipient = recipient.strip() if recipient is not None else None
    channel = channel.strip() if channel is not None else None
    if recipient is not None and (not recipient or "\n" in recipient or "\r" in recipient):
        raise OutreachError("recipient must be one non-empty line")
    if channel is not None and not channel:
        raise OutreachError("channel cannot be empty")
    now = iso()
    with immediate_transaction(connection):
        existing = connection.execute(
            "SELECT * FROM contacts WHERE id=?", (contact_id,)
        ).fetchone()
        if existing:
            if existing["state"] == "do_not_contact" and (
                recipient is not None or channel is not None
            ):
                raise OutreachError("cannot enrich a do_not_contact record")
            resolved_recipient = recipient if recipient is not None else existing["recipient"]
            resolved_channel = channel if channel is not None else existing["channel"]
            fingerprint = existing["endpoint_fingerprint"]
            endpoint_changed = (resolved_recipient, resolved_channel) != (
                existing["recipient"],
                existing["channel"],
            )
            if resolved_recipient and resolved_channel:
                fingerprint = require_endpoint_allowed(
                    connection, resolved_channel, resolved_recipient
                )
            if endpoint_changed and resolved_recipient and resolved_channel:
                retarget_pending_messages(
                    connection,
                    contact_id,
                    resolved_channel,
                    resolved_recipient,
                    fingerprint,
                )
            connection.execute(
                """UPDATE contacts SET name=COALESCE(?,name),recipient=COALESCE(?,recipient),
                   channel=COALESCE(?,channel),kind=COALESCE(?,kind),
                   project_url=COALESCE(?,project_url),endpoint_fingerprint=?,updated_at=?
                   WHERE id=?""",
                (
                    name,
                    recipient,
                    channel,
                    kind,
                    project_url,
                    fingerprint,
                    now,
                    contact_id,
                ),
            )
            event(
                connection,
                "contact_enriched",
                contact_id=contact_id,
                detail={"endpoint_changed": endpoint_changed},
            )
            return
        if not name:
            raise OutreachError("new contacts require --name")
        fingerprint = None
        if recipient and channel:
            fingerprint = require_endpoint_allowed(connection, channel, recipient)
        connection.execute(
            """INSERT INTO contacts(id,name,kind,project_url,recipient,channel,
               endpoint_fingerprint,state,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,'research',?,?)""",
            (
                contact_id,
                name,
                kind,
                project_url,
                recipient,
                channel,
                fingerprint,
                now,
                now,
            ),
        )
        event(connection, "contact_added", contact_id=contact_id)


def set_readiness(
    connection: sqlite3.Connection,
    contact_id: str,
    status: str,
    evidence: str,
) -> None:
    if status not in {"pending", "ready", "blocked"}:
        raise OutreachError("invalid readiness status")
    if status == "ready" and not evidence.strip():
        raise OutreachError("ready requires concrete --evidence")
    with immediate_transaction(connection):
        contact = require_contact(connection, contact_id)
        if contact["state"] == "do_not_contact":
            raise OutreachError("cannot change readiness for do_not_contact")
        if status != "ready":
            approved = connection.execute(
                "SELECT id FROM messages WHERE contact_id=? AND state='approved'",
                (contact_id,),
            ).fetchall()
            for row in approved:
                revoke_approvals(connection, row["id"])
                connection.execute(
                    "UPDATE messages SET state='reviewed',updated_at=? WHERE id=?",
                    (iso(), row["id"]),
                )
                event(
                    connection,
                    "message_approval_revoked_by_readiness",
                    contact_id=contact_id,
                    message_id=row["id"],
                )
        connection.execute(
            "UPDATE contacts SET readiness_status=?,updated_at=? WHERE id=?",
            (status, iso(), contact_id),
        )
        event(
            connection,
            "readiness_changed",
            contact_id=contact_id,
            detail={"from": contact["readiness_status"], "to": status, "evidence": evidence},
        )


def set_contact_state(
    connection: sqlite3.Connection,
    contact_id: str,
    state: str,
    reason: str,
) -> None:
    if state == "do_not_contact":
        raise OutreachError("use suppress to enter do_not_contact")
    if state not in CONTACT_STATES:
        raise OutreachError("invalid contact state")
    if not reason.strip():
        raise OutreachError("state changes require a concrete --reason")
    with immediate_transaction(connection):
        contact = require_contact(connection, contact_id)
        if contact["state"] == "do_not_contact":
            raise OutreachError("do_not_contact is a hard gate and cannot be reopened here")
        if state in {"paused", "closed"}:
            approved = connection.execute(
                "SELECT id FROM messages WHERE contact_id=? AND state='approved'",
                (contact_id,),
            ).fetchall()
            for row in approved:
                revoke_approvals(connection, row["id"])
                connection.execute(
                    "UPDATE messages SET state='reviewed',updated_at=? WHERE id=?",
                    (iso(), row["id"]),
                )
                event(
                    connection,
                    "message_approval_revoked_by_contact_state",
                    contact_id=contact_id,
                    message_id=row["id"],
                )
        connection.execute(
            "UPDATE contacts SET state=?,updated_at=? WHERE id=?",
            (state, iso(), contact_id),
        )
        event(
            connection,
            "contact_state_changed",
            contact_id=contact_id,
            detail={"from": contact["state"], "to": state, "reason": reason},
        )
def draft_message(
    connection: sqlite3.Connection,
    contact_id: str,
    subject: str,
    body: str,
) -> str:
    if not subject.strip() or not body.strip():
        raise OutreachError("subject and body are required")
    if "\n" in subject or "\r" in subject:
        raise OutreachError("subject must be a single line")
    message_id = "msg-" + uuid.uuid4().hex[:12]
    now = iso()
    with immediate_transaction(connection):
        contact = require_contact(connection, contact_id)
        require_contactable(contact)
        require_ready(contact)
        if not contact["recipient"] or not contact["channel"]:
            raise OutreachError("contact needs an exact private recipient and channel before drafting")
        fingerprint = require_endpoint_allowed(
            connection, contact["channel"], contact["recipient"]
        )
        require_single_open_gesture(connection, fingerprint)
        digest = content_hash(contact["channel"], contact["recipient"], subject, body)
        connection.execute(
            """INSERT INTO messages(id,contact_id,channel,recipient,endpoint_fingerprint,
               subject,body,content_hash,state,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,'draft',?,?)""",
            (
                message_id,
                contact_id,
                contact["channel"],
                contact["recipient"],
                fingerprint,
                subject,
                body,
                digest,
                now,
                now,
            ),
        )
        event(connection, "message_drafted", contact_id=contact_id, message_id=message_id, detail={"content_hash": digest})
    return message_id


def revoke_approvals(connection: sqlite3.Connection, message_id: str) -> None:
    connection.execute(
        "UPDATE approvals SET revoked_at=? WHERE message_id=? AND consumed_at IS NULL AND revoked_at IS NULL",
        (iso(), message_id),
    )


def revise_message(
    connection: sqlite3.Connection,
    message_id: str,
    subject: str | None,
    body: str | None,
) -> None:
    if subject is None and body is None:
        raise OutreachError("revise requires --subject, --body-file, or --stdin")
    with immediate_transaction(connection):
        message = require_message(connection, message_id)
        contact = require_contact(connection, message["contact_id"])
        require_contactable(contact)
        if message["state"] in {"exported", "sent", "replied", "cancelled"}:
            raise OutreachError(f"cannot revise a {message['state']} message")
        new_subject = subject if subject is not None else message["subject"]
        new_body = body if body is not None else message["body"]
        if not new_subject.strip() or not new_body.strip():
            raise OutreachError("subject and body are required")
        if "\n" in new_subject or "\r" in new_subject:
            raise OutreachError("subject must be a single line")
        require_endpoint_allowed(connection, message["channel"], message["recipient"])
        digest = content_hash(
            message["channel"], message["recipient"], new_subject, new_body
        )
        revoke_approvals(connection, message_id)
        connection.execute(
            "UPDATE messages SET subject=?,body=?,content_hash=?,state='draft',updated_at=? WHERE id=?",
            (new_subject, new_body, digest, iso(), message_id),
        )
        event(connection, "message_revised", contact_id=message["contact_id"], message_id=message_id, detail={"content_hash": digest})


def transition_message(
    connection: sqlite3.Connection,
    message_id: str,
    expected: str,
    target: str,
    event_type: str,
    detail: dict[str, Any] | None = None,
    work_id: str | None = None,
) -> sqlite3.Row:
    canonical_event = MESSAGE_TRANSITIONS.get((expected, target))
    if canonical_event != event_type:
        raise OutreachError(f"invalid message transition: {expected} -> {target}")
    if target in {"reviewed", "awaiting_approval"} and not work_id:
        raise OutreachError(f"transition to {target} requires a ready work item")
    if target == "sent":
        provider_id = str((detail or {}).get("provider_id") or "").strip()
        if not provider_id:
            raise OutreachError("marking sent requires a delivery receipt reference")
        if "\n" in provider_id or "\r" in provider_id:
            raise OutreachError("delivery receipt reference must be a single line")
    with immediate_transaction(connection):
        message = require_message(connection, message_id)
        contact = require_contact(connection, message["contact_id"])
        require_contactable(contact)
        if target in {"reviewed", "awaiting_approval"}:
            require_ready(contact)
        resolved_detail = dict(detail or {})
        if work_id:
            import relations_pipeline

            snapshot_hash = relations_pipeline.assert_work_ready_for_message(
                connection, work_id, message_id
            )
            resolved_detail.update(
                {"work_id": work_id, "work_snapshot_hash": snapshot_hash}
            )
        if message["state"] != expected:
            raise OutreachError(f"{message_id} is {message['state']}; expected {expected}")
        if target == "sent":
            connection.execute(
                "INSERT INTO delivery_receipts(receipt_ref,message_id,recorded_at) VALUES(?,?,?)",
                (provider_id, message_id, iso()),
            )
        if target in {"reviewed", "awaiting_approval"}:
            require_endpoint_allowed(
                connection, message["channel"], message["recipient"]
            )
        connection.execute("UPDATE messages SET state=?,updated_at=? WHERE id=?", (target, iso(), message_id))
        event(
            connection,
            event_type,
            contact_id=message["contact_id"],
            message_id=message_id,
            detail=resolved_detail,
        )
    return require_message(connection, message_id)


def approve_message(
    connection: sqlite3.Connection,
    message_id: str,
    approved_by: str,
    expires_hours: float,
    expected_hash: str,
    work_id: str,
) -> str:
    if approved_by.strip().casefold() != "yu":
        raise OutreachError("operator approval must be explicitly asserted as --by yu")
    if not work_id.strip():
        raise OutreachError("approval requires a ready work item")
    if not math.isfinite(expires_hours) or expires_hours <= 0 or expires_hours > 168:
        raise OutreachError("approval expiry must be between 0 and 168 hours")
    approval_id = "approval-" + uuid.uuid4().hex[:12]
    now = utc_now()
    with immediate_transaction(connection):
        message = require_message(connection, message_id)
        contact = require_contact(connection, message["contact_id"])
        require_contactable(contact)
        require_ready(contact)
        import relations_pipeline

        work_snapshot_hash = relations_pipeline.assert_work_ready_for_message(
            connection, work_id, message_id
        )
        fingerprint = require_endpoint_allowed(
            connection, message["channel"], message["recipient"]
        )
        require_single_open_gesture(
            connection, fingerprint, exclude_message_id=message_id
        )
        if message["state"] != "awaiting_approval":
            raise OutreachError(
                f"{message_id} is {message['state']}; expected awaiting_approval"
            )
        if expected_hash != message["content_hash"]:
            raise OutreachError(
                "approval hash does not match the current message snapshot; preview again"
            )
        revoke_approvals(connection, message_id)
        connection.execute(
            """INSERT INTO approvals(
               id,message_id,work_id,work_snapshot_hash,channel,recipient,
               content_hash,approved_by,approved_at,expires_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                approval_id,
                message_id,
                work_id,
                work_snapshot_hash,
                message["channel"],
                message["recipient"],
                message["content_hash"],
                approved_by,
                iso(now),
                iso(now + timedelta(hours=expires_hours)),
            ),
        )
        connection.execute("UPDATE messages SET state='approved',updated_at=? WHERE id=?", (iso(now), message_id))
        event(
            connection,
            "message_approved",
            contact_id=message["contact_id"],
            message_id=message_id,
            detail={
                "approval_id": approval_id,
                "approved_by": approved_by,
                "work_id": work_id,
                "work_snapshot_hash": work_snapshot_hash,
            },
        )
    return approval_id


def export_message(connection: sqlite3.Connection, message_id: str) -> dict[str, Any]:
    with immediate_transaction(connection):
        message = require_message(connection, message_id)
        contact = require_contact(connection, message["contact_id"])
        require_contactable(contact)
        if message["state"] != "approved":
            raise OutreachError(f"{message_id} is {message['state']}; expected approved")
        require_ready(contact)
        fingerprint = require_endpoint_allowed(
            connection, message["channel"], message["recipient"]
        )
        require_single_open_gesture(
            connection, fingerprint, exclude_message_id=message_id
        )
        approval = connection.execute(
            """SELECT * FROM approvals WHERE message_id=? AND consumed_at IS NULL
               AND revoked_at IS NULL ORDER BY approved_at DESC LIMIT 1""",
            (message_id,),
        ).fetchone()
        if approval is None:
            raise OutreachError("no active approval")
        if approval["expires_at"] <= iso():
            raise OutreachError("approval expired")
        if not approval["work_id"] or not approval["work_snapshot_hash"]:
            raise OutreachError("approval is not bound to a reviewed work snapshot")
        import relations_pipeline

        current_work_hash = relations_pipeline.assert_work_ready_for_message(
            connection, approval["work_id"], message_id
        )
        if current_work_hash != approval["work_snapshot_hash"]:
            raise OutreachError("approval no longer matches the reviewed work snapshot")
        relations_pipeline.consume_contact_basis(
            connection, approval["work_id"], message_id
        )
        expected = (message["channel"], message["recipient"], message["content_hash"])
        actual = (approval["channel"], approval["recipient"], approval["content_hash"])
        if expected != actual:
            raise OutreachError(
                "approval no longer matches recipient, channel, and content"
            )
        consumed = iso()
        approval_update = connection.execute(
            """UPDATE approvals SET consumed_at=? WHERE id=?
               AND consumed_at IS NULL AND revoked_at IS NULL""",
            (consumed, approval["id"]),
        )
        message_update = connection.execute(
            """UPDATE messages SET state='exported',updated_at=?
               WHERE id=? AND state='approved'""",
            (consumed, message_id),
        )
        if approval_update.rowcount != 1 or message_update.rowcount != 1:
            raise OutreachError("approval was consumed concurrently")
        event(
            connection,
            "message_exported",
            contact_id=message["contact_id"],
            message_id=message_id,
            detail={"approval_id": approval["id"]},
        )
        return {
            "message_id": message_id,
            "channel": message["channel"],
            "recipient": message["recipient"],
            "subject": message["subject"],
            "body": message["body"],
            "content_hash": message["content_hash"],
        }


def suppress_contact(connection: sqlite3.Connection, contact_id: str, reason: str) -> None:
    if not reason.strip():
        raise OutreachError("suppression requires a concrete reason")
    with immediate_transaction(connection):
        contact = require_contact(connection, contact_id)
        fingerprint = contact["endpoint_fingerprint"]
        affected_ids = [contact_id]
        if fingerprint:
            connection.execute(
                """INSERT INTO suppressions(endpoint_fingerprint,created_at,reason)
                   VALUES(?,?,?) ON CONFLICT(endpoint_fingerprint) DO NOTHING""",
                (fingerprint, iso(), reason),
            )
            affected_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM contacts WHERE endpoint_fingerprint=?",
                    (fingerprint,),
                ).fetchall()
            ]
        for affected_id in affected_ids:
            pending = connection.execute(
                """SELECT id FROM messages WHERE contact_id=?
                   AND state IN ('draft','reviewed','awaiting_approval','approved','exported')""",
                (affected_id,),
            ).fetchall()
            for row in pending:
                revoke_approvals(connection, row["id"])
                connection.execute(
                    "UPDATE messages SET state='cancelled',updated_at=? WHERE id=?",
                    (iso(), row["id"]),
                )
                event(
                    connection,
                    "message_cancelled_by_suppression",
                    contact_id=affected_id,
                    message_id=row["id"],
                )
            connection.execute(
                "UPDATE contacts SET state='do_not_contact',updated_at=? WHERE id=?",
                (iso(), affected_id),
            )
            event(
                connection,
                "contact_suppressed",
                contact_id=affected_id,
                detail={"reason": reason, "endpoint_scope": bool(fingerprint)},
            )


def cancel_message(
    connection: sqlite3.Connection,
    message_id: str,
    reason: str,
) -> None:
    if not reason.strip():
        raise OutreachError("cancellation requires a concrete reason")
    with immediate_transaction(connection):
        message = require_message(connection, message_id)
        if message["state"] not in PENDING_MESSAGE_STATES:
            raise OutreachError(f"cannot cancel a {message['state']} message")
        revoke_approvals(connection, message_id)
        connection.execute(
            "UPDATE messages SET state='cancelled',updated_at=? WHERE id=?",
            (iso(), message_id),
        )
        event(
            connection,
            "message_cancelled",
            contact_id=message["contact_id"],
            message_id=message_id,
            detail={"reason": reason},
        )


def record_reply(
    connection: sqlite3.Connection,
    message_id: str,
    receipt_ref: str,
    mail_work_id: str | None = None,
) -> None:
    receipt_ref = receipt_ref.strip()
    if not receipt_ref:
        raise OutreachError("recording a reply requires an inbound receipt reference")
    if "\n" in receipt_ref or "\r" in receipt_ref:
        raise OutreachError("reply receipt reference must be a single line")
    with immediate_transaction(connection):
        message = require_message(connection, message_id)
        if message["state"] != "sent":
            raise OutreachError(f"{message_id} is {message['state']}; expected sent")
        contact = require_contact(connection, message["contact_id"])
        linked_mail_work_id = None
        if receipt_ref.casefold().startswith("imap:"):
            match = re.fullmatch(
                r"imap:([A-Za-z0-9_.-]{1,64}):([1-9]\d{0,9}):([1-9]\d{0,9})",
                receipt_ref,
                flags=re.IGNORECASE,
            )
            if match is None:
                raise OutreachError("IMAP reply receipt must be imap:ACCOUNT:UIDVALIDITY:UID")
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mail_intake'"
            ).fetchone()
            if table_exists is None:
                raise OutreachError("IMAP reply receipt requires hashed mail intake first")
            account, uidvalidity_text, uid_text = match.groups()
            account = account.casefold()
            uidvalidity = int(uidvalidity_text)
            uid = int(uid_text)
            if uidvalidity > 4_294_967_295 or uid > 4_294_967_295:
                raise OutreachError("IMAP reply receipt values exceed the 32-bit UID range")
            receipt_ref = f"imap:{account}:{uidvalidity}:{uid}"
            intake = connection.execute(
                """SELECT work_id FROM mail_intake
                   WHERE account=? AND uidvalidity=? AND uid=?""",
                (account, uidvalidity, uid),
            ).fetchone()
            if intake is None:
                raise OutreachError("IMAP reply receipt is not present in hashed mail intake")
            linked_mail_work_id = intake["work_id"]
            disposition = connection.execute(
                "SELECT outcome FROM mail_dispositions WHERE work_id=?",
                (linked_mail_work_id,),
            ).fetchone()
            if disposition is None or disposition["outcome"] != "needs_action":
                raise OutreachError(
                    "IMAP reply receipt requires a needs_action mail classification"
                )
            if mail_work_id and mail_work_id != linked_mail_work_id:
                raise OutreachError("IMAP reply receipt does not match --mail-work-id")
        elif mail_work_id:
            raise OutreachError("--mail-work-id requires an IMAP reply receipt")
        connection.execute(
            """INSERT INTO reply_receipts(
               receipt_ref,message_id,mail_work_id,recorded_at) VALUES(?,?,?,?)""",
            (receipt_ref, message_id, linked_mail_work_id, iso()),
        )
        connection.execute("UPDATE messages SET state='replied',updated_at=? WHERE id=?", (iso(), message_id))
        pending = connection.execute(
            """SELECT id FROM messages WHERE contact_id=? AND id<>?
               AND state IN ('draft','reviewed','awaiting_approval','approved','exported')""",
            (message["contact_id"], message_id),
        ).fetchall()
        for row in pending:
            revoke_approvals(connection, row["id"])
            connection.execute("UPDATE messages SET state='cancelled',updated_at=? WHERE id=?", (iso(), row["id"]))
            event(connection, "message_cancelled_after_reply", contact_id=message["contact_id"], message_id=row["id"])
        if contact["state"] in {"research", "active"}:
            connection.execute(
                "UPDATE contacts SET state='active',updated_at=? WHERE id=?",
                (iso(), message["contact_id"]),
            )
        event(
            connection,
            "reply_recorded",
            contact_id=message["contact_id"],
            message_id=message_id,
            detail={
                "do_not_contact_preserved": contact["state"] == "do_not_contact",
                "contact_state_preserved": contact["state"] in {"paused", "closed", "do_not_contact"},
                "receipt_ref": receipt_ref,
            },
        )


def body_from_args(args: argparse.Namespace, *, required: bool) -> str | None:
    if getattr(args, "body_file", None):
        return Path(args.body_file).read_text()
    if getattr(args, "stdin", False):
        return sys.stdin.read()
    if required:
        raise OutreachError("message body must come from --body-file or --stdin")
    return None


def recipient_from_args(args: argparse.Namespace) -> str | None:
    value: str | None = None
    if getattr(args, "recipient_file", None):
        value = Path(args.recipient_file).read_text()
    elif getattr(args, "recipient_stdin", False):
        value = sys.stdin.read()
    elif getattr(args, "recipient", None):
        value = args.recipient
    if value is None:
        return None
    value = value.strip()
    if not value:
        raise OutreachError("recipient cannot be empty")
    if "\n" in value or "\r" in value:
        raise OutreachError("recipient must be a single line")
    return value


def private_text_from_args(
    args: argparse.Namespace,
    direct_attr: str,
    file_attr: str,
    label: str,
    *,
    required: bool,
    single_line: bool = False,
) -> str | None:
    value = getattr(args, direct_attr, None)
    source = getattr(args, file_attr, None)
    if source:
        value = Path(source).read_text()
    if value is None:
        if required:
            raise OutreachError(f"{label} is required")
        return None
    value = value.strip()
    if required and not value:
        raise OutreachError(f"{label} is required")
    if single_line and ("\n" in value or "\r" in value):
        raise OutreachError(f"{label} must be a single line")
    return value


def json_print(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Private, approval-bound ecosystem relationship ledger")
    sub = parser.add_subparsers(dest="command", required=True)

    contact = sub.add_parser("contact")
    contact_sub = contact.add_subparsers(dest="contact_command", required=True)
    seed = contact_sub.add_parser("seed")
    seed.add_argument("--file", required=True)
    add = contact_sub.add_parser("add")
    add.add_argument("--id", required=True)
    add.add_argument("--name")
    recipient = add.add_mutually_exclusive_group()
    recipient.add_argument(
        "--recipient",
        help="Exact endpoint (prefer --recipient-stdin so private data avoids shell history)",
    )
    recipient.add_argument("--recipient-file")
    recipient.add_argument("--recipient-stdin", action="store_true")
    add.add_argument("--channel")
    add.add_argument("--kind")
    add.add_argument("--project-url")
    listing = contact_sub.add_parser("list")
    listing.add_argument("--state", choices=sorted(CONTACT_STATES))
    show = contact_sub.add_parser("show")
    show.add_argument("id")
    show.add_argument("--show-recipient", action="store_true")
    readiness = contact_sub.add_parser("readiness")
    readiness.add_argument("id")
    readiness.add_argument("--status", required=True, choices=["pending", "ready", "blocked"])
    readiness_evidence = readiness.add_mutually_exclusive_group()
    readiness_evidence.add_argument("--evidence")
    readiness_evidence.add_argument("--evidence-file")
    state = contact_sub.add_parser("state")
    state.add_argument("id")
    state.add_argument(
        "--state",
        required=True,
        choices=sorted(CONTACT_STATES - {"do_not_contact"}),
    )
    state_reason = state.add_mutually_exclusive_group(required=True)
    state_reason.add_argument("--reason")
    state_reason.add_argument("--reason-file")

    message = sub.add_parser("message")
    message_sub = message.add_subparsers(dest="message_command", required=True)
    draft = message_sub.add_parser("draft")
    draft.add_argument("contact_id")
    draft_subject = draft.add_mutually_exclusive_group(required=True)
    draft_subject.add_argument("--subject")
    draft_subject.add_argument("--subject-file")
    draft_body = draft.add_mutually_exclusive_group(required=True)
    draft_body.add_argument("--body-file")
    draft_body.add_argument("--stdin", action="store_true")
    revise = message_sub.add_parser("revise")
    revise.add_argument("message_id")
    revise_subject = revise.add_mutually_exclusive_group()
    revise_subject.add_argument("--subject")
    revise_subject.add_argument("--subject-file")
    revise_body = revise.add_mutually_exclusive_group()
    revise_body.add_argument("--body-file")
    revise_body.add_argument("--stdin", action="store_true")
    review = message_sub.add_parser(
        "review",
        help="Seal a linked ready work snapshot into message state; work reviews remain separate",
    )
    review.add_argument("message_id")
    review.add_argument("--work-id", required=True)
    request = message_sub.add_parser("request-approval")
    request.add_argument("message_id")
    request.add_argument("--work-id", required=True)
    approve = message_sub.add_parser("approve")
    approve.add_argument("message_id")
    approve.add_argument("--by", required=True)
    approve.add_argument("--work-id", required=True)
    approve.add_argument(
        "--content-hash",
        required=True,
        help="Exact content_hash shown by the approval preview",
    )
    approve.add_argument("--expires-hours", type=float, default=24)
    preview = message_sub.add_parser("preview")
    preview.add_argument("message_id")
    preview.add_argument(
        "--show-recipient",
        action="store_true",
        help="Reveal the exact message-snapshot recipient for deliberate approval review",
    )
    export = message_sub.add_parser("export")
    export.add_argument("message_id")
    sent = message_sub.add_parser("mark-sent")
    sent.add_argument("message_id")
    provider = sent.add_mutually_exclusive_group(required=True)
    provider.add_argument("--provider-id")
    provider.add_argument("--provider-id-file")
    reply = message_sub.add_parser("reply")
    reply.add_argument("message_id")
    reply_receipt = reply.add_mutually_exclusive_group(required=True)
    reply_receipt.add_argument("--receipt")
    reply_receipt.add_argument("--receipt-file")
    reply.add_argument("--mail-work-id")
    cancel = message_sub.add_parser("cancel")
    cancel.add_argument("message_id")
    cancel_reason = cancel.add_mutually_exclusive_group(required=True)
    cancel_reason.add_argument("--reason")
    cancel_reason.add_argument("--reason-file")

    events = sub.add_parser("events")
    events.add_argument("--contact-id")
    events.add_argument("--limit", type=int, default=50)
    suppress = sub.add_parser("suppress")
    suppress.add_argument("contact_id")
    suppress_reason = suppress.add_mutually_exclusive_group(required=True)
    suppress_reason.add_argument("--reason")
    suppress_reason.add_argument("--reason-file")
    return parser


def run(argv: list[str] | None = None, *, path: Path | None = None) -> int:
    args = build_parser().parse_args(argv)
    connection = connect(path)
    try:
        if args.command == "contact":
            if args.contact_command == "seed":
                json_print({"seeded": seed_contacts(connection, Path(args.file))})
            elif args.contact_command == "add":
                add_contact(
                    connection,
                    args.id,
                    args.name,
                    recipient_from_args(args),
                    args.channel,
                    args.kind,
                    args.project_url,
                )
                json_print({"contact_id": args.id, "status": "stored"})
            elif args.contact_command == "list":
                sql = (
                    "SELECT id,name,kind,maturity,priority,state,readiness_status,"
                    "readiness_gate FROM contacts"
                )
                values: tuple[Any, ...] = ()
                if args.state:
                    sql += " WHERE state=?"
                    values = (args.state,)
                sql += " ORDER BY priority IS NULL,priority,name"
                json_print([dict(row) for row in connection.execute(sql, values).fetchall()])
            elif args.contact_command == "show":
                row = dict(require_contact(connection, args.id))
                if not args.show_recipient:
                    row["recipient"] = mask_recipient(row.get("recipient"))
                json_print(row)
            elif args.contact_command == "readiness":
                evidence = private_text_from_args(
                    args,
                    "evidence",
                    "evidence_file",
                    "readiness evidence",
                    required=args.status == "ready",
                )
                set_readiness(connection, args.id, args.status, evidence or "")
                json_print({"contact_id": args.id, "readiness_status": args.status})
            elif args.contact_command == "state":
                reason = private_text_from_args(
                    args, "reason", "reason_file", "state reason", required=True
                )
                set_contact_state(connection, args.id, args.state, reason or "")
                json_print({"contact_id": args.id, "state": args.state})
        elif args.command == "message":
            if args.message_command == "draft":
                subject = private_text_from_args(
                    args,
                    "subject",
                    "subject_file",
                    "subject",
                    required=True,
                    single_line=True,
                )
                message_id = draft_message(
                    connection,
                    args.contact_id,
                    subject or "",
                    body_from_args(args, required=True) or "",
                )
                json_print({"message_id": message_id, "state": "draft"})
            elif args.message_command == "revise":
                subject = private_text_from_args(
                    args,
                    "subject",
                    "subject_file",
                    "subject",
                    required=False,
                    single_line=True,
                )
                revise_message(
                    connection,
                    args.message_id,
                    subject,
                    body_from_args(args, required=False),
                )
                json_print({"message_id": args.message_id, "state": "draft", "approvals": "revoked"})
            elif args.message_command == "review":
                import relations_pipeline

                relations_pipeline.ensure_schema(connection)
                transition_message(
                    connection,
                    args.message_id,
                    "draft",
                    "reviewed",
                    "message_reviewed",
                    work_id=args.work_id,
                )
                json_print({"message_id": args.message_id, "state": "reviewed"})
            elif args.message_command == "request-approval":
                import relations_pipeline

                relations_pipeline.ensure_schema(connection)
                transition_message(
                    connection,
                    args.message_id,
                    "reviewed",
                    "awaiting_approval",
                    "approval_requested",
                    work_id=args.work_id,
                )
                json_print({"message_id": args.message_id, "state": "awaiting_approval"})
            elif args.message_command == "approve":
                import relations_pipeline

                relations_pipeline.ensure_schema(connection)
                approval_id = approve_message(
                    connection,
                    args.message_id,
                    args.by,
                    args.expires_hours,
                    args.content_hash,
                    args.work_id,
                )
                json_print({"message_id": args.message_id, "state": "approved", "approval_id": approval_id})
            elif args.message_command == "preview":
                row = dict(require_message(connection, args.message_id))
                if not args.show_recipient:
                    row["recipient"] = mask_recipient(row["recipient"])
                json_print(row)
            elif args.message_command == "export":
                json_print(export_message(connection, args.message_id))
            elif args.message_command == "mark-sent":
                provider_id = private_text_from_args(
                    args,
                    "provider_id",
                    "provider_id_file",
                    "provider id",
                    required=True,
                    single_line=True,
                )
                transition_message(
                    connection,
                    args.message_id,
                    "exported",
                    "sent",
                    "message_marked_sent",
                    {"provider_id": provider_id},
                )
                json_print({"message_id": args.message_id, "state": "sent"})
            elif args.message_command == "reply":
                receipt_ref = private_text_from_args(
                    args,
                    "receipt",
                    "receipt_file",
                    "reply receipt",
                    required=True,
                    single_line=True,
                )
                record_reply(
                    connection,
                    args.message_id,
                    receipt_ref or "",
                    args.mail_work_id,
                )
                json_print({"message_id": args.message_id, "state": "replied"})
            elif args.message_command == "cancel":
                reason = private_text_from_args(
                    args, "reason", "reason_file", "cancellation reason", required=True
                )
                cancel_message(connection, args.message_id, reason or "")
                json_print({"message_id": args.message_id, "state": "cancelled"})
        elif args.command == "events":
            limit = max(1, min(args.limit, 500))
            if args.contact_id:
                rows = connection.execute(
                    "SELECT * FROM events WHERE contact_id=? ORDER BY seq DESC LIMIT ?",
                    (args.contact_id, limit),
                ).fetchall()
            else:
                rows = connection.execute("SELECT * FROM events ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
            json_print([dict(row) for row in rows])
        elif args.command == "suppress":
            reason = private_text_from_args(
                args, "reason", "reason_file", "suppression reason", required=True
            )
            suppress_contact(connection, args.contact_id, reason or "")
            json_print({"contact_id": args.contact_id, "state": "do_not_contact"})
        return 0
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    try:
        return run(argv)
    except (OutreachError, json.JSONDecodeError, OSError, sqlite3.Error) as error:
        print(f"outreach: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
