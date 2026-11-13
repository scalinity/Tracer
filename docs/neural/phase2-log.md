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
