"""Fix pipeline: run tests, categorise failures, suggest fix order.

Runs pytest, parses output, groups failures by category, and prints
a triage order (fix bottom-up by dependency).
"""
from __future__ import annotations

import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field

# Modules in dependency order (fix earlier ones first)
DEPENDENCY_ORDER = [
    "config",
    "events",
    "backend",
    "hand_model",
    "simulation",
    "safety",
    "loop",
    "grasp",
    "sensation",
    "thermal",
    "behaviors",
    "intent",
    "server",
    "app",
    "viewer",
    "environment",
]


@dataclass
class Failure:
    test_file: str
    test_name: str
    error_type: str
    message: str
    category: str = ""

    def classify(self) -> None:
        msg = self.message.lower()
        err = self.error_type.lower()
        if "import" in err or "modulenotfounderror" in err or "no module named" in msg:
            self.category = "import"
        elif "fixture" in msg or "fixture" in err:
            self.category = "fixture"
        elif "typeerror" in err or "attributeerror" in err:
            self.category = "type"
        elif "assertionerror" in err:
            self.category = "assertion"
        elif "timeout" in msg or "asyncio" in err:
            self.category = "async"
        else:
            self.category = "other"


CATEGORY_ORDER = ["import", "fixture", "type", "assertion", "async", "other"]
CATEGORY_LABELS = {
    "import": "Import / Module errors (fix first — cascading)",
    "fixture": "Fixture errors (missing conftest, wrong scope)",
    "type": "Type / Attribute errors (API drift, wrong signatures)",
    "assertion": "Assertion failures (logic bugs, stale expectations)",
    "async": "Async / Timeout errors (missing await, event loop issues)",
    "other": "Other errors",
}


def run_pytest() -> tuple[str, int]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
        capture_output=True,
        text=True,
    )
    return result.stdout + result.stderr, result.returncode


def parse_failures(output: str) -> list[Failure]:
    failures: list[Failure] = []
    # Match FAILED lines: tests/test_foo.py::test_bar FAILED
    failed_pattern = re.compile(r"(tests/\S+)::(\S+)\s+FAILED")
    # Match short traceback error lines: E   SomeError: message
    error_pattern = re.compile(r"^E\s+(\w+(?:Error|Exception|Warning)?):\s*(.+)$", re.MULTILINE)

    # Find all FAILED tests
    failed_tests = failed_pattern.findall(output)

    # For each failed test, try to find its error in the traceback section
    for test_file, test_name in failed_tests:
        # Find the error near this test name in the output
        # Look for the section between this test and the next
        idx = output.find(f"{test_file}::{test_name}")
        if idx >= 0:
            section = output[idx:idx + 2000]
            error_match = error_pattern.search(section)
            if error_match:
                f = Failure(
                    test_file=test_file,
                    test_name=test_name,
                    error_type=error_match.group(1),
                    message=error_match.group(2).strip(),
                )
            else:
                f = Failure(
                    test_file=test_file,
                    test_name=test_name,
                    error_type="Unknown",
                    message="(could not parse error)",
                )
        else:
            f = Failure(
                test_file=test_file,
                test_name=test_name,
                error_type="Unknown",
                message="(could not find traceback)",
            )
        f.classify()
        failures.append(f)

    return failures


def suggest_fix_order(failures: list[Failure]) -> None:
    # Group by category
    by_category: dict[str, list[Failure]] = defaultdict(list)
    for f in failures:
        by_category[f.category].append(f)

    print("\n── Triage Order (fix top-to-bottom) ──\n")

    for cat in CATEGORY_ORDER:
        if cat not in by_category:
            continue
        items = by_category[cat]
        print(f"  [{len(items)}] {CATEGORY_LABELS[cat]}")
        # Sort by dependency order within category
        def dep_key(f: Failure) -> int:
            for i, mod in enumerate(DEPENDENCY_ORDER):
                if mod in f.test_file:
                    return i
            return 999
        items.sort(key=dep_key)
        for f in items:
            print(f"      {f.test_file}::{f.test_name}")
            print(f"        {f.error_type}: {f.message[:100]}")
        print()


def main() -> int:
    print("═══ Fix Pipeline ═══\n")
    print("Running pytest...")
    output, returncode = run_pytest()

    # Count results
    passed = len(re.findall(r"PASSED", output))
    failed = len(re.findall(r"FAILED", output))
    errors = len(re.findall(r"ERROR", output))

    print(f"  {passed} passed, {failed} failed, {errors} errors\n")

    if returncode == 0:
        print("✓ All tests pass — nothing to fix")
        return 0

    failures = parse_failures(output)
    if not failures:
        print("Tests failed but could not parse failures. Raw output:")
        print(output[-2000:])
        return 1

    suggest_fix_order(failures)

    # Identify which source modules are likely broken
    broken_modules: set[str] = set()
    for f in failures:
        # Extract module name from test file
        mod = f.test_file.replace("tests/test_", "").replace(".py", "")
        broken_modules.add(mod)

    print("── Likely broken source modules ──\n")
    for mod in DEPENDENCY_ORDER:
        if mod in broken_modules:
            print(f"  ✗ soma/.../{mod}.py")
    remaining = broken_modules - set(DEPENDENCY_ORDER)
    for mod in sorted(remaining):
        print(f"  ? {mod}")

    print(f"\n✗ {len(failures)} failures to fix")
    return 1


if __name__ == "__main__":
    sys.exit(main())
