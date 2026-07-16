#!/usr/bin/env python3
"""Read recent inbox metadata without changing message state.

Examples:
    python3 tools/check_email.py --account zerone-dev --since-minutes 10
    python3 tools/check_email.py --account zerone-dev --unseen --summarize-links
    python3 tools/check_email.py --account zerone-dev --after-uid 0
    python3 tools/check_email.py --account zerone-dev --after-uid 12345 --uidvalidity 678

By default, each result contains UID plus stable hashes/lengths for untrusted
headers and a parsed date. Optional body analysis returns counts and hashed
link hosts, never message text, raw URLs, or code values. All fetches use
bounded BODY.PEEK and the mailbox is selected read-only.
"""

import argparse
import email
import hashlib
import imaplib
import json
import os
import re
from urllib.parse import urlsplit
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime


ACCOUNTS = {
    "zerone-dev": {
        "host": "imap.gmail.com",
        "port": 993,
        "email_env": "IMAP_CAMBRIDGETCG_EMAIL",
        "default_email": "contact@zerone-dev.com",
        "password_env": "IMAP_CAMBRIDGETCG_PASS",
    },
    "rewardspro": {
        "host": "imap.gmail.com",
        "port": 993,
        "email_env": "IMAP_REWARDSPRO_EMAIL",
        "default_email": "contact@rewardspro.io",
        "password_env": "IMAP_REWARDSPRO_PASS",
    },
}

HEADER_FIELDS = "MESSAGE-ID FROM SUBJECT DATE"
HEADER_FETCH = f"(UID INTERNALDATE BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])"
MAX_CONTENT_BYTES = 65536
CONTENT_FETCH = f"(UID INTERNALDATE RFC822.SIZE BODY.PEEK[]<0.{MAX_CONTENT_BYTES}>)"
INTERNALDATE_RE = re.compile(rb'INTERNALDATE\s+"([^"]+)"', re.IGNORECASE)
CODE_RE = re.compile(r"\b\d{4,8}\b")
DEFAULT_SINCE = object()


def decode_str(value):
    if value is None:
        return ""
    result = []
    for part, encoding in decode_header(value):
        if isinstance(part, bytes):
            try:
                result.append(part.decode(encoding or "utf-8", errors="replace"))
            except LookupError:
                result.append(part.decode("utf-8", errors="replace"))
        else:
            result.append(part)
    return " ".join(result).replace("\r", " ").replace("\n", " ")[:1000]


def _decode_payload(part):
    payload = part.get_payload(decode=True)
    if payload is None:
        raw_payload = part.get_payload()
        return raw_payload if isinstance(raw_payload, str) else ""
    try:
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def get_body(msg):
    if not msg.is_multipart():
        return _decode_payload(msg)

    plain_parts = []
    html_parts = []
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() == "text/plain":
            plain_parts.append(_decode_payload(part))
        elif part.get_content_type() == "text/html":
            html_parts.append(_decode_payload(part))
    return "".join(plain_parts or html_parts)


def summarize_links(text):
    links = re.findall(r'https?://[^\s\'"<>]+', text)
    hosts = []
    for link in links:
        host = urlsplit(link).hostname
        if host:
            hosts.append(host.casefold())
    host_hashes = {
        hashlib.sha256(host.encode()).hexdigest()
        for host in hosts
    }
    return {"link_count": len(links), "link_host_hashes": sorted(host_hashes)[:20]}


def count_code_like_tokens(text):
    """Count code-shaped tokens without returning credential values."""
    return len(CODE_RE.findall(text))


def hash_header(value):
    decoded = decode_str(value)
    return hashlib.sha256(decoded.encode()).hexdigest()


def safe_header_date(value):
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _account_value(cfg, key, default=""):
    """Resolve a config value, while allowing inert values in unit tests."""
    if key in cfg:
        return cfg[key]
    return os.environ.get(cfg.get(f"{key}_env", ""), cfg.get(f"default_{key}", default))


def _imap_quote(value):
    if any(char in value for char in "\x00\r\n"):
        raise ValueError("IMAP filters cannot contain control characters")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _extract_fetch_payload(msg_data):
    for item in msg_data or []:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            descriptor = item[0] if isinstance(item[0], bytes) else str(item[0]).encode()
            return descriptor, item[1]
    return b"", None


def _parse_internaldate(descriptor):
    match = INTERNALDATE_RE.search(descriptor)
    if not match:
        return None
    try:
        parsed = datetime.strptime(
            match.group(1).decode("ascii"), "%d-%b-%Y %H:%M:%S %z"
        )
    except (UnicodeDecodeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc)


def _normalise_now(now):
    current = now() if callable(now) else now
    if current is None:
        current = datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _base_result(account, account_email, next_uid=None, uidvalidity=None):
    return {
        "account": account,
        "email": account_email,
        "found": 0,
        "next_uid": next_uid,
        "uidvalidity": uidvalidity,
        "emails": [],
    }


def _read_uidvalidity(mail):
    try:
        _, values = mail.response("UIDVALIDITY")
    except (AttributeError, imaplib.IMAP4.error, OSError):
        return None
    for value in values or []:
        if isinstance(value, bytes) and value.isdigit():
            return int(value)
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def check_email(
    account="zerone-dev",
    subject_filter=None,
    from_filter=None,
    unseen_only=None,
    since_minutes=DEFAULT_SINCE,
    limit=10,
    after_uid=None,
    uidvalidity=None,
    analyze_body=False,
    summarize_message_links=False,
    detect_message_codes=False,
    *,
    imap_factory=None,
    now=None,
):
    """Return matching email metadata, optionally enriched with body-derived data.

    ``after_uid`` is a stable IMAP UID cursor.  Pass the returned ``next_uid``
    into the next call to receive only messages with larger UIDs.
    """
    cfg = ACCOUNTS.get(account)
    if not cfg:
        return {"error": f"Unknown account: {account}. Available: {list(ACCOUNTS)}"}
    cursor_mode = after_uid is not None
    if unseen_only is None:
        unseen_only = not cursor_mode
    if since_minutes is DEFAULT_SINCE:
        since_minutes = None if cursor_mode else 30
    if limit <= 0:
        return {"error": "limit must be greater than zero"}
    if since_minutes is not None and since_minutes < 0:
        return {"error": "since_minutes must be zero or greater"}
    if after_uid is not None and after_uid < 0:
        return {"error": "after_uid must be zero or greater"}
    if cursor_mode and unseen_only:
        return {"error": "cursor polling must include seen mail; omit --unseen"}
    if cursor_mode and since_minutes is not None:
        return {"error": "cursor polling cannot use a time cutoff"}
    if cursor_mode and (subject_filter or from_filter):
        return {"error": "cursor polling cannot use subject or sender filters"}
    if cursor_mode and after_uid > 0 and uidvalidity is None:
        return {"error": "cursor polling above UID 0 requires uidvalidity"}

    account_email = _account_value(cfg, "email")
    password = _account_value(cfg, "password")
    password_env = cfg.get("password_env", f"IMAP_{account.upper()}_PASS")
    if not password:
        return {"error": f"No password configured for {account}. Set {password_env}."}

    current_time = _normalise_now(now)
    cutoff = None
    criteria = []
    if unseen_only:
        criteria.append("UNSEEN")
    if since_minutes is not None:
        cutoff = current_time - timedelta(minutes=since_minutes)
        # Search one extra calendar day to cover INTERNALDATE timezone offsets;
        # the timestamp comparison below applies the exact minute cutoff.
        coarse_date = (cutoff - timedelta(days=1)).strftime("%d-%b-%Y")
        criteria.extend(("SINCE", _imap_quote(coarse_date)))
    if after_uid is not None:
        criteria.extend(("UID", f"{after_uid + 1}:*"))
    if subject_filter:
        criteria.extend(("SUBJECT", _imap_quote(subject_filter)))
    if from_filter:
        criteria.extend(("FROM", _imap_quote(from_filter)))
    if not criteria:
        criteria.append("ALL")

    mail = None
    try:
        factory = imap_factory or imaplib.IMAP4_SSL
        mail = factory(cfg["host"], cfg.get("port", 993))
        mail.login(account_email, password)
        status, _ = mail.select("INBOX", readonly=True)
        if status != "OK":
            return {"error": "Could not select INBOX", "status": status}
        current_uidvalidity = _read_uidvalidity(mail)
        if cursor_mode and current_uidvalidity is None:
            return {"error": "Server did not provide UIDVALIDITY; cursor not advanced"}
        if cursor_mode and uidvalidity is not None and uidvalidity != current_uidvalidity:
            return {
                "error": "Mailbox UIDVALIDITY changed; reset the UID cursor",
                "uidvalidity": current_uidvalidity,
                "reset_required": True,
            }

        status, data = mail.uid("SEARCH", None, *criteria)
        if status != "OK":
            return {"error": "Search failed", "status": status}

        raw_uids = data[0].split() if data and data[0] else []
        numeric_uids = sorted({int(uid) for uid in raw_uids if uid.isdigit()})
        if after_uid is not None:
            # RFC sequence-set n:* can include the current maximum when n is
            # larger than it, so enforce the cursor locally as well.
            numeric_uids = [uid for uid in numeric_uids if uid > after_uid]

        result = _base_result(account, account_email, after_uid, current_uidvalidity)
        fetch_query = (
            CONTENT_FETCH
            if analyze_body or summarize_message_links or detect_message_codes
            else HEADER_FETCH
        )

        # Cursor polling processes oldest-to-newest so a bounded batch cannot
        # skip UIDs. One-off queries return the newest matching messages first.
        ordered_uids = numeric_uids if after_uid is not None else reversed(numeric_uids)
        for uid in ordered_uids:
            status, msg_data = mail.uid("FETCH", str(uid), fetch_query)
            if status != "OK":
                if after_uid is not None:
                    break
                continue
            descriptor, raw_message = _extract_fetch_payload(msg_data)
            if raw_message is None:
                if after_uid is not None:
                    break
                continue

            internal_date = _parse_internaldate(descriptor)
            if cutoff is not None and (internal_date is None or internal_date < cutoff):
                if after_uid is not None:
                    result["next_uid"] = uid
                continue

            msg = email.message_from_bytes(raw_message)
            subject = decode_str(msg.get("Subject"))
            item = {
                "uid": str(uid),
                "message_id_hash": hash_header(msg.get("Message-ID")),
                "from_hash": hash_header(msg.get("From")),
                "subject_hash": hashlib.sha256(subject.encode()).hexdigest(),
                "subject_length": len(subject),
                "date": safe_header_date(msg.get("Date")),
            }

            if analyze_body or summarize_message_links or detect_message_codes:
                body = get_body(msg)
                item["content_truncated"] = len(raw_message) >= MAX_CONTENT_BYTES
                if analyze_body:
                    item["body_character_count"] = len(body)
                if summarize_message_links:
                    item.update(summarize_links(body))
                if detect_message_codes:
                    item["code_like_token_count"] = count_code_like_tokens(body)

            result["emails"].append(item)
            result["next_uid"] = max(result["next_uid"] or 0, uid)
            if len(result["emails"]) >= limit:
                break

        result["found"] = len(result["emails"])
        return result
    except (imaplib.IMAP4.error, OSError, ValueError) as exc:
        return {"error": str(exc)}
    finally:
        if mail is not None:
            try:
                mail.logout()
            except (imaplib.IMAP4.error, OSError):
                pass


def _non_negative_int(value):
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def _positive_int(value):
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def build_parser():
    parser = argparse.ArgumentParser(
        description="Read inbox metadata without marking messages as seen"
    )
    parser.add_argument("--account", default="zerone-dev", choices=list(ACCOUNTS))
    parser.add_argument("--subject", help="Filter by subject keyword")
    parser.add_argument("--from", dest="from_addr", help="Filter by sender")
    parser.add_argument(
        "--since-minutes",
        "--since",
        dest="since_minutes",
        type=_non_negative_int,
        default=None,
        help="Exact minutes back (default: 30 for one-off checks; forbidden with a cursor)",
    )
    seen_group = parser.add_mutually_exclusive_group()
    seen_group.add_argument(
        "--unseen",
        dest="unseen_only",
        action="store_true",
        help="Only unread mail (one-off default; forbidden with a cursor)",
    )
    seen_group.add_argument(
        "--all",
        "--include-seen",
        dest="unseen_only",
        action="store_false",
        help="Include messages already marked as read",
    )
    parser.set_defaults(unseen_only=None)
    parser.add_argument("--after-uid", type=_non_negative_int, help="Only UIDs above this cursor")
    parser.add_argument(
        "--uidvalidity",
        type=_non_negative_int,
        help="Mailbox epoch returned by the previous cursor poll",
    )
    parser.add_argument("--limit", type=_positive_int, default=10)
    parser.add_argument(
        "--analyze-body",
        "--body-preview",
        dest="analyze_body",
        action="store_true",
        help="Return body size only; raw content is never printed",
    )
    parser.add_argument(
        "--summarize-links",
        "--extract-links",
        dest="summarize_links",
        action="store_true",
        help="Return link count and hashed hostnames, never raw URLs",
    )
    parser.add_argument(
        "--detect-codes",
        "--extract-codes",
        dest="detect_codes",
        action="store_true",
        help="Count code-like tokens without returning their values",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    since_minutes = args.since_minutes
    if since_minutes is None and args.after_uid is None:
        since_minutes = 30
    result = check_email(
        account=args.account,
        subject_filter=args.subject,
        from_filter=args.from_addr,
        unseen_only=args.unseen_only,
        since_minutes=since_minutes,
        limit=args.limit,
        after_uid=args.after_uid,
        uidvalidity=args.uidvalidity,
        analyze_body=args.analyze_body,
        summarize_message_links=args.summarize_links,
        detect_message_codes=args.detect_codes,
    )
    print(json.dumps(result, indent=2))
    return 2 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
