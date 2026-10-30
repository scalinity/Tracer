# Results

Every number here comes from synthetic data that had no clinical review, and every parameter behind it is an assumption. The first end-to-end results are saved in `mvp-nov8.json` (tag `v0.1-mvp`). That file is the midterm snapshot: the December 2 results are compared against it and it is never edited. Warning is the minutes from the alert to the stability window closing, and in the "all breaches" median a breach that was never alerted counts as zero.

**Headline, midterm snapshot.** On the 6 held-out runs (2,378 specimens with a clock and 100 breaches, 0.79 breaches per 8-hour shift), the risk model's median warning over all breaches was 55 minutes at 6 alerts per shift against 30 minutes for the plan's per-group quantile rule, a lead of 20 to 25 minutes (95% paired bootstrap over the runs); at equal urgency weights it was 45 minutes against 30, a lead of 10 to 15 minutes. That clears the bar set in advance, at least 5 more minutes of median warning at the same alerts per shift, at 2, 4 and 6 alerts per shift and at both weightings. The result beside it matters as much. The plan's rule ignores the stability window, and a rule that alerts once a fixed share of the window has passed, set to the same alert budget on the fit runs, reaches 45 minutes at 6 alerts per shift. The model's lead over that window-aware rule is 5 to 10 minutes at weights 3, 2, 1 and none at equal weights, where the two tie at 6 and at 4 alerts per shift (45 and 40 minutes) and the model trails by 5 minutes at 2. Most of the gain over the plan's rule therefore comes from using the window, and the weights move the headline: the 55 minutes comes from giving tiers 1 and 2 more warning while tier 3, 61 of the 100 breaches and mostly urine, gets 40 minutes against 45 at equal weights. Read every number with these limits: the data and the model share assumptions because both are ours, so these are best-case figures; 6 alerts per shift is about a third of all specimens (the held-out volume at that setting was 6.4); the Brier score is only slightly better than the base rate (0.109 against 0.119 at 30 minutes of silence, 0.182 against 0.187 at 60), so the gain is in timing, not in sorting; and the 33 specimens the validator dropped lean toward long delays.

Median warning over all breaches in minutes, with breaches caught of 100 in brackets, on the held-out runs:

| Alerts per shift | Plan's baseline | Window-aware baseline | Model, equal weights | Model, weights 3, 2, 1 |
|---|---|---|---|---|
| 2 | 17.5 (99) | 30 (100) | 25 (100) | 30 (100) |
| 4 | 25 (100) | 40 (100) | 40 (100) | 40 (100) |
| 6 | 30 (100) | 45 (100) | 45 (100) | 55 (100) |

## Files

| File | What it holds | Made by |
|---|---|---|
| `mvp-nov8.json` | The midterm snapshot: headline by alerts per shift, the bar, the caveats, and the full results below | `python -m src.pipeline.evaluate` and the others, from a clean checkout |
| `evaluation.json` | Both baselines and the model at 1, 2, 4 and 6 alerts per shift, by tier, window and fault, with calibration and bootstrap intervals | `python -m src.pipeline.evaluate` |
| `alerts.csv` | One row per alert at 6 per shift on the held-out runs: time, tier, most likely place, role, warning, closed time and outcome | `python -m src.pipeline.evaluate` |
| `location-accuracy.json`, `location-calibration.svg` | Top-1 and top-3 place accuracy at 15, 30, 60 and 90 minutes of silence, and the calibration of the chance of lost | `python -m src.pipeline.location_eval` |
| `parameter-recovery.json`, `parameter-recovery.svg` | Whether the fit recovers what the generator planted | `python -m src.pipeline.recovery` |
| `interval_params.json` | The fitted transit-time distributions, with the policy hash and the dataset's SHA-256 | `python -m src.pipeline.intervals` |
