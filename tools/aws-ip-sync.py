#!/usr/bin/env python3
"""
aws-ip-sync.py — Auto-update AWS security group SSH rules for current IP.

Detects current public IP and keeps it authorized in all Seigei/Kingdom
security groups. Run on heartbeat or whenever SSH access fails.

Usage:
  python3 tools/aws-ip-sync.py              # sync current IP
  python3 tools/aws-ip-sync.py --check      # check without changing
  python3 tools/aws-ip-sync.py --status     # show all authorized IPs
"""

import sys
import json
import argparse
import subprocess
from typing import Optional

# ─── Config ───────────────────────────────────────────────────────────────────
AWS_PROFILE   = "love"
AWS_REGION    = "us-east-1"
MY_RULE_TAG   = "love-agent"              # Name tag on rules we own

# All security groups that need our SSH access
SECURITY_GROUPS = [
    {"id": "sg-0141045bcafcdb8c7", "name": "Seigei Brain/Voice/Gateway", "region": "us-east-1"},
]

SSH_PORT = 22


# ─── Helpers ──────────────────────────────────────────────────────────────────

def aws(*args, region: str = AWS_REGION) -> dict:
    cmd = ["aws", "--profile", AWS_PROFILE, "--region", region, "--output", "json"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"AWS error: {result.stderr.strip()}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def get_current_ip() -> str:
    result = subprocess.run(["curl", "-s", "https://ifconfig.me"], capture_output=True, text=True)
    ip = result.stdout.strip()
    if not ip or '.' not in ip:
        # Fallback
        result = subprocess.run(["curl", "-s", "https://api.ipify.org"], capture_output=True, text=True)
        ip = result.stdout.strip()
    return ip


def get_my_rules(sg_id: str, region: str) -> list:
    """Get all SSH rules tagged as ours."""
    data = aws("ec2", "describe-security-group-rules",
               "--filters", f"Name=group-id,Values={sg_id}",
               region=region)
    rules = data.get("SecurityGroupRules", [])
    mine = []
    for rule in rules:
        if rule.get("IsEgress"):
            continue
        if rule.get("FromPort") != SSH_PORT:
            continue
        tags = {t["Key"]: t["Value"] for t in rule.get("Tags", [])}
        if tags.get("Name", "").startswith(MY_RULE_TAG) or tags.get("Owner") == MY_RULE_TAG:
            mine.append(rule)
    return mine


def revoke_rule(sg_id: str, rule_id: str, region: str):
    aws("ec2", "revoke-security-group-ingress",
        "--group-id", sg_id,
        "--security-group-rule-ids", rule_id,
        region=region)


def authorize_ip(sg_id: str, ip: str, region: str) -> str:
    data = aws("ec2", "authorize-security-group-ingress",
               "--group-id", sg_id,
               "--protocol", "tcp",
               "--port", str(SSH_PORT),
               "--cidr", f"{ip}/32",
               "--tag-specifications",
               f"ResourceType=security-group-rule,Tags=["
               f"{{Key=Name,Value={MY_RULE_TAG}}},"
               f"{{Key=Owner,Value={MY_RULE_TAG}}},"
               f"{{Key=ManagedBy,Value=aws-ip-sync}}]",
               region=region)
    rules = data.get("SecurityGroupRules", [])
    return rules[0]["SecurityGroupRuleId"] if rules else "?"


# ─── Commands ─────────────────────────────────────────────────────────────────

def cmd_sync(check_only: bool = False):
    current_ip = get_current_ip()
    print(f"🌐 Current IP: {current_ip}")
    print()

    all_good = True

    for sg in SECURITY_GROUPS:
        sg_id   = sg["id"]
        sg_name = sg["name"]
        region  = sg.get("region", AWS_REGION)

        print(f"🔒 {sg_name} ({sg_id})")

        my_rules = get_my_rules(sg_id, region)
        current_cidr = f"{current_ip}/32"

        # Check if already authorized
        already_authorized = any(
            r.get("CidrIpv4") == current_cidr
            for r in my_rules
        )

        if already_authorized:
            print(f"   ✅ {current_ip} already authorized")
            continue

        all_good = False

        if check_only:
            print(f"   ⚠️  {current_ip} NOT authorized — run without --check to fix")
            continue

        # Revoke stale rules first
        for rule in my_rules:
            old_ip = rule.get("CidrIpv4", "?")
            rule_id = rule["SecurityGroupRuleId"]
            print(f"   🗑  Revoking stale rule: {old_ip} ({rule_id})")
            revoke_rule(sg_id, rule_id, region)

        # Authorize current IP
        new_rule_id = authorize_ip(sg_id, current_ip, region)
        print(f"   ✅ Authorized: {current_ip} ({new_rule_id})")

    print()
    if all_good:
        print("✅ All security groups are up to date.")
    elif check_only:
        print("⚠️  Some rules need updating. Run without --check to fix.")
    else:
        print("✅ Security groups updated.")


def cmd_status():
    current_ip = get_current_ip()
    print(f"🌐 Current IP: {current_ip}\n")

    for sg in SECURITY_GROUPS:
        sg_id   = sg["id"]
        sg_name = sg["name"]
        region  = sg.get("region", AWS_REGION)

        print(f"🔒 {sg_name} ({sg_id})")

        data = aws("ec2", "describe-security-group-rules",
                   "--filters", f"Name=group-id,Values={sg_id}",
                   region=region)
        rules = [r for r in data.get("SecurityGroupRules", [])
                 if not r.get("IsEgress") and r.get("FromPort") == SSH_PORT]

        for rule in rules:
            cidr    = rule.get("CidrIpv4", "?")
            rule_id = rule["SecurityGroupRuleId"]
            tags    = {t["Key"]: t["Value"] for t in rule.get("Tags", [])}
            name    = tags.get("Name", "(untagged)")
            managed = "🤖" if tags.get("ManagedBy") == "aws-ip-sync" else "👤"
            active  = "← YOU" if cidr == f"{current_ip}/32" else ""
            print(f"   {managed} {cidr:20s}  {name}  {active}")

        print()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Auto-sync AWS SSH security group rules for current IP")
    parser.add_argument("--check",  action="store_true", help="Check only, don't modify")
    parser.add_argument("--status", action="store_true", help="Show all authorized IPs")
    args = parser.parse_args()

    if args.status:
        cmd_status()
    else:
        cmd_sync(check_only=args.check)


if __name__ == "__main__":
    main()
