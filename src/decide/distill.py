"""X4, the distilled dispatcher (docs/neural/spec.md §11.3): not built.

X4 would label 10,000 train alerts with X2's `place` and `action` questions and train a small head on TracerNet's
128-number fused representation and X2's facts to match Drex Max's probabilities, adopted in place of live calls only
if its role accuracy came within 2 points of Drex Max's. It is the first decision-layer job on the cut list (spec §18, item 2), and
it only matters if Drex Max's dispatcher is adopted by X2's rule on test, which has not been decided. Nothing calls the
API for it, and the pipeline's `decide --job X4` says so.
"""

REASON = ("X4 (the distilled dispatcher) is not built: it is the first decision-layer job on the cut list (spec §18, "
          "item 2) and matters only if X2's adoption rule adopts Drex Max on test, which has not run.")


class NotBuilt(RuntimeError):
    pass


def run(*_args, **_kwargs):
    raise NotBuilt(REASON)
