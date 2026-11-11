# Appendix: acceptance criteria (spec §17)

Audited on November 10, 2026, on branch `n11-closeout` at commit `ac94969` (main `95b4ac8` plus two N11 commits). Revised on November 11, 2026, after an independent review of the report: items 14 and 18 moved from held to partly held, item 6 states the truth-log rule as it holds, and items 12, 13, 20 and 21 were brought up to date. No check was rerun for the revision. Each item gives the criterion in short form, the verdict and the evidence. Evidence is labeled by where it comes from:

- **Run now:** a command or test run for this audit, with its result.
- **Parent's run:** a check the parent session ran, with its log; not rerun here.
- **Record:** a committed file and the field that shows it.
- **Build log:** an entry in `docs/neural/build-log.md`, which is a claim, not a check run here.

A check that nobody ran is reported as not run. Every number comes from synthetic data with no clinical review.

## 1. Keys

**Criterion.** `.env` untracked and ignored; `.env.example` lists the three key names with empty values; the extended key test passes; a full-history scan of every `.env` value finds none.

**Verdict: held.**

- Run now: `git ls-files .env` lists nothing; `git check-ignore -v .env` gives `.gitignore:22:.env`; no commit on any ref touches `.env`.
- Run now: `.env.example` holds exactly `ANTHROPIC_API_KEY=`, `OPENAI_API_KEY=` and `OPENROUTER_API_KEY=`.
- Run now: `pytest tests/test_no_key_in_site.py -k "not value_of_each_key"`: 9 passed, 3 deselected (key shapes for every provider, ordinary words left alone, nothing under `docs/` holds a key or names an API host).
- Run now: `pytest tests/test_key_history_check.py` (fake keys in a temporary repository): 2 passed.
- Parent's run (main checkout at `95b4ac8`, with `.env`): `pytest tests/test_no_key_in_site.py`: 12 passed, the 3 value checks included (each `.env` value against every tracked file).
- Parent's run: `python scripts/key_history_check.py --repo .`: 3 key values searched in every blob of 128 commits on every ref, none found. The scanner was checked by a probe value known to be in the history, which it found in 4 files.

## 2. OpenAI spend

**Criterion.** Settled total never above $74.00; each job within its cap; the O1 dry run and the gate come before O2's first request; the manifest records the reconciliation.

**Verdict: partly held.** The spend limits and the order held. The manifest records settled spend per job and in all, but not per model beside the token sums, which is how §4.6 describes the reconciliation it should record (this matters only for O1, the one job with three models). The order is shown by stage markers, dry-run files and the gate's commit, not by ledger entries: the ledger holds money entries only.

- Run now (main checkout): `python -m src.neural.pipeline status` and `ledger report`: account `openai` spent $62.1739 of $76.43 (margin $2.43, so $74.00 usable), $0.0000 held. Jobs: O1 $0.6003 of $3.00, O2 $43.8659 of $44.00, O3 $17.7077 of $18.00, O1b and O3b $0.0000 and never released.
- Run now (read-only SQLite query of `data/openai/ledger.sqlite`): entry kinds are reserve 52,376, settle 52,292, release 52,292 and void 84, with no `adjust` entry, so the settled total only rises and its final $62.1739 is its maximum.
- Run now: the O1 dry run finished at 10:08 PM on November 8 (stage marker `dry-run-O1`, `data/openai/dryrun-O1.json`); O1's ledger entries run from 10:20 PM to 10:24 PM; the gate (`results/neural/generation/pilot.json`, `"passed": true`) was committed at 10:39 PM (`fc759a9`); O2's dry run finished at 11:23:22 PM and O2's first ledger entry is at 11:23:38 PM. The `generate` command refuses O2 unless that gate file says passed (`src/neural/pipeline.py`, `needs == "pilot_gate"`).
- Record: `datasets/scenarios-v2.manifest.json`, field `ledger`: spend per job and $62.173885 in all against the $76.43 cap; no per-model spend or token sums. The `ledger report` run above prints them (O1: `gpt-6-luna` $0.0170, `gpt-6.1-sol` $0.4188, `gpt-6.1-terra` $0.1645).
- Build log, November 9, 1:02 AM: after O2, OpenAI's usage page and the ledger were $0.51 (1.2%) apart, inside the $1 and 5% limits. O3's total was not compared with the usage page (November 9, 12:32 PM entry). Daniel has since waived that comparison as a stop condition, so it is not counted as a failure.

## 3. Library

**Criterion.** At least 2,000 accepted from O2; every scenario re-validates at load; every factor marginal at least 90% of target; the manifest has every field of §4.11.

**Verdict: partly held.** The O2 count, re-validation and marginals held. The manifest breaks requested, repaired and dropped counts out by job only, while §4.11 asks for them by job, model, stratum and archetype; accepted counts have every breakdown. Note: the load path checks the library's SHA-256 against the splits file and the simulation manifest; it does not rerun the validator. The re-validation below was run for this audit.

- Record: manifest `jobs.O2.counts.accepted` 6,023 (6,002 in the library after near duplicates and family bridging were left out).
- Run now: every one of the 8,142 committed scenarios through validation layers 1 to 5 (schema, semantics, names, plausibility by simulation, brief adherence), as `src/scenarios/freeze.py` `_revalidate` runs them: 8,142 passed, 0 failed, 185 s on 6 workers.
- Run now: all 34 factor levels in manifest `coverage.marginals` are `ok`; the lowest is `stratum/normal` at 2,590 of 2,852 (90.8%). Not part of this criterion: 3 of the 32 stratum-by-archetype cells are under 90% of target (normal academic medical center 395 of 455, normal children's hospital 303 of 340, normal community hospital 482 of 548), so `coverage.ok` is false; `REPORT.md` section 9 states this.
- Run now: the manifest has a field for every item §4.11 lists: `sha256`, `bytes`, `created_at`, `jobs` (model and effort in `arms`, `tier`, `prompt_hashes`), `schema_hash`, `policy_hash`, `planner` (seeds, config hash), counts (`jobs.*.counts`: requests, accepted, repaired, dropped, refused per job; `accepted_by_job_and_model`, `accepted_by_stratum`, `accepted_by_archetype`), rejection reasons (`rejections_by_layer`), `near_duplicates`, `coverage`, `dispersion`, `ledger`, `prices_verified_at`, `splits` (family and split counts), `ood_definition`, `"clinical_review": "none"` and `notice`. The missing breakdowns are the ones named in the verdict.

## 4. Simulator

**Criterion.** Byte-identical reruns; v1-world passes the v1 validator 100%, its leg KS checks and its breach-share interval; the v2 validator passes on every replicate; 2 seconds or less per 1,000-specimen week.

**Verdict: held.**

- Run now: `pytest tests/test_sim_engine.py tests/test_sim_v1_compat.py`: 64 passed in 8.2 s, including `test_reruns_are_byte_identical`, `test_library_files_are_byte_identical_across_runs`, `test_every_v1_world_trail_passes_the_v1_validator`, the six leg KS tests, `test_the_breach_share_is_inside_the_99_percent_interval_around_v1s` and `test_one_week_runs_at_two_seconds_or_less_per_thousand_specimens`.
- Run now: one core, 60 library scenarios drawn with seed 0, one 7-day scored replicate each: 153,178 specimens in 5.43 s, 0.036 s per 1,000 specimens pooled (median 0.027, slowest scenario 0.137).
- Parent's run (`rebuild.log`, HiPerGator, code `50b72fa`): a full rebuild from the frozen library wrote 27,148 of 27,148 replicates with 0 dropped (the simulator runs the v2 validator on every replicate before writing it), and its simulation manifest equals the committed round-two one on all 16 keys, every per-file SHA-256 included. The store's 10 DuckDB tables are content-equal (`store-content.log`); the `.duckdb` bytes differ because DuckDB's file layout varies.

## 5. Splits

**Criterion.** No family in two splits; the OOD rule holds; O3 families only in train and validation; the splits file committed and hashed into the artifact.

**Verdict: held.**

- Run now, over `datasets/scenarios-v2.jsonl.gz` and `datasets/scenarios-v2.splits.json`: 8,142 scenarios in 7,310 families, 0 families in more than one split, 0 scenarios whose split differs from their family's.
- Run now: all 482 scenarios with the OOD feature (a `multi_site_system` archetype or a `scanner_downtime` disruption) are in `test_ood`; the other 367 `test_ood` scenarios travel with their families.
- Run now: the 2,071 O3 scenarios are 1,752 in train and 319 in validation; their 2,012 families are 1,702 train and 310 validation.
- Run now: the artifact's `manifest.json` field `splits_sha256` (`2d495f20…`) equals the SHA-256 recomputed from the committed splits file's scenario-to-split map, as `src/pipeline/store.py` hashes it; the test lock records the splits file's own SHA-256 (`c88626dc…`), which matches the committed file.
- Run now: `pytest tests/test_store_v2.py tests/test_freeze.py` passed (in the 284-test run of item 11).

## 6. Isolation

**Criterion.** `test_truth_isolation.py` passes unchanged; `test_neural_isolation.py` passes; only `store.py` and `evaluate.py` mention the truth file.

**Verdict: held**, with the rule stated as it holds: no truth log on the training or feature path. Training, the features and everything under `src/models/` and `src/decide/` read the observed store only. The truth log does reach the build through evaluation: the orchestration files in `src/neural/` (`pipeline.py`, `rebuild.py`, `ablations.py`, `ensemble.py`, `weakspots.py` and `report.py`) import `src/pipeline/neural_eval.py`, which imports `evaluate.py` and scores through it. Rung selection's warning check (`ensemble.py`) and round two's weak spots (`weakspots.py`, which planned job O3's briefs) used validation metrics computed against the truth log.

- Run now: `pytest tests/test_truth_isolation.py tests/test_neural_isolation.py`: 38 passed.
- Run now: `git log -- tests/test_truth_isolation.py` shows its last change on October 30 (`c98f2ef`), before this work began at `8686475`.
- Run now: in `src/` and `scripts/`, only `src/pipeline/store.py` names `truth.duckdb`; the only caller of `store.open_truth` is `src/pipeline/evaluate.py`.

## 7. Features

**Criterion.** The future-invariance test passes; shuffled labels give validation AUROC within 0.50 ± 0.02.

**Verdict: held.** The test measures AUROC within (window, tick) strata, since the masked head gives every specimen of a stratum the same structural risk; pooled over strata the shuffled-label AUROC is 0.896, which the test expects (`pooled > 0.8`).

- Run now: `pytest tests/test_features.py tests/test_training_smoke.py -s`: 24 passed in 19.4 s, including `test_every_feature_at_tau_ignores_everything_after_tau`.
- Run now, printed by `test_shuffled_labels_give_validation_auroc_near_one_half`: AUROC within strata 0.4985 with shuffled labels (pooled 0.8964), 0.8241 with real labels.

## 8. Training

**Criterion.** The CPU smoke test passes in under 60 seconds and is bit-reproducible; resume equals an uninterrupted run on CPU; MPS reruns match `val_sel` within 0.5%.

**Verdict: held**, on the smoke test's tiny store. The final ensemble trained on CUDA (B200 GPUs), not MPS, so no full-size MPS rerun exists.

- Run now (same 24-test run as item 7): `test_two_cpu_epochs_run_in_under_60_seconds_and_the_loss_falls`, `test_cpu_training_is_bit_reproducible` and `test_a_run_interrupted_after_epoch_1_and_resumed_equals_an_uninterrupted_run` passed; both files together took 19.4 s.
- Run now: on the same tiny store, rung A seed 0 trained twice on this Mac's MPS device and once on CPU (2 epochs each). Best `val_sel` 0.1306258634 and 0.1306258627 on MPS, a relative difference of 5.6e-9; CPU gave 0.1335 (2.1% from MPS, which this criterion does not bound).
- Build log, November 9, 9:37 PM: the one cross-device rerun at full size, ablation arm A5-C seed 0, gave a best `val_sel` of 0.08634 on CPU and 0.08654 on a GPU (0.23% apart).

## 9. Artifact

**Criterion.** Rungs and selection recorded; the ensemble artifact, manifest and model card committed, 10 MB or less; loading under a different policy hash refuses.

**Verdict: held.**

- Record: `results/neural/selection.json` (`chosen: "C"`, all four rungs with `val_sel`, the warning check run) and `results/neural/runs.csv` (31 rows: 24 search trials, 4 ladder rungs and 3 final seeds).
- Run now: `git ls-files artifacts/tracernet/v1` lists all 7 files, `MODEL_CARD.md` and `manifest.json` included; they total 5,103,933 bytes (5.10 MB). The manifest records seeds 0, 1 and 2, rung C and policy `9594d7c019363261`.
- Run now: `pytest tests/test_predictor.py` passed (in the 76-test run of item 16), including `test_loading_under_a_different_policy_hash_refuses`, `test_a_feature_schema_version_mismatch_refuses` and `test_f003_an_artifact_file_that_differs_from_its_manifest_refuses_to_load`.

## 10. Calibration

**Criterion.** Validation expected calibration error of `p_miss` 0.02 or less after calibration, or the residual stated with its reason.

**Verdict: held.** Note: the temperature was fitted on a sample of 3,000,000 of 67,286,117 validation ticks, not on every validation tick as §7.6 describes.

- Record: `artifacts/tracernet/v1/manifest.json`, `validation`: expected calibration error 0.0170 before and 0.0138 after (temperature 1.113, isotonic not needed); `calibration.json` `ece_limit` 0.02.
- Record: `results/neural/val/evaluation.json`, `calibration.tracernet`: 0.0107 at 30 minutes of silence (3,152,487 ticks) and 0.0101 at 60 minutes. On `test`: 0.0108 and 0.0091.

## 11. Evaluation

**Criterion.** Every method of §8.2 on every set of §8.1 at budgets 1, 2, 4 and 6 and both weightings, with §8.4's metrics and §8.5's intervals; the vectorized code equals `evaluate.py` exactly on `legacy_v1`.

**Verdict: partly held.** Coverage and equivalence held. Two gaps: AUROC, AUPRC, Brier score and expected calibration error have point values with no interval, while §8.5 asks for intervals on every metric; and reliability plots exist for `test` only.

- Run now, over the five committed `results/neural/{val,test,test_ood,legacy_v1,control_v1world}/evaluation.json`: each holds all six required methods (TracerNet at 3-2-1, equal and 9-3-1 weights; analytic global and local at each weighting; window rule; quantile local and global) at 1, 2, 4 and 6 alerts per shift and 5, 10 and 30 per 100 specimens, with all 16 alert metrics of §8.4 and a 95% interval on each of their 1,344 values per set; TracerNet-minus-baseline differences with intervals and `share_ahead` from 2,000 resamples; discrimination at 15, 30, 60 and 90 minutes and pooled; calibration at 30 and 60 minutes; the nine slices of §8.4 (five on `legacy_v1`, whose store lacks the v2 fields).
- Run now: in `results/neural/test/evaluation.json`, 72 discrimination and calibration values (AUROC, AUPRC, Brier, expected calibration error) carry no interval. `results/neural/plots/` holds `reliability-test-30.svg` and `reliability-test-60.svg` only.
- Not part of the core methods: A9 (optional) was not run; the decision model's zero-shot forecast (X3) was scored on a 4,000-tick sample in `results/neural/decisions/X3-test-scores.json`.
- Run now: `pytest tests/test_neural_eval.py tests/test_store_v2.py tests/test_freeze.py tests/test_ledger.py tests/test_decide.py tests/test_dispatch_eval.py tests/test_ablations.py`: 284 passed in 60 s, including `test_the_vectorized_score_equals_evaluate_score_exactly` and `test_the_vectorized_bootstrap_equals_evaluate_bootstrap_exactly` at every budget.

## 12. Verdict

**Criterion.** R1 to R4 evaluated and quoted verbatim; the test set scored under a hash lock, with every rerun listed in `results/neural/test_runs.jsonl` with its reason.

**Verdict: held.**

- Run now: the 11 quoted lines of the rule in `results/neural/REPORT.md` section 1 are identical to spec §8.6.
- Record: `REPORT.md` section 1 evaluates R1 (failed), R2 (held), R3 (held) and R4 (failed), each with its table. The analytic model stays primary because R1 failed: spec §8.6 lets R1 to R3 decide, and R4 only qualifies the comparison.
- Record: `results/neural/test_runs.jsonl` holds 15 entries (1 first look, 10 repeats with inputs unchanged, 3 risk tables recorded, 1 relock), each with its reason; `REPORT.md` section 11, "Test runs", lists all 15. The relock at 4:42 PM on November 10 gives its reason: X2 on test adds the decision-model configuration hash (spec §8.7). The ablation arms' 129 runs under their own locks are listed after it.

## 13. Honesty

**Criterion.** The report opens with the verdict and the synthetic-data notice, and gives §8.9's recommendation when the rule fails.

**Verdict: held.**

- Record: `REPORT.md`'s first paragraph gives R1 to R4 with numbers and interval ends, labels the 6-alert budget as chosen, names the analytic model as primary because R1 failed, explains why every R1 median is 0, and ends with the notice that every number comes from synthetic laboratory weeks with no clinical review. A list under it keeps the leakage flag, the `legacy_v1` calibration, the coverage shortfall, the training spikes and the failed deletion check in view.
- Record: `REPORT.md` section 10, "Recommendation (spec §8.9)": the analytic model stays primary because R1 failed, and the hybrid option is recommended as the nearest of §8.9's options, which fit this result imperfectly. TracerNet leads overall on capped mean warning, AUROC (0.957 against 0.917 at 30 minutes of silence) and Brier score, ties only in R1's floored median, and leads on the disruption slice (capped mean warning 17.7 minutes against the best baseline's 11.5).

## 14. Ablations

**Criterion.** A1, A2, A5 and A6 done; the others done or marked "not run (time box)"; the negative control reported.

**Verdict: partly held.** A1, A2, A5 and A6 are done and the negative control is reported. Two parts do not hold as written: A3 and A4 are marked "not run (not buildable in time)", since the pipeline could not build their inputs, rather than dropped for the time box; and the arms used 73.7 hours of compute against §8.8's budget of 6 hours (`REPORT.md` sections 6 and 10). A7, A8 and A9 are marked "not run" with their reasons (A7 outside the core set, A8 cut under Anthropic's Usage Policy, A9 first on the cut list).

- Record: `results/neural/ablations.json`: A1 (4 arms), A2 (2 arms), A5 (rungs L, A, B, C and the ensemble) and A6 (5 arms) are `done`, three seeds per arm except the A5 ensemble, all at 733 steps (`steps_equal: true`).
- Record: `ablations.A6.negative_control`: on `control_v1world` at 6 alerts per shift, TracerNet's median warning leads the local analytic refit by 10 minutes (95% interval 5 to 20), flagged `leakage_suspected: true`; `REPORT.md` section 10 reports the flag with the evidence that the lead over the context-free global fit is 0 minutes. The flag stands: flagged, explained, not resolved.
- Run now: `pytest tests/test_ablations.py` passed (in the 284-test run of item 11).

## 15. Explanations

**Criterion.** Completeness within 1% for the median alert; deletion beats random in at least 70% of alerts; the randomization check passes; every test alert has factors.

**Verdict: failed, on deletion.** The other three parts held.

- Record: `results/neural/explanations.json`, `checks`, over the 264,850 attributed `test` alerts at 6 per shift: completeness median relative gap 0.93% against 1% (passed); deletion: the top 3 groups beat 3 random groups in 68.2% of alerts against the required 70% (failed), although their mean drop in `p_miss` is 0.0346 against 0.0116; randomization: mean Spearman correlation -0.0006 over 927 alerts, below 0.3 (passed). Top-level `passed: false`.
- Record: `alerts_given` 287,374 and `alerts_with_factors` 287,374; the 22,524 alerts at a closed window carry one window factor.
- Run now: `pytest tests/test_explain.py` passed (in the 76-test run of item 16).

## 16. Interface

**Criterion.** The Python API, the command line and both schemas exist; sample outputs and `docs/demo/neural.json` validate; `bundle.json` is unchanged.

**Verdict: held.** The sample outputs are the ones the tests write and validate on a tiny store; no sample prediction or alert files are committed.

- Run now: `src/models/tracernet/predictor.py` defines `Predictor.load`, `score`, `alerts` and `explain`; `src/models/tracernet/predict.py` is the command line of §10.2; `docs/neural/schemas/prediction.schema.json` and `alert.schema.json` are well-formed Draft 2020-12 schemas.
- Run now: `pytest tests/test_predictor.py tests/test_predict_cli.py tests/test_export_neural.py tests/test_explain.py tests/test_tracernet.py tests/test_calibration.py`: 76 passed in 67 s, including `test_run_writes_the_three_files_and_each_validates`, `test_the_schema_is_valid_and_both_sources_validate` and `test_main_writes_only_where_told_and_the_bundle_stays_byte_identical`.
- Run now: `docs/demo/neural.json` (2,378 `legacy_v1` specimens) against `docs/demo/neural.schema.json` with `jsonschema`: 0 errors.
- Run now: `git diff --stat 8686475 HEAD -- docs/demo/bundle.json` is empty; the file last changed on October 30 (`dd4bfff`).

## 17. Decision layer

**Criterion.** Spend within $8.00; outputs frozen; the X2 adoption verdict and the X3 comparison in the report, or "not run" with the reason; the network's pipeline runs with no OpenRouter key.

**Verdict: held.**

- Run now (main checkout, `status`): account `decide` spent $2.9897 of $8.00; X1 $0.6961 of $1.00, X2 $2.1933 of $4.50, X3 $0.1003 of $0.75, X4 $0.0000.
- Run now: `git ls-files results/neural/decisions` lists 13 frozen output files.
- Record: `REPORT.md` section 8: X2 fails its adoption rule on test (Drex Max minus rule R2 is -7.9 points of role accuracy, 95% interval -8.9 to -6.9), so exports dispatch by R2; X3's AUROC is 0.669 against TracerNet's 0.763, a difference of -0.093 (-0.105 to -0.081); X4 is not built and the report says why.
- Parent's run: `scripts/clean_clone.sh` ran the whole suite in a clone with no `.env`, so with no key at all, and passed. The test runs of this audit were also made in a worktree with no `.env` (every key is read from the repository's own `.env` path).

## 18. Single command

**Criterion.** `run --from-frozen` rebuilds the data, retrains into a scratch artifact and matches the committed validation metrics within 0.5%; `run --from-frozen --evaluate-committed` reproduces `results/neural/evaluation.json` byte for byte.

**Verdict: partly held.** The file the criterion names, `results/neural/evaluation.json`, does not exist (the evaluations are per set), and the validation evaluation matched byte for byte only with three fields carried over: the store's path, the store manifest's hash and the wall-clock timing. The retrain matched every gated metric within 0.5%. The test sets are not rescored: the lock now holds the decision layer's look, so a TracerNet look at `test` or `test_ood` would need a relock of its own. The store manifest's hash differs because DuckDB's file bytes vary from build to build, while all 10 tables are content-equal.

- Parent's run, `run --from-frozen` (HiPerGator, code `8656c07`; `results/neural/rebuild/run-from-frozen.json`, with the cluster's folder prefix written as `$T`). Training and calibration ran on a B200 node from 4:37 AM to 9:05 AM on Nov 11 (seeds 0, 1 and 2 in 118, 59 and 90 min), and scoring and evaluation on 64 AMD Rome cores from 9:11 AM to 3:32 PM. Exact checks all passed: the simulation manifest, all 62,438 simulation files, the store's inputs and its 10 tables, the training configuration and the feature schema. The gated metrics, each within 0.5%:

  | Metric (validation) | Committed | Retrained | Relative difference |
  |---|---|---|---|
  | Ensemble `val_sel` | 0.081235 | 0.080918 | 0.39% |
  | Pooled AUROC | 0.98550 | 0.98547 | 0.003% |
  | Pooled AUPRC | 0.89936 | 0.89865 | 0.08% |
  | Brier at 30 min | 0.047274 | 0.047325 | 0.11% |
  | Brier at 60 min | 0.031748 | 0.031659 | 0.28% |

  Reported without a gate: the members' `val_sel` (0.08157, 0.08276, 0.08206 against the committed 0.08227, 0.08196, 0.08317), since each member stops early at its own epoch and epochs are sized from measured throughput.
- Parent's run, `run --from-frozen --evaluate-committed` (code `8656c07`, 64 AMD Rome cores, 5 h 40 min; `results/neural/rebuild/run-evaluate-committed.json`): the same exact checks passed, and the validation evaluation of the committed artifact from the rebuilt data equals the committed `results/neural/val/evaluation.json` once `store.path`, `store.manifest_sha256` and `timing` are carried over.
- Parent's earlier run with the existing commands (`eval-rebuild.log`, code `50b72fa`): risk parts byte-identical to the committed ones, 22 of 22.

## 19. Existing guarantees

**Criterion.** Full pytest passes with no drop in the collected count; `scripts/clean_clone.sh` passes; the plan check passes; the exhibit's `npm test` passes.

**Verdict: held** at N11's final code commit, `26b1044`.

- Parent's run at `26b1044` (4:04 PM to 4:13 PM, November 11): the full suite gave 1,737 passed and 3 skipped (the key-value checks, which need `.env` and passed separately under item 1); `scripts/clean_clone.sh .` passed, its fresh checkout reproducing every committed output and passing 1,737; the exhibit's `npm test` passed 51. The suite has grown from 1,509 after N8 to 1,737, so the collected count never dropped.
- Run now: `pytest --collect-only -q` collects 1,711 tests at `ac94969` (599 when this work began, build log November 8).
- Run now: `node visuals/plan.check.cjs`: "plan data is consistent", exit 0.
- Run now: `npm test` in `visuals/exhibit-3d` (in the main checkout, whose exhibit files equal `95b4ac8`; the worktree has no `node_modules`): 3 files, 51 passed.

## 20. Process

**Criterion.** Every pull request merged to `main` and none open; no `Co-Authored-By` trailer and no "Generated with" or "Generated by" line in any commit message, title or body of this work; a teaming-log entry per pull request; tasks N0 to N11 ticked with their checks; `AGENTS.md` updated.

**Verdict: partly held.** Every pull request through #63 (N11) is merged, none carries a trailer or a "Generated" line, and each has its teaming-log entry. The ticks are the part that does not hold from here: the published plan belongs to another organization, whose policy keeps its tick database closed to Claude Code even with editor access, so N0 to N11 are ticked by hand in the browser.

- Run now: `gh pr list --state all`: 62 pull requests, all merged; `gh pr list --state open` is empty. This work's #49 to #62 all merged into `main`.
- Run now: `git log 8686475..HEAD --format=%B` (16 commits) has no `Co-Authored-By`, "Generated with" or "Generated by" line, and no title or body of #49 to #62 has one. Outside this work: the bodies of #37 and #39 (October 30) ended with "Generated by Claude Code" (both lines were removed on November 11, for the revision), and commit `905b3c4` (October 28) uses "generated with the Claude API" in prose.
- Run now: `docs/ai-teaming-log.md` has 14 entries for the 14 pull requests #49 to #62 (N0, the plan, N1, N2, N3, N4, two spend-path reviews, N5 to N10).
- Run now: `AGENTS.md` (local only, untracked) was last changed at 11:43 PM on November 10 and states the test verdict, the failed deletion check and the decision layer's result.
- Parent's run: reading the plan's tick database (`state/main`) was refused ("shared with you from another organization"), so whether N0 to N11 are ticked could not be checked here. Republishing the plan was refused too: the live page is at a newer version whose source this machine could not receive, and overwriting it unseen would discard that version.
- Parent's run: N11's commits and pull request carry no trailer and no "Generated" line, and its teaming-log entry is in place.

## 21. Timeline

**Criterion.** N9 merged by Friday, November 13; N11 by Saturday, November 14.

**Verdict: held.** N9 merged on November 10, three days early; N11 (#63) merged on November 11, three days before its deadline.

- Run now (`gh pr list`): N9, #61, merged at 11:27 PM EST on November 10; N10, #62, at 11:39 PM EST on November 10; N11, #63, at 4:14 PM EST on November 11 (`gh pr view 63`, read for the revision).

## Summary

Of the 21 criteria, 14 held (items 1, 4 to 10, 12, 13, 16, 17, 19 and 21), 1 failed and 6 held in part.

- **Failed: item 15, explanations.** The deletion check found the top three factor groups lower `p_miss` more than three random groups in 68.2% of test alerts, below the required 70%. Completeness (0.93%), randomization (Spearman -0.0006) and factors on every alert held.
- **Partly held: item 2, OpenAI spend.** Every cap held ($62.1739 of the $74.00 usable); the manifest records spend per job but not per model with token sums.
- **Partly held: item 3, library.** Every scenario re-validates and every marginal is at least 90% of target; the manifest gives requested, repaired and dropped counts by job only.
- **Partly held: item 11, evaluation.** Discrimination and calibration metrics have no bootstrap intervals, and reliability plots exist for `test` only.
- **Partly held: item 14, ablations.** A3 and A4 were not buildable in time, and the arms used 73.7 hours of compute against a 6-hour budget.
- **Partly held: item 18, single command.** The file the criterion names does not exist, and the validation match needed three fields carried over.
- **Partly held: item 20, process.** The plan's ticks could not be read or written from here (another organization's policy), so N0 to N11 are ticked by hand.
