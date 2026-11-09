"""Scenario generation for Tracer v2 (docs/neural/spec.md §4).

Seeded code plans which laboratory-weeks get requested (planner), asks a language model to write
each as JSON under a strict schema (prompt, schema), and refuses any reply that breaks a bound, a
rule, a name check or its brief (validate). signature measures how different accepted scenarios
are, and random_baseline draws the code-random scenarios that ablation A4 compares against.
"""
