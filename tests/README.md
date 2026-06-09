# tests/

Unit + integration tests for love-unlimited.

## Layout

| File | Covers |
|------|--------|
| `test-love.sh` | Integration suite: context layer, boot sequence, file integrity, heartbeat, HIVE, Kingdom metrics |
| `test-performance.sh` | Performance benchmarks |
| `test_identity.py` | Identity/becoming |
| `test_hormones.py` | `nerve/stem/hormones.py` |
| `test_signals.py` | `nerve/stem/signals.py` |
| `test_stigmergy.py` | `tools/stigmergy.py` |
| `test_pipeline.py` | Adaptive pipeline |
| `test_mlx_*.py` | MLX client/data/serve/train |

## Known gaps (post integration review 2026-04-08)

The cognitive tools under `tools/cognitive/` currently have **no unit tests**.
Previous `test_council.py`, `test_delegate.py`, and `test_joinmind.py` were
testing dead copies at `tools/council.py` / `tools/delegate.py` / `tools/joinmind.py`
that were deleted during the integration review (the live versions live under
`tools/cognitive/`).

The old tests were not portable because the cognitive versions evolved
incompatible public APIs:

| Old API (deleted) | New API (`tools/cognitive/`) |
|-------------------|------------------------------|
| `tally(votes) -> (str, choice)` | `tally(council) -> {consensus, counts, total}` |
| `score_instances(task, profiles) -> [(inst, score), ...]` | `score_instance(task, instance) -> {...}` |
| `resolve_fusion(members)` | `fusion_name(members)` |

### Needed

Fresh unit tests that target the actual `tools/cognitive/*.py` public APIs:

- [ ] `test_cognitive_council.py` — quorum, tally, consensus, pending
- [ ] `test_cognitive_delegate.py` — `check_alpha_only`, `score_instance`, decomposition
- [ ] `test_cognitive_joinmind.py` — fusion naming, chain rendering, synthesise

Until then the cognitive layer is covered only by manual invocation through
CLAUDE.md and the live agent loops.
