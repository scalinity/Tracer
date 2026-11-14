# Results v2: the fresh test set's one look

**Verdict: TracerNet becomes Tracer's primary risk model.** On 776 laboratory weeks it had never seen, scored once with the rule fixed before any of them existed, the primary endpoint and all four guardrails held. The analytic mixture-survival model becomes a baseline.

Synthetic data throughout: laboratory weeks designed by a language model and simulated by this project's code. None comes from a laboratory, and nothing had clinical review.

## The rule in five lines

From `docs/neural/preregistration-v2.md`, merged before the fresh set existed and unchanged since:

1. Measure: capped mean warning per late specimen at 6 alerts per shift. A miss counts as 0 and each warning is capped at 120 minutes.
2. Win: at both the 3-2-1 and the equal urgency weights, the lower end of the 95% interval of TracerNet minus every baseline is above 0 (scenarios resampled whole, 2,000 times).
3. Guardrails: Brier at 30 and 60 minutes within 0.01 of the better analytic refit; AUROC at 30 minutes at least every baseline's; false alerts per alert sent no higher than every baseline's; never-received specimens alerted within 2 points of R2's baselines.
4. Data: about 800 new laboratory weeks from the same generator, settings and simulator, on seeds never used, scored once.
5. Pass and TracerNet becomes the primary model. Fail and it stays a challenger, with the analytic model primary.

The model under test is `artifacts/tracernet/v2` (`tracernet-v2`, candidate C1 of `docs/neural/phase2-log.md`, three seeds), frozen in #88 and named in the preregistration's section 6 in #89. G1 and G2 score the probability it reports, its calibrated output passed through the history-only site map; P, G3 and G4 use its alerts, which the site map does not change.

## The verdict

`python -m src.pipeline.rule_v2 results/neural/test_fresh/evaluation.json --site-map` prints `test_fresh: P held, G1 held, G2 held, G3 held, G4 held; TracerNet becomes Tracer's primary risk model.` Its record is `results/neural/test_fresh/verdict.json`. Every interval is the paired cluster bootstrap of the preregistration's section 3: 2,000 resamples of whole scenarios, seed 0.

### Capped mean warning at 6 alerts per shift (minutes per late specimen)

| Method | 3-2-1 weights | 95% interval | Equal weights | 95% interval |
|---|---|---|---|---|
| TracerNet | 23.3 | 21.6 to 25.2 | 12.8 | 11.1 to 14.5 |
| Window rule | 7.6 | 6.7 to 8.7 | 7.6 | 6.7 to 8.7 |
| Analytic, global refit | 12.4 | 11.3 to 13.6 | 7.9 | 6.9 to 9.0 |
| Analytic, local refit | 13.6 | 12.5 to 14.9 | 8.3 | 7.2 to 9.4 |
| Quantile rule, local | 6.9 | 6.4 to 7.3 | 6.9 | 6.4 to 7.3 |
| Quantile rule, global | 6.3 | 5.9 to 6.7 | 6.3 | 5.9 to 6.7 |

### P: TracerNet minus each baseline

| Weights | Baseline | Difference (min) | 95% interval | Held |
|---|---|---|---|---|
| 3-2-1 | Window rule | 15.7 | 14.5 to 16.9 | held |
| 3-2-1 | Analytic, global refit | 10.9 | 10.0 to 11.8 | held |
| 3-2-1 | Analytic, local refit | 9.7 | 8.9 to 10.5 | held |
| 3-2-1 | Quantile rule, local | 16.4 | 15.0 to 18.0 | held |
| 3-2-1 | Quantile rule, global | 17.0 | 15.5 to 18.7 | held |
| equal | Window rule | 5.1 | 4.4 to 5.9 | held |
| equal | Analytic, global refit | 4.9 | 4.1 to 5.6 | held |
| equal | Analytic, local refit | 4.5 | 3.8 to 5.2 | held |
| equal | Quantile rule, local | 5.9 | 4.6 to 7.3 | held |
| equal | Quantile rule, global | 6.5 | 5.1 to 8.0 | held |

### G1: Brier score (lower is better)

| Silence (min) | TracerNet | Better analytic refit | Excess | Allowed | Held |
|---|---|---|---|---|---|
| 30 | 0.0456 | 0.0763 | -0.0307 | 0.01 | held |
| 60 | 0.0311 | 0.0433 | -0.0122 | 0.01 | held |

### G2: AUROC at 30 minutes of silence

| Baseline | TracerNet | Baseline | Held |
|---|---|---|---|
| Window rule | 0.955 | 0.882 | held |
| Analytic, global refit | 0.955 | 0.875 | held |
| Analytic, local refit | 0.955 | 0.914 | held |
| Quantile rule, local | 0.955 | 0.426 | held |
| Quantile rule, global | 0.955 | 0.570 | held |

### G3: false alerts per alert sent (false alerts per shift beside)

| Weights | Baseline | TracerNet per alert | Baseline per alert | TracerNet per shift | Baseline per shift | Held |
|---|---|---|---|---|---|---|
| 3-2-1 | Window rule | 0.503 | 0.593 | 2.90 | 2.22 | held |
| 3-2-1 | Analytic, global refit | 0.503 | 0.613 | 2.90 | 3.39 | held |
| 3-2-1 | Analytic, local refit | 0.503 | 0.599 | 2.90 | 3.31 | held |
| 3-2-1 | Quantile rule, local | 0.503 | 0.860 | 2.90 | 5.75 | held |
| 3-2-1 | Quantile rule, global | 0.503 | 0.863 | 2.90 | 5.20 | held |
| equal | Window rule | 0.495 | 0.593 | 2.48 | 2.22 | held |
| equal | Analytic, global refit | 0.495 | 0.617 | 2.48 | 2.45 | held |
| equal | Analytic, local refit | 0.495 | 0.619 | 2.48 | 2.48 | held |
| equal | Quantile rule, local | 0.495 | 0.860 | 2.48 | 5.75 | held |
| equal | Quantile rule, global | 0.495 | 0.863 | 2.48 | 5.20 | held |

### G4: never-received specimens alerted before the window closed

| Weights | Baseline | TracerNet | Baseline | Deficit (points) | Interval high | Held |
|---|---|---|---|---|---|---|
| 3-2-1 | Window rule | 47.4% | 37.2% | -10.1 | -8.2 | held |
| 3-2-1 | Analytic, global refit | 47.4% | 40.0% | -7.4 | -5.8 | held |
| 3-2-1 | Analytic, local refit | 47.4% | 43.9% | -3.4 | -2.5 | held |
| equal | Window rule | 37.8% | 37.2% | -0.5 | -0.3 | held |
| equal | Analytic, global refit | 37.8% | 37.7% | -0.1 | +0.1 | held |
| equal | Analytic, local refit | 37.8% | 37.3% | -0.4 | -0.2 | held |

The narrowest margins: the smallest lower bound in P is 3.8 minutes (equal weights against the local refit), and the closest guardrail is G4 at equal weights against the global refit, whose deficit's upper bound is +0.1 points against the 2 allowed.

## Reported beside the decision

The preregistration's section 4 lists these to report and never to decide. `python -m src.pipeline.beside_v2 results/neural/test_fresh/evaluation.json` prints them.

### R1's median warning at 6 alerts per shift (all late specimens, a miss counts as 0)

| Method | 3-2-1 | Equal |
|---|---|---|
| TracerNet | 0.0 | 0.0 |
| Window rule | 0.0 | 0.0 |
| Analytic, global refit | 0.0 | 0.0 |
| Analytic, local refit | 0.0 | 0.0 |
| Quantile rule, local | 0.0 | 0.0 |
| Quantile rule, global | 0.0 | 0.0 |

### R4: realized alerts per shift at the 6-per-shift budget (tolerance 10%)

| Weights | Baseline | TracerNet | Baseline | Relative difference | Within 10% |
|---|---|---|---|---|---|
| 3-2-1 | Window rule | 5.76 | 3.75 | +54% | no |
| 3-2-1 | Analytic, global refit | 5.76 | 5.54 | +4% | yes |
| 3-2-1 | Analytic, local refit | 5.76 | 5.52 | +4% | yes |
| equal | Window rule | 5.02 | 3.75 | +34% | no |
| equal | Analytic, global refit | 5.02 | 3.98 | +26% | no |
| equal | Analytic, local refit | 5.02 | 4.00 | +25% | no |

### At matched realized volume (6/shift; knobs re-solved on the scored replicates from alert volume alone, per tuning group: a sensitivity check (R4), never a replacement)

TracerNet minus each baseline. R1's median warning:

| Weights | Baseline | Difference (min) | 95% interval |
|---|---|---|---|
| 3-2-1 | Window rule | 0.0 | 0.0 to 0.0 |
| 3-2-1 | Analytic, global refit | 0.0 | 0.0 to 0.0 |
| 3-2-1 | Analytic, local refit | 0.0 | 0.0 to 0.0 |
| 3-2-1 | Quantile rule, local | 0.0 | 0.0 to 0.0 |
| 3-2-1 | Quantile rule, global | 0.0 | 0.0 to 0.0 |
| equal | Window rule | 0.0 | 0.0 to 0.0 |
| equal | Analytic, global refit | 0.0 | 0.0 to 0.0 |
| equal | Analytic, local refit | 0.0 | 0.0 to 0.0 |
| equal | Quantile rule, local | 0.0 | 0.0 to 0.0 |
| equal | Quantile rule, global | 0.0 | 0.0 to 0.0 |

Capped mean warning:

| Weights | Baseline | Difference (min) | 95% interval |
|---|---|---|---|
| 3-2-1 | Window rule | 15.7 | 14.5 to 16.9 |
| 3-2-1 | Analytic, global refit | 10.9 | 10.0 to 11.7 |
| 3-2-1 | Analytic, local refit | 9.8 | 9.0 to 10.6 |
| 3-2-1 | Quantile rule, local | 16.6 | 15.2 to 18.1 |
| 3-2-1 | Quantile rule, global | 16.9 | 15.4 to 18.5 |
| equal | Window rule | 5.0 | 4.3 to 5.8 |
| equal | Analytic, global refit | 4.7 | 4.0 to 5.5 |
| equal | Analytic, local refit | 4.5 | 3.8 to 5.2 |
| equal | Quantile rule, local | 5.9 | 4.6 to 7.3 |
| equal | Quantile rule, global | 6.2 | 4.9 to 7.6 |

### Capped mean warning at other budgets (TracerNet minus the closest baseline)

| Budget | Weights | TracerNet (min) | Closest baseline | Its value (min) | Difference | 95% interval |
|---|---|---|---|---|---|---|
| 1/shift | 3-2-1 | 3.1 | Quantile rule, local | 4.6 | -1.5 | -1.8 to -1.2 |
| 1/shift | equal | 0.7 | Quantile rule, local | 4.6 | -3.9 | -4.2 to -3.7 |
| 2/shift | 3-2-1 | 8.2 | Quantile rule, local | 5.0 | 3.2 | 2.5 to 4.0 |
| 2/shift | equal | 2.3 | Quantile rule, local | 5.0 | -2.7 | -3.0 to -2.3 |
| 4/shift | 3-2-1 | 16.4 | Analytic, local refit | 8.7 | 7.7 | 6.9 to 8.4 |
| 4/shift | equal | 8.0 | Quantile rule, local | 5.8 | 2.2 | 1.3 to 3.2 |
| 6/shift | 3-2-1 | 23.3 | Analytic, local refit | 13.6 | 9.7 | 8.9 to 10.5 |
| 6/shift | equal | 12.8 | Analytic, local refit | 8.3 | 4.5 | 3.8 to 5.2 |
| 5/100 | 3-2-1 | 19.8 | Analytic, local refit | 10.0 | 9.8 | 9.2 to 10.4 |
| 5/100 | equal | 5.2 | Quantile rule, local | 5.5 | -0.4 | -1.1 to 0.3 |
| 10/100 | 3-2-1 | 33.1 | Analytic, local refit | 21.1 | 12.0 | 10.9 to 13.1 |
| 10/100 | equal | 20.9 | Analytic, local refit | 10.1 | 10.7 | 9.3 to 12.2 |
| 30/100 | 3-2-1 | 64.9 | Analytic, local refit | 53.5 | 11.5 | 10.7 to 12.3 |
| 30/100 | equal | 64.8 | Analytic, local refit | 56.2 | 8.6 | 8.0 to 9.1 |

### The 9-3-1 weights at 6 alerts per shift (TracerNet minus each baseline, capped mean)

| Weights | Baseline | Difference (min) | 95% interval |
|---|---|---|---|
| 9-3-1 | Window rule | 15.5 | 14.3 to 16.6 |
| 9-3-1 | Analytic, global refit | 10.8 | 10.0 to 11.7 |
| 9-3-1 | Analytic, local refit | 9.6 | 8.8 to 10.3 |
| 9-3-1 | Quantile rule, local | 16.2 | 14.8 to 17.7 |
| 9-3-1 | Quantile rule, global | 16.8 | 15.3 to 18.4 |

### AUROC by minutes of silence (TracerNet's reported, site-mapped probability)

| Landmark | TracerNet | Best baseline | Its AUROC |
|---|---|---|---|
| 15 min | 0.952 | Analytic, local refit | 0.903 |
| 30 min | 0.955 | Analytic, local refit | 0.914 |
| 60 min | 0.937 | Analytic, local refit | 0.902 |
| 90 min | 0.958 | Analytic, local refit | 0.922 |
| pooled | 0.986 | Analytic, local refit | 0.956 |

### Slices: capped mean warning by laboratory archetype, 3-2-1 weights, 6 alerts per shift (points)

| Archetype | TracerNet | Best baseline | Its value |
|---|---|---|---|
| academic medical center | 17.0 | Analytic, local refit | 9.2 |
| cancer center | 19.9 | Analytic, local refit | 8.9 |
| childrens hospital | 19.0 | Analytic, local refit | 12.1 |
| community hospital | 31.1 | Analytic, local refit | 19.8 |
| critical access rural | 59.3 | Analytic, local refit | 43.5 |
| safety net urban | 18.4 | Analytic, local refit | 10.6 |
| surgical specialty | 42.8 | Analytic, local refit | 25.2 |

### The explanation deletion check

Not run on the fresh set: `python -m src.neural.pipeline explain` reads only the validation, test and out-of-distribution splits. On validation, the frozen model's deletion check reads 68.9% against the 70% line, and the first artifact's 70.0%, each on about 1,800 alerts, with a 95% interval of about ±2.1 points (`docs/neural/phase2-log.md`); on the first test look the first artifact read 68.2%. The explanations stay at the line, not clearly past it.

## What was surprising

- **The lead barely moved.** TracerNet's capped mean warning at 3-2-1 weights was 23.3 minutes here, 21.9 on validation and 23.9 for the first artifact on the spent test set, where the baselines ran 7.4 to 13.7 (here 6.3 to 13.6). At equal weights it was 12.8 here and 11.6 on validation.
- **Every median warning is still 0.** No method catches half the late specimens at 6 alerts per shift, so R1's median cannot separate any two methods. That is why the second rule measures the capped mean.
- **TracerNet sends more alerts at the same budget.** At equal weights it sent 5.02 alerts per shift against about 4.0 for the analytic refits (R4 fails beside the decision by 25% and 26%), and against the window rule's 3.75 at both weightings. Re-solved to matched realized volume, its lead over the analytic refits stays 4.5 to 4.7 minutes at equal weights and 9.8 to 10.9 at 3-2-1, so the extra alerts are not where the lead comes from.
- **About half of its alerts are false alerts, and per shift it sends more of them than the window rule.** G3 compares false alerts per alert sent: 0.50 for TracerNet against 0.59 to 0.86 for the baselines. Per shift, TracerNet sends 2.90 false alerts at 3-2-1 weights and 2.48 at equal weights, against the window rule's 2.22, which sends fewer alerts of every kind. The per-alert form was chosen after seeing validation (preregistration §10).
- **At small budgets, TracerNet loses.** At 1 alert per shift, the local quantile rule's capped mean warning is 1.5 minutes longer at 3-2-1 weights and 3.9 minutes longer at equal weights. With equal weights it also trails by 2.7 minutes at 2 alerts per shift, and by 0.4 at 5 per 100 specimens, an interval that crosses 0. Validation showed the same pattern. The win holds from 4 alerts per shift up, and at 10 and 30 per 100 specimens.
- **The calibration guardrail held with room.** TracerNet's Brier score was 0.046 at 30 minutes of silence against the better refit's 0.076, and 0.031 at 60 against 0.043.

## The look

| Step | Job | Ran (EST, November 13, 2026) | Resources | Result |
|---|---|---|---|---|
| Simulate | 51208205 | 3:46:59 to 3:47:39 PM | 64 CPUs, 192 GB | 776 scenarios, replicates 0 to 3 |
| Store | 51208206 | 3:50:42 to 3:51:23 PM | 64 CPUs, 192 GB | `data/store-v2-fresh` |
| The one look | 51208207 | 3:54:27 to 8:39:46 PM | 32 CPUs, 600 GB requested, 46.9 GB peak | `results/neural/test_fresh/evaluation.json` |

- All three jobs ran on HiPerGator's default partition, the look on node `c0704a-s13` (AMD Rome), from a checkout of `9c8d0be`, the merge of #90, with nothing changed but the results directory. Python 3.14.10, PyTorch 2.15.0, NumPy 2.5.4, Polars 2.1.0, DuckDB 1.5.5, SciPy 1.18.1. Inference ran on CPU.
- The lock was written at 3:54:52 PM to `results/neural/test_fresh/test_lock.json`. The preregistration's §8.4 names `results/neural/fresh/test_lock.json`; the code names the directory after the split. The lock holds the artifact (`ae7695c4…`), calibration (`1103ff7b…`), configuration (`66d5d231…`), store (`cb47eb18…`) and split manifest (`1899b3e1…`) hashes, and the git blob SHAs of `neural_eval.py` (`076abd76…`) and `evaluate.py` (`6c7dbc43…`), which match `main`.
- One attempt. The run log (`test_runs.jsonl`) has the first evaluation at 3:54:52 PM and the two risk tables recorded at 8:22:33 and 8:22:54 PM. Nothing failed and nothing was resumed.
- `looked.json` was written at 8:39:41 PM with the evaluation's SHA-256, `d3e42257e0b39218f38bf772e030b87c94398f6f3cfe52eabd583fba5726f480`. No look at the fresh set runs again. Any later analysis of it is exploratory and cannot change the verdict.

## The fresh set and its cost

### The fresh set

Job T1 planned 950 briefs with planner seed 501 and the O2 settings of preregistration §5 (`gpt-6.1-terra` at low effort, Flex tier, prompt hash `5876a443517f72a4`, the six validation layers, one repair per brief). 940 were accepted (152 after a repair) and 10 were dropped; no request was refused. The exclusions of §5 then left out 164: 162 that lay within 1.5 δ of a library scenario and would have shared a family with train, validation or test scenarios, and 2 near duplicates of a library scenario. The manifest records no other reason.

That leaves 776 scenarios in 773 families (`datasets/scenarios-v2-fresh.jsonl.gz`, SHA-256 `d13183d697836b8ffaadba1f089ed104febe47df0b70fefde0722011d1528c07`), short of the 800 aimed at and above the 374 the width requirement needs. By stratum: 232 normal, 285 stressed, 172 disrupted, 87 edge. By archetype: 163 community hospital, 134 safety-net urban, 108 academic medical center, 106 surgical specialty, 97 cancer center, 91 critical-access rural, 77 children's hospital. Every one was scored; none was dropped afterward.

### Cost

T1 cost $6.81 against its $20.00 cap and the $6.83 the preregistration projected. The OpenAI ledger stands at $73.22 spent of the $136.43 cap, after the $60 raise of November 13, 2026, so $63.21 is left and the $10 reserve is untouched. The look itself ran on HiPerGator and made no paid call.

## Reproduce

```
# On HiPerGator, from a checkout of 9c8d0be: simulate, store and the one look (refused once looked.json exists)
CODE=$HPG/code-fresh bash docs/neural/hpg/fresh-v2.sh

# Anywhere, from the committed evaluation
python -m src.pipeline.rule_v2 results/neural/test_fresh/evaluation.json --site-map --out results/neural/test_fresh/verdict.json
python -m src.pipeline.beside_v2 results/neural/test_fresh/evaluation.json
```

`tests/test_rule_v2.py` checks that the committed verdict follows from the committed evaluation.

## Limits

- Every number comes from a simulator whose laboratory weeks a language model wrote. None comes from a laboratory, and nothing had clinical review.
- The fresh set comes from the same generator, settings and simulator as training, so this shows TracerNet generalizes to new laboratory weeks of the same kind. It says nothing about a real laboratory or about the out-of-distribution cells (multi-site systems, scanner downtime), which the fresh set left out.
- The capped mean became the measure only after the first test look, and G3's per-alert form only after validation (preregistration §10). The fresh set exists so that neither choice could see the data it was tested on.
- The lead holds at 6 alerts per shift and larger budgets. At 1 alert per shift, and at 2 with equal weights, a per-group quantile rule warns earlier.
- The explanation deletion check was not run on the fresh set and sits at its 70% line on validation.
