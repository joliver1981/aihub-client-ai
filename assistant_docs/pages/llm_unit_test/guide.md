# Data Agent Testing (`/llm_unit_test`)

A developer/QA harness for running automated NL-to-SQL questions against a Data Agent and inspecting the live output. Useful for sanity-checking a data agent's behavior after config or schema changes, and for regression testing query quality before shipping.

> **Developer-tier page.** Requires the developer role (`@developer_required`). Not user-facing for end users.

> The page title in the UI is "Data Agent Testing" but the URL is `/llm_unit_test`.

---

## Page Layout

- **Title bar** — "Data Agent Testing".
- **Configuration form** — pick the data agent to test and choose how many auto-generated questions to run.
- **Live Output panel** — terminal-styled (green-on-black) streaming output as the test runs. Each question shows:
  - Question text
  - Generated SQL query
  - Answer / data returned
  - Pass/fail or warning indicators
- **Controls** — start, stop, and (after completion) summary view.

## How a Test Run Works

1. You choose an agent and a target question count.
2. The harness inspects the agent's connected database schema and samples real data.
3. It asks the LLM (NLQ provider) to generate that many realistic questions about the data.
4. For each generated question:
   - The data agent answers (NL → SQL → results → natural-language reply).
   - Results stream live to the output panel.
5. At the end, a summary aggregates pass/fail/warning counts and the run is saved to results storage.

## Common Tasks

### "Sanity-check a data agent after I changed its schema or prompts"
1. Pick the agent.
2. Choose a small question count (5–10) for a quick smoke test.
3. Start the test; watch the live output for query errors or unexpected answers.
4. If issues appear, stop the run (you don't need to wait for all questions) and iterate.

### "Compare an agent's behavior across config changes"
Run the test before and after the change, with the same question count. Compare pass rates and any specific failing questions in the saved results.

### "Stop a long-running test"
Use the stop control — the harness honors the stop signal between questions and records the partial result.

## Caveats

- **Questions are AI-generated**, not curated — quality varies. Some "failures" reflect a bad question rather than a bad agent answer. Read failures with skepticism.
- **Tests hit the live database** that the data agent is connected to. Make sure the agent is pointed at a non-production or read-only target before running large batches.
- **Cost** — every question is an LLM round-trip (generation + answer). Big runs cost meaningful tokens.

## Related Pages

- **`/custom_data_agent`** — edit the data agent under test (connection, prompts, schema descriptions, sample data).
- **`/data_chat`** — manually chat with the data agent to investigate a specific failure.
- **`/data_dictionary`** — improve the agent's schema understanding by adding column descriptions and synonyms — usually the highest-leverage fix when test pass rates are low.

## What This Page Doesn't Do

- It doesn't run **deterministic unit tests** in the software-engineering sense — it's exploratory regression testing via generated questions.
- It doesn't test **general agents** — only data agents (NL-to-SQL).
- It doesn't deploy or version the agent — it just exercises the current configuration.
