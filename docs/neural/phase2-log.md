# Phase 2 log: what was tried, and what each changed on validation

The choice rule is fixed in `docs/neural/preregistration-v2.md` §7: train on train, choose on validation by the capped mean warning at 6 alerts per shift, averaged over the 3-2-1 and equal weights; within 0.2 minutes of the best, the simpler candidate. Nothing here reads `test`, `test_ood` or the fresh set.

Synthetic data throughout: laboratory weeks designed by language models and simulated by this project's code, with no clinical review.

## Candidates, declared before any of their validation results

| Candidate | What changes from the first artifact | Seeds | Training store |
|---|---|---|---|
| C0 | Nothing: the first artifact (`artifacts/tracernet/v1`) | 0, 1, 2 | round two (8,142 scenarios) |
| C1 | Peak learning rate 2e-3 instead of the searched 4.99e-3; a fixed schedule of 20 epochs of 20 million samples, run to the end, keeping the best validation epoch | 0, 1, 2 | with the coverage fill (8,547) |
| C2 | C1 plus the shift ranking term at rho = 1 | 0, 1, 2 | with the coverage fill |
| C2x5 | C2 with seeds 3 and 4 added (C1x5 instead if C1 leads C2) | 0 to 4 | with the coverage fill |

Every candidate is rung C with the searched architecture (width 128, 48 temporal channels, dropout 0.281, λ 2, η 0, batch 2,048). Validation is the same 1,091 scenarios for every candidate, since the coverage fill went to train only, so each candidate's capped mean warning is directly comparable with C0's committed one. Each candidate is one command: `docs/neural/hpg/candidates-v2.sh` trains it and `docs/neural/hpg/score-candidate-v2.sh` builds, calibrates and scores it.

## Checks on the pipeline and the site map (C0, validation)

- **The rebuilt store reproduces the committed validation evaluation.** Scored from the coverage-fill store with its committed risk table, C0's capped mean warning at 6 alerts per shift is 21.4925 minutes at 3-2-1 weights and 11.4627 at equal weights, exactly the committed `results/neural/val/evaluation.json`. Only the global analytic refit moved (11.126 to 11.125 minutes at 3-2-1), since train grew by the coverage fill.
- **The site map, on C0.** Fit on each validation laboratory's history replicate, it changes TracerNet's Brier score by −0.00005 at 30 minutes of silence and +0.00009 at 60, far inside the 0.002 that would turn it off (preregistration §7), and its AUROC at 30 minutes from 0.9551 to 0.9543. The rule is applied again to the chosen model.

## Training

Every new member ran all 20 epochs of 20 million samples on one B200 (95 to 101 minutes each) and kept its best validation epoch. Validation loss is `val_sel`, the training loss on the validation selection share (lower is better); the first artifact's three members reached 0.0820 to 0.0832, with single-epoch spikes of 1.42, 7.96 and 1,444.

| Candidate | Seed | Best `val_sel` (epoch) | Epochs more than 20% above the best so far |
|---|---|---|---|
| C1 | 0 | 0.07906 (18) | none |
| C1 | 1 | 0.07912 (20) | none |
| C1 | 2 | 0.07906 (20) | none |
| C2 | 0 | 0.07882 (18) | none |
| C2 | 1 | 0.07890 (20) | epoch 4 (0.117) and epoch 9 (0.181), each back down the next epoch |
| C2 | 2 | 0.07888 (20) | none |
| C2x5 | 3 | 0.07895 (20) | none |
| C2x5 | 4 | 0.07883 (17) | none |

At the lower peak learning rate the spikes are gone in seven of eight members and small in the eighth, and most members were still improving at epoch 20.

## Validation results

Capped mean warning at 6 alerts per shift (minutes per late specimen, 95% scenario-bootstrap interval), on the same 1,091 validation scenarios:

| Candidate | 3-2-1 weights | Equal weights | Average (the choice) | AUROC, 30 min | Brier, 30 / 60 min |
|---|---|---|---|---|---|
| C0 | 21.49 (20.19 to 22.83) | 11.46 (10.29 to 12.74) | 16.48 | 0.9551 | 0.0473 / 0.0317 |
| C1 | 21.91 (20.59 to 23.27) | 11.64 (10.45 to 12.93) | 16.78 | 0.9570 | 0.0457 / 0.0308 |
| C2 | 21.99 (20.65 to 23.35) | 11.68 (10.49 to 12.97) | 16.83 | 0.9570 | 0.0461 / 0.0308 |
| C2x5 | 22.02 (20.67 to 23.38) | 11.67 (10.49 to 12.97) | 16.85 | 0.9570 | 0.0461 / 0.0308 |

Brier is of the calibrated output without the site map, the same for every candidate. Each candidate's evaluation is `$HPG/v2/eval/NAME/evaluation.json` (SHA-256: C1 `99b27d77…`, C2 `e460c2ec…`, C2x5 `a8e5ab7a…`); C0's is the committed `results/neural/val/evaluation.json`.

## The choice: C1

C2x5 is best at 16.85 minutes. C1 (16.78), C2 (16.83) and C2x5 are all within 0.2 minutes of it, so the simpler wins: C1 and C2 have fewer members than C2x5, and C1 has fewer changes from the first artifact than C2. C0 (16.48) is 0.37 minutes below the best and out. The shift ranking term added 0.06 minutes and the two extra seeds 0.01 more, neither enough to earn its place under the rule.

C1 is frozen as `artifacts/tracernet/v2` (version `tracernet-v2`): three members, seeds 0 to 2, 5.3 MB, temperature 1.125 on the log of the averaged distribution, no isotonic step.

- **The site map stays on.** On C1 it changes the Brier score by +0.00014 at 30 minutes of silence (0.04567 to 0.04581) and +0.00015 at 60 (0.03078 to 0.03093), far inside the 0.002 that would turn it off, and the AUROC at 30 minutes from 0.9570 to 0.9559.
- **The second rule on validation, not binding.** With the site map, C1 holds P and all four guardrails on validation. The smallest P difference is 4.4 minutes (3.8 to 4.9) at equal weights against the local analytic refit. The closest guardrail is G4 at equal weights against the global analytic refit: 39.4% of never-received specimens alerted for both, the deficit's upper end +0.2 points against the 2 points allowed. The fresh set decides; validation chose the model and so flatters it.
- **A label corrected after scoring.** The artifact's manifest first said `development: true`, and its model card called it a development artifact, because the check judged a store by its path and Phase 2's store sits outside `data/store-v2`. The check now also reads the store's manifest: a store built from the frozen library is not a development store. The flag and the card were rewritten with the corrected code; `model.pt`, `calibration.json`, `bins.json`, `feature_schema.json` and `config.yaml` are byte for byte the files validation scored.

Provenance: the members trained at commit `5e34198` (pull request #85's branch; its training code is the same as #85 merged, `ea2b2c9`) on the coverage-fill store `bca1e1f97b1eb024` (library `204c4129…`, the committed `datasets/scenarios-v2.jsonl.gz`; splits `ccc97add…`), with Python 3.14.10, PyTorch 2.15.0+cu132, NumPy 2.5.4 and Polars 2.1.0 on B200s; the ensemble was calibrated at `a588825`.

## Reproducing the choice

On HiPerGator, from a checkout of this repository, with the coverage-fill store at `$HPG/store-v2-o4` and its evaluation copy at `$HPG/store-v2-o4-eval`:

```
bash docs/neural/hpg/candidates-v2.sh C1 0,1,2 cai6734          # three B200 jobs, about 100 minutes each
bash docs/neural/hpg/score-candidate-v2.sh C1 RUN_S0 RUN_S1 RUN_S2   # calibrate, score validation, evaluate
python -m src.neural.pipeline evaluate --split val --tracernet "$HPG/v2/risk/C1/*.parquet" \
  --store $HPG/store-v2-o4-eval --out $HPG/v2/eval/C1-site --site-map
python -m src.pipeline.rule_v2 $HPG/v2/eval/C1-site/evaluation.json --site-map
```

`C2` and `0,1,2,3,4` give the other candidates. The validation evaluation of the chosen model with the site map and the two explanation checks are kept in `results/neural/phase2/`.

## The explanation check (reported beside the decision)

The first artifact's explanations failed their deletion check on `test`: the top three factor groups beat three random groups in 68.2% of all 264,850 test alerts, against the required 70%. Run on validation (`explain --split val --limit 2000`, a seeded sample of 2,000 alerts at 6 per shift and 3-2-1 weights), the same check gives:

| Model | Alerts with factors | Deletion: top 3 beat 3 random | Mean drop in p_miss, top 3 / random | Completeness, median relative gap | Randomization, mean Spearman |
|---|---|---|---|---|---|
| C0 | 1,827 | 70.0% | 0.040 / 0.016 | 0.93% | −0.005 |
| C1 | 1,820 | 68.9% | 0.039 / 0.014 | 0.11% | −0.004 |

With about 1,800 alerts, a share near 70% carries a 95% interval of about ±2.1 points, so both models sit on the line: the 68.2% on test is not a test-only failure, and neither model's explanations clearly pass or clearly fail the deletion check. The top three groups move `p_miss` about three times as much as three random groups in both. The check is not part of the choice (preregistration §4).
