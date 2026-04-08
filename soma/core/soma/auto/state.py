"""Codebase state observer. Reads the project and produces a structured snapshot.

This is the agent's eyes — before any strategy runs, it sees what exists.
No LLM calls. Pure filesystem observation.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ModuleState:
    path: Path
    lines: int
    classification: str  # "real" | "stub" | "empty" | "missing"
    imports: tuple[str, ...]


@dataclass(frozen=True)
class TestState:
    path: Path
    test_count: int
    lines: int


@dataclass(frozen=True)
class LintResult:
    clean: bool
    error_count: int
    errors: tuple[str, ...]


@dataclass(frozen=True)
class TestResult:
    passed: int
    failed: int
    errors: int
    failure_details: tuple[str, ...]

    @property
    def all_green(self) -> bool:
        return self.failed == 0 and self.errors == 0


@dataclass
class ProjectState:
    """Complete snapshot of the project at a point in time."""

    root: Path
    modules: dict[str, ModuleState] = field(default_factory=dict)
    tests: dict[str, TestState] = field(default_factory=dict)
    lint: LintResult | None = None
    test_result: TestResult | None = None
    git_dirty: bool = False
    git_branch: str = ""

    # Derived properties
    @property
    def real_modules(self) -> list[str]:
        return [k for k, v in self.modules.items() if v.classification == "real"]

    @property
    def stub_modules(self) -> list[str]:
        return [k for k, v in self.modules.items() if v.classification == "stub"]

    @property
    def missing_modules(self) -> list[str]:
        return [k for k, v in self.modules.items() if v.classification == "missing"]

    @property
    def untested_modules(self) -> list[str]:
        tested = {k.replace("test_", "") for k in self.tests}
        source = {
            k for k, v in self.modules.items()
            if v.classification == "real" and not k.startswith("__")
        }
        return sorted(source - tested)

    @property
    def total_source_lines(self) -> int:
        return sum(m.lines for m in self.modules.values())

    @property
    def total_tests(self) -> int:
        return sum(t.test_count for t in self.tests.values())

    @property
    def health(self) -> str:
        """One-word health: green | yellow | red | empty."""
        if not self.modules:
            return "empty"
        if self.test_result and not self.test_result.all_green:
            return "red"
        if self.lint and not self.lint.clean:
            return "yellow"
        if self.stub_modules or self.missing_modules:
            return "yellow"
        return "green"


def _classify_file(path: Path) -> str:
    if not path.exists():
        return "missing"
    text = path.read_text()
    lines = text.splitlines()
    if not lines:
        return "empty"
    meaningful = 0
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or s.startswith('"""') or s.startswith("from ") or s.startswith("import "):
            continue
        meaningful += 1
    return "stub" if meaningful < 3 else "real"


def _get_imports(path: Path) -> tuple[str, ...]:
    if not path.exists():
        return ()
    try:
        tree = ast.parse(path.read_text())
    except Exception:
        return ()
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("soma"):
            imports.add(node.module)
    return tuple(sorted(imports))


def _count_tests(path: Path) -> int:
    if not path.exists():
        return 0
    text = path.read_text()
    return text.count("\ndef test_") + text.count("\nasync def test_")


def _run_capture(cmd: list[str], cwd: Path) -> tuple[str, int]:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return result.stdout + result.stderr, result.returncode


def observe(root: Path | None = None) -> ProjectState:
    """Observe the full project state. No side effects."""
    if root is None:
        root = Path(__file__).parent.parent.parent
    root = root.resolve()

    state = ProjectState(root=root)
    src = root / "soma"
    tests_dir = root / "tests"

    # Scan source modules
    if src.exists():
        for f in sorted(src.rglob("*.py")):
            rel = f.relative_to(src)
            key = str(rel).replace("/", ".").removesuffix(".py")
            state.modules[key] = ModuleState(
                path=f,
                lines=len(f.read_text().splitlines()) if f.exists() else 0,
                classification=_classify_file(f),
                imports=_get_imports(f),
            )

    # Scan tests
    if tests_dir.exists():
        for f in sorted(tests_dir.glob("test_*.py")):
            key = f.stem
            state.tests[key] = TestState(
                path=f,
                test_count=_count_tests(f),
                lines=len(f.read_text().splitlines()),
            )

    # Git state
    try:
        out, _ = _run_capture(["git", "status", "--porcelain"], root)
        state.git_dirty = bool(out.strip())
        out, _ = _run_capture(["git", "branch", "--show-current"], root)
        state.git_branch = out.strip()
    except Exception:
        pass

    return state


def run_lint(root: Path | None = None) -> LintResult:
    """Run ruff and return structured result."""
    if root is None:
        root = Path(__file__).parent.parent.parent
    out, code = _run_capture(
        [sys.executable, "-m", "ruff", "check", "soma/", "tests/"],
        root,
    )
    if code == 0:
        return LintResult(clean=True, error_count=0, errors=())
    lines = [line.strip() for line in out.splitlines() if line.strip() and "-->" in line]
    return LintResult(clean=False, error_count=len(lines), errors=tuple(lines))


def run_tests(root: Path | None = None) -> TestResult:
    """Run pytest and return structured result."""
    if root is None:
        root = Path(__file__).parent.parent.parent
    out, code = _run_capture(
        [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
        root,
    )
    import re
    passed = len(re.findall(r"PASSED", out))
    failed = len(re.findall(r"FAILED", out))
    errors = len(re.findall(r"ERROR", out))

    failure_lines = []
    for match in re.finditer(r"(tests/\S+::\S+)\s+FAILED", out):
        failure_lines.append(match.group(1))

    return TestResult(
        passed=passed,
        failed=failed,
        errors=errors,
        failure_details=tuple(failure_lines),
    )
