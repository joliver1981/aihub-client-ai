"""Training pipeline for AI Hub purpose-built models.

Layout:
- capture/     pull (plan, commands) pairs from live system + driver agents
- curate/      normalize, scrub credentials, validate, dedupe, split
- synthesize/  template + distillation + mutation + adversarial generation
- evaluate/    deterministic + judge-based scoring harness
- data/        per-task datasets (raw/, seeds/, synthetic/, eval.jsonl)
- recipes/     training hyperparameter configs (YAML)
- runs/        training run artifacts (adapters, logs, eval scores)

First target task: cmdgen (workflow plan -> JSON commands).
See plan at C:/Users/james/.claude/plans/i-want-you-to-fizzy-sloth.md
"""
