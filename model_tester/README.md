# model_tester

Internal debug tool for evaluating LLMs against workflow eval scenarios. Runs as
a separate Flask app on its own port (default 6099). No new dependencies — uses
the same `aihub2.1` conda env the main app uses.

## Quick start

```
start_model_tester.bat
```

then open http://localhost:6099 in a browser.

## Layout

```
model_tester/
├── app.py                     Flask app
├── llm_clients.py             OpenAI / Azure / Anthropic / LMStudio abstraction
├── judge.py                   structural + optional LLM judge
├── start_model_tester.bat     start / restart script (kills existing port-bound proc, then launches)
├── data/
│   ├── settings.json          model configs + active/judge model selection
│   ├── system_prompts/
│   │   └── command_generator.txt   the 33K-char production system prompt
│   ├── evals/
│   │   ├── EVAL-1.json … EVAL-10.json   the 10 OOB CommandGen evals
│   │   └── (anything you create)
│   └── results/               one file per run (timestamped)
├── templates/index.html
└── static/{style.css, app.js}
```

## Tabs

1. **Chat & Run** — pick a preset eval (or type system + user prompts ad-hoc),
   pick the active model, click **Run**. Optional structural judge runs immediately;
   LLM judge optional via checkbox. There's also a **Run all OOB evals** button
   that runs all 10 EVAL-* scenarios sequentially against the active model and
   shows pass/fail summary.

2. **Evals** — list, edit, delete, or create eval scenarios. Each eval has:
   - `id`, `name`, `category`, `description`, `tags`
   - `system_prompt_ref` (filename in `data/system_prompts/`) **or** inline `system_prompt`
   - `user_prompt`
   - `expected` block — drives the structural judge:
     - `min_add_nodes`, `required_node_types`, `must_have_set_start_node`,
       `forbid_loop_pass_complete`, `forbid_endloop_back_edge`

3. **Results** — every run is saved as a JSON file. View, re-judge with LLM, delete.

4. **Settings** — model registry. Add / edit / delete entries. Pick the default
   active and judge models. API keys default to whatever the main app uses
   (env vars / secure_config); enter an override per model only if you want to
   test a different key. Existing override values are masked when displayed.

## Supported providers

| Provider   | Required model_config fields |
|---|---|
| `openai`   | `model` (e.g. `gpt-4.1-mini`) |
| `azure`    | `deployment`, `endpoint`, `api_version` (defaults to `2024-08-01-preview`) |
| `anthropic`| `model` (e.g. `claude-sonnet-4-5-20250929`) |
| `lmstudio` | `endpoint` (default `http://localhost:1234/v1`), `model` (any string) |

## API key resolution

1. If a model has a non-null `api_key_override`, use that.
2. Otherwise, fall back to env var:
   - openai → `OPENAI_API_KEY`
   - azure → `AZURE_OPENAI_API_KEY` (then `OPENAI_API_KEY`)
   - anthropic → `ANTHROPIC_API_KEY`
   - lmstudio → no auth required (sends a dummy "lm-studio" key)
3. The main app's `secure_config.load_secure_config()` is invoked on startup to
   populate those env vars from the encrypted store, so whatever keys the main
   aihub app is configured with are also available here automatically.

## Judge modes

- **Structural** (always cheap, deterministic, fast). Parses the model's output
  for a `{action, commands[]}` JSON block and grades against the eval's
  `expected` config. See `judge.py` for the check list.
- **LLM judge** (optional checkbox; uses the configured `judge_model_id`).
  Calls the configured Anthropic/OpenAI model with a fixed rubric and returns
  coverage / correctness / structure scores plus a one-sentence summary.

## Eval JSON schema (example)

```json
{
  "id": "EVAL-6",
  "name": "File + AI Action + Conditional + Alert",
  "category": "CommandGen",
  "description": "Plan-to-commands evaluation captured from production log on 2026-04-30 at 15:57.",
  "system_prompt_ref": "command_generator",
  "user_prompt": "Convert this workflow plan to JSON commands:\n\n1. File node — Read the configuration file from disk.\n   ...",
  "expected": {
    "min_add_nodes": 7,
    "required_node_types": ["File", "AI Action", "Set Variable", "Conditional", "Alert"],
    "must_have_set_start_node": true,
    "forbid_loop_pass_complete": true,
    "forbid_endloop_back_edge": true
  },
  "tags": ["oob", "commandgen"]
}
```

## Notes

- `data/settings.json` and result files are stored in plain JSON. This is an
  internal debug tool, not a production surface. Don't expose it publicly.
- Killing port 6099 on restart is best-effort via `netstat | taskkill`.
- The 10 OOB evals were captured directly from the main app's
  `command_generator_log.txt` on 2026-04-30, so they're the exact plans the
  production planning stage emitted for those high-level user requests.
