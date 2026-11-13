# TracerNet tracernet-v2

Synthetic data: TracerNet was trained and checked only on simulated laboratory weeks, configured by language models and simulated by Tracer's own code, with no clinical review. Every parameter is an assumption, and its numbers describe the simulator, not any laboratory.

## Intended use

A research prototype for a course project (UF CAI 5724). Every 5 minutes, for every clinical-microbiology specimen
collected but not yet scanned as received, it gives the probability that the receipt scan comes after the
specimen's stability window (`p_miss`), split into late and never (`p_late`, `p_never`), with arrival quantiles and
the spread across its members. It is tested against the analytic survival model by the rule of `docs/neural/preregistration-v2.md`. It is not a
clinical tool, and must not be used to make decisions about real specimens.

## Model

Rung C of the TracerNet ladder (spec §7.3), 3 seeds (0, 1, 2),
their receipt-time distributions averaged. Calibration: temperature 1.125 on the log of
the averaged distribution, fitted on validation ticks.
Inference runs on CPU and is bit-reproducible.

## Data

Observed scans of the scenario store `bca1e1f97b1eb024` (library `204c41299477305e`, splits
`ccc97addce40a248`, policy `9594d7c019363261`): train for fitting, validation for early stopping,
selection and calibration. Test and test_ood were never read. Features: `tracernet-features-1`.

## Validation metrics

| Metric | Value |
|---|---|
| val_sel (miss log loss, ticks up to min(window, 240), per specimen) | 0.07892 |
| Members' val_sel | 0.07906, 0.07912, 0.07906 |
| Expected calibration error of p_miss, before | 0.0167 |
| Expected calibration error of p_miss, after | 0.0132 |
| Brier at 30 and 60 min, after | 0.0468, 0.0234 |
| Calibration slope and intercept, after | 0.918, 0.329 |
| Calibration ticks | 3,000,000 of 67,286,117 |

## Limits

- The data is simulated; nothing here has been checked against a real laboratory, and no clinician reviewed it.
- Scenario families in test and test_ood differ from train; validation measures generalization to its own families
  only.
- The network explains its inputs, not the world; its attributions (spec §9) say what moved its output.
- A policy change (windows, clock) needs retraining: the predictor refuses another policy hash.
