# Preregistration v2: can TracerNet become Tracer's primary model?

Written and merged on November 13, 2026, before any new data was generated and before any new training. Section 6, the identity of the model under test, is filled in at the end of Phase 2 and merged before the fresh set is scored. Nothing else in this file changes after it merges.

Synthetic data throughout: laboratory weeks designed by language models and simulated by this project's code, with no clinical review.

## The rule in five lines

1. Measure: capped mean warning per late specimen at 6 alerts per shift. A miss counts as 0 and each warning is capped at 120 minutes.
2. Win: at both the 3-2-1 and the equal urgency weights, the lower end of the 95% interval of TracerNet minus every baseline is above 0 (scenarios resampled whole, 2,000 times).
3. Guardrails: Brier at 30 and 60 minutes within 0.01 of the better analytic refit; AUROC at 30 minutes at least every baseline's; false alerts per alert sent no higher than every baseline's; never-received specimens alerted within 2 points of R2's baselines.
4. Data: about 800 new laboratory weeks from the same generator, settings and simulator, on seeds never used, scored once.
5. Pass and TracerNet becomes the primary model. Fail and it stays a challenger, with the analytic model primary.

## 1. Why a second rule

The first rule (spec §8.6) made TracerNet primary only if R1 to R3 held on `test`. R1 asked for a longer median warning over all breaches, a miss counting as 0. On `test` the laboratories average 6.4 breaches per shift against a budget of 6 alerts, so every method misses more than half of its breaches and every median is 0 (`results/neural/REPORT.md` §1). R1 cannot separate any two methods there, and it stays failed.

Changing the measure after seeing `test` would be moving the goalposts. So this file fixes a new measure and rule now, and the rule is tested once, on a fresh set that does not exist yet. `test` and `test_ood` are spent: nothing in this work reads them again.

## 2. The decision

TracerNet becomes Tracer's primary risk model if and only if the primary endpoint (section 3) holds at both weightings and every guardrail (section 4) holds, all on the fresh set (section 5). Otherwise the analytic model stays primary and TracerNet is reported as a challenger.

## 3. Primary endpoint

**Measure.** The capped mean warning of spec §8.4: over every breach among the scored specimens with a clock (a breach is a specimen received after its window closed or never received, which is what "late specimen" means here), the warning is the minutes from the specimen's first alert to its window's close, 0 when it had no alert before the close, and capped at 120 minutes. The measure is the mean over breaches. It is what `src/pipeline/neural_eval.py` already reports as `capped_mean_warning_min`.

**Budget.** 6 alerts per 8-hour shift. Each method's knob is set by bisection on each scenario's history replicate from alert volume alone, with no labels, to the most aggressive value that stays within the budget, and applied unchanged to the scored replicates (spec §8.3). Where no knob that alerts stays within the budget, the method sends no alerts in that scenario, as before.

**Methods.** TracerNet is the frozen artifact of section 6. Its alert score is `p_miss` times the tier's weight, from its own calibrated output (the site map of section 4 does not change alerts). The baselines are the five of spec §8.2:

| Baseline | Fitted on |
|---|---|
| Window-aware rule | Nothing |
| Analytic model, global refit | The scored replicates of the train split of the frozen model's training store |
| Analytic model, local refit | Each fresh scenario's own history replicate (the global fit below 100 specimens or if the refit fails) |
| Quantile rule, local | Each fresh scenario's own history replicate |
| Quantile rule, global | The train split |

The weighted baselines (the two analytic refits) use the same weighting as TracerNet. The window and quantile rules have no weighting and are compared as they are.

**Weightings.** The default 3-2-1 and equal weights, as R1 required.

**Win condition.** At each weighting and against each of the five baselines, the lower end of the 95% interval of TracerNet's capped mean warning minus the baseline's is above 0. The set includes the baseline with the highest capped mean warning, so this is at least as strict as beating the best baseline. No minimum lead beyond zero is required.

**Interval.** The paired cluster bootstrap of spec §8.5: 2,000 resamples, seed 0, 95% percentile interval, every method resampled with the same draws. The unit resampled is the scenario: one laboratory week, with its knob and all 63 of its scored shifts (3 replicates of 21 shifts) kept together, so a shift is never split. Shifts inside one laboratory week share its knob, its disruptions and its staffing, so they are not independent; resampling single shifts would make the interval too narrow.

## 4. Guardrails

All four must hold on the fresh set.

**G1. Calibration (R3, unchanged).** TracerNet's Brier score at 30 and at 60 minutes of silence is no worse than the better analytic refit's (the lower of the global and local refit's Brier) by more than 0.01. The probability scored is the one TracerNet reports: the artifact's calibrated `p_miss`, passed through the history-only site map of `results/neural/followup-2026-11-13.md` §2 (`src/neural/sitecal.py`, fit on each scenario's history replicate, never on its scored replicates) unless Phase 2 turns the map off by the rule in section 7.

**G2. Discrimination.** TracerNet's AUROC for a miss, among specimens still silent at 30 minutes whose window is longer than 30 minutes (spec §8.4), is at least every baseline's, each method ranked by its own score. Point estimates, no margin. TracerNet is scored on the same probability as G1.

**G3. False alerts.** At each weighting, TracerNet's false alerts per alert sent (alerts on specimens received on time, divided by all alerts) is no higher than each baseline's, at 6 alerts per shift. Point estimates. False alerts per shift are reported beside it but do not decide.

Why per alert and not per shift: the methods do not send the same number of alerts at the same budget. On validation, with the first artifact, the window rule sent 3.66 alerts per shift and TracerNet 5.84, because in about a third of laboratories (384 of 1,091) the window rule has no knob that both alerts and fits the budget. A method that alerts less has fewer false alerts per shift whatever its quality, so a per-shift count would reward sending fewer alerts. Per alert sent, the comparison is at equal volume, and it equals the per-shift comparison whenever two methods send the same number of alerts. This was decided after seeing validation, where the per-shift count favored the window rule (2.08 against TracerNet's 2.83) and the per-alert share favored TracerNet (0.485 against the window rule's 0.567, the lowest of the baselines).

**G4. Losses (R2, unchanged).** At each weighting, TracerNet's share of never-received specimens alerted before the window closes is not lower than that of the window rule, the global refit or the local refit by more than 2 percentage points (upper end of the 95% interval of the deficit below 2 points).

**Reported beside the decision, never folded into it:** R4 (alerts per shift within 10%) and R1 repeated at matched realized volume; R1's median warning; budgets of 1, 2 and 4 alerts per shift and per 100 specimens; the 9-3-1 weights; AUROC at 15, 60 and 90 minutes and pooled; slices; the explanation deletion check.

## 5. The fresh test set

**Generator: the one that made `test`.** The `test` families came from the main run, O2. The fresh set is a new job, T1, with every O2 setting:

- The planner's grid, shares, compatibility table, anchors and quirks (`config/scenario_planner.yaml`), unchanged except added job entries. A test checks that every other key equals the file at commit `7a6bc8e`.
- `gpt-6.1-terra` at low effort, Flex tier, Structured Outputs in strict mode, the O2 prompt (prompt hash `5876a443517f72a4`) and schema.
- The six validation layers of spec §4.8 unchanged, and one repair per brief, as O2.
- In-distribution cells only (the planner's `ood: false`), because `test` held none of the out-of-distribution cells (`multi_site_system`, `scanner_downtime`). A fresh scenario carrying either is left out.

**New seeds.** Planner seed 501 (used so far: 101, 102, 201, 301, 302). The simulator keeps its master seed (20261108) and gives the fresh scenarios indices from 20001 up (the library uses 1 to 8,142 and the v1-world control 9000), so every random stream, `SeedSequence([master_seed, index, replicate])`, is new.

**Left out, as the library left items out.** A fresh scenario is left out if it is a near duplicate of any library scenario or of an earlier fresh one (same archetype, standardized distance below δ = 1.4946), or if it lies within 1.5 δ of any library scenario, since it would then share a family with train, validation or test scenarios, and test families never share one with train. The count left out for each reason goes in the fresh manifest.

**Replicates.** As `test`: replicate 0 is history (knobs, local refits, the site map), replicates 1 to 3 are scored, 7 scored days each, with the warm-up and warm-down days.

**Size.** 950 briefs, aiming at 800 or more scenarios after the exclusions (O2 accepted 98.7% of its briefs after one repair; about 12% of round two's accepted items were left out as bridging or near duplicates). Every scenario that is accepted and not left out is scored; none is dropped afterward for any reason. If fewer than 400 remain, a second plan with seed 502 tops the set up before anything is scored.

**Why that size.** On validation (1,091 scenarios, first artifact, `results/neural/val/evaluation.json`), the 95% interval of TracerNet minus a baseline at 6 alerts per shift had a half-width of 0.53 to 1.17 minutes, the widest against the quantile rules. Half-widths scale with one over the square root of the scenario count:

| Scenarios | 3-2-1 against the local refit | 3-2-1 against the global quantile rule | Equal against the local refit | Equal against the global quantile rule |
|---|---|---|---|---|
| 400 | 0.97 | 1.93 | 0.88 | 1.68 |
| 800 | 0.69 | 1.37 | 0.62 | 1.19 |

The requirement fixed here is a projected half-width of at most 2 minutes for every difference, which holds from 374 scenarios up. 800 meets it with room and matches `test`'s 782. The smallest validation lead, equal weights against the local refit, was 4.2 minutes, about six times the projected half-width at 800.

**Cost and budget.** The OpenAI account's cap rises by exactly $60, dated November 13, 2026, to $136.43. Of that, $10 stays unspent as a reserve. T1's cap is $20. A dry run comes first. At O2's measured cost per brief, repairs included ($43.87 for 6,101 briefs), 950 briefs cost about $6.83. If the dry run's expected cost were above the money left less the reserve, the set would shrink to the largest size that fits, never below the 374 the width requirement needs, and the reason would be recorded. Every request goes through the ledger with a worst-case reservation, as before.

**Isolation.** The truth sits in a separate store that only `src/pipeline/evaluate.py` reads, as for `test`. The fresh set is generated only after section 6 is filled and merged, so no choice in Phase 2 can see it.

## 6. The model under test

Filled in at the end of Phase 2, before the fresh set is generated.

| Field | Value |
|---|---|
| Commit that freezes the model | to be filled |
| Artifact | to be filled |
| Artifact manifest SHA-256 | to be filled |
| Member weights SHA-256 | to be filled |
| Calibration and site map | to be filled |
| Training store and splits | to be filled |

## 7. How Phase 2 chooses the model

Phase 2 uses the train split to fit and the validation split to choose. It never reads `test`, `test_ood` or the fresh set.

- **Candidates.** Each candidate is trained on train (plus any new train scenarios, below) and scored on validation. `docs/neural/phase2-log.md` lists every candidate, what it changed and its validation numbers, including the ones not chosen.
- **Choice.** The candidate with the highest validation capped mean warning at 6 alerts per shift, averaged over the 3-2-1 and equal weights, wins. Within 0.2 minutes of the best, the simpler candidate wins (fewer members, then fewer changes from the first artifact). If validation scoring of every candidate on all 1,091 validation scenarios does not fit the compute available, every candidate is scored on the same fixed subset: the validation selection share (`val_sel`, the 25% of validation scenarios with the lowest salted hash), which the log says.
- **Site map.** On by default. Off only if, on validation, it raises TracerNet's Brier score at 30 or 60 minutes by more than 0.002 against the calibrated output without it. The choice goes in section 6.
- **New training data.** Phase 2 may add laboratory weeks from the same generator to fill the coverage cells that fell short of their targets (job O4, train and validation only, with the exclusion rules of section 5 against `test` and `test_ood` families). None of them can enter the fresh set, which is planned after them and left out from anything within 1.5 δ of the library they join.

## 8. The one look

1. This file merges.
2. Phase 2 freezes the model, fills section 6, and merges.
3. T1 is planned, dry-run, generated and frozen with its manifest (`datasets/scenarios-v2-fresh.*`), and committed.
4. The fresh set is simulated, stored and scored once, on HiPerGator's default partition (CPU inference, bit-reproducible on one processor type, as for `test`). The first evaluation writes `results/neural/fresh/test_lock.json` with the artifact, calibration, configuration, fresh library and store hashes and the git blob SHAs of `neural_eval.py` and `evaluate.py`.
5. The rule is applied as written, and `docs/neural/results-v2.md` gives the numbers, the verdict and anything surprising.

There is no relock for the fresh set and no second try. If a run fails before writing its result (out of memory, a crash), it may be resumed on the same locked inputs with nothing changed between attempts, and every attempt is logged. Any later analysis of the fresh set is labeled exploratory and never changes the verdict.

## 9. What follows

**Pass.** TracerNet becomes Tracer's primary risk model. The README, the deck, the explainer, the phone page and the demo name it primary; the self-playing 3D demo and Beat Tracer show its alerts; the analytic model becomes a baseline.

**Fail.** TracerNet stays a challenger and the analytic model stays primary. `docs/neural/results-v2.md` names each condition that failed, with its numbers, and the same places are updated with the new numbers.

Either way the rule does not change after the look, and the result is published.

## 10. What was known when this was written

- The first test look is public (`results/neural/REPORT.md`). On `test`, TracerNet's capped mean warning led every baseline at 6 alerts per shift (23.9 minutes against 7.4 to 13.7 at 3-2-1 weights). The capped mean was proposed as the next measure only after that (`results/neural/followup-2026-11-13.md` §3), so `test` cannot confirm it. That is why the claim rests on the fresh set alone.
- G3's per-alert form was chosen after seeing validation, for the reason in section 4.
- The 120-minute cap is spec §8.4's, fixed on November 8 before any validation or test result existed.
- Every number is from a simulator whose parameters were written by language models. None comes from a laboratory, and nothing had clinical review.
