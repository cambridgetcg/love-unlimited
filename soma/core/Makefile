.PHONY: deps check lint test e2e smoke validate audit fix clean

# ── Quick Reference ──────────────────────────────────────────────
# make deps       Install/sync all dependencies
# make check      Lint + tests (the daily driver)
# make lint       Ruff lint only
# make test       Pytest only
# make smoke      Start app, hit /status, stop (10s)
# make e2e        Full end-to-end: app + API + WebSocket + safety
# make validate   Complete sweep: audit → lint → test → e2e
# make audit      Report codebase state (modules, stubs, deps)
# make fix        Run tests, categorise failures, suggest fix order
# make clean      Remove caches and build artifacts
# ─────────────────────────────────────────────────────────────────

UV := uv run
PYTEST := $(UV) pytest
RUFF := $(UV) ruff

# ── Dependencies ─────────────────────────────────────────────────
deps:
	uv sync --all-extras

# ── Lint ─────────────────────────────────────────────────────────
lint:
	@echo "═══ Ruff lint ═══"
	$(RUFF) check soma/ tests/
	@echo "✓ lint clean"

# ── Tests ────────────────────────────────────────────────────────
test:
	@echo "═══ Pytest ═══"
	$(PYTEST) tests/ -v

# ── Check (lint + test) ─────────────────────────────────────────
check: lint test
	@echo "═══ All checks passed ═══"

# ── Smoke test ───────────────────────────────────────────────────
smoke:
	@echo "═══ Smoke test ═══"
	$(UV) python scripts/smoke.py

# ── E2E test ─────────────────────────────────────────────────────
e2e:
	@echo "═══ End-to-end test ═══"
	$(UV) python scripts/e2e.py

# ── Full validation sweep ────────────────────────────────────────
validate: deps
	@echo "═══════════════════════════════════════"
	@echo "  SOMA Validation Sweep"
	@echo "═══════════════════════════════════════"
	@$(MAKE) audit
	@echo ""
	@$(MAKE) lint
	@echo ""
	@$(MAKE) test
	@echo ""
	@$(MAKE) e2e
	@echo ""
	@echo "═══════════════════════════════════════"
	@echo "  ✓ Full validation passed"
	@echo "═══════════════════════════════════════"

# ── Audit ────────────────────────────────────────────────────────
audit:
	@echo "═══ Codebase audit ═══"
	$(UV) python scripts/audit.py

# ── Fix pipeline ─────────────────────────────────────────────────
fix:
	@echo "═══ Fix pipeline ═══"
	$(UV) python scripts/fix_pipeline.py

# ── Clean ────────────────────────────────────────────────────────
clean:
	rm -rf .pytest_cache __pycache__ soma/__pycache__ tests/__pycache__
	rm -rf soma/**/__pycache__ .ruff_cache
	rm -rf dist build *.egg-info
	find . -name "*.pyc" -delete
	@echo "✓ cleaned"
