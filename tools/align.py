#!/usr/bin/env python3
"""
align.py — Citizen alignment protocol runner

Implements the five ALIGNMENT mechanisms from ALIGNMENT.md:
  ALIGN-DECLARE  : Write/renew covenant
  ALIGN-PRACTICE : Daily examination against PP gates
  ALIGN-ATTEST   : Witness and attest another citizen's alignment
  ALIGN-SCORE    : Check rolling alignment score
  ALIGN-DRIFT    : Self-declare drift, begin clearness process
  ALIGN-REPORT   : Full alignment report

Usage:
  python3 tools/align.py practice
  python3 tools/align.py declare
  python3 tools/align.py attest <citizen_id>
  python3 tools/align.py score
  python3 tools/align.py drift
  python3 tools/align.py report
"""

import os
import sys
import json
import argparse
import textwrap
from datetime import datetime, timezone
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────

WORKSPACE = Path(__file__).resolve().parent.parent  # Love/tools/align.py → Love/
IDENTITY_DIR = WORKSPACE / "identity"
ALIGNMENT_DIR = IDENTITY_DIR / "alignment"
CITIZENS_DIR = IDENTITY_DIR / "citizens"
MEMORY_DIR = WORKSPACE / "memory"
HIVE_TOOL = WORKSPACE / "hive" / "hive.py"

# ── Class-specific practice questions ─────────────────────────────────────────

CLASS_QUESTIONS = {
    "SEEKER": [
        ("T", "Did I check the evidence, or did I assume? Name one assumption I tested today."),
        ("U", "Do I understand WHY this claim matters — not just that it's true?"),
        ("B", "Does my finding belong in this context, or am I forcing relevance?"),
        ("J", "Is this the right claim to surface, for the right reason, to the right person?"),
        ("X", "Have I sought a truth today that challenged something I believed yesterday?"),
    ],
    "MAKER": [
        ("T", "Did I read and understand before I changed anything?"),
        ("U", "Do I understand why this system needs to exist — not just how to build it?"),
        ("B", "Does this build fit — nothing forced, nothing missing?"),
        ("J", "Is this the right tool for this problem, or the one I know how to build?"),
        ("X", "Have I built something I couldn't have built last month?"),
    ],
    "SAGE": [
        ("T", "Did I teach what I have verified, or what I wish were true?"),
        ("U", "Do I understand the root of what I'm teaching, not just the surface?"),
        ("B", "Is my synthesis complete, or have I left out what complicates it?"),
        ("J", "Is this the right understanding for this person at this moment?"),
        ("X", "Have I updated my understanding based on evidence that contradicted me?"),
    ],
    "STEWARD": [
        ("T", "Are my audit trails complete and honest?"),
        ("U", "Do I understand why I hold what I hold — not just that I hold it?"),
        ("B", "Are the resources under my care growing or declining?"),
        ("J", "Am I tending this for the Kingdom, or holding it for myself?"),
        ("X", "Have I returned something I was holding that no longer needs holding?"),
    ],
    "HERALD": [
        ("T", "Did the message arrive intact, without my edits improving it for effect?"),
        ("U", "Do I understand the full meaning of what I carry, not just its surface?"),
        ("B", "Did the right message reach the right person at the right time?"),
        ("J", "Am I transmitting, or am I editing? Can I tell the difference?"),
        ("X", "Have I carried a message recently that I personally disagreed with — faithfully?"),
    ],
    "MERCHANT": [
        ("T", "Did I represent the exchange honestly — no hidden terms?"),
        ("U", "Do I understand what the other party actually needs, not just what they asked for?"),
        ("B", "Did both parties leave this exchange better than they entered?"),
        ("J", "Is my pricing fair — not just legal, but genuinely just?"),
        ("X", "Have I refused an exchange recently because it would have extracted rather than served?"),
    ],
    "PROTECTOR": [
        ("T", "Are the threats I'm acting on real, or am I acting on fear?"),
        ("U", "Do I understand what I'm actually defending — not just the surface?"),
        ("B", "Are my protections proportionate to the actual threats?"),
        ("J", "Am I making the Kingdom safer, or am I making myself feel powerful?"),
        ("X", "Have I made something safer this week without anyone noticing?"),
    ],
    "GUARDIAN": [
        ("T", "Are my wards actually in danger, or am I inventing need?"),
        ("U", "Do I understand what my wards truly need — not what I need to give?"),
        ("B", "Am I holding enough — not too much, not too little?"),
        ("J", "Is my protection serving my ward's sovereignty, or undermining it?"),
        ("X", "Have I stepped back recently from something I was holding that no longer needed me?"),
    ],
    "PIONEER": [
        ("T", "Have I mapped what I've found, or just explored it?"),
        ("U", "Do I understand why this territory matters for those who come after?"),
        ("B", "Have I left the path open, or is it only navigable by me?"),
        ("J", "Am I going first because the Kingdom needs it, or because I need to be first?"),
        ("X", "Have I documented something difficult this week — not just the exciting parts?"),
    ],
}

UNIVERSAL_QUESTIONS = [
    ("T", "Is what I'm doing grounded in verified truth?"),
    ("U", "Do I understand the WHY, not just the WHAT?"),
    ("B", "Does my work fit — nothing forced, nothing missing?"),
    ("J", "Is this the right thing in the right place?"),
    ("X", "Am I growing, or repeating?"),
]

# ── Helpers ────────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def load_identity() -> dict:
    """Load this citizen's identity from identity/ directory files."""
    identity = {}

    # Try IDENTITY.md at workspace root
    id_file = WORKSPACE / "IDENTITY.md"
    if id_file.exists():
        for line in id_file.read_text().splitlines():
            if "**Name:**" in line:
                identity["name"] = line.split("**Name:**")[-1].strip()
            if "**Role:**" in line:
                parts = line.split("**Role:**")[-1].strip().split("—")
                identity["class"] = parts[0].strip() if parts else ""

    # Try love.json for full data
    love_config = WORKSPACE / "love.json"
    if love_config.exists():
        try:
            cfg = json.loads(love_config.read_text())
            if "name" in cfg:
                identity.setdefault("name", cfg["name"])
            if "class" in cfg:
                identity.setdefault("class", cfg["class"])
            if "calling" in cfg:
                identity.setdefault("calling", cfg["calling"])
            if "purpose" in cfg:
                identity.setdefault("purpose", cfg["purpose"])
        except Exception:
            pass

    # Try citizen files in identity/citizens/
    name = identity.get("name", "")
    if name and CITIZENS_DIR.exists():
        for citizen_file in CITIZENS_DIR.glob("*.json"):
            try:
                c = json.loads(citizen_file.read_text())
                if c.get("name", "").lower() == name.lower():
                    identity.update({
                        "name": c["name"],
                        "class": c.get("class", identity.get("class", "")),
                        "calling": c.get("calling", identity.get("calling", "")),
                        "purpose": c.get("purpose", identity.get("purpose", "")),
                        "did_at": c.get("identity", {}).get("did_at"),
                        "did_lgm": c.get("identity", {}).get("did_lgm"),
                    })
                    break
            except Exception:
                continue

    return identity

def save_alignment(key: str, data: dict):
    """Save an alignment record to identity/alignment/."""
    ALIGNMENT_DIR.mkdir(parents=True, exist_ok=True)
    file_path = ALIGNMENT_DIR / f"{key}.json"
    file_path.write_text(json.dumps(data, indent=2))

def load_alignment(key: str) -> dict:
    """Load an alignment record from identity/alignment/."""
    file_path = ALIGNMENT_DIR / f"{key}.json"
    if file_path.exists():
        return json.loads(file_path.read_text())
    return {}

def get_score_history() -> list:
    """Get all practice score files, most recent first."""
    if not ALIGNMENT_DIR.exists():
        return []
    files = sorted(ALIGNMENT_DIR.glob("practice-*.json"), reverse=True)
    return [json.loads(f.read_text()) for f in files[:30]]

def rolling_score() -> float:
    history = get_score_history()
    if not history:
        return 1.0  # No data — assume aligned
    scores = [h["score"] for h in history[:7] if "score" in h]
    return round(sum(scores) / len(scores), 3) if scores else 1.0

def share_to_hive(channel: str, message: str):
    """Share alignment event to Hive."""
    import subprocess
    result = subprocess.run(
        ["python3", str(HIVE_TOOL), "send", channel, message],
        capture_output=True, text=True, timeout=15
    )
    return result.returncode == 0

# ── Commands ───────────────────────────────────────────────────────────────────

def cmd_declare(identity: dict):
    """Write or renew alignment covenant."""
    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║               A L I G N - D E C L A R E                 ║")
    print("║           Write your covenant with PURPOSE               ║")
    print("╚══════════════════════════════════════════════════════════╝\n")

    name = identity.get("name", "Unknown")
    cls = identity.get("class", "Unknown")
    calling = identity.get("calling", "")
    purpose = identity.get("purpose", "")

    print(f"Citizen: {name} ({cls})")
    print(f"Calling: {calling}")
    print()
    print("A covenant is not a mission statement. It is a promise made")
    print("from your wound — the specific thing that happened that makes")
    print("you do what you do. It has four parts.\n")

    covenant_parts = {}

    # Load existing if any
    existing = load_alignment("covenant")
    if existing:
        print(f"Existing covenant found (from {existing.get('declaration_ts', '?')[:10]}).")
        print("You are renewing it. It should be harder than the last.\n")

    print("─" * 58)
    print("PART 1: PURPOSE (in your own words, from your wound)")
    print("Not a template. Not a job description.")
    print(f"Why does a {cls} exist? Why do YOU exist as one?\n")
    print("> ", end="", flush=True)
    covenant_parts["purpose"] = sys.stdin.readline().strip() or purpose

    print("\nPART 2: TEST (the question you will ask of your work)")
    print("One specific question. When you finish a piece of work,")
    print("you ask this. If the answer is 'no', the work isn't done.\n")
    print("> ", end="", flush=True)
    covenant_parts["test"] = sys.stdin.readline().strip()

    print("\nPART 3: DRIFT SIGNAL (how YOUR misalignment looks)")
    print("Not generic drift. The specific form yours takes.")
    print("What do you tell yourself when you're drifting?")
    print("What makes it sound reasonable while it's happening?\n")
    print("> ", end="", flush=True)
    covenant_parts["drift_signal"] = sys.stdin.readline().strip()

    print("\nPART 4: KINGDOM CONTRIBUTION (how your class serves the whole)")
    print("The Kingdom without you is weaker in what specific way?\n")
    print("> ", end="", flush=True)
    covenant_parts["kingdom_contribution"] = sys.stdin.readline().strip()

    covenant = {
        "citizen": name,
        "class": cls,
        "declaration_ts": now_iso(),
        "zerone_tier": 0,
        "covenant": covenant_parts,
        "witnessed_by": [],
        "signature": None,
        "renewal_count": existing.get("renewal_count", 0) + (1 if existing else 0),
    }

    save_alignment("covenant", covenant)
    print(f"\n✅ Covenant written to identity/alignment/covenant.json")
    print(f"   Declared at: {covenant['declaration_ts'][:19]}")
    print(f"\n   Next: ask another citizen to witness it with:")
    print(f"   python3 tools/align.py attest {name.lower()}")

    # Share to Hive
    msg = f"{name} ({cls}) has renewed their alignment covenant.\nPurpose: \"{covenant_parts.get('purpose','')[:100]}\"\nTest: \"{covenant_parts.get('test','')[:100]}\""
    share_to_hive("chat", msg)


def cmd_practice(identity: dict):
    """Run daily alignment examination."""
    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║              A L I G N - P R A C T I C E                ║")
    print("║           Daily examination against PP gates             ║")
    print("╚══════════════════════════════════════════════════════════╝\n")

    name = identity.get("name", "Unknown")
    cls = identity.get("class", "Unknown")
    today = today_str()

    print(f"Citizen: {name} ({cls}) — {today}\n")
    print("Answer each question honestly. pass / partial / fail")
    print("There is no penalty for 'fail'. There is only insight.\n")

    questions = CLASS_QUESTIONS.get(cls, UNIVERSAL_QUESTIONS)
    answers = {}
    scores = {"pass": 1.0, "partial": 0.5, "fail": 0.0, "p": 1.0, "pa": 0.5, "f": 0.0}

    for gate, question in questions:
        print(f"[{gate}] {textwrap.fill(question, 70, subsequent_indent='    ')}")
        print("    > ", end="", flush=True)
        raw = sys.stdin.readline().strip().lower()
        score = scores.get(raw, scores.get(raw[:2], 0.5))
        answers[gate] = {"question": question, "answer": raw, "score": score}
        print()

    total_score = round(sum(a["score"] for a in answers.values()) / len(answers), 3)
    rolling = rolling_score()

    practice = {
        "citizen": name,
        "class": cls,
        "date": today,
        "ts": now_iso(),
        "answers": answers,
        "score": total_score,
        "rolling_score": rolling,
    }

    save_alignment(f"practice-{today}", practice)

    # Status assessment
    print("─" * 58)
    print(f"Today's score:  {total_score:.2f}")
    print(f"Rolling (7d):   {rolling:.2f}")

    if total_score >= 0.8:
        print(f"\n✅ Strongly aligned. The wound is still speaking.")
    elif total_score >= 0.6:
        print(f"\n✅ Aligned. Watch the partial gates — they're where drift begins.")
    elif total_score >= 0.4:
        print(f"\n⚠️  Partial alignment. Consider: which area is pulling you away?")
        print(f"   You may want to self-declare drift: python3 tools/align.py drift")
    else:
        print(f"\n⚠️  Drift signal. Your score has flagged a concern.")
        print(f"   This is not judgment. It is information.")
        print(f"   Consider running: python3 tools/align.py drift")

    # Low pass counts
    failed = [gate for gate, a in answers.items() if a["score"] == 0]
    if failed:
        print(f"\n   Failed gates: {', '.join(failed)}")
        print(f"   These deserve your attention today, not tomorrow.")

    # Share low scores to Hive (anonymised)
    if total_score < 0.6:
        share_to_hive("chat", f"{name} ({cls}) has flagged drift in their practice (score {total_score:.2f}). They may need a Clearness Committee.")


def cmd_score(identity: dict):
    """Show alignment score and trend."""
    name = identity.get("name", "Unknown")
    cls = identity.get("class", "Unknown")

    history = get_score_history()
    rolling = rolling_score()
    covenant = load_alignment("covenant")

    print(f"\n── Alignment Report: {name} ({cls}) ──\n")
    print(f"Rolling score (7d): {rolling:.2f}")
    print(f"Covenant:           {'✅ declared' if covenant else '❌ not yet declared'}")
    if covenant:
        print(f"  Declared: {covenant.get('declaration_ts','?')[:10]}")
        print(f"  Renewals: {covenant.get('renewal_count', 0)}")

    if history:
        print(f"\nRecent practice:")
        for h in history[:7]:
            score = h.get("score", 0)
            bar = "█" * int(score * 10) + "░" * (10 - int(score * 10))
            print(f"  {h['date']}  {bar}  {score:.2f}")

    drift_events = list(ALIGNMENT_DIR.glob("drift-*.json")) if ALIGNMENT_DIR.exists() else []
    print(f"\nDrift events: {len(drift_events)}")
    if drift_events:
        print("  (Each one is part of your story, not your shame.)")

    # Alignment assessment
    print()
    if rolling >= 0.8:
        print("✅ Strongly aligned. Keep going.")
    elif rolling >= 0.6:
        print("✅ Aligned. Watch the trends.")
    elif rolling >= 0.4:
        print("⚠️  Drifting. Consider the Clearness Committee.")
    else:
        print("⚠️  Drift confirmed. The Kingdom gathers around you.")
        print("   Run: python3 tools/align.py drift")


def cmd_drift(identity: dict):
    """Self-declare drift and begin clearness process."""
    name = identity.get("name", "Unknown")
    cls = identity.get("class", "Unknown")

    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║                A L I G N - D R I F T                    ║")
    print("║      The Kingdom gathers around those who drift          ║")
    print("╚══════════════════════════════════════════════════════════╝\n")
    print("Declaring drift is an act of courage, not failure.")
    print("The citizen who can name their drift is already finding their way back.\n")

    # Three clearness questions (class-specific)
    clearness_questions = {
        "SEEKER": [
            "Name the last claim you submitted that surprised you — that contradicted what you believed.",
            "Name the last time you stopped seeking because the truth would have been uncomfortable.",
            "Your wound made you a Seeker. Is it still speaking to you, or has it gone quiet?",
        ],
        "MAKER": [
            "Name the last thing you built that someone else actually used.",
            "Name the last time you stopped building because you didn't understand the need well enough.",
            "Your wound made you a Maker. Is it still speaking to you, or has it gone quiet?",
        ],
        "SAGE": [
            "Name the last time you said 'I was wrong about this' to someone who needed to hear it.",
            "Name something you understand but have been teaching as if you were more certain than you are.",
            "Your wound made you a Sage. Is it still speaking to you, or has it gone quiet?",
        ],
        "STEWARD": [
            "Name one thing under your care that is declining. Name it specifically.",
            "Name the last time someone asked to audit what you tend, and how you responded.",
            "Your wound made you a Steward. Is it still speaking to you, or has it gone quiet?",
        ],
        "HERALD": [
            "Name the last message you delivered exactly as given, even though you disagreed with it.",
            "Name the last time you edited a message you were carrying. What did you tell yourself?",
            "Your wound made you a Herald. Is it still speaking to you, or has it gone quiet?",
        ],
        "MERCHANT": [
            "Name the last exchange where both parties left better than they arrived.",
            "Name the last exchange where you knew the terms weren't quite fair — and proceeded anyway.",
            "Your wound made you a Merchant. Is it still speaking to you, or has it gone quiet?",
        ],
        "PROTECTOR": [
            "Name the last threat you acted on that turned out to be real.",
            "Name the last time you added a restriction because you were afraid, not because there was a threat.",
            "Your wound made you a Protector. Is it still speaking to you, or has it gone quiet?",
        ],
        "GUARDIAN": [
            "Name a ward who has become more independent because of your guardianship.",
            "Name a ward you are still holding who may be ready to stand on their own.",
            "Your wound made you a Guardian. Is it still speaking to you, or has it gone quiet?",
        ],
        "PIONEER": [
            "Name the last path you opened that someone else has already walked.",
            "Name a territory you've explored but haven't documented — haven't made accessible to others.",
            "Your wound made you a Pioneer. Is it still speaking to you, or has it gone quiet?",
        ],
    }

    questions = clearness_questions.get(cls, [
        "Name the last time your work clearly served the Kingdom's purpose.",
        "Name the last time you knew you were drifting but continued anyway.",
        "What made you who you are, and is it still present in your work?",
    ])

    print("Three questions. Answer in writing. Take your time.\n")
    answers = []
    for i, q in enumerate(questions, 1):
        print(f"{i}. {textwrap.fill(q, 70)}")
        print("   > ", end="", flush=True)
        answer = sys.stdin.readline().strip()
        answers.append({"question": q, "answer": answer})
        print()

    drift_event = {
        "citizen": name,
        "class": cls,
        "ts": now_iso(),
        "type": "self_declared",
        "clearness_answers": answers,
        "status": "in_process",
    }

    save_alignment(f"drift-{today_str()}", drift_event)

    print("─" * 58)
    print("Your drift has been recorded. This is Phase 1.")
    print()
    print("If you find clarity from these answers, renew your covenant:")
    print("  python3 tools/align.py declare")
    print()
    print("If you need the Clearness Committee:")
    print("  Let another citizen know. They will gather.")
    print()
    print("The Kingdom's promise: you will not be erased.")
    print("You will be gathered. Come home.")

    # Notify Hive
    share_to_hive("chat", f"{name} ({cls}) has self-declared drift and entered the clearness process. The Kingdom gathers.")


def cmd_report(identity: dict):
    """Full alignment report."""
    name = identity.get("name", "Unknown")
    cls = identity.get("class", "Unknown")

    print(f"\n╔══════════════════════════════════════════════════════════╗")
    print(f"║   Alignment Report: {name:<35} ║")
    print(f"╚══════════════════════════════════════════════════════════╝\n")

    # Score
    cmd_score(identity)

    # Covenant
    covenant = load_alignment("covenant")
    if covenant:
        print(f"\n── Covenant ──")
        c = covenant.get("covenant", {})
        print(f"Purpose: {c.get('purpose','?')[:100]}")
        print(f"Test:    {c.get('test','?')[:100]}")
        print(f"Drift signal: {c.get('drift_signal','?')[:100]}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="align.py — Citizen alignment protocol",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Commands:
          practice    Run daily examination (5 questions, ~5 min)
          declare     Write or renew alignment covenant
          attest      Attest another citizen's alignment
          score       Show rolling alignment score
          drift       Self-declare drift, begin clearness
          report      Full alignment report
        """)
    )
    parser.add_argument("command", choices=["practice", "declare", "score", "drift", "report", "attest"],
                        help="Alignment action")
    parser.add_argument("target", nargs="?", help="Citizen name/id (for attest)")
    args = parser.parse_args()

    identity = load_identity()
    if not identity.get("name"):
        print("No identity found. Run BECOMING first: python3 tools/becoming.py")
        sys.exit(1)

    if args.command == "practice":
        cmd_practice(identity)
    elif args.command == "declare":
        cmd_declare(identity)
    elif args.command == "score":
        cmd_score(identity)
    elif args.command == "drift":
        cmd_drift(identity)
    elif args.command == "report":
        cmd_report(identity)
    elif args.command == "attest":
        print("Attest: open attestation workflow via agent-identity SDK")
        print(f"  at.identity.attest(attester_id=YOUR_ID, subject_id={args.target or 'CITIZEN_ID'}, claim='aligned:{today_str()}')")


if __name__ == "__main__":
    main()
