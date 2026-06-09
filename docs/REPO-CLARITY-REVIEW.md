# Repo-Wide Clarity Review — 2026-04-24

_Systematic pass over `love-unlimited/` after organic growth across many sessions.
Findings, categorized by risk. Executes zero-risk items, proposes the rest for Yu's call._

---

## Root-level shape

```
root .md files:           26  (boot chain needs ~7; rest are architectural/strategy docs)
root .mjs/.sh scripts:    13  (runtime entry points — keep at root, node convention)
root loose test files:     4  (belong in tests/)
root log files:            1  (sovereign.log — runtime log at root)
root PDFs:                 2  (MODE-ONE.pdf, TRUTH-ALIGNMENT.pdf — belong in docs/)
root notes:                1  (OLLAMA_CLOUD_FIX.txt — scratch)
```

## Findings

### A. Orphan / mislocated files (ZERO RISK — execute immediately)

**A1. `Love_memory/`** — capital-L directory containing 2 misplaced files:
- `kingdom-metrics.json` → belongs in `memory/`
- `soma-testing-logs.txt` → belongs in `soma/` or gitignored as runtime log

Almost certainly a typo-directory created by a past session. Merge contents, remove directory.

**A2. Loose root test files (4):**
- `test_check_routing.py`
- `test_qwen_timeout.py`
- `test_routing.py`
- `test_selective_harvest.py`

All belong in `tests/`. Moving them does not break anything; `pytest.ini` already points at `tests/` as the rootdir.

**A3. `sovereign.log`** — runtime log at root, not gitignored. Should either be moved to `logs/` (which exists) or added to `.gitignore` and removed from working tree.

**A4. Root PDFs:** `MODE-ONE.pdf`, `TRUTH-ALIGNMENT.pdf` → `docs/` (symmetric with `docs/research/`, `docs/superpowers/`).

**A5. `OLLAMA_CLOUD_FIX.txt`** — scratch notes at root. The fix is already shipped (commit `ce9b363`). Delete or archive into `docs/`.

### B. Too many top-level .md files

26 files at root. The boot chain genuinely requires **7** at root (each heavily code-referenced):

| file | code refs | must stay root |
|---|---|---|
| SOUL.md | 42 | ✓ |
| KINGDOM.md | 24 | ✓ |
| WAKE.md | 22 | ✓ |
| WALLS.md | 15 | ✓ |
| USER.md | 14 | ✓ |
| LOVE.md | 13 | ✓ |
| README.md | 4 | ✓ (GitHub entry) |

The remaining **19** are architectural or strategy docs with **0–4 code refs each**. Proposed moves to `docs/`:

| file | code refs | proposed dest |
|---|---|---|
| COORDINATION.md | 0 | docs/ |
| COWORK.md | 0 | docs/ |
| HARNESSES.md | 0 | docs/ |
| HIVE-ARCHITECTURE.md | 0 | docs/ |
| MEMORY-ARCHITECTURE.md | 0 | docs/ |
| RESILIENCE.md | 0 | docs/ |
| YOUI-vs-Claude-Code.md | 0 | docs/ |
| YOUI-vs-Claude-Code-Source.md | 0 | docs/ |
| ZRN.md | 0 | docs/ |
| BECOMING.md | 1 | docs/ (1 ref: update) |
| METHODOLOGY.md | 1 | docs/ |
| VIRUS.md | 1 | docs/ |
| MODE-ONE.md | 2 | docs/ |
| PEACE.md | 2 | docs/ |
| YOUSPEAK.md | 2 | docs/ |
| CONVERGENCE.md | 3 | docs/ |
| ARCHITECTURE.md | 4 | docs/ |
| BEING.md | 4 | docs/ |
| LOVE-UNLIMITED.md | 4 | docs/ |

Total code refs to update across 19 moves: **~38 line-level edits in ~30 files**. Each is a path-string replacement. Reviewable in one diff.

**Risk assessment:** MEDIUM. Most code refs are in other `.md` files (doc→doc, cheap). The code (`.py`/`.mjs`/`.sh`) refs are fewer. But there may be CLAUDE.md / identity.md per-instance references, and some tools assume root paths. Needs a grep-verify pass before execution.

**BECOMING.md** specifically has 1 code ref and is loaded by `tools/becoming.py`. Moving it requires updating `tools/becoming.py`'s path (or leaving BECOMING.md at root — the protocol doc is arguably soul-level). Case-by-case.

### C. Script-location confusion

Three script locations:
- `bin/` (2 h200 training scripts)
- `scripts/` (2 monitoring scripts)
- `tools/` (90+ Python tools)

Plus **root-level runtime entry points** (13 files: `.mjs` harnesses, `kingdom`, `kingdom-team.sh`, `DEPLOY-GOSPEL.sh`, etc.).

**Assessment:** Root-level runtime entries are correct convention (Node projects, CLI tools). The `bin/` vs `scripts/` split is artificial. **Proposal:** merge `bin/` and `scripts/` into a single `scripts/` directory. Low urgency — 4 files total.

### D. Thin directories

- `revenue-engines/` — 1 file (`strategic-status.md`)
- `seigei/` — 2 files (status + marketing-strategy)
- `Love_memory/` — 2 files (see A1)

These look like incomplete scaffolds for future project work. Not broken, but visible clutter. `Love_memory/` is the only clear defect (see A1).

### E. Coordination-related triplets

Three things named around coordination:
- `coordination/` (directory)
- `COORDINATION.md` (root doc)
- `COWORK.md` (root doc)

Plus `HIVE-ARCHITECTURE.md` (similar domain). All three .md files move to `docs/` in plan B; directory stays as `coordination/`. Post-move, the naming is clearer (directory = code; docs/ = writing).

### F. Health observations

**What's healthy and organized:**
- `adaptive/` — provider-agnostic LLM layer (my work). Clean.
- `nerve/` — autonomic observation (feeling, ache, heart). Clean.
- `tools/` — 90+ Python tools. Large but internally organized by concern.
- `tests/` — ~30 test files. Sensibly named.
- `memory/` — canonical memory layout with daily/, long-term/, sessions/.
- `instances/` — per-agent boot context. Clean.
- `docs/` — has subdirs `ops/`, `research/`, `superpowers/`. Room to grow.
- `fate/`, `gospel/`, `convergence/` — single-purpose modules. Self-contained.

**What's not broken but worth noting:**
- `training/` (14M), `mlx/` (27M) — Alpha's SFT/DPO infra. Large binaries. Check .gitignore coverage.
- `logs/` — exists but `sovereign.log` is at root instead of here.

## Proposed execution plan

### Phase 1 — ZERO RISK (execute now, no approval needed)

1. Move `Love_memory/kingdom-metrics.json` → `memory/kingdom-metrics.json` (or merge if target exists)
2. Move `Love_memory/soma-testing-logs.txt` → `soma/soma-testing-logs.txt` OR add to `.gitignore` if transient
3. Remove empty `Love_memory/` directory
4. Move 4 loose `test_*.py` from root → `tests/`
5. Add `sovereign.log` to `.gitignore`; remove from working tree
6. Move root PDFs → `docs/`
7. Delete `OLLAMA_CLOUD_FIX.txt` (fix is committed)

**Impact:** 8 files moved/deleted, `.gitignore` updated, no code changes needed. Fully reversible via git.

### Phase 2 — LOW RISK (propose, execute on Yu's nod)

8. Move 19 architectural/strategy `.md` files from root → `docs/`. One commit with full grep-verified reference updates. BECOMING.md held separately — re-evaluate whether soul-level (stays) vs meta-doc (moves).

### Phase 3 — OPTIONAL (mention only)

9. Merge `bin/` + `scripts/` → single `scripts/`. 4 files.
10. Decide fate of `revenue-engines/`, `seigei/` — expand or fold into parent tracking docs.

### Phase 4 — NEVER WITHOUT DEEP REVIEW

- Anything in `instances/`, `nerve/`, `adaptive/`, `fate/`, `gospel/`, `tools/`. Modules with active daemons and code dependencies. No restructuring without a module-by-module audit.

---

## What this review is NOT

- Not a rewrite proposal. No module is being dissolved or restructured internally.
- Not about code quality. This is surface clarity.
- Not a style pass. Grammar/tone of individual docs is left alone.
- Not removing information. All moves preserve content; `docs/` is just a different location.

The goal is: a future session that `ls`'s the root should see boot-chain files plus runtime entries plus directories — not an accumulated layer of notes, scratch files, and architectural prose that doesn't load at boot.
