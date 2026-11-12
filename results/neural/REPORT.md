# TracerNet against the baselines: the v2 report

**Verdict on `test` at 6 alerts per shift, a chosen budget: R1 failed, R2 held, R3 held, R4 failed. The analytic model stays primary because R1 failed. R4 also failed, mostly because the baselines leave part of the 6-alert budget unused (the window rule alerts on nothing in 268 of 782 scenarios).** Under spec §8.6 the primary model follows R1 to R3, so R1 alone decides it. R1 (at least 5 minutes more warning than each baseline, with the interval's lower end above zero): TracerNet's and every baseline's median warning over all breaches was 0.0 min at 3-2-1 and equal weights (every difference 0.0, interval low 0.0). A missed breach counts as 0 minutes (spec §8.4), and at this budget no method catches half the breaches (best catch rate 39.2%), so every median is 0 and R1 cannot separate the methods; the pre-specified capped mean warning does (TracerNet 23.9 min against 7.4 to 13.7 for R1's three baselines at 3-2-1 weights; section 1). R2 (deficit in never-received specimens alerted below 2 points) held at both weightings: at 3-2-1 weights TracerNet alerts on a share of never-received specimens 9.7 points above the window rule's, 8.1 above the global refit's and 3.5 above the local refit's; at equal weights it is even with all three, within 1 point (largest deficit 0.0 points, interval high 0.2). R3 (Brier no worse than the better analytic refit's by more than 0.01): at 30 min TracerNet 0.0473 against 0.0771 (excess -0.0299); at 60 min TracerNet 0.0312 against 0.0427 (excess -0.0116). R4 (realized alerts within 10% of each baseline's): the largest relative difference was 56.2% against window rule at 3-2-1 weights, so the comparison is reported as unequal and R1 is repeated at matched volume below. Every number in this report comes from synthetic data: laboratory weeks designed by language models and simulated by this project's own code, with no clinical review. They describe the simulator, not any laboratory.

**Read with the verdict:**

- The negative control flags leakage: on `control_v1world`, where no context exists, TracerNet's median warning leads the local analytic refit by 10 minutes (95% interval 5 to 20). Flagged, explained, not resolved (sections 5 and 10).
- TracerNet's calibration does not carry over to `legacy_v1`: its Brier score at 60 minutes of silence is 0.216 against the analytic model's 0.182 (sections 5 and 10).
- The scenario library misses its coverage targets in 20 of 32 stratum-by-archetype cells (section 9).
- Training was unstable: single-epoch validation spikes reached val_sel 1,444 against about 0.082, and each member kept its best validation epoch (section 10).
- The explanations fail their deletion check: the top three factor groups beat three random groups in 68.2% of test alerts, against the required 70% (section 7).

## 1. Verdict against R1 to R4

The pre-registered rule (spec §8.6), quoted verbatim:

> TracerNet is declared better than the baselines only if all four hold on `test` at 6 alerts per shift.
>
> **R1. Warning.** Its median warning over all breaches, a miss counting as zero, exceeds that of the window-aware rule, of the analytic model refit globally and of the analytic model refit locally by at least 5 minutes each, with the lower end of the 95% paired cluster-bootstrap interval of every difference above zero, at both the default 3-2-1 and the equal urgency weights.
>
> **R2. Losses.** Its share of never-received specimens alerted before the window closes is not lower than any of those baselines' by more than 2 percentage points (upper end of the 95% interval of the deficit below 2 points).
>
> **R3. Calibration.** Its Brier score at 30 and at 60 minutes of silence is no worse than the better analytic refit's by more than 0.01 (the rule of `docs/architecture.md` §5.2.1).
>
> **R4. Equal alerts.** Its realized alerts per shift on the scored replicates are within 10% of each baseline's. If not, the comparison is reported as unequal, and R1 is repeated at matched realized volume (knobs re-solved on the scored replicates from alert volume alone, for every method) as a sensitivity check, not a replacement.
>
> If R1 to R3 hold, TracerNet becomes the primary risk model. Otherwise the analytic model stays primary and TracerNet is reported as a challenger result.

### R1. Warning

| Weights | Baseline | TracerNet (min) | Baseline (min) | Difference (min) | Interval low (min) | Held |
|---|---|---|---|---|---|---|
| 3-2-1 | window_rule | 0.0 | 0.0 | 0.0 | 0.0 | failed |
| 3-2-1 | analytic_global | 0.0 | 0.0 | 0.0 | 0.0 | failed |
| 3-2-1 | analytic_local | 0.0 | 0.0 | 0.0 | 0.0 | failed |
| equal | window_rule | 0.0 | 0.0 | 0.0 | 0.0 | failed |
| equal | analytic_global | 0.0 | 0.0 | 0.0 | 0.0 | failed |
| equal | analytic_local | 0.0 | 0.0 | 0.0 | 0.0 | failed |

**Why R1 cannot separate the methods here.** A missed breach counts as 0 minutes of warning (spec §8.4). `test` has 6.4 breaches per shift (316,182 in 49,266 shifts) against a budget of 6 alerts, and the best catch rate is 39.2% (TracerNet, 3-2-1 weights), so more than half of every method's breaches count as 0 and every median is 0. The build log predicted this on November 8 at 11:42 PM EST, before any validation or test result existed (docs/neural/build-log.md, "a secondary analysis"), and R1 stays as written. The capped mean warning, fixed then as the non-binding companion, does separate them: at 3-2-1 weights TracerNet 23.9 min against 7.4 to 13.7 min for the baselines (window rule 7.4, global refit 12.0, local refit 13.7); TracerNet minus each baseline is 16.5 min (15.4 to 17.6) against the window rule, 11.8 min (11.0 to 12.7) against the global refit, 10.1 min (9.4 to 10.9) against the local refit. The secondary view below gives it at every budget, with R1 at 30 alerts per 100 specimens. Rung selection's warning check (spec §7.9) compared rung C's validation median warning with rung A's, 0.0 against 0.0 min, so it tested nothing.

### R2. Losses

| Weights | Baseline | TracerNet alerted | Baseline alerted | Deficit | Interval high | Held |
|---|---|---|---|---|---|---|
| 3-2-1 | window_rule | 48.2% | 38.5% | -9.7% | -8.0% | held |
| 3-2-1 | analytic_global | 48.2% | 40.1% | -8.1% | -6.4% | held |
| 3-2-1 | analytic_local | 48.2% | 44.7% | -3.5% | -2.7% | held |
| equal | window_rule | 39.0% | 38.5% | -0.5% | -0.2% | held |
| equal | analytic_global | 39.0% | 39.0% | 0.0% | 0.2% | held |
| equal | analytic_local | 39.0% | 38.9% | -0.1% | 0.1% | held |

### R3. Calibration

| Silence (min) | TracerNet Brier | Better analytic refit's Brier | Excess | Held |
|---|---|---|---|---|
| 30 | 0.0473 | 0.0771 | -0.0299 | held |
| 60 | 0.0312 | 0.0427 | -0.0116 | held |

### R4. Equal alerts

| Weights | Baseline | TracerNet per shift | Baseline per shift | Relative difference | Held |
|---|---|---|---|---|---|
| 3-2-1 | window_rule | 5.83 | 3.74 | 56.2% | failed |
| 3-2-1 | analytic_global | 5.83 | 5.53 | 5.6% | held |
| 3-2-1 | analytic_local | 5.83 | 5.63 | 3.7% | held |
| equal | window_rule | 5.07 | 3.74 | 35.7% | failed |
| equal | analytic_global | 5.07 | 3.96 | 27.8% | failed |
| equal | analytic_local | 5.07 | 4.00 | 26.9% | failed |

R1 repeated at matched realized volume (a sensitivity check, not a replacement): failed; TracerNet's and every baseline's median warning over all breaches was 0.0 min at 3-2-1 and equal weights (every difference 0.0, interval low 0.0).

### Scenarios with no in-budget knob

In a scenario with more breaches per shift than the budget has alerts, a method whose risk reaches 1 when the window closes has no knob that both alerts and stays within 6 alerts per shift, so it alerts on nothing there. Counted on test, beside R1:

| Method | Weights | Scenarios with no in-budget knob |
|---|---|---|
| analytic_global | 3-2-1 | 11 of 782 |
| analytic_global | equal | 268 of 782 |
| analytic_global | 9-3-1 | 11 of 782 |
| analytic_local | 3-2-1 | 11 of 782 |
| analytic_local | equal | 268 of 782 |
| analytic_local | 9-3-1 | 11 of 782 |
| window_rule | none | 268 of 782 |
| quantile_local | none | 0 of 782 |
| quantile_global | none | 0 of 782 |
| tracernet | 3-2-1 | 0 of 782 |
| tracernet | equal | 129 of 782 |
| tracernet | 9-3-1 | 0 of 782 |

### Secondary view (non-binding)

Fixed before any validation or test result existed (docs/neural/build-log.md, "a secondary analysis"): R1 to R3 recomputed at 30 alerts per 100 specimens with the same margins and intervals, and the capped mean warning at every budget. It never replaces the rule above.

At 30 per 100 specimens on `test`: R1 failed, R2 held, R3 held. R1: at 3-2-1 weights TracerNet's median warning over all breaches was 55.0 min against window rule 40.0 (difference 15.0, interval low 15.0), analytic global 40.0 (difference 15.0, interval low 15.0), analytic local 55.0 (difference 0.0, interval low 0.0); at equal weights TracerNet's median warning over all breaches was 55.0 min against window rule 40.0 (difference 15.0, interval low 15.0), analytic global 45.0 (difference 10.0, interval low 10.0), analytic local 55.0 (difference 0.0, interval low 0.0).

Capped mean warning (minutes, each warning capped at 120) at every budget, on test:

| Method | Weights | 1/shift | 2/shift | 4/shift | 6/shift | 5/100 | 10/100 | 30/100 |
|---|---|---|---|---|---|---|---|---|
| analytic_global | 3-2-1 | 1.4 | 3.5 | 7.5 | 12.0 | 8.8 | 17.3 | 48.3 |
| analytic_global | equal | 0.4 | 1.2 | 4.2 | 7.6 | 2.8 | 9.8 | 50.2 |
| analytic_global | 9-3-1 | 1.5 | 3.8 | 8.1 | 12.2 | 10.0 | 17.7 | 44.9 |
| analytic_local | 3-2-1 | 1.5 | 3.7 | 8.4 | 13.7 | 10.3 | 20.8 | 53.5 |
| analytic_local | equal | 0.4 | 1.3 | 4.3 | 8.0 | 2.9 | 9.9 | 56.1 |
| analytic_local | 9-3-1 | 1.7 | 4.1 | 9.1 | 13.8 | 11.4 | 20.3 | 49.2 |
| window_rule | none | 0.4 | 1.2 | 4.0 | 7.4 | 2.7 | 9.4 | 47.4 |
| quantile_local | none | 4.3 | 4.6 | 5.4 | 6.3 | 5.1 | 6.6 | 16.1 |
| quantile_global | none | 4.2 | 4.5 | 5.1 | 5.8 | 4.9 | 6.1 | 15.2 |
| tracernet | 3-2-1 | 2.9 | 7.5 | 16.1 | 23.9 | 19.2 | 32.6 | 64.2 |
| tracernet | equal | 0.6 | 2.1 | 7.4 | 12.5 | 4.4 | 19.9 | 64.1 |
| tracernet | 9-3-1 | 3.1 | 7.9 | 15.9 | 23.1 | 19.5 | 31.1 | 63.1 |

Recommendations when the rule fails (spec §8.9) are in section 10. Every test run, with its reason, is listed in section 11.

## 2. Headline table

`test`: 4,047,933 scored specimens with a clock in 782 scenarios, 316,182 breaches (17,489 never received), 49,266 shifts. Knobs are set on each scenario's history replicate from alert volume alone; intervals are the paired cluster bootstrap (2,000 resamples). Every budget and every weighting is shown.

### 1 alert per shift

| Method | Weights | Alerts per shift | Alerts per 100 | Alerts | Breaches | Caught | Catch rate | Median warning, all breaches (min) | 95% interval | Median warning, caught (min) | Caught with 15 min or more | False alerts | False per shift | Never received | Missed losses | Missed-loss rate | Capped mean warning (min) | 95% interval  |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| analytic_global | 3-2-1 | 0.67 | 0.8 | 32,801 | 316,182 | 15,681 | 5.0% | 0.0 | 0.0 to 0.0 | 25.0 | 3.9% | 15,004 | 0.30 | 17,489 | 15,680 | 89.7% | 1.4 | 1.2 to 1.6 |
| analytic_global | equal | 0.17 | 0.2 | 8,617 | 316,182 | 4,540 | 1.4% | 0.0 | 0.0 to 0.0 | 15.0 | 0.8% | 3,781 | 0.08 | 17,489 | 16,705 | 95.5% | 0.4 | 0.3 to 0.5 |
| analytic_global | 9-3-1 | 0.73 | 0.9 | 35,740 | 316,182 | 12,510 | 4.0% | 0.0 | 0.0 to 0.0 | 35.0 | 3.2% | 21,997 | 0.45 | 17,489 | 16,093 | 92.0% | 1.5 | 1.4 to 1.7 |
| analytic_local | 3-2-1 | 0.64 | 0.8 | 31,589 | 316,182 | 17,137 | 5.4% | 0.0 | 0.0 to 0.0 | 15.0 | 3.6% | 11,999 | 0.24 | 17,489 | 15,473 | 88.5% | 1.5 | 1.3 to 1.7 |
| analytic_local | equal | 0.17 | 0.2 | 8,490 | 316,182 | 4,476 | 1.4% | 0.0 | 0.0 to 0.0 | 15.0 | 0.7% | 3,654 | 0.07 | 17,489 | 16,712 | 95.6% | 0.4 | 0.3 to 0.5 |
| analytic_local | 9-3-1 | 0.69 | 0.8 | 34,210 | 316,182 | 14,120 | 4.5% | 0.0 | 0.0 to 0.0 | 30.0 | 3.4% | 18,198 | 0.37 | 17,489 | 15,814 | 90.4% | 1.7 | 1.5 to 1.9 |
| window_rule | none | 0.16 | 0.2 | 7,901 | 316,182 | 4,121 | 1.3% | 0.0 | 0.0 to 0.0 | 10.0 | 0.6% | 3,065 | 0.06 | 17,489 | 16,732 | 95.7% | 0.4 | 0.3 to 0.5 |
| quantile_local | none | 1.58 | 1.9 | 78,023 | 316,182 | 14,219 | 4.5% | 0.0 | 0.0 to 0.0 | 1,300.0 | 4.0% | 62,660 | 1.27 | 17,489 | 6,366 | 36.4% | 4.3 | 4.0 to 4.5 |
| quantile_global | none | 1.05 | 1.3 | 51,880 | 316,182 | 13,089 | 4.1% | 0.0 | 0.0 to 0.0 | 1,295.0 | 3.8% | 37,947 | 0.77 | 17,489 | 6,438 | 36.8% | 4.2 | 4.0 to 4.4 |
| tracernet | 3-2-1 | 0.85 | 1.0 | 41,899 | 316,182 | 18,857 | 6.0% | 0.0 | 0.0 to 0.0 | 35.0 | 4.5% | 10,148 | 0.21 | 17,489 | 15,363 | 87.8% | 2.9 | 2.6 to 3.3 |
| tracernet | equal | 0.33 | 0.4 | 16,249 | 316,182 | 4,561 | 1.4% | 0.0 | 0.0 to 0.0 | 20.0 | 1.0% | 3,801 | 0.08 | 17,489 | 16,710 | 95.5% | 0.6 | 0.4 to 0.7 |
| tracernet | 9-3-1 | 0.89 | 1.1 | 43,790 | 316,182 | 16,250 | 5.1% | 0.0 | 0.0 to 0.0 | 55.0 | 4.4% | 15,822 | 0.32 | 17,489 | 15,641 | 89.4% | 3.1 | 2.7 to 3.5 |

### 2 alerts per shift

| Method | Weights | Alerts per shift | Alerts per 100 | Alerts | Breaches | Caught | Catch rate | Median warning, all breaches (min) | 95% interval | Median warning, caught (min) | Caught with 15 min or more | False alerts | False per shift | Never received | Missed losses | Missed-loss rate | Capped mean warning (min) | 95% interval  |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| analytic_global | 3-2-1 | 1.58 | 1.9 | 77,663 | 316,182 | 36,672 | 11.6% | 0.0 | 0.0 to 0.0 | 25.0 | 9.1% | 38,038 | 0.77 | 17,489 | 14,290 | 81.7% | 3.5 | 3.1 to 3.9 |
| analytic_global | equal | 0.64 | 0.8 | 31,489 | 316,182 | 14,781 | 4.7% | 0.0 | 0.0 to 0.0 | 15.0 | 2.8% | 15,424 | 0.31 | 17,489 | 15,554 | 88.9% | 1.2 | 1.1 to 1.5 |
| analytic_global | 9-3-1 | 1.72 | 2.1 | 84,923 | 316,182 | 28,374 | 9.0% | 0.0 | 0.0 to 0.0 | 35.0 | 7.3% | 54,866 | 1.11 | 17,489 | 15,203 | 86.9% | 3.8 | 3.4 to 4.2 |
| analytic_local | 3-2-1 | 1.54 | 1.9 | 75,990 | 316,182 | 38,856 | 12.3% | 0.0 | 0.0 to 0.0 | 20.0 | 9.3% | 34,022 | 0.69 | 17,489 | 14,041 | 80.3% | 3.7 | 3.4 to 4.2 |
| analytic_local | equal | 0.64 | 0.8 | 31,716 | 316,182 | 14,671 | 4.6% | 0.0 | 0.0 to 0.0 | 15.0 | 2.9% | 15,651 | 0.32 | 17,489 | 15,568 | 89.0% | 1.3 | 1.1 to 1.5 |
| analytic_local | 9-3-1 | 1.69 | 2.1 | 83,046 | 316,182 | 32,661 | 10.3% | 0.0 | 0.0 to 0.0 | 30.0 | 8.2% | 47,879 | 0.97 | 17,489 | 14,659 | 83.8% | 4.1 | 3.7 to 4.5 |
| window_rule | none | 0.59 | 0.7 | 29,307 | 316,182 | 13,616 | 4.3% | 0.0 | 0.0 to 0.0 | 15.0 | 2.6% | 13,242 | 0.27 | 17,489 | 15,600 | 89.2% | 1.2 | 1.0 to 1.4 |
| quantile_local | none | 2.59 | 3.2 | 127,570 | 316,182 | 18,406 | 5.8% | 0.0 | 0.0 to 0.0 | 1,190.0 | 4.7% | 106,734 | 2.17 | 17,489 | 5,885 | 33.6% | 4.6 | 4.3 to 4.8 |
| quantile_global | none | 2.04 | 2.5 | 100,659 | 316,182 | 16,277 | 5.1% | 0.0 | 0.0 to 0.0 | 1,245.0 | 4.4% | 82,112 | 1.67 | 17,489 | 6,096 | 34.9% | 4.5 | 4.2 to 4.7 |
| tracernet | 3-2-1 | 1.80 | 2.2 | 88,526 | 316,182 | 42,408 | 13.4% | 0.0 | 0.0 to 0.0 | 55.0 | 11.0% | 27,781 | 0.56 | 17,489 | 13,783 | 78.8% | 7.5 | 6.8 to 8.3 |
| tracernet | equal | 1.13 | 1.4 | 55,589 | 316,182 | 15,053 | 4.8% | 0.0 | 0.0 to 0.0 | 40.0 | 3.6% | 15,863 | 0.32 | 17,489 | 15,559 | 89.0% | 2.1 | 1.8 to 2.5 |
| tracernet | 9-3-1 | 1.85 | 2.3 | 91,323 | 316,182 | 38,189 | 12.1% | 0.0 | 0.0 to 0.0 | 55.0 | 10.7% | 39,323 | 0.80 | 17,489 | 14,223 | 81.3% | 7.9 | 7.1 to 8.7 |

### 4 alerts per shift

| Method | Weights | Alerts per shift | Alerts per 100 | Alerts | Breaches | Caught | Catch rate | Median warning, all breaches (min) | 95% interval | Median warning, caught (min) | Caught with 15 min or more | False alerts | False per shift | Never received | Missed losses | Missed-loss rate | Capped mean warning (min) | 95% interval  |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| analytic_global | 3-2-1 | 3.46 | 4.2 | 170,681 | 316,182 | 71,114 | 22.5% | 0.0 | 0.0 to 0.0 | 25.0 | 18.3% | 93,574 | 1.90 | 17,489 | 12,337 | 70.5% | 7.5 | 6.8 to 8.3 |
| analytic_global | equal | 2.22 | 2.7 | 109,251 | 316,182 | 46,095 | 14.6% | 0.0 | 0.0 to 0.0 | 20.0 | 10.1% | 57,311 | 1.16 | 17,489 | 12,546 | 71.7% | 4.2 | 3.7 to 4.8 |
| analytic_global | 9-3-1 | 3.68 | 4.5 | 181,121 | 316,182 | 50,867 | 16.1% | 0.0 | 0.0 to 0.0 | 50.0 | 13.9% | 126,628 | 2.57 | 17,489 | 14,007 | 80.1% | 8.1 | 7.4 to 8.9 |
| analytic_local | 3-2-1 | 3.46 | 4.2 | 170,443 | 316,182 | 75,751 | 24.0% | 0.0 | 0.0 to 0.0 | 25.0 | 19.2% | 88,835 | 1.80 | 17,489 | 11,609 | 66.4% | 8.4 | 7.7 to 9.3 |
| analytic_local | equal | 2.24 | 2.7 | 110,391 | 316,182 | 46,457 | 14.7% | 0.0 | 0.0 to 0.0 | 20.0 | 10.1% | 58,451 | 1.19 | 17,489 | 12,577 | 71.9% | 4.3 | 3.8 to 5.0 |
| analytic_local | 9-3-1 | 3.67 | 4.5 | 180,673 | 316,182 | 59,394 | 18.8% | 0.0 | 0.0 to 0.0 | 40.0 | 15.7% | 117,262 | 2.38 | 17,489 | 12,981 | 74.2% | 9.1 | 8.3 to 10.0 |
| window_rule | none | 2.07 | 2.5 | 101,857 | 316,182 | 43,134 | 13.6% | 0.0 | 0.0 to 0.0 | 20.0 | 9.4% | 49,917 | 1.01 | 17,489 | 12,661 | 72.4% | 4.0 | 3.5 to 4.6 |
| quantile_local | none | 4.68 | 5.7 | 230,739 | 316,182 | 29,142 | 9.2% | 0.0 | 0.0 to 0.0 | 35.0 | 6.9% | 197,146 | 4.00 | 17,489 | 5,269 | 30.1% | 5.4 | 5.1 to 5.7 |
| quantile_global | none | 4.04 | 4.9 | 199,197 | 316,182 | 25,374 | 8.0% | 0.0 | 0.0 to 0.0 | 50.0 | 6.0% | 169,422 | 3.44 | 17,489 | 5,501 | 31.5% | 5.1 | 4.8 to 5.4 |
| tracernet | 3-2-1 | 3.88 | 4.7 | 191,136 | 316,182 | 86,160 | 27.3% | 0.0 | 0.0 to 0.0 | 55.0 | 23.5% | 77,024 | 1.56 | 17,489 | 11,134 | 63.7% | 16.1 | 14.7 to 17.5 |
| tracernet | equal | 2.97 | 3.6 | 146,238 | 316,182 | 48,617 | 15.4% | 0.0 | 0.0 to 0.0 | 55.0 | 12.7% | 58,921 | 1.20 | 17,489 | 12,558 | 71.8% | 7.4 | 6.4 to 8.5 |
| tracernet | 9-3-1 | 3.88 | 4.7 | 191,073 | 316,182 | 75,039 | 23.7% | 0.0 | 0.0 to 0.0 | 55.0 | 21.5% | 93,474 | 1.90 | 17,489 | 12,022 | 68.7% | 15.9 | 14.7 to 17.3 |

### 6 alerts per shift

| Method | Weights | Alerts per shift | Alerts per 100 | Alerts | Breaches | Caught | Catch rate | Median warning, all breaches (min) | 95% interval | Median warning, caught (min) | Caught with 15 min or more | False alerts | False per shift | Never received | Missed losses | Missed-loss rate | Capped mean warning (min) | 95% interval  |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| analytic_global | 3-2-1 | 5.53 | 6.7 | 272,259 | 316,182 | 105,086 | 33.2% | 0.0 | 0.0 to 0.0 | 25.0 | 28.2% | 161,128 | 3.27 | 17,489 | 10,479 | 59.9% | 12.0 | 11.0 to 13.1 |
| analytic_global | equal | 3.96 | 4.8 | 195,323 | 316,182 | 74,123 | 23.4% | 0.0 | 0.0 to 0.0 | 25.0 | 18.1% | 117,073 | 2.38 | 17,489 | 10,673 | 61.0% | 7.6 | 6.7 to 8.6 |
| analytic_global | 9-3-1 | 5.73 | 7.0 | 282,091 | 316,182 | 76,583 | 24.2% | 0.0 | 0.0 to 0.0 | 50.0 | 21.0% | 202,052 | 4.10 | 17,489 | 12,844 | 73.4% | 12.2 | 11.3 to 13.2 |
| analytic_local | 3-2-1 | 5.63 | 6.8 | 277,208 | 316,182 | 111,589 | 35.3% | 0.0 | 0.0 to 0.0 | 30.0 | 29.9% | 159,891 | 3.25 | 17,489 | 9,672 | 55.3% | 13.7 | 12.7 to 14.9 |
| analytic_local | equal | 4.00 | 4.9 | 196,839 | 316,182 | 74,384 | 23.5% | 0.0 | 0.0 to 0.0 | 25.0 | 18.6% | 118,589 | 2.41 | 17,489 | 10,693 | 61.1% | 8.0 | 7.0 to 9.1 |
| analytic_local | 9-3-1 | 5.70 | 6.9 | 280,727 | 316,182 | 85,395 | 27.0% | 0.0 | 0.0 to 0.0 | 45.0 | 23.3% | 190,584 | 3.87 | 17,489 | 11,429 | 65.3% | 13.8 | 12.7 to 14.9 |
| window_rule | none | 3.74 | 4.5 | 184,029 | 316,182 | 72,501 | 22.9% | 0.0 | 0.0 to 0.0 | 25.0 | 17.3% | 105,779 | 2.15 | 17,489 | 10,756 | 61.5% | 7.4 | 6.4 to 8.4 |
| quantile_local | none | 6.68 | 8.1 | 329,312 | 316,182 | 40,561 | 12.8% | 0.0 | 0.0 to 0.0 | 25.0 | 9.4% | 282,797 | 5.74 | 17,489 | 4,797 | 27.4% | 6.3 | 5.9 to 6.8 |
| quantile_global | none | 6.02 | 7.3 | 296,353 | 316,182 | 34,882 | 11.0% | 0.0 | 0.0 to 0.0 | 25.0 | 8.2% | 255,329 | 5.18 | 17,489 | 5,040 | 28.8% | 5.8 | 5.5 to 6.2 |
| tracernet | 3-2-1 | 5.83 | 7.1 | 287,374 | 316,182 | 124,028 | 39.2% | 0.0 | 0.0 to 0.0 | 55.0 | 35.2% | 140,822 | 2.86 | 17,489 | 9,054 | 51.8% | 23.9 | 22.3 to 25.6 |
| tracernet | equal | 5.07 | 6.2 | 249,704 | 316,182 | 75,958 | 24.0% | 0.0 | 0.0 to 0.0 | 55.0 | 21.3% | 118,867 | 2.41 | 17,489 | 10,677 | 61.0% | 12.5 | 11.0 to 14.1 |
| tracernet | 9-3-1 | 5.93 | 7.2 | 291,943 | 316,182 | 109,282 | 34.6% | 0.0 | 0.0 to 0.0 | 55.0 | 31.9% | 159,622 | 3.24 | 17,489 | 10,028 | 57.3% | 23.1 | 21.6 to 24.7 |

Per-100-specimen budgets (the secondary view of spec §8.3; volumes differ about thirtyfold):

### 5 alerts per 100 specimens

| Method | Weights | Alerts per shift | Alerts per 100 | Alerts | Breaches | Caught | Catch rate | Median warning, all breaches (min) | 95% interval | Median warning, caught (min) | Caught with 15 min or more | False alerts | False per shift | Never received | Missed losses | Missed-loss rate | Capped mean warning (min) | 95% interval  |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| analytic_global | 3-2-1 | 3.67 | 4.5 | 180,610 | 316,182 | 82,816 | 26.2% | 0.0 | 0.0 to 0.0 | 25.0 | 22.3% | 90,429 | 1.84 | 17,489 | 11,675 | 66.8% | 8.8 | 8.3 to 9.3 |
| analytic_global | equal | 1.44 | 1.8 | 71,019 | 316,182 | 32,171 | 10.2% | 0.0 | 0.0 to 0.0 | 15.0 | 6.2% | 32,978 | 0.67 | 17,489 | 12,390 | 70.8% | 2.8 | 2.3 to 3.4 |
| analytic_global | 9-3-1 | 3.89 | 4.7 | 191,523 | 316,182 | 61,128 | 19.3% | 0.0 | 0.0 to 0.0 | 65.0 | 16.7% | 126,785 | 2.57 | 17,489 | 13,672 | 78.2% | 10.0 | 9.6 to 10.5 |
| analytic_local | 3-2-1 | 3.65 | 4.4 | 179,896 | 316,182 | 92,020 | 29.1% | 0.0 | 0.0 to 0.0 | 25.0 | 23.6% | 83,553 | 1.70 | 17,489 | 10,577 | 60.5% | 10.3 | 9.7 to 10.9 |
| analytic_local | equal | 1.47 | 1.8 | 72,187 | 316,182 | 31,367 | 9.9% | 0.0 | 0.0 to 0.0 | 15.0 | 5.8% | 34,146 | 0.69 | 17,489 | 12,460 | 71.2% | 2.9 | 2.4 to 3.4 |
| analytic_local | 9-3-1 | 3.85 | 4.7 | 189,876 | 316,182 | 68,219 | 21.6% | 0.0 | 0.0 to 0.0 | 55.0 | 18.7% | 116,340 | 2.36 | 17,489 | 12,373 | 70.7% | 11.4 | 10.8 to 12.0 |
| window_rule | none | 1.35 | 1.6 | 66,312 | 316,182 | 29,371 | 9.3% | 0.0 | 0.0 to 0.0 | 15.0 | 5.5% | 28,271 | 0.57 | 17,489 | 12,502 | 71.5% | 2.7 | 2.2 to 3.2 |
| quantile_local | none | 4.81 | 5.8 | 236,727 | 316,182 | 26,504 | 8.4% | 0.0 | 0.0 to 0.0 | 45.0 | 5.8% | 204,296 | 4.15 | 17,489 | 5,217 | 29.8% | 5.1 | 4.8 to 5.4 |
| quantile_global | none | 4.13 | 5.0 | 203,528 | 316,182 | 24,969 | 7.9% | 0.0 | 0.0 to 0.0 | 45.0 | 5.5% | 173,287 | 3.52 | 17,489 | 5,303 | 30.3% | 4.9 | 4.6 to 5.2 |
| tracernet | 3-2-1 | 3.80 | 4.6 | 187,375 | 316,182 | 99,505 | 31.5% | 0.0 | 0.0 to 0.0 | 55.0 | 26.5% | 63,193 | 1.28 | 17,489 | 10,246 | 58.6% | 19.2 | 18.3 to 20.1 |
| tracernet | equal | 2.77 | 3.4 | 136,571 | 316,182 | 34,248 | 10.8% | 0.0 | 0.0 to 0.0 | 25.0 | 8.2% | 34,229 | 0.69 | 17,489 | 12,428 | 71.1% | 4.4 | 3.6 to 5.2 |
| tracernet | 9-3-1 | 3.91 | 4.8 | 192,688 | 316,182 | 85,970 | 27.2% | 0.0 | 0.0 to 0.0 | 55.0 | 24.8% | 87,794 | 1.78 | 17,489 | 11,200 | 64.0% | 19.5 | 18.6 to 20.3 |

### 10 alerts per 100 specimens

| Method | Weights | Alerts per shift | Alerts per 100 | Alerts | Breaches | Caught | Catch rate | Median warning, all breaches (min) | 95% interval | Median warning, caught (min) | Caught with 15 min or more | False alerts | False per shift | Never received | Missed losses | Missed-loss rate | Capped mean warning (min) | 95% interval  |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| analytic_global | 3-2-1 | 7.23 | 8.8 | 356,002 | 316,182 | 138,391 | 43.8% | 0.0 | 0.0 to 0.0 | 25.0 | 37.2% | 207,976 | 4.22 | 17,489 | 7,810 | 44.7% | 17.3 | 16.6 to 18.1 |
| analytic_global | equal | 5.53 | 6.7 | 272,508 | 316,182 | 127,336 | 40.3% | 0.0 | 0.0 to 0.0 | 15.0 | 26.4% | 137,168 | 2.78 | 17,489 | 6,481 | 37.1% | 9.8 | 8.6 to 11.0 |
| analytic_global | 9-3-1 | 8.08 | 9.8 | 398,134 | 316,182 | 102,067 | 32.3% | 0.0 | 0.0 to 0.0 | 50.0 | 29.1% | 293,080 | 5.95 | 17,489 | 11,868 | 67.9% | 17.7 | 17.0 to 18.3 |
| analytic_local | 3-2-1 | 7.53 | 9.2 | 370,802 | 316,182 | 147,649 | 46.7% | 0.0 | 0.0 to 0.0 | 35.0 | 40.7% | 217,907 | 4.42 | 17,489 | 7,219 | 41.3% | 20.8 | 20.0 to 21.6 |
| analytic_local | equal | 5.60 | 6.8 | 276,121 | 316,182 | 127,479 | 40.3% | 0.0 | 0.0 to 0.0 | 15.0 | 26.9% | 140,781 | 2.86 | 17,489 | 6,516 | 37.3% | 9.9 | 8.7 to 11.1 |
| analytic_local | 9-3-1 | 7.99 | 9.7 | 393,613 | 316,182 | 112,532 | 35.6% | 0.0 | 0.0 to 0.0 | 45.0 | 31.5% | 274,315 | 5.57 | 17,489 | 9,395 | 53.7% | 20.3 | 19.6 to 21.0 |
| window_rule | none | 5.16 | 6.3 | 254,310 | 316,182 | 123,307 | 39.0% | 0.0 | 0.0 to 0.0 | 15.0 | 24.0% | 118,970 | 2.41 | 17,489 | 6,575 | 37.6% | 9.4 | 8.3 to 10.5 |
| quantile_local | none | 8.92 | 10.9 | 439,551 | 316,182 | 51,546 | 16.3% | 0.0 | 0.0 to 0.0 | 15.0 | 10.5% | 378,524 | 7.68 | 17,489 | 3,952 | 22.6% | 6.6 | 6.2 to 7.1 |
| quantile_global | none | 8.17 | 9.9 | 402,534 | 316,182 | 42,293 | 13.4% | 0.0 | 0.0 to 0.0 | 20.0 | 9.2% | 352,004 | 7.14 | 17,489 | 4,376 | 25.0% | 6.1 | 5.7 to 6.5 |
| tracernet | 3-2-1 | 7.77 | 9.5 | 382,995 | 316,182 | 173,801 | 55.0% | 15.0 | 10.0 to 15.0 | 55.0 | 50.0% | 166,643 | 3.38 | 17,489 | 5,792 | 33.1% | 32.6 | 31.3 to 33.9 |
| tracernet | equal | 8.14 | 9.9 | 400,835 | 316,182 | 131,004 | 41.4% | 0.0 | 0.0 to 0.0 | 55.0 | 36.2% | 140,180 | 2.85 | 17,489 | 6,538 | 37.4% | 19.9 | 17.6 to 22.3 |
| tracernet | 9-3-1 | 7.76 | 9.4 | 382,472 | 316,182 | 152,003 | 48.1% | 0.0 | 0.0 to 5.0 | 55.0 | 44.5% | 195,183 | 3.96 | 17,489 | 7,094 | 40.6% | 31.1 | 29.9 to 32.2 |

### 30 alerts per 100 specimens

| Method | Weights | Alerts per shift | Alerts per 100 | Alerts | Breaches | Caught | Catch rate | Median warning, all breaches (min) | 95% interval | Median warning, caught (min) | Caught with 15 min or more | False alerts | False per shift | Never received | Missed losses | Missed-loss rate | Capped mean warning (min) | 95% interval  |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| analytic_global | 3-2-1 | 24.52 | 29.8 | 1,208,055 | 316,182 | 313,647 | 99.2% | 40.0 | 35.0 to 40.0 | 40.0 | 97.8% | 894,297 | 18.15 | 17,489 | 59 | 0.3% | 48.3 | 47.3 to 49.3 |
| analytic_global | equal | 24.46 | 29.8 | 1,205,212 | 316,182 | 312,935 | 99.0% | 45.0 | 45.0 to 45.0 | 45.0 | 98.8% | 891,050 | 18.09 | 17,489 | 24 | 0.1% | 50.2 | 49.0 to 51.3 |
| analytic_global | 9-3-1 | 24.31 | 29.6 | 1,197,783 | 316,182 | 284,766 | 90.1% | 35.0 | 35.0 to 35.0 | 35.0 | 83.6% | 905,978 | 18.39 | 17,489 | 1,318 | 7.5% | 44.9 | 43.8 to 46.0 |
| analytic_local | 3-2-1 | 24.65 | 30.0 | 1,214,335 | 316,182 | 310,552 | 98.2% | 55.0 | 45.0 to 55.0 | 55.0 | 97.0% | 902,793 | 18.32 | 17,489 | 174 | 1.0% | 53.5 | 52.6 to 54.3 |
| analytic_local | equal | 24.65 | 30.0 | 1,214,633 | 316,182 | 314,162 | 99.4% | 55.0 | 55.0 to 55.0 | 55.0 | 99.3% | 900,471 | 18.28 | 17,489 | 19 | 0.1% | 56.1 | 55.1 to 57.0 |
| analytic_local | 9-3-1 | 24.48 | 29.8 | 1,205,995 | 316,182 | 295,224 | 93.4% | 40.0 | 40.0 to 40.0 | 40.0 | 89.2% | 908,961 | 18.45 | 17,489 | 777 | 4.4% | 49.2 | 48.1 to 50.3 |
| window_rule | none | 23.09 | 28.1 | 1,137,524 | 316,182 | 314,162 | 99.4% | 40.0 | 40.0 to 40.0 | 40.0 | 99.0% | 823,362 | 16.71 | 17,489 | 19 | 0.1% | 47.4 | 46.3 to 48.4 |
| quantile_local | none | 25.04 | 30.5 | 1,233,489 | 316,182 | 146,923 | 46.5% | 0.0 | 0.0 to 0.0 | 25.0 | 36.1% | 1,072,262 | 21.76 | 17,489 | 1,791 | 10.2% | 16.1 | 15.2 to 17.1 |
| quantile_global | none | 24.43 | 29.7 | 1,203,645 | 316,182 | 138,440 | 43.8% | 0.0 | 0.0 to 0.0 | 25.0 | 34.2% | 1,039,559 | 21.10 | 17,489 | 1,905 | 10.9% | 15.2 | 14.4 to 16.1 |
| tracernet | 3-2-1 | 24.67 | 30.0 | 1,215,532 | 316,182 | 315,334 | 99.7% | 55.0 | 55.0 to 55.0 | 55.0 | 99.7% | 900,113 | 18.27 | 17,489 | 11 | 0.1% | 64.2 | 63.7 to 64.7 |
| tracernet | equal | 24.68 | 30.0 | 1,215,946 | 316,182 | 314,162 | 99.4% | 55.0 | 55.0 to 55.0 | 55.0 | 99.4% | 899,858 | 18.27 | 17,489 | 19 | 0.1% | 64.1 | 63.0 to 64.8 |
| tracernet | 9-3-1 | 24.63 | 30.0 | 1,213,265 | 316,182 | 313,790 | 99.2% | 55.0 | 55.0 to 55.0 | 55.0 | 98.9% | 898,559 | 18.24 | 17,489 | 60 | 0.3% | 63.1 | 62.5 to 63.8 |

### TracerNet minus each method

Paired bootstrap differences in the median warning over all breaches and in the capped mean warning (minutes), with the share of resamples in which TracerNet is ahead:

| Budget | Weights | Against | Median, all breaches | 95% interval | Capped mean | 95% interval | Ahead (capped mean) |
|---|---|---|---|---|---|---|---|
| 1/shift | 3-2-1 | analytic_global | 0.0 | 0.0 to 0.0 | 1.5 | 1.3 to 1.8 | 100% |
| 1/shift | 3-2-1 | analytic_local | 0.0 | 0.0 to 0.0 | 1.5 | 1.3 to 1.7 | 100% |
| 1/shift | 3-2-1 | window_rule | 0.0 | 0.0 to 0.0 | 2.6 | 2.2 to 2.9 | 100% |
| 1/shift | 3-2-1 | quantile_local | 0.0 | 0.0 to 0.0 | -1.3 | -1.7 to -1.0 | 0% |
| 1/shift | 3-2-1 | quantile_global | 0.0 | 0.0 to 0.0 | -1.3 | -1.6 to -0.9 | 0% |
| 1/shift | equal | analytic_global | 0.0 | 0.0 to 0.0 | 0.2 | 0.1 to 0.2 | 100% |
| 1/shift | equal | analytic_local | 0.0 | 0.0 to 0.0 | 0.2 | 0.1 to 0.3 | 100% |
| 1/shift | equal | window_rule | 0.0 | 0.0 to 0.0 | 0.2 | 0.1 to 0.3 | 100% |
| 1/shift | equal | quantile_local | 0.0 | 0.0 to 0.0 | -3.7 | -3.9 to -3.4 | 0% |
| 1/shift | equal | quantile_global | 0.0 | 0.0 to 0.0 | -3.6 | -3.9 to -3.4 | 0% |
| 1/shift | 9-3-1 | analytic_global | 0.0 | 0.0 to 0.0 | 1.6 | 1.4 to 1.8 | 100% |
| 1/shift | 9-3-1 | analytic_local | 0.0 | 0.0 to 0.0 | 1.4 | 1.2 to 1.6 | 100% |
| 1/shift | 9-3-1 | window_rule | 0.0 | 0.0 to 0.0 | 2.7 | 2.4 to 3.1 | 100% |
| 1/shift | 9-3-1 | quantile_local | 0.0 | 0.0 to 0.0 | -1.2 | -1.5 to -0.8 | 0% |
| 1/shift | 9-3-1 | quantile_global | 0.0 | 0.0 to 0.0 | -1.1 | -1.4 to -0.8 | 0% |
| 2/shift | 3-2-1 | analytic_global | 0.0 | 0.0 to 0.0 | 4.0 | 3.6 to 4.6 | 100% |
| 2/shift | 3-2-1 | analytic_local | 0.0 | 0.0 to 0.0 | 3.8 | 3.4 to 4.2 | 100% |
| 2/shift | 3-2-1 | window_rule | 0.0 | 0.0 to 0.0 | 6.3 | 5.7 to 7.0 | 100% |
| 2/shift | 3-2-1 | quantile_local | 0.0 | 0.0 to 0.0 | 2.9 | 2.3 to 3.7 | 100% |
| 2/shift | 3-2-1 | quantile_global | 0.0 | 0.0 to 0.0 | 3.0 | 2.4 to 3.8 | 100% |
| 2/shift | equal | analytic_global | 0.0 | 0.0 to 0.0 | 0.9 | 0.7 to 1.0 | 100% |
| 2/shift | equal | analytic_local | 0.0 | 0.0 to 0.0 | 0.8 | 0.7 to 1.0 | 100% |
| 2/shift | equal | window_rule | 0.0 | 0.0 to 0.0 | 0.9 | 0.7 to 1.1 | 100% |
| 2/shift | equal | quantile_local | 0.0 | 0.0 to 0.0 | -2.5 | -2.8 to -2.2 | 0% |
| 2/shift | equal | quantile_global | 0.0 | 0.0 to 0.0 | -2.4 | -2.7 to -2.0 | 0% |
| 2/shift | 9-3-1 | analytic_global | 0.0 | 0.0 to 0.0 | 4.1 | 3.6 to 4.6 | 100% |
| 2/shift | 9-3-1 | analytic_local | 0.0 | 0.0 to 0.0 | 3.8 | 3.4 to 4.2 | 100% |
| 2/shift | 9-3-1 | window_rule | 0.0 | 0.0 to 0.0 | 6.7 | 6.0 to 7.4 | 100% |
| 2/shift | 9-3-1 | quantile_local | 0.0 | 0.0 to 0.0 | 3.3 | 2.6 to 4.0 | 100% |
| 2/shift | 9-3-1 | quantile_global | 0.0 | 0.0 to 0.0 | 3.4 | 2.7 to 4.1 | 100% |
| 4/shift | 3-2-1 | analytic_global | 0.0 | 0.0 to 0.0 | 8.5 | 7.8 to 9.4 | 100% |
| 4/shift | 3-2-1 | analytic_local | 0.0 | 0.0 to 0.0 | 7.6 | 6.9 to 8.3 | 100% |
| 4/shift | 3-2-1 | window_rule | 0.0 | 0.0 to 0.0 | 12.0 | 11.0 to 13.1 | 100% |
| 4/shift | 3-2-1 | quantile_local | 0.0 | 0.0 to 0.0 | 10.7 | 9.5 to 12.0 | 100% |
| 4/shift | 3-2-1 | quantile_global | 0.0 | 0.0 to 0.0 | 11.0 | 9.8 to 12.3 | 100% |
| 4/shift | equal | analytic_global | 0.0 | 0.0 to 0.0 | 3.2 | 2.7 to 3.7 | 100% |
| 4/shift | equal | analytic_local | 0.0 | 0.0 to 0.0 | 3.0 | 2.6 to 3.6 | 100% |
| 4/shift | equal | window_rule | 0.0 | 0.0 to 0.0 | 3.4 | 2.9 to 3.9 | 100% |
| 4/shift | equal | quantile_local | 0.0 | 0.0 to 0.0 | 2.0 | 1.2 to 2.9 | 100% |
| 4/shift | equal | quantile_global | 0.0 | 0.0 to 0.0 | 2.3 | 1.5 to 3.3 | 100% |
| 4/shift | 9-3-1 | analytic_global | 0.0 | 0.0 to 0.0 | 7.8 | 7.1 to 8.6 | 100% |
| 4/shift | 9-3-1 | analytic_local | 0.0 | 0.0 to 0.0 | 6.9 | 6.2 to 7.5 | 100% |
| 4/shift | 9-3-1 | window_rule | 0.0 | 0.0 to 0.0 | 11.9 | 10.9 to 13.0 | 100% |
| 4/shift | 9-3-1 | quantile_local | 0.0 | 0.0 to 0.0 | 10.6 | 9.4 to 11.8 | 100% |
| 4/shift | 9-3-1 | quantile_global | 0.0 | 0.0 to 0.0 | 10.9 | 9.7 to 12.1 | 100% |
| 6/shift | 3-2-1 | analytic_global | 0.0 | 0.0 to 0.0 | 11.8 | 11.0 to 12.7 | 100% |
| 6/shift | 3-2-1 | analytic_local | 0.0 | 0.0 to 0.0 | 10.1 | 9.4 to 10.9 | 100% |
| 6/shift | 3-2-1 | window_rule | 0.0 | 0.0 to 0.0 | 16.5 | 15.4 to 17.6 | 100% |
| 6/shift | 3-2-1 | quantile_local | 0.0 | 0.0 to 0.0 | 17.5 | 16.2 to 19.0 | 100% |
| 6/shift | 3-2-1 | quantile_global | 0.0 | 0.0 to 0.0 | 18.1 | 16.6 to 19.5 | 100% |
| 6/shift | equal | analytic_global | 0.0 | 0.0 to 0.0 | 4.9 | 4.2 to 5.7 | 100% |
| 6/shift | equal | analytic_local | 0.0 | 0.0 to 0.0 | 4.5 | 3.9 to 5.2 | 100% |
| 6/shift | equal | window_rule | 0.0 | 0.0 to 0.0 | 5.1 | 4.4 to 5.9 | 100% |
| 6/shift | equal | quantile_local | 0.0 | 0.0 to 0.0 | 6.2 | 4.9 to 7.6 | 100% |
| 6/shift | equal | quantile_global | 0.0 | 0.0 to 0.0 | 6.7 | 5.4 to 8.1 | 100% |
| 6/shift | 9-3-1 | analytic_global | 0.0 | 0.0 to 0.0 | 10.9 | 10.1 to 11.7 | 100% |
| 6/shift | 9-3-1 | analytic_local | 0.0 | 0.0 to 0.0 | 9.4 | 8.7 to 10.1 | 100% |
| 6/shift | 9-3-1 | window_rule | 0.0 | 0.0 to 0.0 | 15.8 | 14.7 to 16.9 | 100% |
| 6/shift | 9-3-1 | quantile_local | 0.0 | 0.0 to 0.0 | 16.8 | 15.5 to 18.2 | 100% |
| 6/shift | 9-3-1 | quantile_global | 0.0 | 0.0 to 0.0 | 17.3 | 16.0 to 18.7 | 100% |
| 5/100 | 3-2-1 | analytic_global | 0.0 | 0.0 to 0.0 | 10.5 | 9.8 to 11.1 | 100% |
| 5/100 | 3-2-1 | analytic_local | 0.0 | 0.0 to 0.0 | 9.0 | 8.5 to 9.5 | 100% |
| 5/100 | 3-2-1 | window_rule | 0.0 | 0.0 to 0.0 | 16.5 | 15.7 to 17.3 | 100% |
| 5/100 | 3-2-1 | quantile_local | 0.0 | 0.0 to 0.0 | 14.2 | 13.4 to 15.0 | 100% |
| 5/100 | 3-2-1 | quantile_global | 0.0 | 0.0 to 0.0 | 14.3 | 13.5 to 15.1 | 100% |
| 5/100 | equal | analytic_global | 0.0 | 0.0 to 0.0 | 1.6 | 1.2 to 1.9 | 100% |
| 5/100 | equal | analytic_local | 0.0 | 0.0 to 0.0 | 1.5 | 1.2 to 1.9 | 100% |
| 5/100 | equal | window_rule | 0.0 | 0.0 to 0.0 | 1.7 | 1.3 to 2.1 | 100% |
| 5/100 | equal | quantile_local | 0.0 | 0.0 to 0.0 | -0.7 | -1.3 to -0.0 | 2% |
| 5/100 | equal | quantile_global | 0.0 | 0.0 to 0.0 | -0.5 | -1.1 to 0.1 | 6% |
| 5/100 | 9-3-1 | analytic_global | 0.0 | 0.0 to 0.0 | 9.4 | 8.9 to 9.9 | 100% |
| 5/100 | 9-3-1 | analytic_local | 0.0 | 0.0 to 0.0 | 8.1 | 7.6 to 8.5 | 100% |
| 5/100 | 9-3-1 | window_rule | 0.0 | 0.0 to 0.0 | 16.8 | 15.9 to 17.5 | 100% |
| 5/100 | 9-3-1 | quantile_local | 0.0 | 0.0 to 0.0 | 14.4 | 13.6 to 15.2 | 100% |
| 5/100 | 9-3-1 | quantile_global | 0.0 | 0.0 to 0.0 | 14.6 | 13.7 to 15.3 | 100% |
| 10/100 | 3-2-1 | analytic_global | 15.0 | 10.0 to 15.0 | 15.2 | 14.3 to 16.2 | 100% |
| 10/100 | 3-2-1 | analytic_local | 15.0 | 10.0 to 15.0 | 11.8 | 10.7 to 12.8 | 100% |
| 10/100 | 3-2-1 | window_rule | 15.0 | 10.0 to 15.0 | 23.2 | 22.3 to 24.0 | 100% |
| 10/100 | 3-2-1 | quantile_local | 15.0 | 10.0 to 15.0 | 25.9 | 24.8 to 27.1 | 100% |
| 10/100 | 3-2-1 | quantile_global | 15.0 | 10.0 to 15.0 | 26.4 | 25.3 to 27.6 | 100% |
| 10/100 | equal | analytic_global | 0.0 | 0.0 to 0.0 | 10.1 | 8.8 to 11.5 | 100% |
| 10/100 | equal | analytic_local | 0.0 | 0.0 to 0.0 | 10.0 | 8.6 to 11.3 | 100% |
| 10/100 | equal | window_rule | 0.0 | 0.0 to 0.0 | 10.5 | 9.1 to 11.9 | 100% |
| 10/100 | equal | quantile_local | 0.0 | 0.0 to 0.0 | 13.3 | 11.2 to 15.4 | 100% |
| 10/100 | equal | quantile_global | 0.0 | 0.0 to 0.0 | 13.8 | 11.7 to 15.9 | 100% |
| 10/100 | 9-3-1 | analytic_global | 0.0 | 0.0 to 5.0 | 13.4 | 12.5 to 14.3 | 100% |
| 10/100 | 9-3-1 | analytic_local | 0.0 | 0.0 to 5.0 | 10.8 | 9.9 to 11.7 | 100% |
| 10/100 | 9-3-1 | window_rule | 0.0 | 0.0 to 5.0 | 21.7 | 20.7 to 22.6 | 100% |
| 10/100 | 9-3-1 | quantile_local | 0.0 | 0.0 to 5.0 | 24.4 | 23.4 to 25.4 | 100% |
| 10/100 | 9-3-1 | quantile_global | 0.0 | 0.0 to 5.0 | 24.9 | 23.9 to 25.9 | 100% |
| 30/100 | 3-2-1 | analytic_global | 15.0 | 15.0 to 20.0 | 15.9 | 15.1 to 16.7 | 100% |
| 30/100 | 3-2-1 | analytic_local | 0.0 | 0.0 to 10.0 | 10.7 | 10.0 to 11.4 | 100% |
| 30/100 | 3-2-1 | window_rule | 15.0 | 15.0 to 15.0 | 16.8 | 16.0 to 17.7 | 100% |
| 30/100 | 3-2-1 | quantile_local | 55.0 | 55.0 to 55.0 | 48.1 | 46.9 to 49.1 | 100% |
| 30/100 | 3-2-1 | quantile_global | 55.0 | 55.0 to 55.0 | 49.0 | 47.9 to 50.0 | 100% |
| 30/100 | equal | analytic_global | 10.0 | 10.0 to 10.0 | 13.9 | 13.0 to 14.8 | 100% |
| 30/100 | equal | analytic_local | 0.0 | 0.0 to 0.0 | 7.9 | 7.4 to 8.5 | 100% |
| 30/100 | equal | window_rule | 15.0 | 15.0 to 15.0 | 16.7 | 16.0 to 17.4 | 100% |
| 30/100 | equal | quantile_local | 55.0 | 55.0 to 55.0 | 47.9 | 46.3 to 49.2 | 100% |
| 30/100 | equal | quantile_global | 55.0 | 55.0 to 55.0 | 48.9 | 47.3 to 50.1 | 100% |
| 30/100 | 9-3-1 | analytic_global | 20.0 | 20.0 to 20.0 | 18.2 | 17.4 to 19.0 | 100% |
| 30/100 | 9-3-1 | analytic_local | 15.0 | 15.0 to 15.0 | 13.9 | 13.1 to 14.8 | 100% |
| 30/100 | 9-3-1 | window_rule | 15.0 | 15.0 to 15.0 | 15.8 | 15.1 to 16.6 | 100% |
| 30/100 | 9-3-1 | quantile_local | 55.0 | 55.0 to 55.0 | 47.0 | 45.9 to 48.1 | 100% |
| 30/100 | 9-3-1 | quantile_global | 55.0 | 55.0 to 55.0 | 47.9 | 46.9 to 49.0 | 100% |

## 3. Discrimination and calibration

AUROC and AUPRC for a miss among specimens still silent at each landmark whose window is longer, and pooled over every tick with each specimen weighted equally; rules are ranked by their own scores:

| Method | 15 min (AUROC / AUPRC) | 30 min (AUROC / AUPRC) | 60 min (AUROC / AUPRC) | 90 min (AUROC / AUPRC) | Pooled |
|---|---|---|---|---|---|
| analytic_global | 0.876 / 0.349 | 0.876 / 0.448 | 0.876 / 0.330 | 0.904 / 0.567 | 0.945 / 0.653 |
| analytic_local | 0.906 / 0.487 | 0.917 / 0.619 | 0.906 / 0.537 | 0.926 / 0.713 | 0.957 / 0.710 |
| window_rule | 0.854 / 0.273 | 0.884 / 0.459 | 0.865 / 0.347 | 0.886 / 0.560 | 0.939 / 0.629 |
| quantile_local | 0.365 / 0.067 | 0.415 / 0.121 | 0.516 / 0.075 | 0.574 / 0.140 | 0.665 / 0.141 |
| quantile_global | 0.434 / 0.083 | 0.564 / 0.179 | 0.697 / 0.132 | 0.750 / 0.258 | 0.707 / 0.171 |
| tracernet | 0.954 / 0.834 | 0.957 / 0.867 | 0.940 / 0.759 | 0.958 / 0.843 | 0.986 / 0.904 |

Calibration of the probabilistic methods at 30 and 60 minutes of silence:

| Method | Silence (min) | Specimens | Miss rate | Brier | Brier of the base rate | ECE | Slope | Intercept |
|---|---|---|---|---|---|---|---|---|
| analytic_global | 30 | 2,115,469 | 0.1495 | 0.0879 | 0.1271 | 0.0269 | 0.941 | -0.098 |
| analytic_global | 60 | 825,685 | 0.0718 | 0.0485 | 0.0666 | 0.0078 | 0.962 | -0.015 |
| analytic_local | 30 | 2,115,469 | 0.1495 | 0.0771 | 0.1271 | 0.0087 | 0.973 | -0.113 |
| analytic_local | 60 | 825,685 | 0.0718 | 0.0427 | 0.0666 | 0.0025 | 0.978 | -0.049 |
| tracernet | 30 | 2,115,469 | 0.1495 | 0.0473 | 0.1271 | 0.0108 | 0.898 | 0.130 |
| tracernet | 60 | 825,685 | 0.0718 | 0.0312 | 0.0666 | 0.0091 | 0.836 | 0.068 |

Reliability plots: [30 min](plots/reliability-test-30.svg), [60 min](plots/reliability-test-60.svg).

### TracerNet's own calibration (validation, spec §7.6)

Temperature 1.113, isotonic map not needed; expected calibration error of p_miss 0.0170 before and 0.0138 after on 3,000,000 validation ticks.

## 4. Slices

At 6/shift, each cell is the median warning over all breaches / the capped mean warning (minutes):

### window class

| Slice | Specimens | Breaches | analytic_global (3-2-1) | analytic_global (equal) | analytic_local (3-2-1) | analytic_local (equal) | window_rule (none) | quantile_local (none) | quantile_global (none) | tracernet (3-2-1) | tracernet (equal) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| long (a day or more) | 2,514,443 | 11,226 | 0.0 / 38.6 | 0.0 / 46.2 | 0.0 / 45.0 | 0.0 / 46.1 | 0.0 / 46.0 | 1,395.0 / 114.2 | 1,395.0 / 114.8 | 0.0 / 49.6 | 0.0 / 46.1 |
| short (up to 2 h) | 1,533,490 | 304,956 | 0.0 / 11.0 | 0.0 / 6.2 | 0.0 / 12.6 | 0.0 / 6.6 | 0.0 / 5.9 | 0.0 / 2.4 | 0.0 / 1.8 | 0.0 / 22.9 | 0.0 / 11.3 |

### outcome

| Slice | Specimens | Breaches | analytic_global (3-2-1) | analytic_global (equal) | analytic_local (3-2-1) | analytic_local (equal) | window_rule (none) | quantile_local (none) | quantile_global (none) | tracernet (3-2-1) | tracernet (equal) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| in time | 3,731,751 | 0 | n/a / n/a | n/a / n/a | n/a / n/a | n/a / n/a | n/a / n/a | n/a / n/a | n/a / n/a | n/a / n/a | n/a / n/a |
| late | 298,693 | 298,693 | 0.0 / 10.8 | 0.0 / 6.1 | 0.0 / 12.4 | 0.0 / 6.5 | 0.0 / 5.8 | 0.0 / 2.2 | 0.0 / 1.7 | 0.0 / 22.9 | 0.0 / 11.2 |
| never received | 17,489 | 17,489 | 0.0 / 32.7 | 0.0 / 33.5 | 0.0 / 36.6 | 0.0 / 33.4 | 0.0 / 33.4 | 1,295.0 / 76.8 | 1,285.0 / 76.3 | 0.0 / 40.4 | 0.0 / 34.4 |

### tier

| Slice | Specimens | Breaches | analytic_global (3-2-1) | analytic_global (equal) | analytic_local (3-2-1) | analytic_local (equal) | window_rule (none) | quantile_local (none) | quantile_global (none) | tracernet (3-2-1) | tracernet (equal) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 583,536 | 48,523 | 35.0 / 41.8 | 0.0 / 9.4 | 45.0 / 46.3 | 0.0 / 10.0 | 0.0 / 11.1 | 0.0 / 10.4 | 0.0 / 7.8 | 115.0 / 80.2 | 0.0 / 19.3 |
| 2 | 670,980 | 65,983 | 10.0 / 15.5 | 0.0 / 7.2 | 10.0 / 17.4 | 0.0 / 7.6 | 0.0 / 6.6 | 0.0 / 4.1 | 0.0 / 4.0 | 15.0 / 27.4 | 0.0 / 11.7 |
| 3 | 2,793,417 | 201,676 | 0.0 / 3.7 | 0.0 / 7.3 | 0.0 / 4.7 | 0.0 / 7.7 | 0.0 / 6.7 | 0.0 / 6.1 | 0.0 / 5.9 | 0.0 / 9.2 | 0.0 / 11.1 |

### archetype

| Slice | Specimens | Breaches | analytic_global (3-2-1) | analytic_global (equal) | analytic_local (3-2-1) | analytic_local (equal) | window_rule (none) | quantile_local (none) | quantile_global (none) | tracernet (3-2-1) | tracernet (equal) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| academic_medical_center | 761,753 | 66,929 | 0.0 / 8.8 | 0.0 / 4.2 | 0.0 / 10.2 | 0.0 / 4.3 | 0.0 / 4.1 | 0.0 / 4.9 | 0.0 / 4.6 | 0.0 / 19.5 | 0.0 / 7.5 |
| cancer_center | 613,035 | 41,145 | 0.0 / 10.7 | 0.0 / 6.9 | 0.0 / 11.7 | 0.0 / 6.9 | 0.0 / 6.6 | 0.0 / 6.9 | 0.0 / 6.5 | 0.0 / 22.2 | 0.0 / 12.1 |
| childrens_hospital | 786,832 | 53,861 | 0.0 / 9.5 | 0.0 / 3.1 | 0.0 / 10.9 | 0.0 / 3.1 | 0.0 / 3.0 | 0.0 / 5.6 | 0.0 / 5.4 | 0.0 / 19.2 | 0.0 / 5.0 |
| community_hospital | 563,461 | 51,762 | 0.0 / 14.5 | 0.0 / 11.1 | 0.0 / 16.4 | 0.0 / 11.9 | 0.0 / 10.7 | 0.0 / 6.5 | 0.0 / 5.8 | 0.0 / 26.7 | 0.0 / 17.3 |
| critical_access_rural | 119,249 | 9,439 | 35.0 / 41.3 | 40.0 / 42.5 | 40.0 / 43.3 | 40.0 / 45.6 | 35.0 / 41.0 | 0.0 / 15.1 | 0.0 / 12.0 | 55.0 / 59.1 | 55.0 / 59.7 |
| safety_net_urban | 986,136 | 77,249 | 0.0 / 8.8 | 0.0 / 4.1 | 0.0 / 10.5 | 0.0 / 4.2 | 0.0 / 3.9 | 0.0 / 5.3 | 0.0 / 5.0 | 0.0 / 19.8 | 0.0 / 7.5 |
| surgical_specialty | 217,467 | 15,797 | 25.0 / 28.2 | 15.0 / 24.3 | 25.0 / 32.8 | 20.0 / 27.0 | 15.0 / 24.0 | 0.0 / 12.8 | 0.0 / 10.6 | 55.0 / 51.9 | 55.0 / 41.1 |

### stratum

| Slice | Specimens | Breaches | analytic_global (3-2-1) | analytic_global (equal) | analytic_local (3-2-1) | analytic_local (equal) | window_rule (none) | quantile_local (none) | quantile_global (none) | tracernet (3-2-1) | tracernet (equal) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| disrupted | 803,411 | 71,582 | 0.0 / 11.5 | 0.0 / 6.6 | 0.0 / 13.1 | 0.0 / 6.9 | 0.0 / 6.4 | 0.0 / 5.7 | 0.0 / 5.2 | 0.0 / 23.4 | 0.0 / 10.8 |
| edge | 414,313 | 41,760 | 0.0 / 10.8 | 0.0 / 5.1 | 0.0 / 12.9 | 0.0 / 5.9 | 0.0 / 5.2 | 0.0 / 5.4 | 0.0 / 5.0 | 0.0 / 22.1 | 0.0 / 8.5 |
| normal | 1,273,601 | 55,232 | 0.0 / 18.7 | 0.0 / 15.8 | 5.0 / 21.0 | 0.0 / 16.3 | 0.0 / 15.3 | 0.0 / 10.0 | 0.0 / 9.3 | 15.0 / 33.4 | 0.0 / 23.7 |
| stressed | 1,556,608 | 147,608 | 0.0 / 10.2 | 0.0 / 5.7 | 0.0 / 11.6 | 0.0 / 6.0 | 0.0 / 5.5 | 0.0 / 5.5 | 0.0 / 5.1 | 0.0 / 21.1 | 0.0 / 10.3 |

### disruption

| Slice | Specimens | Breaches | analytic_global (3-2-1) | analytic_global (equal) | analytic_local (3-2-1) | analytic_local (equal) | window_rule (none) | quantile_local (none) | quantile_global (none) | tracernet (3-2-1) | tracernet (equal) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| disrupted | 55,175 | 7,672 | 0.0 / 10.2 | 0.0 / 5.6 | 0.0 / 11.5 | 0.0 / 6.1 | 0.0 / 5.5 | 0.0 / 3.0 | 0.0 / 3.2 | 0.0 / 17.7 | 0.0 / 8.7 |
| undisrupted | 3,992,758 | 308,510 | 0.0 / 12.1 | 0.0 / 7.7 | 0.0 / 13.8 | 0.0 / 8.1 | 0.0 / 7.4 | 0.0 / 6.4 | 0.0 / 5.9 | 0.0 / 24.0 | 0.0 / 12.6 |

### route kind

| Slice | Specimens | Breaches | analytic_global (3-2-1) | analytic_global (equal) | analytic_local (3-2-1) | analytic_local (equal) | window_rule (none) | quantile_local (none) | quantile_global (none) | tracernet (3-2-1) | tracernet (equal) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| courier | 1,456,270 | 253,122 | 0.0 / 11.3 | 0.0 / 5.8 | 0.0 / 13.0 | 0.0 / 6.3 | 0.0 / 5.7 | 0.0 / 3.8 | 0.0 / 3.3 | 0.0 / 25.0 | 0.0 / 11.4 |
| tube | 2,194,665 | 51,256 | 0.0 / 15.9 | 0.0 / 16.0 | 0.0 / 17.6 | 0.0 / 15.9 | 0.0 / 15.0 | 0.0 / 17.8 | 0.0 / 17.3 | 0.0 / 20.4 | 0.0 / 17.9 |
| walk_in | 396,998 | 11,804 | 0.0 / 11.7 | 0.0 / 10.8 | 0.0 / 12.9 | 0.0 / 10.8 | 0.0 / 10.2 | 0.0 / 11.6 | 0.0 / 10.9 | 0.0 / 14.4 | 0.0 / 12.8 |

### volume tercile

| Slice | Specimens | Breaches | analytic_global (3-2-1) | analytic_global (equal) | analytic_local (3-2-1) | analytic_local (equal) | window_rule (none) | quantile_local (none) | quantile_global (none) | tracernet (3-2-1) | tracernet (equal) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| low | 385,114 | 30,589 | 35.0 / 39.5 | 35.0 / 40.0 | 40.0 / 42.8 | 40.0 / 43.4 | 35.0 / 38.8 | 0.0 / 15.4 | 0.0 / 12.6 | 55.0 / 60.2 | 55.0 / 60.7 |
| middle | 1,065,646 | 79,375 | 0.0 / 16.4 | 0.0 / 10.1 | 0.0 / 19.1 | 0.0 / 10.3 | 0.0 / 9.7 | 0.0 / 6.8 | 0.0 / 6.3 | 15.0 / 32.7 | 0.0 / 19.2 |
| high | 2,597,173 | 206,218 | 0.0 / 6.3 | 0.0 / 1.9 | 0.0 / 7.4 | 0.0 / 1.9 | 0.0 / 1.8 | 0.0 / 4.8 | 0.0 / 4.6 | 0.0 / 15.1 | 0.0 / 2.8 |

### edge tag

| Slice | Specimens | Breaches | analytic_global (3-2-1) | analytic_global (equal) | analytic_local (3-2-1) | analytic_local (equal) | window_rule (none) | quantile_local (none) | quantile_global (none) | tracernet (3-2-1) | tracernet (equal) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| batching_night_unit | 33,028 | 2,764 | 0.0 / 14.4 | 0.0 / 8.7 | 0.0 / 16.3 | 0.0 / 9.6 | 0.0 / 8.4 | 0.0 / 6.8 | 0.0 / 6.5 | 0.0 / 26.2 | 0.0 / 12.9 |
| chronic_non_dispatch_unit | 33,460 | 4,175 | 0.0 / 9.5 | 0.0 / 3.6 | 0.0 / 10.9 | 0.0 / 4.5 | 0.0 / 3.6 | 0.0 / 5.7 | 0.0 / 5.1 | 0.0 / 24.5 | 0.0 / 7.0 |
| compound_failure | 34,959 | 5,444 | 0.0 / 6.2 | 0.0 / 3.2 | 0.0 / 7.6 | 0.0 / 3.5 | 0.0 / 3.0 | 0.0 / 3.8 | 0.0 / 4.7 | 0.0 / 16.4 | 0.0 / 5.1 |
| courier_capacity_crunch | 38,532 | 7,543 | 0.0 / 9.8 | 0.0 / 5.0 | 0.0 / 12.5 | 0.0 / 7.9 | 0.0 / 6.0 | 0.0 / 3.1 | 0.0 / 3.6 | 0.0 / 18.1 | 0.0 / 11.7 |
| holiday_skeleton | 32,605 | 3,480 | 0.0 / 10.0 | 0.0 / 4.2 | 0.0 / 10.3 | 0.0 / 5.0 | 0.0 / 4.2 | 0.0 / 4.5 | 0.0 / 4.0 | 0.0 / 22.9 | 0.0 / 7.0 |
| instrument_staff_drain | 22,741 | 2,490 | 0.0 / 8.1 | 0.0 / 0.0 | 0.0 / 14.6 | 0.0 / 0.0 | 0.0 / 0.0 | 0.0 / 4.5 | 0.0 / 3.8 | 0.0 / 20.4 | 0.0 / 0.0 |
| mass_casualty_surge | 65,197 | 3,838 | 0.0 / 11.9 | 0.0 / 5.0 | 0.0 / 13.3 | 0.0 / 5.0 | 0.0 / 4.9 | 0.0 / 7.6 | 0.0 / 6.8 | 0.0 / 23.0 | 0.0 / 5.9 |
| misroute_long_search | 45,375 | 3,017 | 5.0 / 18.3 | 10.0 / 15.7 | 5.0 / 20.4 | 10.0 / 16.1 | 10.0 / 15.6 | 0.0 / 11.2 | 0.0 / 9.6 | 20.0 / 35.0 | 10.0 / 25.2 |
| misroute_storm | 50,965 | 5,027 | 0.0 / 11.6 | 0.0 / 7.7 | 0.0 / 13.6 | 0.0 / 8.2 | 0.0 / 7.2 | 0.0 / 5.7 | 0.0 / 5.2 | 0.0 / 25.3 | 0.0 / 12.7 |
| night_station_neglect | 43,047 | 3,365 | 0.0 / 11.6 | 0.0 / 7.0 | 0.0 / 13.2 | 0.0 / 6.9 | 0.0 / 6.5 | 0.0 / 7.4 | 0.0 / 6.8 | 0.0 / 23.7 | 0.0 / 7.9 |
| none | 3,633,620 | 274,422 | 0.0 / 12.2 | 0.0 / 8.0 | 0.0 / 13.9 | 0.0 / 8.3 | 0.0 / 7.7 | 0.0 / 6.5 | 0.0 / 5.9 | 0.0 / 24.1 | 0.0 / 13.1 |
| offsite_road_closure | 40,890 | 4,827 | 0.0 / 17.9 | 0.0 / 5.8 | 0.0 / 22.0 | 0.0 / 9.9 | 0.0 / 7.4 | 0.0 / 4.6 | 0.0 / 4.1 | 0.0 / 29.8 | 0.0 / 14.2 |
| outage_at_shift_change | 48,982 | 4,609 | 0.0 / 11.7 | 0.0 / 5.5 | 0.0 / 13.2 | 0.0 / 6.2 | 0.0 / 5.6 | 0.0 / 6.0 | 0.0 / 5.6 | 0.0 / 25.9 | 0.0 / 8.5 |
| outage_during_peak | 46,493 | 4,133 | 0.0 / 7.6 | 0.0 / 5.9 | 0.0 / 7.9 | 0.0 / 5.8 | 0.0 / 5.6 | 0.0 / 6.2 | 0.0 / 5.6 | 0.0 / 10.5 | 0.0 / 7.2 |
| slow_degradation | 95,636 | 7,671 | 0.0 / 7.6 | 0.0 / 2.5 | 0.0 / 11.5 | 0.0 / 2.5 | 0.0 / 2.3 | 0.0 / 5.2 | 0.0 / 4.9 | 0.0 / 18.0 | 0.0 / 3.0 |
| stat_heavy_icu_night | 50,454 | 5,988 | 0.0 / 9.8 | 0.0 / 4.0 | 0.0 / 10.0 | 0.0 / 4.4 | 0.0 / 3.8 | 0.0 / 4.7 | 0.0 / 4.3 | 0.0 / 25.4 | 0.0 / 6.4 |
| unnoticed_outage | 42,850 | 2,703 | 5.0 / 19.6 | 0.0 / 13.3 | 10.0 / 20.6 | 0.0 / 13.6 | 0.0 / 13.1 | 0.0 / 11.4 | 0.0 / 9.9 | 15.0 / 35.4 | 0.0 / 21.5 |
| weekend_courier_no_show | 53,740 | 8,916 | 0.0 / 4.8 | 0.0 / 0.4 | 0.0 / 6.8 | 0.0 / 0.4 | 0.0 / 0.4 | 0.0 / 2.7 | 0.0 / 3.1 | 0.0 / 10.7 | 0.0 / 0.4 |


## 5. test_ood, legacy_v1 and the negative control

Reported beside the claim, never folded into it (spec §8.6).

### test_ood, every scenario

Families go to `test_ood` whole, so it also holds ordinary weeks that travel with an out-of-distribution family member (docs/neural/build-log.md).

849 scenarios, 5,965,032 scored specimens, 381,168 breaches.

| Budget | Method | Weights | Median warning, all (min) | 95% interval | Capped mean (min) | 95% interval | Catch rate | Alerts per shift | No in-budget knob |
|---|---|---|---|---|---|---|---|---|---|
| 6/shift | analytic_global | 3-2-1 | 0.0 | 0.0 to 0.0 | 10.6 | 9.9 to 11.3 | 29.6% | 5.44 | 5 of 849 |
| 6/shift | analytic_global | equal | 0.0 | 0.0 to 0.0 | 6.0 | 5.3 to 6.7 | 21.1% | 3.50 | 361 of 849 |
| 6/shift | analytic_local | 3-2-1 | 0.0 | 0.0 to 0.0 | 11.7 | 11.0 to 12.6 | 32.8% | 5.45 | 5 of 849 |
| 6/shift | analytic_local | equal | 0.0 | 0.0 to 0.0 | 6.0 | 5.3 to 6.7 | 21.0% | 3.55 | 361 of 849 |
| 6/shift | window_rule | none | 0.0 | 0.0 to 0.0 | 5.6 | 5.0 to 6.3 | 19.8% | 3.23 | 361 of 849 |
| 6/shift | tracernet | 3-2-1 | 0.0 | 0.0 to 0.0 | 19.6 | 18.5 to 20.7 | 35.7% | 5.75 | 0 of 849 |
| 6/shift | tracernet | equal | 0.0 | 0.0 to 0.0 | 9.0 | 7.9 to 10.1 | 22.0% | 5.10 | 143 of 849 |
| 30/100 | analytic_global | 3-2-1 | 40.0 | 40.0 to 40.0 | 49.9 | 49.0 to 50.7 | 99.9% | 33.29 | 0 of 849 |
| 30/100 | analytic_global | equal | 50.0 | 50.0 to 50.0 | 52.9 | 52.2 to 53.7 | 100.0% | 33.25 | 0 of 849 |
| 30/100 | analytic_local | 3-2-1 | 45.0 | 45.0 to 50.0 | 53.7 | 53.0 to 54.4 | 99.5% | 33.47 | 0 of 849 |
| 30/100 | analytic_local | equal | 55.0 | 55.0 to 55.0 | 56.4 | 55.9 to 56.9 | 100.0% | 33.53 | 0 of 849 |
| 30/100 | window_rule | none | 40.0 | 40.0 to 45.0 | 49.2 | 48.5 to 49.9 | 100.0% | 31.05 | 0 of 849 |
| 30/100 | tracernet | 3-2-1 | 55.0 | 55.0 to 55.0 | 63.5 | 63.2 to 63.9 | 100.0% | 33.53 | 0 of 849 |
| 30/100 | tracernet | equal | 55.0 | 55.0 to 55.0 | 63.7 | 63.4 to 64.0 | 100.0% | 33.55 | 0 of 849 |

The rule applied here, non-binding: R1 failed, R2 held, R3 held, R4 failed.

### test_ood, the scenarios that carry the out-of-distribution feature

Archetype `multi_site_system` or a `scanner_downtime` disruption.

482 scenarios, 3,084,251 scored specimens, 236,605 breaches.

| Budget | Method | Weights | Median warning, all (min) | 95% interval | Capped mean (min) | 95% interval | Catch rate | Alerts per shift | No in-budget knob |
|---|---|---|---|---|---|---|---|---|---|
| 6/shift | analytic_global | 3-2-1 | 0.0 | 0.0 to 0.0 | 10.7 | 9.9 to 11.7 | 28.8% | 5.39 | 5 of 482 |
| 6/shift | analytic_global | equal | 0.0 | 0.0 to 0.0 | 5.0 | 4.2 to 5.9 | 16.4% | 3.08 | 237 of 482 |
| 6/shift | analytic_local | 3-2-1 | 0.0 | 0.0 to 0.0 | 11.9 | 11.0 to 13.0 | 30.4% | 5.40 | 5 of 482 |
| 6/shift | analytic_local | equal | 0.0 | 0.0 to 0.0 | 5.1 | 4.3 to 6.0 | 16.5% | 3.11 | 237 of 482 |
| 6/shift | window_rule | none | 0.0 | 0.0 to 0.0 | 4.7 | 4.0 to 5.6 | 15.7% | 2.88 | 237 of 482 |
| 6/shift | tracernet | 3-2-1 | 0.0 | 0.0 to 0.0 | 20.1 | 18.8 to 21.7 | 33.0% | 5.64 | 0 of 482 |
| 6/shift | tracernet | equal | 0.0 | 0.0 to 0.0 | 7.9 | 6.8 to 9.4 | 17.3% | 4.97 | 90 of 482 |
| 30/100 | analytic_global | 3-2-1 | 40.0 | 35.0 to 40.0 | 49.0 | 47.8 to 50.1 | 99.8% | 30.36 | 0 of 482 |
| 30/100 | analytic_global | equal | 45.0 | 45.0 to 50.0 | 51.9 | 50.9 to 52.9 | 100.0% | 30.32 | 0 of 482 |
| 30/100 | analytic_local | 3-2-1 | 55.0 | 45.0 to 55.0 | 54.0 | 53.0 to 54.9 | 99.1% | 30.42 | 0 of 482 |
| 30/100 | analytic_local | equal | 55.0 | 55.0 to 55.0 | 57.0 | 56.3 to 57.6 | 100.0% | 30.49 | 0 of 482 |
| 30/100 | window_rule | none | 40.0 | 40.0 to 40.0 | 48.5 | 47.5 to 49.4 | 100.0% | 28.65 | 0 of 482 |
| 30/100 | tracernet | 3-2-1 | 55.0 | 55.0 to 55.0 | 64.1 | 63.6 to 64.6 | 100.0% | 30.54 | 0 of 482 |
| 30/100 | tracernet | equal | 55.0 | 55.0 to 55.0 | 64.2 | 63.8 to 64.7 | 100.0% | 30.55 | 0 of 482 |

### legacy_v1

The v1 store's held-out runs 24 to 29: 6 clusters, so its intervals are wide.

6 runs, 2,378 scored specimens, 100 breaches.

| Budget | Method | Weights | Median warning, all (min) | 95% interval | Capped mean (min) | 95% interval | Catch rate | Alerts per shift | No in-budget knob |
|---|---|---|---|---|---|---|---|---|---|
| 6/shift | analytic_global | 3-2-1 | 55.0 | 50.0 to 55.0 | 70.7 | 65.3 to 77.4 | 100.0% | 6.40 | 0 of 1 |
| 6/shift | analytic_global | equal | 45.0 | 45.0 to 45.0 | 69.2 | 63.5 to 76.1 | 100.0% | 6.32 | 0 of 1 |
| 6/shift | analytic_local | 3-2-1 | 55.0 | 50.0 to 55.0 | 70.7 | 65.3 to 77.4 | 100.0% | 6.40 | 0 of 1 |
| 6/shift | analytic_local | equal | 45.0 | 45.0 to 45.0 | 69.2 | 63.5 to 76.1 | 100.0% | 6.32 | 0 of 1 |
| 6/shift | window_rule | none | 45.0 | 45.0 to 45.0 | 71.0 | 65.8 to 77.5 | 100.0% | 5.31 | 0 of 1 |
| 6/shift | tracernet | 3-2-1 | 55.0 | 55.0 to 55.0 | 73.2 | 68.1 to 79.7 | 100.0% | 6.53 | 0 of 1 |
| 6/shift | tracernet | equal | 55.0 | 55.0 to 55.0 | 73.7 | 68.3 to 79.9 | 100.0% | 6.41 | 0 of 1 |
| 30/100 | analytic_global | 3-2-1 | 50.0 | 45.0 to 55.0 | 69.7 | 64.4 to 76.3 | 100.0% | 6.09 | 0 of 1 |
| 30/100 | analytic_global | equal | 45.0 | 45.0 to 45.0 | 68.6 | 62.9 to 75.5 | 100.0% | 6.13 | 0 of 1 |
| 30/100 | analytic_local | 3-2-1 | 50.0 | 45.0 to 55.0 | 69.7 | 64.4 to 76.3 | 100.0% | 6.09 | 0 of 1 |
| 30/100 | analytic_local | equal | 45.0 | 45.0 to 45.0 | 68.6 | 62.9 to 75.5 | 100.0% | 6.13 | 0 of 1 |
| 30/100 | window_rule | none | 45.0 | 45.0 to 45.0 | 71.0 | 65.8 to 77.5 | 100.0% | 5.31 | 0 of 1 |
| 30/100 | tracernet | 3-2-1 | 55.0 | 55.0 to 55.0 | 72.3 | 67.2 to 78.6 | 100.0% | 6.25 | 0 of 1 |
| 30/100 | tracernet | equal | 55.0 | 55.0 to 55.0 | 72.0 | 66.7 to 78.3 | 100.0% | 6.14 | 0 of 1 |

The rule applied here, non-binding: R1 failed, R2 held, R3 failed, R4 failed.

### The negative control (control_v1world)

19 runs, 7,695 scored specimens, 288 breaches.

| Budget | Method | Weights | Median warning, all (min) | 95% interval | Capped mean (min) | 95% interval | Catch rate | Alerts per shift | No in-budget knob |
|---|---|---|---|---|---|---|---|---|---|
| 6/shift | analytic_global | 3-2-1 | 55.0 | 55.0 to 55.0 | 69.6 | 65.6 to 73.6 | 100.0% | 5.00 | 0 of 1 |
| 6/shift | analytic_global | equal | 55.0 | 55.0 to 55.0 | 74.7 | 71.5 to 77.7 | 100.0% | 5.66 | 0 of 1 |
| 6/shift | analytic_local | 3-2-1 | 45.0 | 35.0 to 45.0 | 66.8 | 62.7 to 70.7 | 100.0% | 4.89 | 0 of 1 |
| 6/shift | analytic_local | equal | 40.0 | 40.0 to 40.0 | 67.6 | 63.8 to 71.3 | 100.0% | 5.28 | 0 of 1 |
| 6/shift | window_rule | none | 45.0 | 45.0 to 45.0 | 71.5 | 67.8 to 74.9 | 100.0% | 5.19 | 0 of 1 |
| 6/shift | tracernet | 3-2-1 | 55.0 | 50.0 to 55.0 | 68.1 | 64.1 to 71.8 | 100.0% | 4.83 | 0 of 1 |
| 6/shift | tracernet | equal | 55.0 | 45.0 to 55.0 | 68.6 | 64.9 to 72.1 | 100.0% | 5.05 | 0 of 1 |
| 30/100 | analytic_global | 3-2-1 | 55.0 | 55.0 to 55.0 | 69.8 | 65.7 to 73.8 | 100.0% | 5.05 | 0 of 1 |
| 30/100 | analytic_global | equal | 55.0 | 55.0 to 55.0 | 74.8 | 71.6 to 77.8 | 100.0% | 5.67 | 0 of 1 |
| 30/100 | analytic_local | 3-2-1 | 45.0 | 35.0 to 45.0 | 66.8 | 62.7 to 70.7 | 100.0% | 4.99 | 0 of 1 |
| 30/100 | analytic_local | equal | 40.0 | 40.0 to 40.0 | 67.6 | 63.8 to 71.3 | 100.0% | 5.44 | 0 of 1 |
| 30/100 | window_rule | none | 45.0 | 45.0 to 45.0 | 71.5 | 67.8 to 74.9 | 100.0% | 5.19 | 0 of 1 |
| 30/100 | tracernet | 3-2-1 | 55.0 | 50.0 to 55.0 | 68.5 | 64.6 to 72.3 | 100.0% | 5.00 | 0 of 1 |
| 30/100 | tracernet | equal | 55.0 | 45.0 to 55.0 | 69.3 | 65.7 to 72.8 | 100.0% | 5.24 | 0 of 1 |

Where no context exists, TracerNet's lead over the local analytic refit should be small; a lead of 5 minutes or more whose interval's lower end is above zero is treated as leakage until shown otherwise (spec §8.8 A6).

| Budget | Metric | Lead (min) | 95% interval | Reading |
|---|---|---|---|---|
| 6/shift | median warning, all breaches | 10.0 | 5.0 to 20.0 | large: treated as leakage |
| 6/shift | capped mean warning | 1.3 | 0.3 to 2.5 | small |
| 30/100 | median warning, all breaches | 10.0 | 10.0 to 20.0 | large: treated as leakage |
| 30/100 | capped mean warning | 1.8 | 0.8 to 2.9 | small |

The lead is large somewhere above: treat it as leakage until shown otherwise.

### Sensitivity sets and face validity (spec §8.11)

- Sensitivity sets (`sens_tail`, `sens_disrupt_half`, `sens_disrupt_double`, `sens_pace_fast`, `sens_pace_slow`): not run. Spec §8.11 puts them outside the critical path of N0 to N11, to run when compute and time allow; none was simulated or scored, so the result is not shown to be robust to any simulator assumption.
- Blinded face-validity rating: not run. It needs Karissa's agreement, arranged outside the hands-off build, which asks nothing new of her, and it was not arranged; no laboratory week has been rated.

## 6. Ablations A1 to A9

Every trained arm took the same 733 gradient steps (equal across arms); compute 73.74 hours against spec §8.8's budget of 6 hours, over it by 67.74 hours. Test numbers are descriptive, scored after the lock, and never change the headline model.

### A1. More data or more variety?

| Arm | Seeds | val_sel | Val capped mean (min) | Val median, all (min) | Test capped mean (min) | Test median, all (min) | Test Brier 30 | Test Brier 60 |
|---|---|---|---|---|---|---|---|---|
| 100x8 | 3 | 0.0874 (0.0872 to 0.0875) | 22.05 (22.02 to 22.12) | 0.00 (0.00 to 0.00) | 21.68 (21.63 to 21.75) | 0.00 (0.00 to 0.00) | 0.0493 (0.0493 to 0.0493) | 0.0319 (0.0318 to 0.0320) |
| 200x4 | 3 | 0.0868 (0.0867 to 0.0869) | 21.99 (21.84 to 22.06) | 0.00 (0.00 to 0.00) | 21.59 (21.52 to 21.63) | 0.00 (0.00 to 0.00) | 0.0488 (0.0487 to 0.0489) | 0.0319 (0.0318 to 0.0319) |
| 400x2 | 3 | 0.0868 (0.0866 to 0.0871) | 22.05 (21.87 to 22.19) | 0.00 (0.00 to 0.00) | 21.72 (21.60 to 21.88) | 0.00 (0.00 to 0.00) | 0.0487 (0.0485 to 0.0490) | 0.0318 (0.0317 to 0.0318) |
| 800x1 | 3 | 0.0870 (0.0868 to 0.0871) | 22.18 (22.14 to 22.27) | 0.00 (0.00 to 0.00) | 21.72 (21.71 to 21.73) | 0.00 (0.00 to 0.00) | 0.0485 (0.0483 to 0.0487) | 0.0317 (0.0316 to 0.0318) |

### A2. Do the hard edge cases matter?

| Arm | Seeds | val_sel | Val capped mean (min) | Val median, all (min) | Test capped mean (min) | Test median, all (min) | Test Brier 30 | Test Brier 60 |
|---|---|---|---|---|---|---|---|---|
| with_edge | 3 | 0.0867 (0.0865 to 0.0868) | 22.21 (22.19 to 22.24) | 0.00 (0.00 to 0.00) | 21.78 (21.76 to 21.81) | 0.00 (0.00 to 0.00) | 0.0484 (0.0483 to 0.0485) | 0.0316 (0.0316 to 0.0316) |
| without_edge | 3 | 0.0879 (0.0876 to 0.0882) | 22.20 (22.10 to 22.33) | 0.00 (0.00 to 0.00) | 21.87 (21.81 to 21.94) | 0.00 (0.00 to 0.00) | 0.0484 (0.0483 to 0.0485) | 0.0316 (0.0315 to 0.0317) |

### A3. Did round 2 help?

Not run (not buildable in time): it needs each train scenario's generation job (O2 or O3), which the scenario cells do not carry.

### A4. Was the API spend worth it?

Not run (not buildable in time): it needs code-random scenarios simulated into their own store and a code-random test set, which the pipeline cannot build yet.

### A5. Which parts of the network earn their place?

| Arm | Seeds | val_sel | Val capped mean (min) | Val median, all (min) | Test capped mean (min) | Test median, all (min) | Test Brier 30 | Test Brier 60 |
|---|---|---|---|---|---|---|---|---|
| A | 3 | 0.0867 (0.0865 to 0.0869) | 22.06 (21.84 to 22.18) | 0.00 (0.00 to 0.00) | 21.74 (21.64 to 21.85) | 0.00 (0.00 to 0.00) | 0.0484 (0.0483 to 0.0485) | 0.0317 (0.0316 to 0.0317) |
| B | 3 | 0.0866 (0.0863 to 0.0868) | 22.18 (22.09 to 22.25) | 0.00 (0.00 to 0.00) | 21.81 (21.76 to 21.88) | 0.00 (0.00 to 0.00) | 0.0483 (0.0482 to 0.0484) | 0.0317 (0.0316 to 0.0317) |
| C | 3 | 0.0864 (0.0862 to 0.0865) | 22.25 (22.14 to 22.35) | 0.00 (0.00 to 0.00) | 21.85 (21.79 to 21.92) | 0.00 (0.00 to 0.00) | 0.0482 (0.0482 to 0.0483) | 0.0315 (0.0314 to 0.0317) |
| L | 3 | 0.0929 (0.0927 to 0.0932) | 20.94 (20.88 to 21.00) | 0.00 (0.00 to 0.00) | 20.69 (20.57 to 20.79) | 0.00 (0.00 to 0.00) | 0.0511 (0.0510 to 0.0512) | 0.0339 (0.0338 to 0.0339) |
| ensemble | 1 | 0.0861 | 22.21 | 0.00 | 21.86 | 0.00 | 0.0482 | 0.0315 |

### A6. Is it the context?

| Arm | Seeds | val_sel | Val capped mean (min) | Val median, all (min) | Test capped mean (min) | Test median, all (min) | Test Brier 30 | Test Brier 60 |
|---|---|---|---|---|---|---|---|---|
| full | 3 | 0.0864 (0.0862 to 0.0865) | 22.25 (22.14 to 22.35) | 0.00 (0.00 to 0.00) | 21.85 (21.79 to 21.92) | 0.00 (0.00 to 0.00) | 0.0482 (0.0482 to 0.0483) | 0.0315 (0.0314 to 0.0317) |
| no_aggregates | 3 | 0.0865 (0.0864 to 0.0866) | 22.09 (21.81 to 22.26) | 0.00 (0.00 to 0.00) | 21.71 (21.51 to 21.84) | 0.00 (0.00 to 0.00) | 0.0483 (0.0481 to 0.0486) | 0.0317 (0.0316 to 0.0318) |
| no_context | 3 | 0.0958 (0.0955 to 0.0960) | 20.71 (20.64 to 20.85) | 0.00 (0.00 to 0.00) | 20.29 (20.12 to 20.45) | 0.00 (0.00 to 0.00) | 0.0547 (0.0546 to 0.0548) | 0.0360 (0.0358 to 0.0361) |
| no_fingerprint | 3 | 0.0873 (0.0872 to 0.0874) | 22.06 (21.94 to 22.14) | 0.00 (0.00 to 0.00) | 21.56 (21.52 to 21.59) | 0.00 (0.00 to 0.00) | 0.0491 (0.0490 to 0.0492) | 0.0320 (0.0319 to 0.0321) |
| no_roster | 3 | 0.0868 (0.0866 to 0.0870) | 22.15 (21.93 to 22.32) | 0.00 (0.00 to 0.00) | 21.76 (21.59 to 21.92) | 0.00 (0.00 to 0.00) | 0.0484 (0.0483 to 0.0485) | 0.0317 (0.0317 to 0.0318) |

The full arm is A5's rung-C arm, trained identically.

Negative control: TracerNet's lead over the local analytic refit on control_v1world at 6/shift is 10.0 (95% interval 5.0 to 20.0) min (median, all breaches) and 1.3 (95% interval 0.3 to 2.5) min (capped mean); large: treated as leakage until shown otherwise.

### A7. Does the loss matter?

Not run: not in the core set.

### A8. Does mixing generators help across generators?

Not run (cut): it trains on the Claude-written v1 trails, which Anthropic's Usage Policy does not allow without prior authorization (spec §18 cuts A8 second among the ablations).

### A9. Neural or tabular?

Not run: the gradient-boosted reference is first on the cut list (spec §18).


## 7. Explanation checks and example alerts

Integrated gradients on the logit of the ensemble's mean p_miss, summed into ten factor groups (spec §9), on 287,374 alerts of `test` at 6 alerts per shift (default weights), 256 steps. Attributions explain the network, not the world, and the data is synthetic.

| Check | Result | Rule | Outcome |
|---|---|---|---|
| Completeness | median gap 0.93% at 256 steps | within 1% for the median alert | held |
| Deletion | top 3 beat 3 random groups in 68.2% of 264,850 alerts; mean drop in p_miss 0.0346 against 0.0116 | at least 70%, with a larger mean drop | failed |
| Randomization | mean Spearman -0.00 (median 0.00) over 927 alerts | below 0.3 | held |
| Factors | 287,374 of 287,374 alerts carry factors | every alert | held |

Closed-window alerts: 22,524. At or past its window's close an alert's p_miss is 1 by construction, so it carries one window factor with no attribution and is left out of the three checks above.

Groups among the factors that raise the risk, by how often: route_flow 135,646, unit_flow 128,852, lab_flow 116,043, batch_mates 89,077, window 75,844, silence 21,318.

### Example alerts

The highest, the median and the lowest p_miss among the alerts explained. Related specimens are the batch-mates the network attended to most, which is not an explanation.

**SP-r108422-000338@5** (S0842, minute 5 of a 60-minute window, p_miss 1.000)

| Direction | Group | Share | Attribution (logit) | Factor |
|---|---|---|---|---|
| raises | batch_mates | 87% | 0.211 | 0 of 1 specimen sent from this unit around the same time have arrived |
| raises | lab_flow | 13% | 0.031 | Receiving is behind: 3 receipts in the last hour against 6 collections the hour before |
| lowers | route_flow | 77% | -0.061 | No receipts via this route for 139 min, usually none within an hour |

Related specimens: SP-r108422-000337, SP-r108422-000336.

**SP-r125301-001234@5** (S2530, minute 5 of a 60-minute window, p_miss 0.408)

| Direction | Group | Share | Attribution (logit) | Factor |
|---|---|---|---|---|
| raises | route_flow | 81% | 0.576 | No receipts via this route for 11 min, usually every 30 min |
| raises | unit_flow | 19% | 0.135 | 3 specimens from this on-site clinic are still outstanding |
| lowers | batch_mates | 100% | -0.888 | 0 of 1 specimen sent from this unit around the same time have arrived |

Related specimens: SP-r125301-001200, SP-r125301-001213, SP-r125301-001222, SP-r125301-001227, SP-r125301-001230.

**SP-r136472-000080@5** (S3647, minute 5 of a 1,440-minute window, p_miss 0.001)

| Direction | Group | Share | Attribution (logit) | Factor |
|---|---|---|---|---|
| raises | unit_flow | 100% | 0.007 | 1 specimen from this emergency department is still outstanding |
| lowers | route_flow | 67% | -0.055 | No receipts via this route for 84 min, usually every 60 min |

Related specimens: SP-r136472-000076, SP-r136472-000077.

## 8. Decision layer

### What it is

Drex Max v1.1 (Nace.AI), reached through OpenRouter's Decisions API, answers typed questions with a probability for every option and writes no text. It is used zero-shot: it was never trained on this project's data and never sees a label. It labels the scenario library (X1), proposes where to look, what to do and whom to call for each TracerNet alert (X2), and is tested as a forecaster on its own (X3). X2 is compared with two rules on the same alerts: R1, today's routing (a tube specimen to the receiving technologist, a hand-carried one to the courier coordinator), and R2, hand-written rules over the same facts the model sees. The adoption rule, pre-registered in spec §11.3: "exports and the demo use Drex Max's decisions only if its role accuracy beats both R1 and R2 by at least 5 points, with the 95% interval of each difference above zero on test. Otherwise they use R2, and the comparison is reported."

### Spend

| Run | Prompt | Items | Calls billed this run | Billed this run | Job's total so far, of its cap | Worst billed input / input bound | Worst cost / reservation |
|---|---|---|---|---|---|---|---|
| X1 pilot | p1 | 320 (0 from the cache) | 320 | $0.0247 | $0.0247 of $1.00 | 0.33 | 0.17 |
| X1 paid run | p1 | 8,442 (320 from the cache) | 8,122 | $0.6714 | $0.6961 of $1.00 | 0.35 | 0.18 |
| X1 refresh, from the cache | p1 | 8,442 (8,442 from the cache) | 0 | $0.0000 | $0.6961 of $1.00 | n/a | n/a |
| X2 on validation, prompt round one | p1 | 5,000 (0 from the cache) | 5,000 | $0.3314 | $0.3314 of $4.50 | 0.31 | 0.16 |
| X2 on validation, prompt round two | p2 | 5,000 (0 from the cache) | 5,000 | $0.3725 | $0.7039 of $4.50 | 0.30 | 0.15 |
| X2 on test | p2 | 20,000 (0 from the cache) | 20,000 | $1.4894 | $2.1933 of $4.50 | 0.30 | 0.15 |
| X3 on test | p2 | 4,000 (0 from the cache) | 4,000 | $0.1003 | $0.1003 of $0.75 | 0.30 | 0.15 |

X1 cost $0.0247 for its 320-call pilot, then $0.6714 for the 8,122 calls of its paid run: $0.6961 in all, the ledger's total for the job. Its worst call, across both paid runs, billed 0.35 of its input bound and 0.18 of its reservation. The refresh the scores come from answered 8,442 of its 8,442 items from the cache and was billed $0.0000.

The decide account so far: $2.9897 of its $8.00 cap, the latest total of each job. A worst ratio under 1 means every billed call stayed inside its reservation. Resolved model: `nace-ai/drex-max-20261016`.

### X1: scenario labels and audit

8,142 of 8,142 scenarios labeled, and 300 negative controls (code-made corruptions mixed with accepted scenarios, rendered without plausibility measures in both arms).

Plausibility on the controls: AUROC 0.980 (0.966 to 0.991) against the bar of 0.80, so plausibility works as a soft filter. 726 scenarios, whose most likely level is one of the two lowest, go to a review list; none is deleted.

Mean plausibility score (0, not plausible, to 4, highly plausible) by control:

| Control | Scenarios | Mean score |
|---|---|---|
| clean | 150 | 2.74 |
| contradictory schedules | 37 | 2.04 |
| impossible routes | 38 | 1.21 |
| rates 10x | 37 | 0.40 |
| staffing absurd | 38 | 1.56 |

Most likely plausibility level across the library: level 1 726, level 2 1,265, level 3 6,031, level 4 120.

The dominant failure agrees with the planner's failure focus for 57.1% of 8,142 scenarios. Edge-tag questions were answered yes for 98.3% of the 2,246 asked; only the tags each scenario's brief requested were asked, so this measures a lean toward yes rather than agreement.

### X2: prompt rounds on validation (not binding)

Percentages with Drex Max's 95% interval, then R1 and R2 on the same alerts:

| Round | Alerts | Role accuracy, % (Drex Max; R1; R2) | Place accuracy, % | Action appropriate, % | Drex Max's most-chosen action |
|---|---|---|---|---|---|
| Round one (p1) | 5,000 | 33.5 (31.9 to 35.1); 46.3; 68.5 | 31.2 (29.7 to 32.7); 20.0; 42.2 | 20.3 (19.0 to 21.6); 37.4; 59.2 | `check_receiving_queue`, 67% (shares counted from this round's outputs) |
| Round two (p2) | 5,000 | 62.0 (60.4 to 63.5); 46.3; 68.5 | 37.4 (36.0 to 38.8); 20.0; 42.2 | 55.3 (53.4 to 57.1); 37.4; 59.2 | `call_unit_confirm_sent`, 38% |

Selection: test uses the round with the higher validation role accuracy, round two (p2, 62.0% against 33.5%).

Validation is for prompt work only; no number on it is binding (spec §8.7).

### X2 on test (binding)

20,000 alerts from 782 scenarios, 20,000 with a true place at the alert tick; prompt p2 (the selected round).

The adoption rule, binding on test, in percentage points of role accuracy:

| Difference | Points (95% interval) | Needed | Outcome |
|---|---|---|---|
| Drex Max minus R1 | 16.7 (15.2 to 18.1) | at least +5.0, interval above zero | held |
| Drex Max minus R2 | -7.9 (-8.9 to -6.9) | at least +5.0, interval above zero | failed |

The adoption rule did not hold (binding): any dispatch the exports carry is rule R2's dispatch. The demo's specimens come from `legacy_v1`, whose v1 store records no route, unit kind or staffing roster, so neither dispatcher can run on them and `docs/demo/neural.json` carries no dispatch (null).

Every method on the same alerts, with 95% intervals:

| Method | Place accuracy, % | Action appropriate, % | Role accuracy, % | Breaches averted per 100 alerts | Recollections per 100 alerts |
|---|---|---|---|---|---|
| Drex Max | 37.7 (36.8 to 38.5) | 54.5 (53.2 to 55.7) | 61.2 (60.2 to 62.2) | 25.0 (23.6 to 26.3) | 0.1 (0.0 to 0.1) |
| R1 (today's routing) | 19.0 (18.2 to 19.9) | 35.4 (34.1 to 36.8) | 44.5 (43.2 to 45.9) | 7.4 (6.8 to 8.0) | 0.1 (0.0 to 0.1) |
| R2 (hand-written rules) | 44.5 (43.5 to 45.4) | 60.5 (59.4 to 61.7) | 69.1 (68.3 to 70.0) | 25.2 (23.9 to 26.4) | 0.2 (0.1 to 0.3) |

The yes-no questions, against their labels: a shared problem (8,222 of 20,000 alerts) and an unrecoverable specimen (4,380 of 20,000); Drex Max says yes at 0.7, fixed in advance.

| Question and method | AUROC | AUPRC | Precision | Recall |
|---|---|---|---|---|
| Shared problem: Drex Max | 0.708 (0.697 to 0.719) | 0.610 (0.587 to 0.636) | 0.613 (0.589 to 0.639) | 0.496 (0.479 to 0.513) |
| Shared problem: R2 | 0.603 (0.593 to 0.611) | 0.480 (0.455 to 0.505) | 0.571 (0.544 to 0.598) | 0.432 (0.416 to 0.448) |
| Escalate: Drex Max | 0.790 (0.778 to 0.802) | 0.433 (0.411 to 0.455) | 0.418 (0.379 to 0.455) | 0.087 (0.077 to 0.097) |
| Escalate: R2 | 0.503 (0.498 to 0.508) | 0.220 (0.206 to 0.234) | 0.233 (0.205 to 0.261) | 0.066 (0.057 to 0.075) |

By true place (points; the intervals are in the scores file):

| True place | Alerts | Role, %: Drex Max | R1 | R2 | Action, %: Drex Max | Averted per 100: Drex Max |
|---|---|---|---|---|---|---|
| `at_lab_tube_station` | 1,805 | 74.6 | 100.0 | 86.5 | 52.9 | 6.1 |
| `in_lab_awaiting_scan` | 1,258 | 36.3 | 49.2 | 45.2 | 0.0 | 0.0 |
| `in_tube_system` | 1,035 | 78.5 | 100.0 | 69.1 | 45.8 | 1.3 |
| `lost` | 75 | 100.0 | 100.0 | 100.0 | 18.7 | 0.0 |
| `misrouted_elsewhere` | 100 | 66.0 | 100.0 | 98.0 | 0.0 | 0.0 |
| `still_at_unit` | 10,458 | 47.9 | 0.0 | 59.0 | 47.7 | 34.6 |
| `waiting_for_courier` | 3,268 | 96.9 | 100.0 | 99.7 | 96.9 | 35.9 |
| `with_courier` | 2,001 | 65.2 | 100.0 | 68.9 | 65.2 | 3.9 |

By route kind (points; the intervals are in the scores file):

| Route kind | Alerts | Role, %: Drex Max | R1 | R2 | Action, %: Drex Max | Averted per 100: Drex Max |
|---|---|---|---|---|---|---|
| `courier` | 12,114 | 66.9 | 38.4 | 73.4 | 66.7 | 35.0 |
| `tube` | 6,585 | 55.9 | 55.0 | 65.1 | 36.1 | 9.6 |
| `walk_in` | 1,301 | 35.0 | 48.5 | 49.9 | 34.0 | 9.5 |

The counterfactual behind "breaches averted" (`source: assumed`): acting on an appropriate action finds the specimen 10 minutes after the alert, and it is received 5 minutes later; for a lost specimen it starts a recollection, counted separately.

### X3: can a decision model forecast?

4,000 of 4,000 test ticks from 100 scenarios (0 refused or unanswered, left out for every method), 2,398 of them misses; the same ticks for all three, no threshold tuned.

| Method | AUROC | AUPRC | Brier | ECE |
|---|---|---|---|---|
| Drex Max | 0.669 (0.641 to 0.697) | 0.760 (0.726 to 0.790) | 0.408 (0.385 to 0.434) | 0.396 (0.369 to 0.425) |
| TracerNet | 0.763 (0.738 to 0.785) | 0.859 (0.837 to 0.876) | 0.264 (0.246 to 0.284) | 0.216 (0.200 to 0.238) |
| Analytic model, local refit | 0.702 (0.679 to 0.724) | 0.801 (0.777 to 0.823) | 0.281 (0.263 to 0.300) | 0.225 (0.204 to 0.251) |

No: on the same ticks Drex Max ranks the misses worse than TracerNet (AUROC difference -0.093 (-0.105 to -0.081)) and the local analytic refit (-0.033 (-0.047 to -0.018)). X3 compares Drex Max with the risk models; it leaves the primary-model verdict of section 1 as it is.

### X4: the distilled dispatcher

X4 (the distilled dispatcher) is not built: it is the first decision-layer job on the cut list (spec §18, item 2), and it matters only if X2's adoption rule adopts Drex Max on test. The rule did not hold on test, so X4 is not needed.

### Notice

Synthetic data: laboratory weeks designed by language models and simulated by this project's code, with no clinical review. Decision-model answers are zero-shot and their calibration on this task is measured, never assumed.

## 9. Data

Every parameter in this library is an assumption written by a language model for a synthetic laboratory simulator. No value comes from a real laboratory, and nothing in it had clinical review.

The library: 8,142 scenarios (7,092,570 bytes, SHA-256 `257290db3d69618b5a488e59011d5283a4a873b14bae94d6d2f86edcd87528f6`), clinical review: none. Prices verified at 2026-11-08T23:23:19-05:00.

| Job | Model arms | Tier | Briefs | Requests | Accepted | Acceptance | Repaired | Dropped | Refused | Rejections by layer | Spent | Per accepted |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| O1 | luna-low, sol-medium, terra-low | flex | 24 × 3 arms | 79 | 69 | 95.8% | 4 | 3 | 0 | adherence 3, plausibility 7 | $0.6003 | $0.008700 |
| O2 | terra-low | flex | 6,101 | 7,038 | 6,023 | 98.7% | 886 | 52 | 26 | adherence 57, plausibility 958 | $43.8659 | $0.007283 |
| O3 | terra-low | flex | 2,378 | 2,733 | 2,367 | 99.5% | 344 | 11 | 0 | adherence 23, plausibility 343 | $17.7077 | $0.007481 |

Accepted by job and model: O1 gpt-6-luna 22, O1 gpt-6.1-sol 24, O1 gpt-6.1-terra 23, O2 gpt-6.1-terra 6,002, O3 gpt-6.1-terra 2,071. By stratum: disrupted 1,533, edge 1,372, normal 2,590, stressed 2,647. By archetype: academic_medical_center 1,254, cancer_center 983, childrens_hospital 938, community_hospital 1,548, critical_access_rural 762, multi_site_system 301, safety_net_urban 1,434, surgical_specialty 922.

Coverage: NOT within tolerance; 20 of 32 stratum-by-archetype cells below their exact targets (disrupted/academic_medical_center 250 of 259, disrupted/community_hospital 300 of 306, edge/academic_medical_center 216 of 221, edge/childrens_hospital 157 of 159, edge/safety_net_urban 238 of 245, normal/academic_medical_center 395 of 455, normal/cancer_center 328 of 340, normal/childrens_hospital 303 of 340, normal/community_hospital 482 of 548, normal/critical_access_rural 236 of 251, normal/multi_site_system 103 of 106, normal/safety_net_urban 441 of 489, normal/surgical_specialty 302 of 323, stressed/academic_medical_center 393 of 406, stressed/cancer_center 314 of 315, stressed/childrens_hospital 298 of 313, stressed/community_hospital 498 of 538, stressed/critical_access_rural 259 of 261, stressed/multi_site_system 106 of 107, stressed/safety_net_urban 486 of 496).

Dispersion: Vendi score 61.9 on 2,000 library scenarios against 1,962.9 on 2,000 code-random ones; mean nearest-neighbor distance 3.46 against 20.71. Near duplicates left out: 22.

Splits: test 782, test_ood 849, train 5,420, val 1,091 scenarios in 7,310 families. archetype multi_site_system, or any scanner_downtime disruption, goes to test_ood.

### The pilot gate (O1)

| Arm | Briefs | Accepted | Spent | Per accepted | Eligible |
|---|---|---|---|---|---|
| luna-low | 24 | 22 | $0.0170 | $0.000772 | yes |
| sol-medium | 24 | 24 | $0.4188 | $0.017452 | yes |
| terra-low | 24 | 23 | $0.1645 | $0.007150 | yes |

Gate passed: yes; core and edge model: terra-low and terra-low.

### Round 2 (O3)

Planned 2,378 briefs from the validation weak spots `courier` (route kind), `high` (volume tercile), `tube` (route kind), `middle` (volume tercile); see results/neural/generation/round2.md.

### Spend

From the ledger mirror ledger.jsonl, replayed read-only:

| Job | Account | Cap | Spent | Held |
|---|---|---|---|---|
| O1 | openai | $3.00 | $0.6003 | $0.0000 |
| O1b | openai | $2.00 | $0.0000 | $0.0000 |
| O2 | openai | $44.00 | $43.8659 | $0.0000 |
| O3 | openai | $18.00 | $17.7077 | $0.0000 |
| O3b | openai | $7.00 | $0.0000 | $0.0000 |
| X1 | decide | $1.00 | $0.6961 | $0.0000 |
| X2 | decide | $4.50 | $2.1933 | $0.0000 |
| X3 | decide | $0.75 | $0.1003 | $0.0000 |
| X4 | decide | $1.75 | $0.0000 | $0.0000 |

Accounts: openai $62.1739 spent of $76.43; decide $2.9897 spent of $8.00.

### Evaluation sets

| Set | Clusters | Cluster | Scored specimens | Breaches | Never received | TracerNet scored |
|---|---|---|---|---|---|---|
| val | 1,091 | scenario | 6,111,584 | 456,728 | 26,665 | yes |
| test | 782 | scenario | 4,047,933 | 316,182 | 17,489 | yes |
| test_ood | 849 | scenario | 5,965,032 | 381,168 | 26,329 | yes |
| legacy_v1 | 6 | run | 2,378 | 100 | 47 | yes |
| control_v1world | 19 | run | 7,695 | 288 | 143 | yes |
| test_ood_feature | 482 | scenario | 3,084,251 | 236,605 | 13,481 | yes |

## 10. Limits and next steps

- Every number in this report comes from synthetic data: laboratory weeks designed by language models and simulated by this project's own code, with no clinical review. They describe the simulator, not any laboratory.
- `legacy_v1` has 6 clusters, so its intervals are wide.
- Under the strict 6-per-shift budget a laboratory with more breaches per shift than alerts gets no alerts from a method with no in-budget knob; section 1 counts those scenarios per method.
- The ablation arms were scored on test on a fixed salted share of its scenarios (25%; 196 scenarios), the same for every arm, so their intervals are wider than the main evaluation's.
- The ablation arms were scored on val on a fixed salted share of its scenarios (25%; 273 scenarios), the same for every arm, so their intervals are wider than the main evaluation's.
- The final ensemble trained with the searched peak learning rate (0.00499), found in search trials capped at 1.5 million samples. Under the 30-epoch schedule it stayed near its peak for hours and training was unstable: single-epoch validation spikes (val_sel 1.42, 7.96 and 1,444 against about 0.082) and surges in the mean training loss. Each member kept its best validation epoch (epochs 7, 11 and 3 for seeds 0, 1 and 2), as spec §7.7 sets. Searching the schedule at the length it runs is the next step.
- CPU inference is bit-reproducible on one processor type only. HiPerGator's AMD Rome nodes and its Intel B200 nodes load the feature table with last-bit differences, so a byte-for-byte rerun (spec §17 item 18) must run on the node type that produced the result; every evaluation here ran on AMD Rome nodes.
- On `test`, the first look's evaluation step ran out of memory after the risk table was scored and recorded in the lock; the evaluation was rerun on that recorded table and the run log lists it as a repeat with unchanged inputs. The explanation checks of section 7 add their own repeats.
- On `legacy_v1`, TracerNet's Brier score at 60 minutes of silence is 0.216 against the analytic model's 0.182, so its calibration does not carry over to the v1 generator's world.
- Ablation A8 is cut: it would train on the Claude-written v1 trails, which Anthropic's Usage Policy does not allow without prior authorization.
- The negative control's rule flags leakage, and the flag stands: flagged, explained, not resolved. On `control_v1world` TracerNet's median warning leads the local analytic refit by 10 minutes (95% interval 5 to 20). Against the globally fit analytic model, which reads no context at all, the lead is 0 minutes (-5 to 0) on the median and -1.58 minutes (-2.82 to -0.13) on the capped mean, so TracerNet matches a context-free fit there. The likely explanation is the local refit's small history (428 specimens of one replicate); this evidence fits that explanation but does not rule leakage out, and no check here resolves it.
- The ablation arms took 1.5 million samples each, against the final ensemble's 216 to 556 million, so they compare designs under a short, equal training budget rather than at convergence. Their compute came to 73.7 hours across arms, over spec §8.8's 6-hour box, most of it CPU scoring.

### Recommendation (spec §8.9)

The rule failed. The analytic model stays primary because R1 failed. R4 also failed, mostly because the baselines leave part of the 6-alert budget unused (the window rule alerts on nothing in 268 of 782 scenarios). TracerNet is reported as a challenger result.

**Hybrid.** §8.9's options fit this result imperfectly. This one is framed as "ties overall, leads on disruption slices", but TracerNet does not tie overall: it leads on capped mean warning (23.9 min against the best baseline's 13.7 at 6 alerts per shift, 3-2-1 weights), AUROC (0.957 against 0.917 at 30 minutes of silence) and Brier score (0.0473 against 0.0771 at 30 min and 0.0312 against 0.0427 at 60 min). The tie exists only in R1's floored median (section 1). It also leads on the disruption slice (capped mean warning 17.7 against the best baseline's 11.5). The option: score routine silence with the analytic model or the window rule and let TracerNet add a context alert when its p_miss exceeds the analytic risk by a fixed margin. Evaluate the blend on validation before claiming anything.

## 11. How to reproduce

```sh
python -m src.neural.pipeline freeze --jobs O1,O2,O3
python -m src.neural.pipeline simulate
python -m src.neural.pipeline store
python -m src.neural.pipeline train --hpo
python -m src.neural.pipeline train --rung A|B|C --seeds 0,1,2
python -m src.neural.pipeline select
python -m src.neural.pipeline calibrate
python -m src.neural.pipeline evaluate --split val --artifact artifacts/tracernet/v1 --matched
python -m src.neural.pipeline weakspots
python -m src.neural.pipeline evaluate --split test --artifact artifacts/tracernet/v1 --matched
python -m src.neural.pipeline evaluate --split test_ood --artifact artifacts/tracernet/v1 --matched
python -m src.neural.pipeline evaluate --split legacy_v1 --artifact artifacts/tracernet/v1 --matched
python -m src.neural.pipeline store --control
python -m src.neural.pipeline evaluate --split control_v1world --artifact artifacts/tracernet/v1 --store data/store-v2-control --train-store data/store-v2 --matched
python -m src.neural.pipeline ablate --set core
python -m src.neural.pipeline ablate --collect
python -m src.neural.pipeline explain --split test --matched
python -m src.neural.pipeline export --source legacy_v1
python -m src.neural.pipeline report
python -m src.neural.pipeline run --from-frozen --out SCRATCH
python -m src.neural.pipeline run --from-frozen --evaluate-committed --out SCRATCH
```

`report` renders this page byte for byte with the local ledger mirror (`data/openai/ledger.jsonl`, not committed), which section 9's spend table reads; without it, section 9 gives the library manifest's record of the ledger at the freeze instead.

### Hashes

| Input | SHA-256 or hash |
|---|---|
| Library | `257290db3d69618b5a488e59011d5283a4a873b14bae94d6d2f86edcd87528f6` |
| Policy | `9594d7c019363261` |
| Schema | `ac7d5fff8cc86db8` |
| Store behind `val` | `6def05dd713be943ec772539c5df8fa87da07b827f62b1863dacf4175d857cc3` |
| Store behind `test` | `6def05dd713be943ec772539c5df8fa87da07b827f62b1863dacf4175d857cc3` |
| Store behind `test_ood` | `6def05dd713be943ec772539c5df8fa87da07b827f62b1863dacf4175d857cc3` |
| Store behind `control_v1world` | `f31c00ec801bb1deda83517cc7b73bffb22f2bbff4cf41fef78fc0a1c240cf51` |
| Store behind `test_ood_feature` | `6def05dd713be943ec772539c5df8fa87da07b827f62b1863dacf4175d857cc3` |
| Artifact manifest | `d82402e37ca0ea76733ff6f1f180fa90a629753d55f3fc10b46a5d848ce2ad9d` |
| Artifact store | `e5a083c4088c0021` |
| Artifact splits | `2d495f203ad64fc8f08b9ee17a1df03143744a1f84a97f5ba0c31f36b5fdc09a` |

### The model

Rung C chosen by the rule of spec §7.9 (lowest val_sel: C); rung A val_sel 0.08347, rung B val_sel 0.08340, rung C val_sel 0.08245, rung L val_sel 0.09188. Warning check: run.
Search: complete 15, fail 0, pruned 9, running 0 trials; best val_sel 0.08750 (trial 21) with batch 2048, dropout 0.2809288560133388, eta 0.0, lam 2.0, learning_rate 0.004989069999481969, temporal_channels 48, weight_decay 0.00012124301372748123, width 128.

### Test runs

Test sets are scored under a hash lock; every rerun is listed in `results/neural/test_runs.jsonl`, and below. Locked first on `test` at 2026-11-10T01:25:46-05:00, relocked at 2026-11-10T16:42:32-05:00.

| Time | Set | Status | Reason |
|---|---|---|---|
| 2026-11-10T01:25:46-05:00 | test | first | first evaluation of a test set |
| 2026-11-10T01:29:30-05:00 | test_ood | repeat | inputs unchanged |
| 2026-11-10T06:12:32-05:00 | test | risk table recorded | the parts this run read, recorded once written |
| 2026-11-10T06:33:30-05:00 | test | repeat | inputs unchanged |
| 2026-11-10T06:35:57-05:00 | test_ood | risk table recorded | the parts this run read, recorded once written |
| 2026-11-10T07:02:25-05:00 | test | repeat | inputs unchanged |
| 2026-11-10T07:05:28-05:00 | test | repeat | inputs unchanged |
| 2026-11-10T07:14:47-05:00 | test | repeat | inputs unchanged |
| 2026-11-10T07:14:59-05:00 | test | repeat | inputs unchanged |
| 2026-11-10T08:09:24-05:00 | test | repeat | inputs unchanged |
| 2026-11-10T16:42:32-05:00 | test | relock | X2 on test adds the decision-model configuration hash (spec 8.7) |
| 2026-11-10T16:42:32-05:00 | test | risk table recorded | the parts this run read, recorded once written |
| 2026-11-10T17:42:25-05:00 | test | repeat | inputs unchanged |
| 2026-11-10T18:37:09-05:00 | test | repeat | inputs unchanged |
| 2026-11-10T19:26:37-05:00 | test | repeat | inputs unchanged |

Ablation test runs (each arm under its own lock, after the main lock):

| Time | Set | Status | Reason | Artifact |
|---|---|---|---|---|
| 2026-11-10T02:29:48-05:00 | test | first | first evaluation of a test set | `0ae9d5f7b2defd46` |
| 2026-11-10T02:30:16-05:00 | test | first | first evaluation of a test set | `de29a90b160bb8d1` |
| 2026-11-10T02:32:51-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T02:33:06-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T02:33:34-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T02:33:48-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T02:37:11-05:00 | test | first | first evaluation of a test set | `80929117d4affcf5` |
| 2026-11-10T02:41:00-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T02:41:16-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T02:42:08-05:00 | test | first | first evaluation of a test set | `6213f8ef7ea50d18` |
| 2026-11-10T02:45:13-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T02:45:29-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T02:46:25-05:00 | test | first | first evaluation of a test set | `4d5e96a6c708ef7c` |
| 2026-11-10T02:46:40-05:00 | test | first | first evaluation of a test set | `73c5f0eb3a1069ad` |
| 2026-11-10T02:49:29-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T02:49:44-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T02:50:02-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T02:50:16-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T03:45:28-05:00 | test | first | first evaluation of a test set | `c3293e48c8f46348` |
| 2026-11-10T04:12:51-05:00 | test | first | first evaluation of a test set | `0e26d7b5db65b956` |
| 2026-11-10T04:16:50-05:00 | test | first | first evaluation of a test set | `b32aa7f0e7a85699` |
| 2026-11-10T04:17:05-05:00 | test | first | first evaluation of a test set | `01c08faff6b28cca` |
| 2026-11-10T04:17:47-05:00 | test | first | first evaluation of a test set | `53a7e3aac2c1ba0e` |
| 2026-11-10T04:23:32-05:00 | test | first | first evaluation of a test set | `4e8cea8418be4143` |
| 2026-11-10T04:24:22-05:00 | test | first | first evaluation of a test set | `2395cbcb3f02cbe7` |
| 2026-11-10T04:24:53-05:00 | test | first | first evaluation of a test set | `ef6a2842dc1d248c` |
| 2026-11-10T04:25:02-05:00 | test | first | first evaluation of a test set | `123de65a2ad976d8` |
| 2026-11-10T04:25:53-05:00 | test | first | first evaluation of a test set | `77bb52a5385bf627` |
| 2026-11-10T04:29:17-05:00 | test | first | first evaluation of a test set | `3c444fccf2f3e2ab` |
| 2026-11-10T04:29:20-05:00 | test | first | first evaluation of a test set | `325b67720ebe5ac5` |
| 2026-11-10T04:29:36-05:00 | test | first | first evaluation of a test set | `e99dbefb29f8fe9b` |
| 2026-11-10T04:32:49-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T04:33:07-05:00 | test | first | first evaluation of a test set | `cda476c4d2999bb8` |
| 2026-11-10T04:33:09-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T04:34:46-05:00 | test | first | first evaluation of a test set | `b1e74b056389b5d2` |
| 2026-11-10T04:34:52-05:00 | test | first | first evaluation of a test set | `090dcc6c716fd139` |
| 2026-11-10T04:34:54-05:00 | test | first | first evaluation of a test set | `10ec19463ab35d5d` |
| 2026-11-10T04:48:11-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T04:48:27-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T04:52:46-05:00 | test | first | first evaluation of a test set | `8c8de4ff8f8dc280` |
| 2026-11-10T04:54:14-05:00 | test | first | first evaluation of a test set | `52f1974286e1a53d` |
| 2026-11-10T04:55:46-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T04:55:59-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T04:56:21-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T04:56:36-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T04:57:26-05:00 | test | first | first evaluation of a test set | `bfef32b654328ff9` |
| 2026-11-10T04:58:23-05:00 | test | first | first evaluation of a test set | `90f97592c2f73991` |
| 2026-11-10T05:00:42-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:00:58-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:03:36-05:00 | test | first | first evaluation of a test set | `feea3bb0edeaf433` |
| 2026-11-10T05:04:38-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:04:40-05:00 | test | first | first evaluation of a test set | `47da4cab37c6baf2` |
| 2026-11-10T05:04:50-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:04:57-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:05:05-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:06:20-05:00 | test | first | first evaluation of a test set | `1349a125d83ee97d` |
| 2026-11-10T05:07:18-05:00 | test | first | first evaluation of a test set | `ca1e72f4f78969b9` |
| 2026-11-10T05:08:53-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:09:09-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:09:17-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:09:27-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:09:30-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:09:44-05:00 | test | first | first evaluation of a test set | `af3184552680b8c3` |
| 2026-11-10T05:09:45-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:10:02-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:10:19-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:11:18-05:00 | test | first | first evaluation of a test set | `bdc5f8ad5d38aecb` |
| 2026-11-10T05:12:04-05:00 | test | first | first evaluation of a test set | `95c778867d111c5a` |
| 2026-11-10T05:13:02-05:00 | test | first | first evaluation of a test set | `106da81b37187469` |
| 2026-11-10T05:15:30-05:00 | test | first | first evaluation of a test set | `ad682a20ba760e6b` |
| 2026-11-10T05:15:41-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:15:57-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:16:44-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:17:00-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:17:28-05:00 | test | first | first evaluation of a test set | `e32e2494bbbe2444` |
| 2026-11-10T05:21:21-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:21:41-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:23:10-05:00 | test | first | first evaluation of a test set | `0dda12ceb3cfbd79` |
| 2026-11-10T05:23:53-05:00 | test | first | first evaluation of a test set | `86407e7bd5b657f2` |
| 2026-11-10T05:24:22-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:24:38-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:24:45-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:24:49-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:25:03-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:25:04-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:28:11-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:28:27-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:31:04-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:31:16-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:36:35-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:36:51-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:37:46-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:38:04-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:46:44-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:47:00-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:49:11-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:49:26-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:50:43-05:00 | test | first | first evaluation of a test set | `115cfed8ae45da5d` |
| 2026-11-10T05:50:54-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:51:11-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:52:36-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:52:52-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:54:52-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:55:09-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:59:00-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T05:59:18-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T06:02:10-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T06:02:27-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T06:03:11-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T06:03:26-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T06:06:09-05:00 | test | first | first evaluation of a test set | `086d60e9d40c3412` |
| 2026-11-10T06:06:43-05:00 | test | first | first evaluation of a test set | `4a4bd58a3565c915` |
| 2026-11-10T06:07:27-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T06:07:46-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T06:12:04-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T06:12:23-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T06:18:04-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T06:18:22-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T06:20:04-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T06:20:24-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T06:41:39-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T06:41:54-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T06:44:58-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T06:45:22-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T06:48:50-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T06:49:06-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T08:13:59-05:00 | test | first | first evaluation of a test set | `5ce7b138688d1183` |
| 2026-11-10T10:19:54-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |
| 2026-11-10T10:20:12-05:00 | test | risk table recorded | the parts this run read, recorded once written | `` |

## Appendix: acceptance criteria (spec §17)

Audited on November 10, 2026, on branch `n11-closeout` at commit `ac94969` (main `95b4ac8` plus two N11 commits). Revised on November 11, 2026, after an independent review of the report: items 14 and 18 moved from held to partly held, item 6 states the truth-log rule as it holds, and items 12, 13, 20 and 21 were brought up to date; after a second review, item 12's criterion was restored to the spec's text and item 20's counts were refreshed. No result was rerun; PR metadata was read. Each item gives the criterion in short form, the verdict and the evidence. Evidence is labeled by where it comes from:

- **Run now:** a command or test run for this audit, with its result.
- **Parent's run:** a check the parent session ran, with its log; not rerun here.
- **Record:** a committed file and the field that shows it.
- **Build log:** an entry in `docs/neural/build-log.md`, which is a claim, not a check run here.

A check that nobody ran is reported as not run. Every number comes from synthetic data with no clinical review.

### 1. Keys

**Criterion.** `.env` untracked and ignored; `.env.example` lists the three key names with empty values; the extended key test passes; a full-history scan of every `.env` value finds none.

**Verdict: held.**

- Run now: `git ls-files .env` lists nothing; `git check-ignore -v .env` gives `.gitignore:22:.env`; no commit on any ref touches `.env`.
- Run now: `.env.example` holds exactly `ANTHROPIC_API_KEY=`, `OPENAI_API_KEY=` and `OPENROUTER_API_KEY=`.
- Run now: `pytest tests/test_no_key_in_site.py -k "not value_of_each_key"`: 9 passed, 3 deselected (key shapes for every provider, ordinary words left alone, nothing under `docs/` holds a key or names an API host).
- Run now: `pytest tests/test_key_history_check.py` (fake keys in a temporary repository): 2 passed.
- Parent's run (main checkout at `95b4ac8`, with `.env`): `pytest tests/test_no_key_in_site.py`: 12 passed, the 3 value checks included (each `.env` value against every tracked file).
- Parent's run: `python scripts/key_history_check.py --repo .`: 3 key values searched in every blob of 128 commits on every ref, none found. The scanner was checked by a probe value known to be in the history, which it found in 4 files.

### 2. OpenAI spend

**Criterion.** Settled total never above $74.00; each job within its cap; the O1 dry run and the gate come before O2's first request; the manifest records the reconciliation.

**Verdict: partly held.** The spend limits and the order held. The manifest records settled spend per job and in all, but not per model beside the token sums, which is how §4.6 describes the reconciliation it should record (this matters only for O1, the one job with three models). The order is shown by stage markers, dry-run files and the gate's commit, not by ledger entries: the ledger holds money entries only.

- Run now (main checkout): `python -m src.neural.pipeline status` and `ledger report`: account `openai` spent $62.1739 of $76.43 (margin $2.43, so $74.00 usable), $0.0000 held. Jobs: O1 $0.6003 of $3.00, O2 $43.8659 of $44.00, O3 $17.7077 of $18.00, O1b and O3b $0.0000 and never released.
- Run now (read-only SQLite query of `data/openai/ledger.sqlite`): entry kinds are reserve 52,376, settle 52,292, release 52,292 and void 84, with no `adjust` entry, so the settled total only rises and its final $62.1739 is its maximum.
- Run now: the O1 dry run finished at 10:08 PM on November 8 (stage marker `dry-run-O1`, `data/openai/dryrun-O1.json`); O1's ledger entries run from 10:20 PM to 10:24 PM; the gate (`results/neural/generation/pilot.json`, `"passed": true`) was committed at 10:39 PM (`fc759a9`); O2's dry run finished at 11:23:22 PM and O2's first ledger entry is at 11:23:38 PM. The `generate` command refuses O2 unless that gate file says passed (`src/neural/pipeline.py`, `needs == "pilot_gate"`).
- Record: `datasets/scenarios-v2.manifest.json`, field `ledger`: spend per job and $62.173885 in all against the $76.43 cap; no per-model spend or token sums. The `ledger report` run above prints them (O1: `gpt-6-luna` $0.0170, `gpt-6.1-sol` $0.4188, `gpt-6.1-terra` $0.1645).
- Build log, November 9, 1:02 AM: after O2, OpenAI's usage page and the ledger were $0.51 (1.2%) apart, inside the $1 and 5% limits. O3's total was not compared with the usage page (November 9, 12:32 PM entry). Daniel has since waived that comparison as a stop condition, so it is not counted as a failure.

### 3. Library

**Criterion.** At least 2,000 accepted from O2; every scenario re-validates at load; every factor marginal at least 90% of target; the manifest has every field of §4.11.

**Verdict: partly held.** The O2 count, re-validation and marginals held. The manifest breaks requested, repaired and dropped counts out by job only, while §4.11 asks for them by job, model, stratum and archetype; accepted counts have every breakdown. Note: the load path checks the library's SHA-256 against the splits file and the simulation manifest; it does not rerun the validator. The re-validation below was run for this audit.

- Record: manifest `jobs.O2.counts.accepted` 6,023 (6,002 in the library after near duplicates and family bridging were left out).
- Run now: every one of the 8,142 committed scenarios through validation layers 1 to 5 (schema, semantics, names, plausibility by simulation, brief adherence), as `src/scenarios/freeze.py` `_revalidate` runs them: 8,142 passed, 0 failed, 185 s on 6 workers.
- Run now: all 34 factor levels in manifest `coverage.marginals` are `ok`; the lowest is `stratum/normal` at 2,590 of 2,852 (90.8%). Not part of this criterion: 3 of the 32 stratum-by-archetype cells are under 90% of target (normal academic medical center 395 of 455, normal children's hospital 303 of 340, normal community hospital 482 of 548), so `coverage.ok` is false; `REPORT.md` section 9 states this.
- Run now: the manifest has a field for every item §4.11 lists: `sha256`, `bytes`, `created_at`, `jobs` (model and effort in `arms`, `tier`, `prompt_hashes`), `schema_hash`, `policy_hash`, `planner` (seeds, config hash), counts (`jobs.*.counts`: requests, accepted, repaired, dropped, refused per job; `accepted_by_job_and_model`, `accepted_by_stratum`, `accepted_by_archetype`), rejection reasons (`rejections_by_layer`), `near_duplicates`, `coverage`, `dispersion`, `ledger`, `prices_verified_at`, `splits` (family and split counts), `ood_definition`, `"clinical_review": "none"` and `notice`. The missing breakdowns are the ones named in the verdict.

### 4. Simulator

**Criterion.** Byte-identical reruns; v1-world passes the v1 validator 100%, its leg KS checks and its breach-share interval; the v2 validator passes on every replicate; 2 seconds or less per 1,000-specimen week.

**Verdict: held.**

- Run now: `pytest tests/test_sim_engine.py tests/test_sim_v1_compat.py`: 64 passed in 8.2 s, including `test_reruns_are_byte_identical`, `test_library_files_are_byte_identical_across_runs`, `test_every_v1_world_trail_passes_the_v1_validator`, the six leg KS tests, `test_the_breach_share_is_inside_the_99_percent_interval_around_v1s` and `test_one_week_runs_at_two_seconds_or_less_per_thousand_specimens`.
- Run now: one core, 60 library scenarios drawn with seed 0, one 7-day scored replicate each: 153,178 specimens in 5.43 s, 0.036 s per 1,000 specimens pooled (median 0.027, slowest scenario 0.137).
- Parent's run (`rebuild.log`, HiPerGator, code `50b72fa`): a full rebuild from the frozen library wrote 27,148 of 27,148 replicates with 0 dropped (the simulator runs the v2 validator on every replicate before writing it), and its simulation manifest equals the committed round-two one on all 16 keys, every per-file SHA-256 included. The store's 10 DuckDB tables are content-equal (`store-content.log`); the `.duckdb` bytes differ because DuckDB's file layout varies.

### 5. Splits

**Criterion.** No family in two splits; the OOD rule holds; O3 families only in train and validation; the splits file committed and hashed into the artifact.

**Verdict: held.**

- Run now, over `datasets/scenarios-v2.jsonl.gz` and `datasets/scenarios-v2.splits.json`: 8,142 scenarios in 7,310 families, 0 families in more than one split, 0 scenarios whose split differs from their family's.
- Run now: all 482 scenarios with the OOD feature (a `multi_site_system` archetype or a `scanner_downtime` disruption) are in `test_ood`; the other 367 `test_ood` scenarios travel with their families.
- Run now: the 2,071 O3 scenarios are 1,752 in train and 319 in validation; their 2,012 families are 1,702 train and 310 validation.
- Run now: the artifact's `manifest.json` field `splits_sha256` (`2d495f20…`) equals the SHA-256 recomputed from the committed splits file's scenario-to-split map, as `src/pipeline/store.py` hashes it; the test lock records the splits file's own SHA-256 (`c88626dc…`), which matches the committed file.
- Run now: `pytest tests/test_store_v2.py tests/test_freeze.py` passed (in the 284-test run of item 11).

### 6. Isolation

**Criterion.** `test_truth_isolation.py` passes unchanged; `test_neural_isolation.py` passes; only `store.py` and `evaluate.py` mention the truth file.

**Verdict: held**, with the rule stated as it holds: no truth log on the training or feature path. Training, the features and everything under `src/models/` and `src/decide/` read the observed store only. The truth log does reach the build through evaluation: the orchestration files in `src/neural/` (`pipeline.py`, `rebuild.py`, `ablations.py`, `ensemble.py`, `weakspots.py` and `report.py`) import `src/pipeline/neural_eval.py`, which imports `evaluate.py` and scores through it. Rung selection's warning check (`ensemble.py`) and round two's weak spots (`weakspots.py`, which planned job O3's briefs) used validation metrics computed against the truth log.

- Run now: `pytest tests/test_truth_isolation.py tests/test_neural_isolation.py`: 38 passed.
- Run now: `git log -- tests/test_truth_isolation.py` shows its last change on October 30 (`c98f2ef`), before this work began at `8686475`.
- Run now: in `src/` and `scripts/`, only `src/pipeline/store.py` names `truth.duckdb`; the only caller of `store.open_truth` is `src/pipeline/evaluate.py`.

### 7. Features

**Criterion.** The future-invariance test passes; shuffled labels give validation AUROC within 0.50 ± 0.02.

**Verdict: held.** The test measures AUROC within (window, tick) strata, since the masked head gives every specimen of a stratum the same structural risk; pooled over strata the shuffled-label AUROC is 0.896, which the test expects (`pooled > 0.8`).

- Run now: `pytest tests/test_features.py tests/test_training_smoke.py -s`: 24 passed in 19.4 s, including `test_every_feature_at_tau_ignores_everything_after_tau`.
- Run now, printed by `test_shuffled_labels_give_validation_auroc_near_one_half`: AUROC within strata 0.4985 with shuffled labels (pooled 0.8964), 0.8241 with real labels.

### 8. Training

**Criterion.** The CPU smoke test passes in under 60 seconds and is bit-reproducible; resume equals an uninterrupted run on CPU; MPS reruns match `val_sel` within 0.5%.

**Verdict: held**, on the smoke test's tiny store. The final ensemble trained on CUDA (B200 GPUs), not MPS, so no full-size MPS rerun exists.

- Run now (same 24-test run as item 7): `test_two_cpu_epochs_run_in_under_60_seconds_and_the_loss_falls`, `test_cpu_training_is_bit_reproducible` and `test_a_run_interrupted_after_epoch_1_and_resumed_equals_an_uninterrupted_run` passed; both files together took 19.4 s.
- Run now: on the same tiny store, rung A seed 0 trained twice on this Mac's MPS device and once on CPU (2 epochs each). Best `val_sel` 0.1306258634 and 0.1306258627 on MPS, a relative difference of 5.6e-9; CPU gave 0.1335 (2.1% from MPS, which this criterion does not bound).
- Build log, November 9, 9:37 PM: the one cross-device rerun at full size, ablation arm A5-C seed 0, gave a best `val_sel` of 0.08634 on CPU and 0.08654 on a GPU (0.23% apart).

### 9. Artifact

**Criterion.** Rungs and selection recorded; the ensemble artifact, manifest and model card committed, 10 MB or less; loading under a different policy hash refuses.

**Verdict: held.**

- Record: `results/neural/selection.json` (`chosen: "C"`, all four rungs with `val_sel`, the warning check run) and `results/neural/runs.csv` (31 rows: 24 search trials, 4 ladder rungs and 3 final seeds).
- Run now: `git ls-files artifacts/tracernet/v1` lists all 7 files, `MODEL_CARD.md` and `manifest.json` included; they total 5,103,933 bytes (5.10 MB). The manifest records seeds 0, 1 and 2, rung C and policy `9594d7c019363261`.
- Run now: `pytest tests/test_predictor.py` passed (in the 76-test run of item 16), including `test_loading_under_a_different_policy_hash_refuses`, `test_a_feature_schema_version_mismatch_refuses` and `test_f003_an_artifact_file_that_differs_from_its_manifest_refuses_to_load`.

### 10. Calibration

**Criterion.** Validation expected calibration error of `p_miss` 0.02 or less after calibration, or the residual stated with its reason.

**Verdict: held.** Note: the temperature was fitted on a sample of 3,000,000 of 67,286,117 validation ticks, not on every validation tick as §7.6 describes.

- Record: `artifacts/tracernet/v1/manifest.json`, `validation`: expected calibration error 0.0170 before and 0.0138 after (temperature 1.113, isotonic not needed); `calibration.json` `ece_limit` 0.02.
- Record: `results/neural/val/evaluation.json`, `calibration.tracernet`: 0.0107 at 30 minutes of silence (3,152,487 ticks) and 0.0101 at 60 minutes. On `test`: 0.0108 and 0.0091.

### 11. Evaluation

**Criterion.** Every method of §8.2 on every set of §8.1 at budgets 1, 2, 4 and 6 and both weightings, with §8.4's metrics and §8.5's intervals; the vectorized code equals `evaluate.py` exactly on `legacy_v1`.

**Verdict: partly held.** Coverage and equivalence held. Two gaps: AUROC, AUPRC, Brier score and expected calibration error have point values with no interval, while §8.5 asks for intervals on every metric; and reliability plots exist for `test` only.

- Run now, over the five committed `results/neural/{val,test,test_ood,legacy_v1,control_v1world}/evaluation.json`: each holds all six required methods (TracerNet at 3-2-1, equal and 9-3-1 weights; analytic global and local at each weighting; window rule; quantile local and global) at 1, 2, 4 and 6 alerts per shift and 5, 10 and 30 per 100 specimens, with all 16 alert metrics of §8.4 and a 95% interval on each of their 1,344 values per set; TracerNet-minus-baseline differences with intervals and `share_ahead` from 2,000 resamples; discrimination at 15, 30, 60 and 90 minutes and pooled; calibration at 30 and 60 minutes; the nine slices of §8.4 (five on `legacy_v1`, whose store lacks the v2 fields).
- Run now: in `results/neural/test/evaluation.json`, 72 discrimination and calibration values (AUROC, AUPRC, Brier, expected calibration error) carry no interval. `results/neural/plots/` holds `reliability-test-30.svg` and `reliability-test-60.svg` only.
- Not part of the core methods: A9 (optional) was not run; the decision model's zero-shot forecast (X3) was scored on a 4,000-tick sample in `results/neural/decisions/X3-test-scores.json`.
- Run now: `pytest tests/test_neural_eval.py tests/test_store_v2.py tests/test_freeze.py tests/test_ledger.py tests/test_decide.py tests/test_dispatch_eval.py tests/test_ablations.py`: 284 passed in 60 s, including `test_the_vectorized_score_equals_evaluate_score_exactly` and `test_the_vectorized_bootstrap_equals_evaluate_bootstrap_exactly` at every budget.

### 12. Verdict

**Criterion.** R1 to R4 evaluated and quoted verbatim; the test set scored once, or every rescore listed with its reason.

**Verdict: held.** The test set was scored under a hash lock, and every rerun is listed with its reason in `results/neural/test_runs.jsonl`.

- Run now: the 11 quoted lines of the rule in `results/neural/REPORT.md` section 1 are identical to spec §8.6.
- Record: `REPORT.md` section 1 evaluates R1 (failed), R2 (held), R3 (held) and R4 (failed), each with its table. The analytic model stays primary because R1 failed: spec §8.6 lets R1 to R3 decide, and R4 only qualifies the comparison.
- Record: `results/neural/test_runs.jsonl` holds 15 entries (1 first look, 10 repeats with inputs unchanged, 3 risk tables recorded, 1 relock), each with its reason; `REPORT.md` section 11, "Test runs", lists all 15. The relock at 4:42 PM on November 10 gives its reason: X2 on test adds the decision-model configuration hash (spec §8.7). The ablation arms' 129 runs under their own locks are listed after it.

### 13. Honesty

**Criterion.** The report opens with the verdict and the synthetic-data notice, and gives §8.9's recommendation when the rule fails.

**Verdict: held.**

- Record: `REPORT.md`'s first paragraph gives R1 to R4 with numbers and interval ends, labels the 6-alert budget as chosen, names the analytic model as primary because R1 failed, explains why every R1 median is 0, and ends with the notice that every number comes from synthetic laboratory weeks with no clinical review. A list under it keeps the leakage flag, the `legacy_v1` calibration, the coverage shortfall, the training spikes and the failed deletion check in view.
- Record: `REPORT.md` section 10, "Recommendation (spec §8.9)": the analytic model stays primary because R1 failed, and the hybrid option is recommended as the nearest of §8.9's options, which fit this result imperfectly. TracerNet leads overall on capped mean warning, AUROC (0.957 against 0.917 at 30 minutes of silence) and Brier score, ties only in R1's floored median, and leads on the disruption slice (capped mean warning 17.7 minutes against the best baseline's 11.5).

### 14. Ablations

**Criterion.** A1, A2, A5 and A6 done; the others done or marked "not run (time box)"; the negative control reported.

**Verdict: partly held.** A1, A2, A5 and A6 are done and the negative control is reported. Two parts do not hold as written: A3 and A4 are marked "not run (not buildable in time)", since the pipeline could not build their inputs, rather than dropped for the time box; and the arms used 73.7 hours of compute against §8.8's budget of 6 hours (`REPORT.md` sections 6 and 10). A7, A8 and A9 are marked "not run" with their reasons (A7 outside the core set, A8 cut under Anthropic's Usage Policy, A9 first on the cut list).

- Record: `results/neural/ablations.json`: A1 (4 arms), A2 (2 arms), A5 (rungs L, A, B, C and the ensemble) and A6 (5 arms) are `done`, three seeds per arm except the A5 ensemble, all at 733 steps (`steps_equal: true`).
- Record: `ablations.A6.negative_control`: on `control_v1world` at 6 alerts per shift, TracerNet's median warning leads the local analytic refit by 10 minutes (95% interval 5 to 20), flagged `leakage_suspected: true`; `REPORT.md` section 10 reports the flag with the evidence that the lead over the context-free global fit is 0 minutes. The flag stands: flagged, explained, not resolved.
- Run now: `pytest tests/test_ablations.py` passed (in the 284-test run of item 11).

### 15. Explanations

**Criterion.** Completeness within 1% for the median alert; deletion beats random in at least 70% of alerts; the randomization check passes; every test alert has factors.

**Verdict: failed, on deletion.** The other three parts held.

- Record: `results/neural/explanations.json`, `checks`, over the 264,850 attributed `test` alerts at 6 per shift: completeness median relative gap 0.93% against 1% (passed); deletion: the top 3 groups beat 3 random groups in 68.2% of alerts against the required 70% (failed), although their mean drop in `p_miss` is 0.0346 against 0.0116; randomization: mean Spearman correlation -0.0006 over 927 alerts, below 0.3 (passed). Top-level `passed: false`.
- Record: `alerts_given` 287,374 and `alerts_with_factors` 287,374; the 22,524 alerts at a closed window carry one window factor.
- Run now: `pytest tests/test_explain.py` passed (in the 76-test run of item 16).

### 16. Interface

**Criterion.** The Python API, the command line and both schemas exist; sample outputs and `docs/demo/neural.json` validate; `bundle.json` is unchanged.

**Verdict: held.** The sample outputs are the ones the tests write and validate on a tiny store; no sample prediction or alert files are committed.

- Run now: `src/models/tracernet/predictor.py` defines `Predictor.load`, `score`, `alerts` and `explain`; `src/models/tracernet/predict.py` is the command line of §10.2; `docs/neural/schemas/prediction.schema.json` and `alert.schema.json` are well-formed Draft 2020-12 schemas.
- Run now: `pytest tests/test_predictor.py tests/test_predict_cli.py tests/test_export_neural.py tests/test_explain.py tests/test_tracernet.py tests/test_calibration.py`: 76 passed in 67 s, including `test_run_writes_the_three_files_and_each_validates`, `test_the_schema_is_valid_and_both_sources_validate` and `test_main_writes_only_where_told_and_the_bundle_stays_byte_identical`.
- Run now: `docs/demo/neural.json` (2,378 `legacy_v1` specimens) against `docs/demo/neural.schema.json` with `jsonschema`: 0 errors.
- Run now: `git diff --stat 8686475 HEAD -- docs/demo/bundle.json` is empty; the file last changed on October 30 (`dd4bfff`).

### 17. Decision layer

**Criterion.** Spend within $8.00; outputs frozen; the X2 adoption verdict and the X3 comparison in the report, or "not run" with the reason; the network's pipeline runs with no OpenRouter key.

**Verdict: held.**

- Run now (main checkout, `status`): account `decide` spent $2.9897 of $8.00; X1 $0.6961 of $1.00, X2 $2.1933 of $4.50, X3 $0.1003 of $0.75, X4 $0.0000.
- Run now: `git ls-files results/neural/decisions` lists 13 frozen output files.
- Record: `REPORT.md` section 8: X2 fails its adoption rule on test (Drex Max minus rule R2 is -7.9 points of role accuracy, 95% interval -8.9 to -6.9), so exports dispatch by R2; X3's AUROC is 0.669 against TracerNet's 0.763, a difference of -0.093 (-0.105 to -0.081); X4 is not built and the report says why.
- Parent's run: `scripts/clean_clone.sh` ran the whole suite in a clone with no `.env`, so with no key at all, and passed. The test runs of this audit were also made in a worktree with no `.env` (every key is read from the repository's own `.env` path).

### 18. Single command

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

### 19. Existing guarantees

**Criterion.** Full pytest passes with no drop in the collected count; `scripts/clean_clone.sh` passes; the plan check passes; the exhibit's `npm test` passes.

**Verdict: held** at N11's final code commit, `26b1044`.

- Parent's run at `26b1044` (4:04 PM to 4:13 PM, November 11): the full suite gave 1,737 passed and 3 skipped (the key-value checks, which need `.env` and passed separately under item 1); `scripts/clean_clone.sh .` passed, its fresh checkout reproducing every committed output and passing 1,737; the exhibit's `npm test` passed 51. The suite has grown from 1,509 after N8 to 1,737, so the collected count never dropped.
- Run now: `pytest --collect-only -q` collects 1,711 tests at `ac94969` (599 when this work began, build log November 8).
- Run now: `node visuals/plan.check.cjs`: "plan data is consistent", exit 0.
- Run now: `npm test` in `visuals/exhibit-3d` (in the main checkout, whose exhibit files equal `95b4ac8`; the worktree has no `node_modules`): 3 files, 51 passed.

### 20. Process

**Criterion.** Every pull request merged to `main` and none open; no `Co-Authored-By` trailer and no "Generated with" or "Generated by" line in any commit message, title or body of this work; a teaming-log entry per pull request; tasks N0 to N11 ticked with their checks; `AGENTS.md` updated.

**Verdict: partly held.** Every pull request through #64 is merged, none carries a trailer or a "Generated" line, and each has its teaming-log entry. The ticks are the part that does not hold from here: the published plan belongs to another organization, whose policy keeps its tick database closed to Claude Code even with editor access, so N0 to N11 are ticked by hand in the browser.

- Run now (Nov 10 audit): `gh pr list --state all`: 62 pull requests, all merged; `gh pr list --state open` is empty. This work's #49 to #62 all merged into `main`.
- Run now (Nov 10 audit): `git log 8686475..HEAD --format=%B` (16 commits) has no `Co-Authored-By`, "Generated with" or "Generated by" line, and no title or body of #49 to #62 has one. Outside this work: the bodies of #37 and #39 (October 30) ended with "Generated by Claude Code" (both lines were removed on November 11, for the revision), and commit `905b3c4` (October 28) uses "generated with the Claude API" in prose.
- Run now (Nov 10 audit): `docs/ai-teaming-log.md` has 14 entries for the 14 pull requests #49 to #62 (N0, the plan, N1, N2, N3, N4, two spend-path reviews, N5 to N10).
- Run now (Nov 11 revision, PR metadata read at 6:57 PM EST): `gh pr list --state all`: 64 pull requests, all merged, none open, before this revision's own pull request (#65). `git log 8686475..main --format=%B` (16 commits, #49 to #64) has no `Co-Authored-By`, "Generated with" or "Generated by" line, and no title or body of #49 to #64 has one. `docs/ai-teaming-log.md` has 16 entries for #49 to #64, N11 and the first review revision included; #65 adds its own.
- Run now (Nov 10 audit): `AGENTS.md` (local only, untracked) was last changed at 11:43 PM on November 10 and states the test verdict, the failed deletion check and the decision layer's result.
- Parent's run: reading the plan's tick database (`state/main`) was refused ("shared with you from another organization"), so whether N0 to N11 are ticked could not be checked here. Republishing the plan was refused too: the live page is at a newer version whose source this machine could not receive, and overwriting it unseen would discard that version.
- Parent's run: N11's commits and pull request carry no trailer and no "Generated" line, and its teaming-log entry is in place.

### 21. Timeline

**Criterion.** N9 merged by Friday, November 13; N11 by Saturday, November 14.

**Verdict: held.** N9 merged on November 10, three days early; N11 (#63) merged on November 11, three days before its deadline.

- Run now (`gh pr list`): N9, #61, merged at 11:27 PM EST on November 10; N10, #62, at 11:39 PM EST on November 10; N11, #63, at 4:14 PM EST on November 11 (`gh pr view 63`, read for the revision).

### Summary

Of the 21 criteria, 14 held (items 1, 4 to 10, 12, 13, 16, 17, 19 and 21), 1 failed and 6 held in part.

- **Failed: item 15, explanations.** The deletion check found the top three factor groups lower `p_miss` more than three random groups in 68.2% of test alerts, below the required 70%. Completeness (0.93%), randomization (Spearman -0.0006) and factors on every alert held.
- **Partly held: item 2, OpenAI spend.** Every cap held ($62.1739 of the $74.00 usable); the manifest records spend per job but not per model with token sums.
- **Partly held: item 3, library.** Every scenario re-validates and every marginal is at least 90% of target; the manifest gives requested, repaired and dropped counts by job only.
- **Partly held: item 11, evaluation.** Discrimination and calibration metrics have no bootstrap intervals, and reliability plots exist for `test` only.
- **Partly held: item 14, ablations.** A3 and A4 were not buildable in time, and the arms used 73.7 hours of compute against a 6-hour budget.
- **Partly held: item 18, single command.** The file the criterion names does not exist, and the validation match needed three fields carried over.
- **Partly held: item 20, process.** The plan's ticks could not be read or written from here (another organization's policy), so N0 to N11 are ticked by hand.
