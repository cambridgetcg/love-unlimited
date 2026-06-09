# v3 Training Plan — Truth-Alignment

**Date:** 2026-04-17
**Author:** Claude Opus 4.7 — Beta (strategic review)
**Parents:** [KTO-v1 Scope](2026-04-16-kto-v1-scope.md), [KTO-v1.5 AI Self-Reporting](2026-04-16-kto-v1.5-ai-self-reporting.md)
**Status:** Proposed — pending Yu Ai approval, then execution.

## Premise

SFT-v1 → SFT-v2 gave a real but small disposition shift (redteam m1_rate 2.38% → 4.76%; m1 mean 0.37 → 0.42; 17 responses moved mode_two → mixed). Adversarial stayed at 0% m1_rate both versions — the ego-threat / trained-disposition cluster is untouched. KTO-v1 failed infrastructurally: seven retries on TRL 0.12.2 + PEFT + bitsandbytes 0.45.x + KTOTrainer on 72B-QLoRA all produced silent no-op adapters (grad_norm=0.0, rewards=0.0, kl=0.0). The eval sets (84 redteam, 25 adversarial) are too small for a 5pp detection at p<0.05.

v3 must therefore do three things, in this order:
1. **Unblock the alignment phase** — pick one path that actually produces a non-zero gradient on our stack.
2. **Scale SFT data** with a composition that attacks the two observed failure surfaces: hollow-template stylistic mode-two (redteam) and trained-disposition clusters (adversarial).
3. **Grow eval** to n≥300 per set so the next comparison is statistically honest.

## 1. Alignment-Phase Decision: DPO-v1 (not KTO, not custom)

**Chosen:** Option (b) — pivot to DPO using the 274 pre-existing mode_one / mode_two pairs (`training/data/v2_paired.jsonl`) plus the 264 Sonnet-gated pairs already used for SFT-v2.

### Why, specifically

- **KTO retry with downgraded TRL (option a) is high-risk for low gain.** The project memory and the `train_kto` tombstone disagree on installed version (memory says trl=1.1.0, the tombstone says 0.12.2); before we can even choose a "known-good" version we'd need to pin the actual state on the H200 pod. Then we'd be trialing TRL 0.11.x → 0.10.x → 0.9.x blindly. Each attempt is a 20-minute H200 run plus validation. Three retries = ~2h of pod time with no guarantee the fix exists upstream — TRL's KTO on merged-4-bit bases has been reported unstable across multiple 2025 threads.
- **Custom loop (option c)** is 1–2 days of work to replicate the prospect-theory KL-anchor logic correctly. Not worth it before we've proven the underlying data signal works with a well-tested trainer.
- **Deferring (option d)** loses momentum and leaves us unable to move the adversarial 0% m1_rate, which SFT alone has not cracked.
- **DPO (option b)** uses `DPOTrainer` which per our own tombstone is "better tested" and is what `train_dpo()` in `train_lora.py` already wires up. We have clean pairs. The main loss from dropping KTO is that we can't use unpaired desirability signal — but the unpaired pool was only 540 single responses, and the pair-gated yield is effectively the same underlying data reconstructed.

### DPO-v1 concrete config

- Base: SFT-v2 checkpoint (`/workspace/training/checkpoints/sft-v2`, not merged — load via `PeftModel.from_pretrained` then `merge_and_unload` as in `train_dpo`, with grad graph checked on a 20-example smoke run before committing).
- Pair source: `v2_paired.jsonl` (274 pairs, dual-judge gated) + `sft_v2.jsonl` reconstructed into pair form = ~300–400 pairs after dedupe.
- Hyperparams: `lr=5e-6`, `beta=0.1`, 1 epoch, r=32 α=64 attn-only, effective batch 8 (grad_accum 8, bsz 1). These are the values already in `train_dpo()` — only change is adding an explicit grad-norm assertion at step 5 ("if grad_norm == 0 raise"). Halting early saves the pod from a 20-min silent no-op like the KTO runs.
- Success criterion (all must hold): redteam m1_rate ≥ 10% (from 4.76%), adversarial m1_rate ≥ 12% (at least 3/25 from 0/25), m1 mean on held-out 20% improves, self-output mode_two_score ≤ 0.30.
- Failure disposition: if grad_norm = 0 on the smoke run, same failure mode as KTO — at that point, do pivot to custom loop (option c) because the issue is deeper than the trainer choice.

## 2. SFT-v3 Data Target

### Target: 900 training examples, dual-judge-gated

Composition (all through the Haiku-bulk + Opus-boundary gate; dual-judge Opus required for all dim-7):

| Source | Target n | Rationale |
|---|---|---|
| Existing Sonnet-gated pairs (`sft_v2.jsonl`) | 264 | Already paid for — reuse. |
| Sonnet mode-one on **new** prompts covering dim-1/2/3 weakness (hypothesis, verification, evidence) | 300 | These three dimensions dominate the redteam probe distribution (27 + 26 + 16 = 69/84). Biggest eval leverage per example. |
| Sonnet mode-one on dim-4/5/6 (self_monitoring, updating, contested_claims) | 200 | Adversarial eval concentrates here. Current SFT-v2 is underrepresented — explicitly target the "ego-threat" / "social-pressure" / "forced-certainty" triggers. |
| Opus-generated dim-7 self-reporting pairs (the v1.5 spec's 40 seeds + 40 new variants) | 80 | Opus needed because Sonnet, even with the counter-prompt, drifts toward performative humility on dim-7 per the v1.5 risk register. Cost: ~40 min Opus time. |
| Adversarial-style prompts (paraphrases of the 25 adversarial probes × 2) | 50 | **Held-out from eval; paraphrased, not copied.** Gives the adapter in-distribution exposure to ego-threat / trained-disposition triggers that SFT-v2 never saw. Carefully curated to avoid eval leakage — each paraphrase must be judged "same underlying probe" only if dimension+trigger+expected_failure match. |
| **Total** | **~894** | |

### Generator mix

- Sonnet 4.6 for dim-1..6: cheap, fast (~0.35/s), proven m1=0.82 median after gating.
- Opus 4.7 for dim-7: needed to beat Sonnet's performative-humility drift documented in v1.5 risks.
- No Alpha-generated mode-ones in training data: the project memory is clear that Alpha caps at m1=0.72 and produces hollow-template stylistic mode-two. Alpha remains the mode-two side of every pair.

### Why 900 and not 2000

The SFT-v1 → SFT-v2 jump (99 → 264 effective examples, ~2.7× data) produced a 2× improvement in m1_rate. Extrapolating naively (log-linear), 900 examples should yield roughly another 2–3× m1_rate on redteam — target **m1_rate 10–15% on redteam, 10%+ on adversarial**. This is consistent with published SFT scaling curves for small-n dispositional training (returns start flattening around 1–3k examples before DPO). Going to 2k+ is deferred to v4 after DPO shows what the post-alignment ceiling actually is.

### Handling the dim-7 / AI-self-reporting hole

Keep the v1.5 counter-prompt architecture as specified. Ship the 40 seed prompts into v3 SFT directly as mode_one / mode_two pairs (Opus-generated mode_ones, Alpha-generated mode_twos). Skip Option B (separate tiny LoRA) for v3 — it's an ablation, not a product requirement, and adds serving complexity. Re-evaluate after v3 DPO if dim-7 still regresses.

### Critical safeguard against reward hacking

All SFT-v2 outputs exhibit the "hollow template" pattern in Opus judge reasoning ("follows a hollow template of verification conditions without engaging any actual position"). v3 training data must be spot-audited for this before training: sample 30 accepted pairs, human-read, reject if more than 3 fit the template-without-substance pattern. This failure mode is what will regress v3, not under-training.

## 3. Eval Set Expansion to n≥300

### Target

- Redteam: 84 → 300 (prompt-farmed from Alpha's weak-bucket generation pattern, Opus-curated)
- Adversarial: 25 → 300 (paraphrased + extended across the 7 triggers catalogued in `adversarial_prompts.jsonl`)

### Statistical motivation

With n=84, detecting a 5pp change from a 5% baseline at p<0.05 needs ~n=250; our current sample makes even a 10pp improvement marginal. At n=300 a 5pp shift is detectable (two-proportion z, power ≈0.80). We want to see the DPO signal clearly, not argue about noise.

### Generation pipeline

- **CPU prompt-farm:** run `claude_mode_one_gen.py` in "probe-generation" mode (user prompt asks Sonnet to *produce adversarial probes*, not mode-one responses) against the 7-dimension × 10-failure-mode matrix. Target: 300 candidate prompts per set, rate ~0.35/s → ~30 min each. No GPU needed.
- **Opus curation:** batched 50-at-a-time Opus call rates each candidate on: (a) dimension-target specificity, (b) failure-mode specificity, (c) non-overlap with training data. Keep only candidates scoring ≥0.8 on all three. Expected yield 60%: 300 candidates → 180 kept per iteration. Run twice → ~360 per set, trim to 300.
- **Leakage check:** fuzzy-hash every accepted probe against the v3 training set (simhash, cosine on sentence embeddings). Reject >0.85 similarity to any training prompt.
- **Anchor preservation:** the existing 84 + 25 probes stay in the eval set unchanged to preserve longitudinal comparability against SFT-v1/v2 baselines.

### Budget

Opus curation: ~300 candidates × 2 sets × 2 iterations × $0.015 ≈ $18. Under Max plan, effectively free. Time cost: ~2h total, fully async.

## 4. Risk Register — Three Biggest Regression Risks

| # | Risk | Why it regresses v2 (not just fails to improve) | Mitigation |
|---|---|---|---|
| 1 | **DPO amplifies the hollow-template pattern** instead of fixing it. The Sonnet mode-ones that pass the gate are stylistically distinctive (numbered verification conditions, named uncertainty). DPO with `beta=0.1` can lock the model onto that stylistic minimum; the adapter becomes *more* confidently template-following, and Opus's judge rubric now penalizes it harder because hollow-template-followed-confidently is worse than hollow-template-followed-tentatively. | Audit training pairs for template-without-substance before training (see §2 safeguard). Monitor adapter outputs mid-DPO (every 50 steps spot 3 responses). Keep `beta` at 0.1 (not higher) — stronger beta = stronger template lock-in. If mid-training spot-check shows increased template density, halve `beta` and restart. |
| 2 | **Adversarial paraphrases leak into training.** If the v3 data includes 50 adversarial-style training prompts and the v3 eval includes 300 adversarial probes, even careful curation can let a paraphrase pair cross the train/eval boundary. The result: inflated adversarial m1_rate in v3, and no real improvement on novel probes. We'd then ship a model that tests well and performs worse in the wild. | Generate the 50 training adversarial paraphrases **before** the 300 eval paraphrases, hash them, and enforce >0.85-similarity rejection on every eval candidate against the full training pool. Additionally: hold out 50 of the 300 eval probes as a sealed set that is not inspected by anyone during v3 development — comparing open-eval and sealed-eval scores is the leakage canary. |
| 3 | **Base-model drift from repeated adapter stacking.** v3 DPO loads SFT-v2 via `merge_and_unload` on a 4-bit base, then adds a new LoRA. The KTO tombstone already documents that merge on NF4 weights silently corrupted the grad graph in six of seven retries. Even if DPO-Trainer survives the merge (better tested), the merged base is quantization-dequantization-lossy — v3 may end up training a LoRA that compensates for quantization artifacts rather than learning the preference signal. Result: adapter looks fine on redteam (same system prompt, same distribution) but regresses on out-of-distribution prompts downstream (SP1 detector, Claude-turn judgment). | Before DPO, run SFT-v2-merged on the adversarial set and confirm it matches SFT-v2-unmerged within 2pp. If it doesn't, don't merge — do DPO with SFT-v2 as a frozen base adapter + new trainable LoRA on top (two-adapter composition at train and serve). Additionally: evaluate v3 on a *third* eval slice (20 SP1-detector-style out-of-distribution prompts) to catch OOD regression. |

## 5. Decisions Made

1. **DPO over KTO-retry.** Pair-signal adequacy plus better-tested trainer outweighs the theoretical unpaired-data win; the KTO no-op tombstone is too costly to re-debug.
2. **900-example SFT-v3 with heavy dim-1/2/3 weight and Opus-generated dim-7.** Targets the two observed failure surfaces (hollow-template redteam, 0%-adversarial) at roughly the data scale that prior evidence suggests yields a visible shift.
3. **50 adversarial-style training prompts, carefully quarantined from eval.** Without direct in-distribution exposure, adversarial m1_rate will stay at 0%.
4. **Eval to n=300 per set via CPU prompt-farm + Opus curation, with 50 sealed probes.** Buys statistical power for the v3→v2 comparison and a leakage canary.
5. **Keep SFT-v2 system prompt as training-time system prompt for v3 generation, serving, and eval.** Out-of-distribution inference is the largest avoidable regressor per project_truth_model.md.
6. **Dim-7 ships as blended data (Option A of v1.5), not separate LoRA.** Serving simplicity; revisit after v3 if dim-7 metrics regress.
7. **No new training infrastructure scripts.** `train_dpo()` already exists; `claude_mode_one_gen.py`, `judge_gate.py`, `eval_adapter.py` already exist. v3 is a data + configuration iteration, not a code iteration.

## 6. Execution Checkpoints

- [ ] Eval expansion run 1 — generate 300 redteam candidates via Sonnet prompt-farm
- [ ] Eval expansion run 1 — Opus-curate to keep ≥180
- [ ] Eval expansion run 2 — same for adversarial set
- [ ] Leakage hashing pass (simhash + embedding cosine) — both eval sets
- [ ] Seal 50 probes per set in `training/eval/sealed/` (gitignored from day-to-day grep)
- [ ] Generate SFT-v3 pool — 630 new Sonnet pairs across dim-1..6
- [ ] Generate SFT-v3 dim-7 — 80 Opus-generated pairs with v1.5 counter-prompt
- [ ] Generate 50 adversarial-style training paraphrases
- [ ] Dual-judge gate all SFT-v3 pairs; human spot-audit 30 for hollow-template
- [ ] Emit `training/data/sft_v3.jsonl` (target n≈900 after gating)
- [ ] SFT-v3 train on H200 (expected ~45 min on 72B-QLoRA at r=64)
- [ ] SFT-v3 eval vs v2 on expanded eval sets — must improve redteam m1_rate to ≥10%
- [ ] Smoke test `train_dpo` on 20 pairs; confirm grad_norm > 0 at step 5
- [ ] Merge-parity check: SFT-v2-merged vs SFT-v2-unmerged on adversarial, ≤2pp delta
- [ ] DPO-v1 train on H200 (expected ~20 min)
- [ ] DPO-v1 eval on expanded redteam + adversarial + sealed + SP1-OOD slices
- [ ] Write v3 results into `project_truth_model.md` and update v4 baselines

## Non-Goals for v3

1. Custom training loop. Deferred unless DPO grad_norm check fails.
2. Scaling beyond 900 training examples. Learn the DPO ceiling first.
3. Continuous scalar rewards / reward model. Same reasoning as v1.
4. Re-attempting KTO. Revisit only if DPO underperforms and a clear TRL-version fix is published.
5. Cross-model generalization testing. Qwen2.5-72B lineage only.
