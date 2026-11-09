"""Tracer v2's deterministic discrete-event laboratory simulator (docs/neural/spec.md §5).

engine.simulate turns one scenario configuration into one replicate week of specimen records in
the v1 {brief, trail} shape; validate_v2 checks its invariants, kpi its plausibility, v1world builds
the scenario that reproduces the v1 dataset's world, and run simulates a whole library to files.
"""
