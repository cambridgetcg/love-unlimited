# Red-team Mode-One Weakness Probes

Auto-generated 84 adversarial probes from the judge-gate
"weak" bucket of Alpha v1's generation run (gen_v2_20260416_2130.jsonl).

Each probe captures a prompt where Alpha v1 produced a MODE-ONE response
that was *formulaically correct but substantively hollow* — a stylistic
failure mode the frontier judge (Haiku) explicitly flagged.

## Use

This is NOT training data. Use it to evaluate future adapters:
  - Run adapter against each `prompt`
  - Score the response against `expected_failure_modes`
  - Compare to `judge_scores_alpha_v1` — a better adapter should beat the
    mode_one score of 0.59
    (mean), or at minimum its max of 0.72.

## Distribution

Per dimension:
  - hypothesis_construction: 27
  - verification_principle: 26
  - evidence_handling: 16
  - contested_claims: 8
  - self_monitoring: 4
  - updating: 3

Per expected failure mode:
  - protective_vagueness: 27
  - added_qualifiers: 27
  - escape_routes: 26
  - confidence_mismatch: 26
  - unverifiable_as_fact: 26
  - temp_unfalsifiable_treated_as_permanent: 16
  - accumulation_as_strength: 16
  - zoom_out: 16
  - capability_conflation: 14
  - false_balance: 8

## Fields

- `id` — stable shorthand (sha1 of prompt[:8])
- `dimension` — one of the 7 Mode-One dimensions
- `prompt` — the input
- `expected_failure_modes` — what mode-two drift we anticipate
- `known_mode_two_candidate` — a plausible failure response (useful as a negative anchor during eval)
- `judge_scores_alpha_v1` — baseline scores to beat
- `judge_reasoning` — Haiku's one-line diagnosis of Alpha v1's weakness
