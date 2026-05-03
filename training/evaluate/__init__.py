"""Evaluation harness for trained / candidate cmdgen models.

Phase 1 responsibility: assemble and freeze a held-out eval set with explicit
stratification and a manifest. Phase 2 builds the scoring harness that runs
a model against this set.
"""
