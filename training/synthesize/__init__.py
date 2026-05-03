"""Synthesize training data via LLM-mediated templates, mutations, and adversarial cases.

Four strategies, each writes to data/<task>/synthetic/<strategy>.jsonl with a
`_meta.source` tag for provenance-aware curation and eval slicing:

  - templates.py        structural skeleton + domain fill-in
  - rephrase.py         take a gold (plan, commands), produce N plan variants
  - mutation.py         take a gold plan, permute one dimension, resynth commands
  - adversarial.py      hand-authored + LLM-extended footgun corrections

Every record passes through the four-gate judge in judge.py before landing
in synthetic/<strategy>.jsonl:

  1. JSON round-trips cleanly.
  2. Passes schema checks (training/curate/validate._schema_errors).
  3. Passes compile (training/curate/validate._materialize).
  4. Judge model confirms the commands realize the plan's intent.

Gates 1-3 are free. Gate 4 costs ~$0.01/example and is optional (--no-judge).
"""
