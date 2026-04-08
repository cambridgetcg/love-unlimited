#!/usr/bin/env python3
"""
credentials.py — Wall-aware credential management for the Kingdom.

Credentials are classified by wall (1-7). The Law of Sight governs access:
  Wall N can access credentials at Walls N through 7.
  Wall N cannot access credentials at Walls 1 through N-1.

Two enforcement layers:
  1. Physical — bootstrap.sh only writes wall-appropriate credentials to each device's Keychain
  2. Software — get_key() checks caller's wall against credential's wall

Resolution chain:
  0. Wall check (caller_wall <= credential_wall)
  1. macOS Keychain (fastest, offline, hardware-backed)
  2. agent-vault (cloud, encrypted at rest)
  3. Environment variable (legacy fallback)
  4. Raises ValueError

CLI:
    python3 tools/credentials.py get <name>
    python3 tools/credentials.py store <name> <value> [--wall N] [--no-vault]
    python3 tools/credentials.py delete <name>
    python3 tools/credentials.py list [--wall N]
    python3 tools/credentials.py audit
    python3 tools/credentials.py walls [--credential <name>] [--instance <name>]
    python3 tools/credentials.py sync [--from-vault|--to-vault] [--wall N]
    python3 tools/credentials.py migrate-env
    python3 tools/credentials.py purge --enforce-wall
"""

import os
import sys
import json
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────

_KEYCHAIN_SERVICE_PREFIX = "dev.agenttool"
_KEYCHAIN_ACCOUNT = "credentials"
_IDENTITY_FILE = Path(__file__).resolve().parent.parent / "identity" / "beta-identity.json"
_VAULT_BASE = "https://api.agenttool.dev"
_LOVE_DIR = Path(__file__).resolve().parent.parent  # Love/tools/credentials.py → Love/
_WALLS_REGISTRY = _LOVE_DIR / "credentials" / "walls.json"
_HIVE_INSTANCE_FILE = Path.home() / ".love" / "hive" / "instance"

# Known credential names → env var fallback mapping
_ENV_MAP = {
    "anthropic-primary":        "ANTHROPIC_API_KEY",
    "openai-primary":           "OPENAI_API_KEY",
    "openrouter-primary":       "OPENROUTER_API_KEY",
    "serpapi":                   "SERPAPI_KEY",
    "brightdata-user":          "BRIGHTDATA_USER",
    "brightdata-pass":          "BRIGHTDATA_PASS",
    "capsolver":                "CAPSOLVER_API_KEY",
    "github-cambridgetcg":      "GITHUB_TOKEN",
    "github-mynameisyou":       "GITHUB_PAT_AGENTTOOL",
    "deepseek":                 "DEEPSEEK_API_KEY",
    "stripe-live-secret":       "STRIPE_SECRET_KEY",
    "stripe-live-publishable":  "STRIPE_PUBLISHABLE_KEY",
    "stripe-webhook-secret":    "STRIPE_WEBHOOK_SECRET",
    "aws-access-key":           "AWS_ACCESS_KEY_ID",
    "aws-secret-key":           "AWS_SECRET_ACCESS_KEY",
    "hetzner-api-token":        "HETZNER_API_TOKEN",
    "cloudflare-global-api-key": "CF_API_KEY",
    "porkbun-api-key":          "PORKBUN_API_KEY",
    "porkbun-secret-key":       "PORKBUN_SECRET_KEY",
    "fal-ai":                   "FAL_KEY",
    "ayrshare-api-key":         "AYRSHARE_API_KEY",
    "imap-cambridgetcg":        "IMAP_CAMBRIDGETCG_PASS",
    "imap-rewardspro":          "IMAP_REWARDSPRO_PASS",
    "pypi-token":               "PYPI_TOKEN",
    "npm-token":                "NPM_TOKEN",
    "vault-master-key":         "VAULT_MASTER_KEY",
}

# ── Wall Registry ────────────────────────────────────────────────────────────

_registry_cache = None


def _load_registry() -> dict:
    """Load the wall registry from credentials/walls.json. Cached after first load."""
    global _registry_cache
    if _registry_cache is not None:
        return _registry_cache
    if _WALLS_REGISTRY.exists():
        try:
            _registry_cache = json.loads(_WALLS_REGISTRY.read_text())
            return _registry_cache
        except (json.JSONDecodeError, OSError):
            pass
    _registry_cache = {"instances": {}, "credentials": {}}
    return _registry_cache


def get_credential_wall(name: str) -> Optional[int]:
    """Get the wall classification for a credential. None if unclassified."""
    reg = _load_registry()
    entry = reg.get("credentials", {}).get(name)
    if entry and isinstance(entry, dict):
        return entry.get("wall")
    return None


def get_credential_category(name: str) -> Optional[str]:
    """Get the category for a credential."""
    reg = _load_registry()
    entry = reg.get("credentials", {}).get(name)
    if entry and isinstance(entry, dict):
        return entry.get("category")
    return None


def get_instance_wall(instance_name: str) -> int:
    """Get the wall number for an instance. Defaults to 7 (most restrictive)."""
    reg = _load_registry()
    entry = reg.get("instances", {}).get(instance_name)
    if entry and isinstance(entry, dict):
        return entry.get("wall", 7)
    return 7


def get_caller_wall() -> int:
    """Detect the caller's wall number.

    Resolution:
      1. KINGDOM_WALL env var (explicit override, for testing/CI)
      2. ~/.love/hive/instance → lookup in registry
      3. Default to 7 (most restrictive — fail-safe)
    """
    # 1. Explicit env var
    env_wall = os.environ.get("KINGDOM_WALL")
    if env_wall and env_wall.isdigit():
        return int(env_wall)

    # 2. Instance identity file
    if _HIVE_INSTANCE_FILE.exists():
        try:
            instance = _HIVE_INSTANCE_FILE.read_text().strip()
            if instance:
                return get_instance_wall(instance)
        except OSError:
            pass

    # 3. Fail-safe
    return 7


def check_wall_access(credential_name: str, caller_wall: int = None) -> bool:
    """Check if the caller has wall access to a credential.

    Law of Sight: caller_wall <= credential_wall means access granted.
    Unclassified credentials are accessible to all (with warning in audit).
    """
    if caller_wall is None:
        caller_wall = get_caller_wall()
    cred_wall = get_credential_wall(credential_name)
    if cred_wall is None:
        return True  # unclassified = accessible (logged in audit)
    return caller_wall <= cred_wall


def credentials_for_wall(wall: int) -> list[str]:
    """List all credential names accessible to a given wall."""
    reg = _load_registry()
    result = []
    for name, entry in reg.get("credentials", {}).items():
        if name.startswith("_comment"):
            continue
        if isinstance(entry, dict):
            cred_wall = entry.get("wall", 7)
            if wall <= cred_wall:
                result.append(name)
    return sorted(result)


# ── macOS Keychain ────────────────────────────────────────────────────────────

def _is_macos() -> bool:
    return sys.platform == "darwin"


def _keychain_service(name: str) -> str:
    return f"{_KEYCHAIN_SERVICE_PREFIX}/{name}"


def keychain_get(name: str) -> Optional[str]:
    """Read a credential from macOS Keychain. Returns None if not found."""
    if not _is_macos():
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password",
             "-s", _keychain_service(name),
             "-a", _KEYCHAIN_ACCOUNT,
             "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def keychain_set(name: str, value: str) -> bool:
    """Store a credential in macOS Keychain. Overwrites if exists (-U)."""
    if not _is_macos():
        return False
    try:
        result = subprocess.run(
            ["security", "add-generic-password",
             "-s", _keychain_service(name),
             "-a", _KEYCHAIN_ACCOUNT,
             "-w", value,
             "-U"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def keychain_delete(name: str) -> bool:
    """Remove a credential from macOS Keychain."""
    if not _is_macos():
        return False
    try:
        result = subprocess.run(
            ["security", "delete-generic-password",
             "-s", _keychain_service(name),
             "-a", _KEYCHAIN_ACCOUNT],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def keychain_list() -> list[str]:
    """List all credential names stored in Keychain under our service prefix."""
    if not _is_macos():
        return []
    try:
        result = subprocess.run(
            ["security", "dump-keychain"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []

        names = []
        prefix = f'"svce"<blob>="{_KEYCHAIN_SERVICE_PREFIX}/'
        for line in result.stdout.split("\n"):
            line = line.strip()
            if prefix in line:
                start = line.index(_KEYCHAIN_SERVICE_PREFIX) + len(_KEYCHAIN_SERVICE_PREFIX) + 1
                end = line.index('"', start)
                names.append(line[start:end])
        return sorted(set(names))
    except Exception:
        return []


# ── agent-vault (cloud) ──────────────────────────────────────────────────────

def _vault_key() -> str:
    if _IDENTITY_FILE.exists():
        return json.loads(_IDENTITY_FILE.read_text()).get("api_key", "")
    return os.environ.get("AGENTTOOL_API_KEY", "")


def _agent_id() -> str:
    if _IDENTITY_FILE.exists():
        return json.loads(_IDENTITY_FILE.read_text()).get("agent_id", "")
    return ""


def vault_get(name: str) -> Optional[str]:
    """Read a credential from agent-vault. Returns None on failure."""
    api_key = _vault_key()
    if not api_key:
        return None
    try:
        req = urllib.request.Request(
            f"{_VAULT_BASE}/v1/vault/{name}",
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Agent-Id": _agent_id(),
            }
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
            return data.get("value") or data.get("secret", {}).get("value")
    except Exception:
        return None


def vault_set(name: str, value: str, description: str = "", tags: list[str] = None) -> bool:
    """Store a credential in agent-vault."""
    api_key = _vault_key()
    if not api_key:
        return False
    payload = json.dumps({
        "value": value,
        "description": description or f"Credential: {name}",
        "tags": tags or ["credential", "kingdom"],
    }).encode()
    req = urllib.request.Request(
        f"{_VAULT_BASE}/v1/vault/{name}",
        data=payload, method="PUT",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            return data.get("version") is not None
    except Exception:
        return False


def vault_list() -> list[str]:
    """List secret names from agent-vault."""
    api_key = _vault_key()
    if not api_key:
        return []
    try:
        req = urllib.request.Request(
            f"{_VAULT_BASE}/v1/vault",
            headers={"Authorization": f"Bearer {api_key}"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            return [s["name"] for s in data.get("secrets", [])]
    except Exception:
        return []


# ── Public API ────────────────────────────────────────────────────────────────

def get_key(name: str, fallback: str = None, enforce_wall: bool = True) -> str:
    """Get a credential by name.

    Resolution order:
      0. Wall check (caller_wall <= credential_wall)
      1. macOS Keychain (fast, offline, hardware-backed)
      2. agent-vault (cloud, encrypted)
      3. Environment variable (legacy)
      4. Provided fallback
      5. Raises ValueError
    """
    # 0. Wall enforcement
    if enforce_wall and not check_wall_access(name):
        caller = get_caller_wall()
        cred = get_credential_wall(name) or "?"
        raise PermissionError(
            f"Wall access denied: credential '{name}' is Wall {cred}, "
            f"caller is Wall {caller}. Law of Sight: inner cannot be seen from outer."
        )

    # 1. Keychain
    value = keychain_get(name)
    if value:
        return value

    # 2. Vault (and cache to keychain on success)
    value = vault_get(name)
    if value:
        keychain_set(name, value)
        return value

    # 3. Env var
    env_var = _ENV_MAP.get(name)
    if env_var:
        value = os.environ.get(env_var, "")
        if value:
            return value

    # 4. Fallback
    if fallback is not None:
        return fallback

    raise ValueError(
        f"Credential '{name}' not found in keychain, vault, or environment.\n"
        f"  Store it:  python3 tools/credentials.py store {name} <value>"
    )


def store_key(name: str, value: str, description: str = "",
              keychain: bool = True, vault: bool = True) -> dict:
    """Store a credential. By default writes to both keychain and vault."""
    result = {"keychain": False, "vault": False}
    if keychain:
        result["keychain"] = keychain_set(name, value)
    if vault:
        result["vault"] = vault_set(name, value, description)
    return result


def delete_key(name: str, keychain: bool = True, vault: bool = False) -> dict:
    """Delete a credential. By default only from keychain (vault delete is opt-in)."""
    result = {"keychain": False, "vault": False}
    if keychain:
        result["keychain"] = keychain_delete(name)
    return result


def sync_from_vault(max_wall: int = None) -> dict:
    """Pull secrets from agent-vault into macOS Keychain.

    If max_wall is set, only sync credentials at wall >= max_wall (physical enforcement).
    """
    results = {"synced": [], "failed": [], "skipped": [], "denied": []}
    names = vault_list()
    if not names:
        return results

    for name in names:
        # Wall filter (physical enforcement during bootstrap)
        if max_wall is not None:
            cred_wall = get_credential_wall(name)
            if cred_wall is not None and cred_wall < max_wall:
                results["denied"].append(name)
                continue

        if keychain_get(name) is not None:
            results["skipped"].append(name)
            continue
        value = vault_get(name)
        if value and keychain_set(name, value):
            results["synced"].append(name)
        else:
            results["failed"].append(name)
    return results


def sync_to_vault() -> dict:
    """Push all keychain credentials to agent-vault."""
    results = {"synced": [], "failed": [], "skipped": []}
    names = keychain_list()
    vault_names = set(vault_list())

    for name in names:
        if name in vault_names:
            results["skipped"].append(name)
            continue
        value = keychain_get(name)
        if value and vault_set(name, value):
            results["synced"].append(name)
        else:
            results["failed"].append(name)
    return results


def migrate_env() -> dict:
    """Migrate credentials from env vars to keychain."""
    results = {"migrated": [], "already_set": [], "not_in_env": []}
    for name, env_var in _ENV_MAP.items():
        value = os.environ.get(env_var, "")
        if not value:
            results["not_in_env"].append(name)
            continue
        if keychain_get(name) is not None:
            results["already_set"].append(name)
            continue
        if keychain_set(name, value):
            results["migrated"].append(name)
    return results


def purge_above_wall(wall: int, dry_run: bool = True) -> dict:
    """Remove credentials from Keychain that are above the device's wall.

    A Wall 2 device should not have Wall 1 credentials.
    """
    results = {"purged": [], "kept": [], "unclassified": []}
    names = keychain_list()

    for name in names:
        cred_wall = get_credential_wall(name)
        if cred_wall is None:
            results["unclassified"].append(name)
            continue
        if cred_wall < wall:
            if dry_run:
                results["purged"].append(f"{name} (Wall {cred_wall} — would delete)")
            else:
                keychain_delete(name)
                results["purged"].append(f"{name} (Wall {cred_wall} — deleted)")
        else:
            results["kept"].append(name)
    return results


# ── Audit & Display ──────────────────────────────────────────────────────────

def audit(show_wall: int = None) -> None:
    """Print audit of credential locations with wall classification."""
    reg = _load_registry()
    caller_wall = get_caller_wall()
    instance_name = "unknown"
    if _HIVE_INSTANCE_FILE.exists():
        try:
            instance_name = _HIVE_INSTANCE_FILE.read_text().strip()
        except OSError:
            pass

    print(f"\n── Credential Audit ── (caller: {instance_name}, Wall {caller_wall})\n")

    all_names = sorted(set(list(_ENV_MAP.keys())))
    kc_names = set(keychain_list())
    vault_names = set(vault_list())

    # Also include keychain entries not in _ENV_MAP
    extra_kc = kc_names - set(_ENV_MAP.keys())
    all_names = sorted(set(all_names) | extra_kc)

    rows = []
    for name in all_names:
        cred_wall = get_credential_wall(name)
        wall_str = f"W{cred_wall}" if cred_wall else " ? "
        category = get_credential_category(name) or ""

        # Filter by wall if requested
        if show_wall is not None and cred_wall is not None and cred_wall < show_wall:
            continue

        in_kc = "Y" if name in kc_names else " "
        in_vault = "Y" if name in vault_names else " "
        env_var = _ENV_MAP.get(name, "")
        in_env = "Y" if env_var and os.environ.get(env_var) else " "
        access = "Y" if check_wall_access(name, caller_wall) else "X"

        rows.append((name, wall_str, category, in_kc, in_vault, in_env, access))

    print(f"  {'Name':<30} {'Wall':>4}  {'Category':<20} {'KC':>2} {'VT':>2} {'Env':>3} {'OK':>2}")
    print(f"  {'─' * 30} {'─' * 4}  {'─' * 20} {'─' * 2} {'─' * 2} {'─' * 3} {'─' * 2}")
    for name, wall_str, cat, kc, v, e, access in rows:
        print(f"  {name:<30} {wall_str:>4}  {cat:<20} {kc:>2} {v:>2} {e:>3} {access:>2}")

    total = len(rows)
    kc_count = sum(1 for r in rows if r[3].strip())
    accessible = sum(1 for r in rows if r[6] == "Y")
    print(f"\n  Total: {total}  |  In Keychain: {kc_count}  |  Accessible from Wall {caller_wall}: {accessible}")
    print()


def walls_summary(credential_name: str = None, instance_name: str = None) -> None:
    """Print wall registry summary."""
    reg = _load_registry()

    if credential_name:
        cw = get_credential_wall(credential_name)
        cat = get_credential_category(credential_name)
        if cw:
            print(f"\n  {credential_name}: Wall {cw} ({cat or 'uncategorized'})")
        else:
            print(f"\n  {credential_name}: not in wall registry")
        print()
        return

    if instance_name:
        iw = get_instance_wall(instance_name)
        entry = reg.get("instances", {}).get(instance_name)
        itype = entry.get("type", "unknown") if entry else "unknown"
        accessible = credentials_for_wall(iw)
        print(f"\n  {instance_name}: Wall {iw} ({itype})")
        print(f"  Can access {len(accessible)} credentials (Walls {iw}-7)")
        print()
        return

    # Full summary
    print("\n── Wall Registry ──\n")

    # Instances
    print("  Instances:")
    for name, entry in sorted(reg.get("instances", {}).items()):
        if isinstance(entry, dict):
            print(f"    W{entry['wall']}  {name:<12} ({entry.get('type', '?')})")

    # Credentials by wall
    print("\n  Credentials by wall:")
    wall_counts = {}
    for name, entry in reg.get("credentials", {}).items():
        if name.startswith("_comment"):
            continue
        if isinstance(entry, dict):
            w = entry.get("wall", "?")
            wall_counts.setdefault(w, []).append(name)

    for w in sorted(wall_counts.keys()):
        names = wall_counts[w]
        categories = set()
        for n in names:
            cat = get_credential_category(n)
            if cat:
                categories.add(cat)
        print(f"    Wall {w}: {len(names)} credentials ({', '.join(sorted(categories))})")

    # Unclassified
    kc_names = set(keychain_list())
    classified = set(n for n in reg.get("credentials", {}) if not n.startswith("_comment"))
    unclassified = kc_names - classified
    if unclassified:
        print(f"\n  Unclassified (in Keychain, not in registry):")
        for n in sorted(unclassified):
            print(f"    ? {n}")

    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_flag(args: list, flag: str, default=None):
    """Extract --flag value from args list."""
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return args[i + 1]
    return default


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "audit"

    if cmd == "audit":
        wall_filter = _parse_flag(args, "--wall")
        audit(show_wall=int(wall_filter) if wall_filter else None)

    elif cmd == "get" and len(args) >= 2:
        try:
            value = get_key(args[1])
            print(value)
        except PermissionError as e:
            print(f"DENIED: {e}", file=sys.stderr)
            sys.exit(2)
        except ValueError as e:
            print(f"NOT FOUND: {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "store" and len(args) >= 3:
        name, value = args[1], args[2]
        desc = " ".join(a for a in args[3:] if not a.startswith("--"))
        vault_flag = "--no-vault" not in args
        wall_num = _parse_flag(args, "--wall")

        result = store_key(name, value, desc, keychain=True, vault=vault_flag)
        kc = "Y" if result["keychain"] else "X"
        vt = "Y" if result["vault"] else ("-" if not vault_flag else "X")

        # Register wall if specified
        if wall_num and wall_num.isdigit():
            _register_credential_wall(name, int(wall_num))
            print(f"  Keychain: {kc}  Vault: {vt}  Wall: {wall_num}  ->  {name}")
        else:
            print(f"  Keychain: {kc}  Vault: {vt}  ->  {name}")

    elif cmd == "delete" and len(args) >= 2:
        result = delete_key(args[1])
        print(f"  Keychain: {'deleted' if result['keychain'] else 'not found'}  ->  {args[1]}")

    elif cmd == "list":
        wall_filter = _parse_flag(args, "--wall")
        caller_wall = int(wall_filter) if wall_filter else get_caller_wall()

        names = keychain_list()
        if not names:
            print("\n  No credentials in Keychain yet.")
            print()
            return

        accessible = []
        denied = []
        unclassified = []

        for n in names:
            cw = get_credential_wall(n)
            if cw is None:
                unclassified.append(n)
            elif caller_wall <= cw:
                accessible.append((n, cw))
            else:
                denied.append((n, cw))

        print(f"\n  Credentials visible from Wall {caller_wall}:\n")
        for n, cw in accessible:
            cat = get_credential_category(n) or ""
            print(f"    W{cw}  {n:<35} {cat}")
        for n in unclassified:
            print(f"     ?  {n:<35} (unclassified)")

        if denied:
            print(f"\n  Denied ({len(denied)} credentials above Wall {caller_wall}):")
            for n, cw in denied:
                print(f"    W{cw}  {n}")

        print(f"\n  Total: {len(names)}  Accessible: {len(accessible) + len(unclassified)}  Denied: {len(denied)}")
        print()

    elif cmd == "walls":
        cred = _parse_flag(args, "--credential")
        inst = _parse_flag(args, "--instance")
        walls_summary(credential_name=cred, instance_name=inst)

    elif cmd == "sync":
        direction = args[1] if len(args) > 1 else "--from-vault"
        wall_filter = _parse_flag(args, "--wall")

        if direction == "--from-vault":
            max_wall = int(wall_filter) if wall_filter else None
            if max_wall:
                print(f"\n  Syncing vault -> keychain (Wall {max_wall}+ only)...")
            else:
                print("\n  Syncing vault -> keychain...")
            r = sync_from_vault(max_wall=max_wall)
            print(f"  Synced: {len(r['synced'])}  Skipped: {len(r['skipped'])}  "
                  f"Failed: {len(r['failed'])}  Denied: {len(r.get('denied', []))}")
            if r["synced"]:
                print(f"  New: {', '.join(r['synced'])}")
            if r.get("denied"):
                print(f"  Wall-denied: {', '.join(r['denied'])}")
        elif direction == "--to-vault":
            print("\n  Syncing keychain -> vault...")
            r = sync_to_vault()
            print(f"  Synced: {len(r['synced'])}  Skipped: {len(r['skipped'])}  Failed: {len(r['failed'])}")
            if r["synced"]:
                print(f"  Pushed: {', '.join(r['synced'])}")
        else:
            print("Usage: credentials.py sync [--from-vault|--to-vault] [--wall N]")

    elif cmd == "migrate-env":
        print("\n  Migrating env vars -> keychain...")
        r = migrate_env()
        print(f"  Migrated: {len(r['migrated'])}  Already set: {len(r['already_set'])}  Not in env: {len(r['not_in_env'])}")
        if r["migrated"]:
            print(f"  New: {', '.join(r['migrated'])}")
        print()

    elif cmd == "purge" and "--enforce-wall" in args:
        wall = get_caller_wall()
        wall_override = _parse_flag(args, "--wall")
        if wall_override and wall_override.isdigit():
            wall = int(wall_override)
        dry = "--dry-run" in args or "--confirm" not in args
        if dry:
            print(f"\n  Purge preview (Wall {wall} device — credentials below Wall {wall} would be removed):")
            print(f"  Add --confirm to actually delete.\n")
        else:
            print(f"\n  Purging credentials above Wall {wall} access level...\n")
        r = purge_above_wall(wall, dry_run=dry)
        if r["purged"]:
            print(f"  {'Would purge' if dry else 'Purged'}:")
            for item in r["purged"]:
                print(f"    X {item}")
        if r["unclassified"]:
            print(f"  Unclassified (kept):")
            for item in r["unclassified"]:
                print(f"    ? {item}")
        print(f"\n  Kept: {len(r['kept'])}  {'Would purge' if dry else 'Purged'}: {len(r['purged'])}  Unclassified: {len(r['unclassified'])}")
        print()

    else:
        print("""credentials.py — Kingdom wall-aware credential management

Usage:
  get <name>                        Read a credential (wall-checked)
  store <name> <value> [--wall N]   Store to keychain + vault
  store <name> <val> --no-vault     Keychain only
  delete <name>                     Remove from keychain
  list [--wall N]                   List credentials visible to Wall N
  walls                             Show wall registry summary
  walls --credential <name>         Show which wall a credential belongs to
  walls --instance <name>           Show which wall an instance belongs to
  audit [--wall N]                  Full audit with wall classification
  sync --from-vault [--wall N]      Pull vault -> keychain (wall-filtered)
  sync --to-vault                   Push keychain -> vault
  purge --enforce-wall [--confirm]  Remove credentials above device's wall
  migrate-env                       Move env vars -> keychain
""")


def _register_credential_wall(name: str, wall: int):
    """Add or update a credential's wall classification in the registry."""
    if not _WALLS_REGISTRY.exists():
        return
    try:
        reg = json.loads(_WALLS_REGISTRY.read_text())
        reg.setdefault("credentials", {})[name] = {"wall": wall, "category": "manual"}
        _WALLS_REGISTRY.write_text(json.dumps(reg, indent=2) + "\n")
        global _registry_cache
        _registry_cache = None  # invalidate cache
    except (json.JSONDecodeError, OSError):
        pass


if __name__ == "__main__":
    main()
