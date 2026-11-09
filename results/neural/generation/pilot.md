# Pilot gate (O1)

Synthetic data: laboratory weeks written by language models for our own simulator, with no clinical review. Every threshold below is an assumption (spec §4.10, Appendix D, A-04).

Spent $0.6003 of the $3.00 pilot cap. 69 scenarios accepted. Near-duplicate distance delta = 1.952.

| Model | Briefs | Accepted | G1 structure | G2 acceptance | G3 adherence | G3, edge briefs | G4 duplicates | Cost per accepted | G5 vs estimate | G6 variety | Eligible |
|---|---|---|---|---|---|---|---|---|---|---|---|
| luna-low | 24 | 22 | 100% pass | 92% pass | 91% pass | 83% | 0% pass | $0.0008 | 0.64x ok | pass | yes |
| sol-medium | 24 | 24 | 100% pass | 100% pass | 100% pass | 100% | 0% pass | $0.0175 | 0.44x ok | pass | yes |
| terra-low | 24 | 23 | 100% pass | 96% pass | 100% pass | 100% | 0% pass | $0.0072 | 0.53x ok | pass | yes |

**Model rule.** Core strata (normal, stressed, disrupted): **terra-low**. Edge stratum: **terra-low**. Gate passed.

Failures by validation layer (attempts that needed a repair or were dropped):

- luna-low: {'adherence': 3, 'plausibility': 3}; unknown outcomes 0
- sol-medium: {'plausibility': 1}; unknown outcomes 0
- terra-low: {'plausibility': 3}; unknown outcomes 0

Written 2026-11-08T22:26-05:00 by `python -m src.neural.pipeline gate --job O1`.
