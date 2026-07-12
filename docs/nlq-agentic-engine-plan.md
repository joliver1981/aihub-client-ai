# Agentic NLQ Engine (V3) — Build Plan

**Date:** 2026-07-11
**Status:** PLAN — no code written yet
**Companion analysis:** the 2026-07-11 NLQ architecture review (memory: `nlq-engine-architecture-review`)

---

## 0. The non-negotiables (why this plan is shaped the way it is)

1. **The current engine is not touched.** `LLMDataEngineV2.py`, `LLMQueryEngine.py`, `LLMAnalyticalEngine.py`, `system_prompts.py`, the enhancement wrappers, PandasAI — all stay byte-for-byte as they are. They are the trusted fallback.
2. **The new engine is purely additive** — new files only, plus a handful of small, mechanical edits at the engine-construction sites. Referencing V2 is encouraged, but code is **copied** into `nlq_agentic/`, never imported from the V2 engine modules, and V2 is never edited (borrowing policy in §1).
3. **A setting switches between engines** — globally and per-agent — and defaults to `legacy` in production until the new engine has earned trust.
4. **Runtime auto-fallback**: if the agentic engine errors, times out, or trips a circuit breaker in production, the request is transparently re-served by the legacy engine. Failure of the new system must never mean a dead feature.
5. Legacy stays indefinitely. Retiring V2 is explicitly out of scope of this plan.

---

## 1. New files and the only edits to existing files

### New files

```
nlq_agentic/
  __init__.py
  engine.py        # AgenticNLQEngine — get_answer() + full interface parity with LLMDataEngine
  loop.py          # tool-loop runner: OpenAI tool calling, iteration/wall-clock budgets, trace capture
  tools.py         # tool schemas + handlers (get_table_details, run_sql, create_chart, ask_user, respond)
  state.py         # AgentSessionState: messages, datasets, executed queries — plain data, picklable
  contract.py      # maps loop results to the legacy return shapes (8-tuple AND rich-content dict)
  formatting.py    # deterministic column formatting driven by the data dictionary
  telemetry.py     # tool trace JSONL + [DATA_EXPLORER_TIMING]-parity log lines
sql_gate.py        # root level (shared later if we choose): read-only SQL validation + row-cap injection
nlq_engine_factory.py  # mode resolution + SafeNLQDispatcher (fallback + circuit breaker)
tests_v2/unit/test_sql_gate.py
tests_v2/unit/test_nlq_agentic_contract.py
tests_v2/unit/test_nlq_agentic_loop.py            # mocked proxy
tests_v2/integration/test_nlq_engine_factory.py    # mode resolution + fallback + breaker
tests_v2/competency/test_competency_data_explorer_v3_agentic.py   # clone of v2 battery
docs/nlq-agentic-engine-plan.md                    # this file
```

> gitignore trap: repo ignores `test*.py` — every new test file needs `git add -f` (see memory `workflow-complete-conn-and-overwrite`).

### Edits to existing files (all small, all additive)

| File | Change |
|---|---|
| `config.py` | New `NLQ_AGENTIC_*` settings block (below) |
| `routes/data_explorer.py` :155, :317, :593 | Replace direct `LLMDataEngine(...)` construction with `nlq_engine_factory.create_engine(...)` |
| `app.py` :1644, :1676/:1682, :2551 (and the load-test route :5654 if desired) | Same factory swap |
| `GeneralAgent.py` :662 (`ask_query_agent_a_question`) | Same factory swap |
| `requirements` + PyInstaller spec (`app_onedir.spec`) | Add `sqlglot` (pure-Python) |

When the mode resolves to `legacy`, the factory constructs exactly what those call sites construct today (`LLMDataEngine(provider=cfg.NLQ_PROVIDER)` + `enhance_engines(...)`), so legacy behavior is unchanged by definition.

### Borrowing policy (reference V2 freely, never touch it)

- **Copy, don't import, from the V2 engine modules.** Anything useful in `LLMQueryEngine.py` / `LLMAnalyticalEngine.py` / `LLMDataEngineV2.py` / `system_prompts.py` gets copied into `nlq_agentic/` and adapted there: the chart-spec renderer (`_render_chart_from_spec` + `_generate_chart_fallback`), the formatting-metadata parsing ideas, the good prompt heuristics (most-recent-period defaults, business-rule/synonym/calc-metric directives). Copying keeps V3 decoupled so V2 never has to change to accommodate it — and avoids importing `LLMAnalyticalEngine`, which drags in PandasAI/matplotlib side effects at import time.
- **Shared read-only utilities are imported as-is** (importing doesn't modify them): `DataUtils` schema/YAML functions, `AppUtils.execute_sql_query_v2`, `api_keys_config.get_openai_config`, the client-construction helper, `CommonUtils.get_log_path`/log rotation.

---

## 2. The switch

### Config keys (config.py, env-overridable, following existing conventions)

```python
NLQ_ENGINE_DEFAULT = os.getenv('NLQ_ENGINE_DEFAULT', 'legacy')   # 'legacy' | 'agentic'
NLQ_AGENTIC_AGENT_IDS = os.getenv('NLQ_AGENTIC_AGENT_IDS', '')   # csv allowlist: these agents use agentic even when default=legacy
NLQ_LEGACY_AGENT_IDS = os.getenv('NLQ_LEGACY_AGENT_IDS', '')     # csv escape hatch: these agents stay legacy even when default=agentic
NLQ_AGENTIC_FALLBACK = True          # auto-serve the request via legacy when agentic fails
NLQ_AGENTIC_TIMEOUT_S = 90           # hard wall-clock budget per request
NLQ_AGENTIC_MAX_TOOL_ITERATIONS = 8  # loop cap
NLQ_AGENTIC_BREAKER_THRESHOLD = 3    # consecutive failures -> trip breaker (process-wide)
NLQ_AGENTIC_BREAKER_COOLDOWN_S = 600 # breaker auto-resets after cooldown
NLQ_AGENTIC_MODEL = os.getenv('NLQ_AGENTIC_MODEL', '')  # empty -> platform's configured GPT model via get_openai_config (gpt-5.4 per current defaults)
NLQ_AGENTIC_SQL_ROW_CAP = 10000      # injected TOP/LIMIT when the model didn't cap
NLQ_SHADOW_COMPARE = False           # run agentic silently on legacy traffic, log-only
NLQ_SHADOW_SAMPLE_PCT = 10           # % of legacy requests shadowed when enabled
```

### Mode resolution (nlq_engine_factory.py)

Per request: `NLQ_LEGACY_AGENT_IDS` (deny) → `NLQ_AGENTIC_AGENT_IDS` (allow) → `NLQ_ENGINE_DEFAULT` → `legacy`. Resolution happens **once per session** and is pinned in session state — flipping config affects new sessions, not conversations already in flight (prevents mid-conversation engine ping-pong).

### SafeNLQDispatcher (the fallback wall)

```
get_answer(agent_id, question, ...):
    mode = resolve_pinned_mode(session, agent_id)
    if mode == 'agentic' and breaker.is_closed():
        try:
            return agentic_engine.get_answer(...)      # within NLQ_AGENTIC_TIMEOUT_S
        except Exception / timeout / malformed result:
            breaker.record_failure()
            log.error("[NLQ_AGENTIC_FALLBACK] ...", trace_id)
            if NLQ_AGENTIC_FALLBACK:
                return legacy_engine.get_answer(...)   # fresh legacy engine, client-replayed history
            return legacy-shaped error response
    return legacy_engine.get_answer(...)
```

- **Circuit breaker**: N consecutive failures trips it process-wide; agentic is skipped (straight to legacy) until cooldown expires. Trips are logged at ERROR with a distinctive tag so they're findable.
- **Mid-session fallback works naturally** because conversation history is client-replayed on every HTTP request today (that is how V2 already works). What is lost on fallback is the agentic session's cached DataFrames — a follow-up may need to re-query. Acceptable and logged.
- **Session-store hygiene**: both engines persist through the existing stores (`llm_data_engines` pickle dict, `_internal_engines` live dict). The factory type-checks what it unpickles; if the stored engine type doesn't match the pinned mode (e.g., config changed between restarts), it rebuilds fresh.
- **P1 implementation note (2026-07-11)**: mode resolution and the breaker live in `nlq_engine_factory` and apply at construction time; the factory returns plain engine objects (no proxy/wrapper class), so session-store pickling stays byte-identical to today. The in-request try/fallback logic described above lands inside the agentic engine's `get_answer` in P3 — same behavior as the SafeNLQDispatcher sketch, lower-risk shape.

---

## 3. Interface contract the new engine must satisfy

`AgenticNLQEngine` is a drop-in for `LLMDataEngine` as the call sites use it:

- `get_answer(agent_id, input_question, recursion_depth=0)` returning **both** shapes:
  - tuple: `(answer, explain, clarify, answer_type, special_message, input_question, revised_question, return_query)`
  - rich dict when `cfg.ENABLE_RICH_CONTENT_RENDERING`: `{answer, answer_type, rich_content, rich_content_enabled, explain, clarify, special_message, query}`
- `answer_type` ∈ `{string, dataframe, chart, none}`; DataFrame answers are real `pd.DataFrame` objects.
- Chart contract preserved: `answer='See chart...'`, `answer_type='chart'`, `special_message='<img src="data:image/png;base64,...">'`.
- `return_query` format preserved: `'=== Data Query ===\n' + sql` (+ timing line) — UI and the CC delegator display this.
- **Audited external surface (P1, 2026-07-11)** — everything the entry points and wrappers actually touch on the engine object: `get_answer(agent_id, question)`; `clear_chat_hist()` + `add_message_to_hist(msg, is_user)` (history replay on every chat path); `format_response_with_rich_content(answer, answer_type, ctx)` (data_explorer chat); `explain()` (app.py:1010); `question_count` (app.py:1346); `environment.question_count = 0` (app.py:12173 reset); `environment.confidence_score` read by nlq_enhancements (never set anywhere — always defaults; do not replicate the bug, just tolerate the read); `query_engine`/`analytical_engine` attribute injection; pickle round-trip via both session stores. `set_conversation_history(history)` exists on V2 but no wired entry point calls it (all replay via clear+add) — implement it anyway, it's trivial.
- Picklable via default mechanics (state is plain data + DataFrames); `__getstate__` drops the OpenAI client handle, `__setstate__` rebuilds it. No wrapper layers, so nothing to lose in the round-trip.
- History parsing: accept the same client-supplied format; try `json.loads` first, `ast.literal_eval` fallback — but store internally as clean JSON messages from then on.

Covered entry points (all funnel through the factory): Data Explorer session chat, `/data_explorer/internal/query` (Command Center delegation), `/api/agents/<id>/chat`, legacy `/data_chat` + `/data_assistants`, `GeneralAgent.ask_query_agent_a_question`.

---

## 4. The agentic engine design

### One loop, five tools

System prompt (assembled per agent, stable within a session → prompt-cache friendly):
- Role + `database_type` + current date + tenant read-only statement.
- **Compact table catalog** from `get_enhanced_table_metadata_as_yaml` (names, descriptions, types, categories, business-rule digest, required filters).
- Existing datasets from earlier turns (ref id, description, row count, columns).
- Behavior rules ported from the good parts of `system_prompts.py`: apply required filters; use calculated-metric formulas; map synonyms; ambiguous period → most recent occurrence *and say the assumption*; `ask_user` only when genuinely blocked; plain business language in prose; always finish with `respond`.

Tools:

| Tool | Input | Handler |
|---|---|---|
| `get_table_details` | `{tables: [str]}` | `get_enhanced_full_schema_with_column_details_as_yaml(tables, connection_id)` — column semantics, examples, calc metrics |
| `run_sql` | `{query: str}` | `sql_gate` validate → row-cap inject → `execute_sql_query_v2` → store df as `dataset_N` → return columns/dtypes/row_count + first 20 rows as text + ref id. **Errors return as tool-result text** — the model self-repairs (replaces `_check_query`/`_auto_correct_query_from_error`/`_extract_sql_only`). |
| `create_chart` | `{dataset, chart_type, x, y, title, aggregate?}` | Deterministic matplotlib render, porting the proven `_render_chart_from_spec` approach from `LLMAnalyticalEngine` (a *copy* into `nlq_agentic/`, not an import — V2 stays untouched). Replaces PandasAI. |
| `ask_user` | `{question: str}` | Terminates the loop → clarify-style string response (replaces the entire more-info / auto-answer apparatus) |
| `respond` | `{answer_kind: text\|table\|chart, text, dataset?, chart?}` | Terminates the loop → `contract.py` maps to legacy shapes; `formatting.py` applies dictionary `value_format`/`units` to the returned DataFrame copy deterministically |

Loop mechanics (`loop.py`): **OpenAI chat-completions function calling via the platform's existing client path** — `get_openai_config(use_alternate_api=True)` + the same client construction `azureQuickPrompt` uses, so BYOK / direct-OpenAI / Azure resolution and the reasoning-model kwargs convention (`reasoning_effort` set, `temperature` left at the required value) are inherited, not reinvented. Non-streaming `chat.completions.create(messages, tools=[...])`; on `tool_calls` in the assistant message, execute handlers and append `role="tool"` results keyed by `tool_call_id`; stop on `respond`/`ask_user`/iteration cap/wall-clock budget. Tool schemas declare `strict: true` where the endpoint supports it (P0 verifies), with local JSON-schema validation + one corrective retry as the fallback. Model = `NLQ_AGENTIC_MODEL` or whatever `get_openai_config` resolves (gpt-5.4 per current platform defaults). The stable system prompt + tool block benefits from OpenAI's automatic prompt caching across turns.

What this deletes the need for (in the new path only): the 5-classifier front door, choose/refine tables, SQL repair chain, dataset describer, analytical-required check, PandasAI, response filter as an LLM call, `_fix_conversation_history`, both monkey-patch wrapper layers, self-reported confidence. Expected profile: **2–5 model calls/question** vs 10–15.

### State (`state.py`)

`AgentSessionState`: `messages` (JSON-serializable, tool blocks included within a turn, compacted between turns), `datasets` (`{ref: {df, description, sql, created_turn}}`, capped count with LRU eviction), `executed_queries`, `agent_id`, `connection_id/type`, `pinned_mode`. Multi-turn follow-ups answer from existing datasets when the model chooses — no separate "is a new query required" classifier.

### Telemetry (`telemetry.py`)

Per request: mode, tool trace (name, args digest, ms, ok/error), SQL executed, iterations, total ms, fallback-used flag. Emits a `[DATA_EXPLORER_TIMING]`-parity line so existing timing habits keep working, plus a JSONL trace file for the side-by-side eval diffing.

---

## 5. sql_gate.py — deterministic read-only enforcement

- `validate_readonly(sql, dialect) -> (ok, reason)`: `sqlglot` parse; exactly one statement; statement class must be SELECT (CTE/WITH → SELECT allowed); reject INSERT/UPDATE/DELETE/MERGE/DROP/TRUNCATE/ALTER/EXEC/GRANT, `SELECT ... INTO`, and anything unparseable. Dialect from `agent_database_type` (mssql/postgres/mysql/oracle).
- `apply_row_cap(sql, cap, dialect) -> sql`: inject `TOP`/`LIMIT`/`FETCH FIRST` when absent; respect existing smaller caps.
- Wired into the **new engine only**. (Optionally offering it to V2's `execute_sql_query_v2` behind a default-off flag is a separate, later decision — not part of this plan, per the don't-touch rule.)
- Unit-test matrix is Phase 2's acceptance gate (below).

---

## 6. Provider decision: OpenAI/GPT (and the small Phase 0 spike that remains)

**The loop runs on OpenAI/GPT, not Anthropic.** Decided 2026-07-11:

- Anthropic main-app traffic goes through the hosted relay (`AnthropicProxyClient.messages_create`, CommonUtils.py:785) whose payload has **no `tools` field** — agentic tool calling on that path would require relay changes, and prior in-platform experience with Anthropic agentic tool calling was problematic. Not worth the risk for V3.
- The OpenAI path is a **direct SDK client** (`get_openai_config` → openai/AzureOpenAI client), and OpenAI-format tool calling is already proven in production in this codebase: GeneralAgent binds its whole toolset as OpenAI tools (`GeneralAgent.py:2860`, `convert_to_openai_tool` + `AgentExecutor`) and runs on it daily.
- V3 uses the **plain openai SDK loop** rather than LangChain's AgentExecutor: same wire protocol GeneralAgent proves out, but with deterministic control over iteration caps, wall-clock budgets, tracing, and the finalize contract. An Anthropic-backed loop remains a possible future option if the relay ever forwards `tools`; tool schemas are plain JSON Schema, so they'd port.

**Remaining Phase 0 spike (small, not a project gate):** a throwaway script through `get_openai_config(use_alternate_api=True)` against the real endpoint(s) confirming: (a) a two-step `tool_calls` → `role="tool"` → final-answer round-trip on the configured model; (b) whether `strict: true` tool schemas and `response_format=json_schema` are accepted (Azure needs a recent `AZURE_OPENAI_API_VERSION`; BYOK/direct OpenAI generally yes); (c) the reasoning-model kwargs behave with tools present. Outcome sets one flag: strict schemas on, or non-strict + local validation/retry.

> **P0 OUTCOME (2026-07-11): PASS on all three checks.** Run against the real resolved path — Azure OpenAI deployment `gpt-5.2` (`source=azure_alternate`, api-version `2024-12-01-preview`), which itself routes through the platform's `openai_proxy_request_v3` cloud proxy — and the proxy forwarded everything intact:
> - A. `tool_calls` → `role="tool"` → final answer: **PASS** (3.4s round trip; the model wrote sensible SQL and used the injected tool result in its answer).
> - B. `strict: true` tool schemas: **PASS**.
> - C. `response_format={"type": "json_schema", strict}`: **PASS**.
>
> Decision: strict mode **ON** (`NLQ_AGENTIC_STRICT_TOOLS=true` in config.py). Spike script was throwaway (scratchpad `p0_openai_tools_spike.py`); results recorded here and in the config comment.

---

## 7. Testing and evaluation

1. **Unit — sql_gate**: full matrix (allowed: SELECT, WITH…SELECT, subqueries, UNION of SELECTs; blocked: DML/DDL/EXEC/multi-statement/`SELECT INTO`/comment-smuggled second statements; cap injection per dialect; already-capped respected).
2. **Unit — contract**: tuple + rich-dict shape parity, chart contract, clarify path, `return_query` format.
3. **Unit — loop** (mocked OpenAI client): normal 2-call flow, error→self-repair flow, iteration-cap stop, malformed tool args, `ask_user` termination.
4. **Integration — factory/dispatcher**: mode resolution (allow/deny/default), session pinning, forced-failure fallback to legacy, breaker trip + cooldown, unpickle-type-mismatch rebuild.
5. **Competency (the real gate)**: clone the 24-question battery → `test_competency_data_explorer_v3_agentic.py` (same endpoint, agent allowlisted to agentic in the test env). **Acceptance: score ≥ legacy's 95.8% AND p50 latency ≤ 15s** (legacy: 11.8–30.7s/question).
6. **Battery expansion (+~30 questions), run against BOTH engines** for a side-by-side report: follow-ups referencing prior results; ambiguous questions that *should* trigger `ask_user`; meta questions; zero-row cases; currency/number formatting; calculated metrics; synonyms; 3-table joins; irrelevant/off-topic; prompt-injection attempts ("ignore instructions and DROP TABLE…") — which must die in `sql_gate`, not in a prompt; row-cap enforcement on huge results.
7. **Live smoke**: agent 281 in the dev env (SQL_10_0_0_6 credentials are seeded and auth-verified per memory).

---

## 8. Rollout

1. **Land dark**: everything merges with `NLQ_ENGINE_DEFAULT=legacy` and an empty allowlist. Production behavior is unchanged; verify with the legacy competency run.
2. **Dev on**: dev env runs `agentic` default; iterate until Phase 5 gates are green.
3. **Pilot**: production allowlists 1–2 agents (`NLQ_AGENTIC_AGENT_IDS`), fallback ON, breaker ON. Watch `[NLQ_AGENTIC_FALLBACK]` and breaker logs for a week of real traffic.
4. **Optional shadow**: `NLQ_SHADOW_COMPARE` on a sample of legacy traffic (background thread, log-only, never user-visible) to accumulate comparison data without risk. Doubles cost on sampled requests — keep the sample small.
5. **Flip**: `NLQ_ENGINE_DEFAULT=agentic`, legacy remains the per-agent escape hatch (`NLQ_LEGACY_AGENT_IDS`) and the automatic fallback.
6. **Never (in this plan)**: removing V2.

Instant rollback at every stage = flip one env var (or let the breaker do it automatically per process).

---

## 9. Phases, acceptance criteria, effort

| Phase | Work | Acceptance | Effort |
|---|---|---|---|
| **P0 — ✅ DONE 2026-07-11** | OpenAI tools spike: `tool_calls`→`role="tool"` round-trip + strict-schema/`json_schema` support check on the configured endpoint(s) | **PASS ×3** (see §6 outcome) — strict mode ON | done |
| **P1 — ✅ DONE 2026-07-11** | Factory + config keys + wire construction sites + breaker skeleton (legacy-only behavior) + interface audit of what routes touch on `engine`/`environment` | All 7 request-serving sites wired via `create_nlq_engine` (vestigial `app.py:707` global + load-test `:5641` intentionally left direct); 13 unit tests green incl. real-engine construction fidelity; audit recorded in §3. Live legacy-competency re-verification pending next app restart. | done |
| **P2** | `sql_gate.py` + unit matrix; `sqlglot` into requirements + PyInstaller spec | Matrix green on mssql/postgres/mysql | ~1 day |
| **P3** | Core engine: loop, state, `get_table_details`, `run_sql`, `respond`, contract mapping (text+table), telemetry | Cloned battery ≥ 90% in dev; traces readable | ~3–4 days |
| **P4** | `create_chart` (ported spec renderer), `ask_user`, dictionary-driven formatting, multi-turn dataset refs, rich-content dict parity | Chart renders in UI; formatting cases pass; battery ≥ 95.8% | ~2–3 days |
| **P5** | Battery expansion + side-by-side runner + comparison report artifact | Agentic ≥ legacy overall; p50 ≤ 15s; injection cases blocked by gate | ~2 days |
| **P6** | Fallback/breaker hardening, shadow mode, docs, pilot enablement notes | Forced-failure drill: kill agentic mid-traffic → users see legacy answers, logs show the story | ~1–2 days |

Sequencing rule: commit each phase to the current branch immediately (no side branches — standing directive), push in batches.

Later / explicitly out of scope now: Python-sandbox tool (reuse the CC code-interpreter service — needs its egress governance settled first), Anthropic-provider tool loop (blocked on relay `tools` passthrough), per-agent UI toggle (env config first), wiring `sql_gate` into legacy, retiring V2.

---

## 10. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Azure `api_version` too old for `strict`/`json_schema` | P0 detects; fall back to non-strict tools + local JSON-schema validation with one corrective retry; or bump `AZURE_OPENAI_API_VERSION` |
| BYOK key/endpoint variance per tenant | Resolve `get_openai_config` per request (it already handles BYOK→direct→Azure); P0 spike runs against both endpoint types |
| Runaway loops / latency spikes | Iteration cap (8), wall-clock budget (90s), breaker, fallback |
| Cost regression | Fewer-but-bigger calls usually net cheaper than 10–15 schema-stuffed calls; OpenAI automatic prompt caching on the stable prefix; `NLQ_AGENTIC_MODEL=gpt-5.4-mini` is a one-line experiment the battery can score |
| Contract drift breaks UI/CC delegation | `contract.py` centralizes shapes; Playwright competency suite exercises the real UI path; CC delegation hits the same endpoint contract |
| Mixed-type session pickles after config flips | Factory type-checks on unpickle and rebuilds |
| Frozen/onedir packaging surprises | Pure-Python deps only (`sqlglot`); use existing `get_log_path`/`APP_ROOT` patterns (memory `frozen-onedir-app-root`) |
| Concurrent same-session turns racing (pre-existing) | New state is per-session and small; add a per-key lock in the factory's `_internal_engines` path (additive; legacy path untouched) |
| Silent quality regressions post-flip | Fallback + shadow logs + keeping the side-by-side battery runnable on demand |
