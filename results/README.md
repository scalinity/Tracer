# Results

Every number here comes from synthetic data that had no clinical review, and every parameter behind it is an assumption. The first end-to-end results are saved in `mvp-nov8.json` (tag `v0.1-mvp`). That file is the midterm snapshot: the December 2 results are compared against it and it is never edited. Warning is the minutes from the alert to the stability window closing, and in the "all breaches" median a breach that was never alerted counts as zero.

**Headline, midterm snapshot.** On the 6 held-out runs (2,378 specimens with a clock and 100 breaches, 0.79 breaches per 8-hour shift), the risk model's median warning over all breaches was 55 minutes at 6 alerts per shift against 30 minutes for the plan's per-group quantile rule, a lead of 20 to 25 minutes (95% paired bootstrap over the runs); at equal urgency weights it was 45 minutes against 30, a lead of 10 to 15 minutes. That clears the bar set in advance, at least 5 more minutes of median warning at the same alerts per shift, at 2, 4 and 6 alerts per shift and at both weightings. The result beside it matters as much. The plan's rule ignores the stability window, and a rule that alerts once a fixed share of the window has passed, set to the same alert budget on the fit runs, reaches 45 minutes at 6 alerts per shift. The model's lead over that window-aware rule is 5 to 10 minutes at weights 3, 2, 1 and none at equal weights, where the two tie at 6 and at 4 alerts per shift (45 and 40 minutes) and the model trails by 5 minutes at 2. Most of the gain over the plan's rule therefore comes from using the window, and the weights move the headline: the 55 minutes comes from giving tiers 1 and 2 more warning while tier 3, 61 of the 100 breaches and mostly urine, gets 40 minutes against 45 at equal weights. Read every number with these limits: the data and the model share assumptions because both are ours, so these are best-case figures; 6 alerts per shift is about a third of all specimens (the held-out volume at that setting was 6.4); the Brier score is only slightly better than the base rate (0.109 against 0.119 at 30 minutes of silence, 0.182 against 0.187 at 60), so the gain is in timing, not in sorting; and the 33 specimens the validator dropped lean toward long delays.

Median warning over all breaches in minutes, with breaches caught of 100 in brackets, on the held-out runs:

| Alerts per shift | Plan's baseline | Window-aware baseline | Model, equal weights | Model, weights 3, 2, 1 |
|---|---|---|---|---|
| 2 | 17.5 (99) | 30 (100) | 25 (100) | 30 (100) |
| 4 | 25 (100) | 40 (100) | 40 (100) | 40 (100) |
| 6 | 30 (100) | 45 (100) | 45 (100) | 55 (100) |

## Sensitivity (A2)

**Sensitivity to the arrival profile and the specimen mix (A2).** Reweighting the held-out specimens keeps each specimen's own timings, so it shows what a different crowd does and not what congestion would do, which the generator does not model. The arrival profile did not matter: a flat day, a sharper morning peak and a night-heavy day leave the alerts per shift within 0.4 of 6.4 and the risk model's median warning at 55 minutes. Volume matters. With the knobs left alone, alerts per shift scale with arrivals: the risk model's 6.4 becomes 12.8 at twice the volume and 3.2 at half. Retuned to hold 6 alerts per shift, the model's median warning falls from 55 minutes to 40 at 1.5 times the volume and 35 at twice, and the plan's rule falls from 30 to 25 and 20, while at 0.75 and half the volume the plan's rule gains (40 and 45 minutes) and the model stays at 55. The model's lead over the plan's rule is 10 to 15 minutes at every volume but the original, against 25 there. The window-aware rule ties the model at every volume but the original, so the 10-minute lead over it at the original volume, which comes from the weights 3, 2, 1, is not robust. The specimen mix moves the model's median warning by at most 5 minutes (50 with more urine, 55 with more swabs, 55 with more urgent specimens). Warning comes in 5-minute steps, every parameter and weight is an assumption, the data is synthetic with no clinical review, and the gradient-boosted challenger has not been run.

Median warning over all breaches in minutes with the alerts per shift in brackets, each method retuned to 6 alerts per shift in the scenario (`python -m src.pipeline.sensitivity`, `sensitivity.json`):

| Scenario | Plan's baseline | Window-aware baseline | Risk model, weights 3, 2, 1 |
|---|---|---|---|
| as generated | 30 (6.3) | 45 (5.3) | 55 (6.4) |
| volume ×0.5 | 45 (6.1) | 55 (5.7) | 55 (6.3) |
| volume ×0.75 | 40 (6.3) | 55 (6.3) | 55 (6.3) |
| volume ×1.5 | 25 (6.5) | 40 (5.8) | 40 (6.5) |
| volume ×2 | 20 (6.9) | 35 (5.7) | 35 (6.6) |
| flat day | 30 (6.3) | 45 (5.3) | 55 (6.4) |
| sharper morning peak | 30 (6.3) | 45 (5.3) | 55 (6.3) |
| night-heavy | 30 (6.1) | 45 (5.3) | 55 (6.3) |
| more urine | 30 (6.3) | 45 (5.8) | 50 (6.4) |
| more swabs | 35 (6.3) | 50 (5.8) | 55 (6.4) |
| more urgent specimens | 35 (6.2) | 45 (5.4) | 55 (6.5) |

## Files

| File | What it holds | Made by |
|---|---|---|
| `mvp-nov8.json` | The midterm snapshot: headline by alerts per shift, the bar, the caveats, and the full results below | `python -m src.pipeline.evaluate` and the others, from a clean checkout |
| `evaluation.json` | Both baselines and the model at 1, 2, 4 and 6 alerts per shift, by tier, window and fault, with calibration and bootstrap intervals | `python -m src.pipeline.evaluate` |
| `alerts.csv` | One row per alert at 6 per shift on the held-out runs: time, tier, most likely place, role, warning, closed time and outcome | `python -m src.pipeline.evaluate` |
| `location-accuracy.json`, `location-calibration.svg` | Top-1 and top-3 place accuracy at 15, 30, 60 and 90 minutes of silence, and the calibration of the chance of lost | `python -m src.pipeline.location_eval` |
| `parameter-recovery.json`, `parameter-recovery.svg` | Whether the fit recovers what the generator planted | `python -m src.pipeline.recovery` |
| `sensitivity.json` | The headline reweighted for other arrival volumes, hours and specimen mixes, with the knobs fixed and retuned | `python -m src.pipeline.sensitivity` |
| `interval_params.json` | The fitted transit-time distributions, with the policy hash and the dataset's SHA-256 | `python -m src.pipeline.intervals` |
