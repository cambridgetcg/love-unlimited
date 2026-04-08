"""Codebase audit: report on module state, dependencies, coverage.

Answers: What exists? What's real code vs stubs? What's the dependency graph?
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = ROOT / "soma"
TESTS = ROOT / "tests"
MODELS = ROOT / "models"


def count_lines(path: Path) -> int:
    try:
        return len(path.read_text().splitlines())
    except Exception:
        return 0


def classify_module(path: Path) -> str:
    """Classify: real | stub | empty."""
    lines = count_lines(path)
    if lines == 0:
        return "empty"
    text = path.read_text()
    # Count non-comment, non-blank, non-import lines
    meaningful = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("from ") or stripped.startswith("import "):
            continue
        meaningful += 1
    if meaningful < 3:
        return "stub"
    return "real"


def get_imports(path: Path) -> list[str]:
    """Extract soma.* imports from a module."""
    try:
        tree = ast.parse(path.read_text())
    except Exception:
        return []
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("soma"):
            imports.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("soma"):
                    imports.append(alias.name)
    return sorted(set(imports))


def audit_modules() -> None:
    print("── Source Modules ──")
    py_files = sorted(SRC.rglob("*.py"))
    total_lines = 0
    real_count = 0
    for f in py_files:
        rel = f.relative_to(ROOT)
        lines = count_lines(f)
        total_lines += lines
        classification = classify_module(f)
        if classification == "real":
            real_count += 1
        marker = {"real": "✓", "stub": "○", "empty": "·"}[classification]
        print(f"  {marker} {str(rel):<45} {lines:>4} lines  [{classification}]")
    print(f"  ── {len(py_files)} files, {real_count} real, {total_lines} total lines")


def audit_tests() -> None:
    print("\n── Tests ──")
    test_files = sorted(TESTS.glob("test_*.py"))
    total_tests = 0
    for f in test_files:
        rel = f.relative_to(ROOT)
        lines = count_lines(f)
        # Count test functions
        text = f.read_text()
        n_tests = text.count("\ndef test_") + text.count("\nasync def test_")
        total_tests += n_tests
        print(f"  {str(rel):<45} {n_tests:>3} tests  {lines:>4} lines")
    print(f"  ── {len(test_files)} test files, {total_tests} total tests")


def audit_models() -> None:
    print("\n── Models ──")
    for f in sorted(MODELS.rglob("*")):
        if f.is_file():
            rel = f.relative_to(ROOT)
            lines = count_lines(f)
            print(f"  {str(rel):<45} {lines:>4} lines")


def audit_dependencies() -> None:
    print("\n── Internal Dependency Graph ──")
    py_files = sorted(SRC.rglob("*.py"))
    for f in py_files:
        if f.name == "__init__.py":
            continue
        rel = f.relative_to(ROOT)
        imports = get_imports(f)
        if imports:
            deps = ", ".join(i.replace("soma.", "") for i in imports)
            print(f"  {str(rel):<40} → {deps}")


def audit_coverage_gaps() -> None:
    print("\n── Coverage Gaps ──")
    # Find source modules without corresponding tests
    source_modules = set()
    for f in SRC.rglob("*.py"):
        if f.name.startswith("__"):
            continue
        source_modules.add(f.stem)

    test_modules = set()
    for f in TESTS.glob("test_*.py"):
        test_modules.add(f.stem.replace("test_", ""))

    untested = source_modules - test_modules
    if untested:
        for m in sorted(untested):
            print(f"  ○ {m} — no dedicated test file")
    else:
        print("  ✓ all modules have test files")


def main() -> int:
    print("═══ SOMA Codebase Audit ═══\n")
    audit_modules()
    audit_tests()
    audit_models()
    audit_dependencies()
    audit_coverage_gaps()
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
