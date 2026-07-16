"""Offline tests for the safe IMAP inbox reader."""

from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
import hashlib
import importlib.util

import pytest


MODULE_PATH = Path(__file__).parent.parent / "tools" / "check_email.py"
SPEC = importlib.util.spec_from_file_location("check_email", MODULE_PATH)
check_email = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_email)

NOW = datetime(2026, 7, 16, 10, 30, tzinfo=timezone.utc)
UIDVALIDITY = 678


def _message(uid, internaldate, body="hello", subject=None):
    message = EmailMessage()
    message["Message-ID"] = f"<{uid}@example.test>"
    message["From"] = "Sender <sender@example.test>"
    message["To"] = "inbox@example.test"
    message["Subject"] = subject or f"Message {uid}"
    message["Date"] = "Thu, 16 Jul 2026 10:00:00 +0000"
    message.set_content(body)
    return internaldate, message.as_bytes()


class FakeIMAP:
    def __init__(self, host, port, messages):
        self.host = host
        self.port = port
        self.messages = messages
        self.calls = []
        self.logged_out = False

    def login(self, address, password):
        self.calls.append(("login", address, password))
        return "OK", [b"logged in"]

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [" ".join(str(uid) for uid in self.messages).encode()]
        if command == "FETCH":
            uid = int(args[0])
            internaldate, raw = self.messages[uid]
            descriptor = (
                f'1 (UID {uid} INTERNALDATE "{internaldate}" BODY[] {{{len(raw)}}}'
            ).encode()
            return "OK", [(descriptor, raw), b")"]
        raise AssertionError(f"Unexpected command: {command}")

    def response(self, name):
        assert name == "UIDVALIDITY"
        self.calls.append(("response", name))
        return "UIDVALIDITY", [str(UIDVALIDITY).encode()]

    def logout(self):
        self.logged_out = True
        self.calls.append(("logout",))
        return "BYE", [b"logged out"]


@pytest.fixture
def fake_mailbox(monkeypatch):
    messages = {
        40: _message(40, "16-Jul-2026 09:59:59 +0000", "old secret 1111"),
        41: _message(41, "16-Jul-2026 10:00:00 +0000", "visit https://example.test/a code 234567"),
        42: _message(42, "16-Jul-2026 10:29:00 +0000", "new secret 8765"),
    }
    instances = []

    def factory(host, port):
        instance = FakeIMAP(host, port, messages)
        instances.append(instance)
        return instance

    monkeypatch.setattr(
        check_email,
        "ACCOUNTS",
        {
            "test": {
                "host": "imap.invalid",
                "port": 993,
                "email": "inbox@example.test",
                "password": "fake-test-password",
            }
        },
    )
    monkeypatch.setattr(check_email.imaplib, "IMAP4_SSL", factory)
    return messages, instances


def _fetch_queries(instance):
    return [call[3] for call in instance.calls if call[:2] == ("uid", "FETCH")]


def test_default_is_metadata_only_and_never_marks_seen(fake_mailbox):
    _, instances = fake_mailbox

    result = check_email.check_email(account="test", now=NOW)

    assert result["found"] == 2
    assert result["emails"][0]["uid"] == "42"
    assert set(result["emails"][0]) == {
        "uid",
        "message_id_hash",
        "from_hash",
        "subject_hash",
        "subject_length",
        "date",
    }
    instance = instances[0]
    assert ("select", "INBOX", True) in instance.calls
    assert instance.logged_out
    assert _fetch_queries(instance)
    assert all("BODY.PEEK[HEADER.FIELDS" in query for query in _fetch_queries(instance))
    assert all("RFC822" not in query for query in _fetch_queries(instance))


def test_body_analysis_never_returns_raw_content_links_or_codes(fake_mailbox):
    _, instances = fake_mailbox

    result = check_email.check_email(
        account="test",
        now=NOW,
        limit=1,
        analyze_body=True,
        summarize_message_links=True,
        detect_message_codes=True,
    )

    item = result["emails"][0]
    assert item["body_character_count"] > 0
    assert item["link_count"] == 0
    assert item["link_host_hashes"] == []
    assert item["code_like_token_count"] == 1
    serialized = str(item)
    assert "new secret" not in serialized
    assert "8765" not in serialized
    assert "body_preview" not in item
    assert "links" not in item
    assert "codes" not in item
    assert _fetch_queries(instances[0]) == [check_email.CONTENT_FETCH]
    assert "BODY.PEEK[]" in _fetch_queries(instances[0])[0]


def test_exact_minute_cutoff_excludes_older_message_on_same_day(fake_mailbox):
    _, instances = fake_mailbox

    result = check_email.check_email(account="test", now=NOW, since_minutes=30)

    assert [item["uid"] for item in result["emails"]] == ["42", "41"]
    search_call = next(call for call in instances[0].calls if call[:2] == ("uid", "SEARCH"))
    assert "SINCE" in search_call


def test_after_uid_uses_uid_commands_and_returns_stable_cursor(fake_mailbox):
    _, instances = fake_mailbox

    result = check_email.check_email(
        account="test",
        now=NOW,
        after_uid=41,
        uidvalidity=UIDVALIDITY,
        since_minutes=None,
    )

    assert [item["uid"] for item in result["emails"]] == ["42"]
    assert result["next_uid"] == 42
    assert result["uidvalidity"] == UIDVALIDITY
    search_call = next(call for call in instances[0].calls if call[:2] == ("uid", "SEARCH"))
    assert "UID" in search_call
    assert "42:*" in search_call
    assert "UNSEEN" not in search_call
    fetch_calls = [call for call in instances[0].calls if call[:2] == ("uid", "FETCH")]
    assert [call[2] for call in fetch_calls] == ["42"]


def test_link_summary_returns_hosts_not_raw_urls(fake_mailbox):
    _, _ = fake_mailbox

    result = check_email.check_email(
        account="test",
        now=NOW,
        after_uid=40,
        uidvalidity=UIDVALIDITY,
        since_minutes=None,
        limit=1,
        summarize_message_links=True,
    )

    item = result["emails"][0]
    assert item["link_count"] == 1
    assert item["link_host_hashes"] == [
        hashlib.sha256(b"example.test").hexdigest()
    ]
    assert "https://example.test/a" not in str(item)
    assert "code_like_token_count" not in item
    assert "body_character_count" not in item


def test_untrusted_headers_are_hashed_not_printed(fake_mailbox):
    messages, _ = fake_mailbox
    sensitive_header = "Reset https://example.test/private?token=not-a-real-secret"
    messages[42] = _message(
        42,
        "16-Jul-2026 10:29:00 +0000",
        "ordinary body",
        subject=sensitive_header,
    )

    result = check_email.check_email(account="test", now=NOW, limit=1)

    item = result["emails"][0]
    assert sensitive_header not in str(item)
    assert "example.test" not in str(item)
    assert item["subject_length"] == len(sensitive_header)
    assert len(item["subject_hash"]) == 64


def test_parser_exposes_documented_safe_flags(monkeypatch):
    monkeypatch.setattr(check_email, "ACCOUNTS", {"test": {}})
    parser = check_email.build_parser()

    defaults = parser.parse_args(["--account", "test"])
    assert defaults.unseen_only is None
    assert defaults.analyze_body is False
    assert defaults.summarize_links is False
    assert defaults.detect_codes is False

    args = parser.parse_args(
        [
            "--account",
            "test",
            "--since-minutes",
            "12",
            "--after-uid",
            "99",
            "--uidvalidity",
            str(UIDVALIDITY),
            "--all",
            "--analyze-body",
            "--summarize-links",
            "--detect-codes",
        ]
    )
    assert args.since_minutes == 12
    assert args.after_uid == 99
    assert args.uidvalidity == UIDVALIDITY
    assert args.unseen_only is False
    assert args.analyze_body and args.summarize_links and args.detect_codes


def test_cursor_rejects_seen_filter_and_uidvalidity_epoch_change(fake_mailbox):
    _, _ = fake_mailbox

    unsafe = check_email.check_email(
        account="test",
        now=NOW,
        after_uid=41,
        uidvalidity=UIDVALIDITY,
        unseen_only=True,
        since_minutes=None,
    )
    assert "must include seen mail" in unsafe["error"]

    changed = check_email.check_email(
        account="test",
        now=NOW,
        after_uid=41,
        uidvalidity=999,
        since_minutes=None,
    )
    assert changed["reset_required"] is True
    assert changed["uidvalidity"] == UIDVALIDITY


def test_unknown_charsets_fall_back_without_crashing():
    assert check_email.decode_str("=?x-unknown?b?SGVsbG8=?=") == "Hello"

    message = EmailMessage()
    message.set_payload(b"safe text")
    message.set_type("text/plain")
    message.set_param("charset", "x-unknown")
    assert check_email.get_body(message) == "safe text"


def test_cli_returns_nonzero_for_mailbox_errors(monkeypatch, capsys):
    monkeypatch.setattr(
        check_email,
        "ACCOUNTS",
        {
            "test": {
                "host": "imap.invalid",
                "port": 993,
                "email": "inbox@example.test",
                "password": "",
                "password_env": "IMAP_TEST_PASS",
            }
        },
    )

    assert check_email.main(["--account", "test"]) == 2
    assert "No password configured" in capsys.readouterr().out
