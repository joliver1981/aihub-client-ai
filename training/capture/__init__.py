"""Capture: wire up inbound training data sources.

Includes:
  - verify_export.py: offline smoke test that validates plan_to_commands.jsonl
    is growing and its records pass normalization + schema + compile gates.
  - (Phase 1.3) driver.py: agent that runs e2e test cases against a live AI
    Hub instance and POSTs to the Export endpoint at session end.
"""
