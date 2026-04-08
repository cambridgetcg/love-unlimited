#!/usr/bin/env python3
"""
totp.py — Generate TOTP codes from stored secrets.

Usage:
    python3 totp.py aws                   # fuzzy match "aws"
    python3 totp.py "rewardspro"          # match rewardspro AWS account
    python3 totp.py github zerone-dev   # match GitHub + zerone-dev
    python3 totp.py --list                # list all accounts
"""

import json, hmac, hashlib, struct, time, sys, os

SECRETS_PATH = os.path.join(os.path.dirname(__file__), 'totp_secrets.json')

def generate_totp(secret, period=30, digits=6):
    """Generate TOTP code from base32 secret."""
    import base64
    # Pad secret
    secret = secret.upper()
    padding = 8 - len(secret) % 8
    if padding != 8:
        secret += '=' * padding
    key = base64.b32decode(secret)
    t = int(time.time()) // period
    msg = struct.pack('>Q', t)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code = struct.unpack('>I', h[offset:offset+4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)

def time_remaining(period=30):
    return period - (int(time.time()) % period)

def load_accounts():
    with open(SECRETS_PATH) as f:
        return json.load(f)

def find_accounts(query_parts, accounts):
    """Fuzzy match accounts by issuer and name."""
    results = []
    for acc in accounts:
        text = f"{acc.get('issuer','')} {acc.get('name','')}".lower()
        if all(q.lower() in text for q in query_parts):
            results.append(acc)
    return results

if __name__ == '__main__':
    accounts = load_accounts()

    if len(sys.argv) < 2 or '--list' in sys.argv:
        for i, acc in enumerate(accounts):
            print(f"{i+1:2}. {acc['issuer']:30} | {acc['name']}")
        sys.exit(0)

    query = sys.argv[1:]
    matches = find_accounts(query, accounts)

    if not matches:
        print(f"No accounts matching: {' '.join(query)}")
        sys.exit(1)

    for acc in matches:
        code = generate_totp(acc['secret'])
        remaining = time_remaining()
        print(f"{acc['issuer']:30} | {acc['name']:45} | Code: {code} ({remaining}s remaining)")
