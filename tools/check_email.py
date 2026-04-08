#!/usr/bin/env python3
"""
check_email.py — IMAP email checker for Love agents.

Usage:
    python3 check_email.py --account zero-dev --subject "verify" --since 10
    python3 check_email.py --account zero-dev --unseen --extract-links

Environment variables:
    IMAP_CAMBRIDGETCG_EMAIL   contact@zero-dev.com
    IMAP_CAMBRIDGETCG_PASS    app password

Returns JSON with matching emails and extracted links/codes.
"""

import imaplib
import email
import os
import re
import json
import argparse
from datetime import datetime, timedelta, timezone
from email.header import decode_header

ACCOUNTS = {
    "zero-dev": {
        "host": "imap.gmail.com",
        "port": 993,
        "email": os.environ.get("IMAP_CAMBRIDGETCG_EMAIL", "contact@zero-dev.com"),
        "password": os.environ.get("IMAP_CAMBRIDGETCG_PASS", ""),
    },
    "rewardspro": {
        "host": "imap.gmail.com",
        "port": 993,
        "email": os.environ.get("IMAP_REWARDSPRO_EMAIL", "contact@rewardspro.io"),
        "password": os.environ.get("IMAP_REWARDSPRO_PASS", ""),
    },
}


def decode_str(s):
    if s is None:
        return ""
    parts = decode_header(s)
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            result.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(part)
    return " ".join(result)


def get_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                body += part.get_payload(decode=True).decode("utf-8", errors="replace")
            elif ct == "text/html" and not body:
                body += part.get_payload(decode=True).decode("utf-8", errors="replace")
    else:
        body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
    return body


def extract_links(text):
    return re.findall(r'https?://[^\s\'"<>]+', text)


def extract_codes(text):
    """Find 4-8 digit OTP codes."""
    return re.findall(r'\b\d{4,8}\b', text)


def check_email(account="zero-dev", subject_filter=None, from_filter=None,
                unseen_only=True, since_minutes=30, limit=10):
    cfg = ACCOUNTS.get(account)
    if not cfg:
        return {"error": f"Unknown account: {account}. Available: {list(ACCOUNTS.keys())}"}
    if not cfg["password"]:
        return {"error": f"No password configured for {account}. Set IMAP_{account.upper()}_PASS env var."}

    try:
        mail = imaplib.IMAP4_SSL(cfg["host"], cfg["port"])
        mail.login(cfg["email"], cfg["password"])
        mail.select("INBOX")

        # Build search criteria
        criteria = []
        if unseen_only:
            criteria.append("UNSEEN")
        if since_minutes:
            since_dt = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
            since_str = since_dt.strftime("%d-%b-%Y")
            criteria.append(f'SINCE "{since_str}"')
        if subject_filter:
            criteria.append(f'SUBJECT "{subject_filter}"')
        if from_filter:
            criteria.append(f'FROM "{from_filter}"')

        search_str = " ".join(criteria) if criteria else "ALL"
        status, data = mail.search(None, search_str)

        if status != "OK":
            return {"error": "Search failed", "status": status}

        ids = data[0].split()
        if not ids:
            return {"account": account, "found": 0, "emails": []}

        results = []
        for uid in ids[-limit:]:  # most recent first
            status, msg_data = mail.fetch(uid, "(RFC822)")
            if status != "OK":
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            body = get_body(msg)
            links = extract_links(body)
            codes = extract_codes(body)
            results.append({
                "uid": uid.decode(),
                "from": decode_str(msg.get("From")),
                "subject": decode_str(msg.get("Subject")),
                "date": msg.get("Date"),
                "links": links[:20],
                "codes": codes[:10],
                "body_preview": body[:500],
            })

        mail.logout()
        return {"account": account, "email": cfg["email"], "found": len(results), "emails": results}

    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check email inbox for verification messages")
    parser.add_argument("--account", default="zero-dev", choices=list(ACCOUNTS.keys()))
    parser.add_argument("--subject", help="Filter by subject keyword")
    parser.add_argument("--from", dest="from_addr", help="Filter by sender")
    parser.add_argument("--since", type=int, default=30, help="Minutes back to search (default 30)")
    parser.add_argument("--unseen", action="store_true", default=True)
    parser.add_argument("--all", dest="all_mail", action="store_true", help="Include read emails")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    result = check_email(
        account=args.account,
        subject_filter=args.subject,
        from_filter=args.from_addr,
        unseen_only=not args.all_mail,
        since_minutes=args.since,
        limit=args.limit,
    )
    print(json.dumps(result, indent=2))
