# training/

Reusable pipeline for training purpose-built models for AI Hub agents.

First task: **cmdgen** — fine-tune a 7B open-weight model to replace the GPT-5/Claude call inside [`CommandGenerator.generate_commands()`](../CommandGenerator.py) with a specialized SLM that emits valid workflow JSON commands from a natural-language plan.

## Layout

```
training/
  capture/      pull (plan, commands) pairs from live system + driver agents
  curate/       normalize, scrub credentials, validate, dedupe, split
  synthesize/   template + distillation + mutation + adversarial generation
  evaluate/     deterministic + judge-based scoring harness
  data/
    cmdgen/
      raw/        untouched captures (one file per source)
      seeds/      extracted from existing artifacts (workflow JSONs, e2e md)
      synthetic/  LLM-generated, provenance-tagged
      train.jsonl eval.jsonl dev.jsonl   # produced by curate/split.py
  recipes/      training hyperparameter configs (YAML)
  runs/         adapters + eval scores + training logs per run
```

## Data flow

```
 live user builds        workflows/*.json         e2e_app_tests/*.md
 (Export button)           (seed script)           (seed script)
       │                       │                       │
       ▼                       ▼                       ▼
 training_data/...      data/cmdgen/seeds/from_json.jsonl
 plan_to_commands.jsonl data/cmdgen/seeds/from_e2e.jsonl
       │                       │
       └───────────┬───────────┘
                   ▼
       data/cmdgen/raw/*.jsonl
                   │
                   ▼
          curate/ pipeline
            normalize  →  scrub  →  validate  →  dedupe  →  split
                   │
                   ▼
    data/cmdgen/{train,dev,eval}.jsonl

 synthesize/ pipeline  ──►  data/cmdgen/synthetic/*.jsonl  ──►  curate/  ──►  train.jsonl
```

## Running

All scripts accept absolute paths and run under the aihub2 conda env:

```
C:/Users/james/miniconda3/envs/aihub2/python.exe -m training.curate.run --task cmdgen
C:/Users/james/miniconda3/envs/aihub2/python.exe -m training.evaluate.baseline --task cmdgen --model gpt-5.2
C:/Users/james/miniconda3/envs/aihub2/python.exe -m training.synthesize.run --task cmdgen --strategy templates --n 500
```

## Reused from the main codebase (no changes)

- [`workflow_compiler.materialize_commands`](../workflow_compiler.py) — commands → workflow state dict. Used as the "does this compile" gate.
- [`workflow_command_validator.check_missing_save_to_variable`](../workflow_command_validator.py) — deterministic rule check.
- [`workflow_command_validator.check_variable_references`](../workflow_command_validator.py) — deterministic rule check.
- [`CommandGenerator.COMMAND_GENERATOR_SYSTEM_PROMPT`](../CommandGenerator.py) — system prompt included verbatim in every training example.
