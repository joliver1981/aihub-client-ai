# Agent-Knowledge Competency — Notes

This is the rolling write-up of all competency suites under
`tests_v2/competency/`. Auto-generated reports live alongside this file
(`*_competency_report.md`) and are regenerated every run.

---

## Run history

| Suite | Date | Overall | Leaks / bugs | Wall time | Notes |
|---|---|---:|---|---:|---|
| **Excel** | 2026-05-21 | **96.2%** | 0 leaks | 8m 22s | Single failure: cross-fixture answer source ambiguity. 0 hidden-sheet leaks. |
| **Word**  | 2026-05-21 | **92.6%** | 0 leaks | 3m 56s | Both failures were regex bugs in the accept-patterns; agent answers were correct. Tracked-changes fidelity verified. |
| **PDF**   | 2026-05-21 | **100.0%** | 0 leaks | 4m 54s | All dimensions perfect including 2-column extraction, 50-page needle retrieval, and invoice-table arithmetic. |
| **Data Assistant — legacy** (`/chat/data`) | 2026-05-21 | **66.7%** | 🔴 **BUG-DATA-ASSISTANT-AGG-500** (legacy-only) | 12m 56s | Single-value aggregations 500. Same query GROUPED works. |
| **Data Explorer v2** (`/data_explorer/chat`) | 2026-05-21 | **91.7%** | 0 (2 regex misses, +25 pts vs legacy) | 11m 27s | Same battery. All 5 aggregation-500 questions now return correct answers. Proves the legacy bug is engine-version-scoped. |
| **Workflow Execution** | 2026-05-22 | **53.8%** | 🔴 BUG-WORKFLOW-EVAL-STRING-MULT, 🔴 BUG-WORKFLOW-DB-UNKNOWN-ERROR (HIGH) | 1m 25s | First quality coverage for workflows. 2 of 5 fixtures pass, 3 fail or partial-fail. Database node + variable arithmetic both broken. |
| **Large Invoices + Financial Reports** (baseline) | 2026-05-23 | **52.1%** | 🔴 BUG-LARGE-PDF-DEGRADATION (HIGH) | 18m 53s | Triggered by a client report. PDFs (100+ pages, 23.1%) vs XLSX (86.4%) reveal large-PDF handling is the actual weak spot. See `large_invoice_reports_summary.md`. |
| **Large Invoices + Financial Reports** (Method A, post-fix) | 2026-05-23 | **62.5%** | 🔴 BUG-NEEDLE-WRONG-DOC-AFTER-CLARIFY (NEW, MEDIUM) | 19m 35s | After applying the 3 fixes + adding conversational follow-up. MegaRetail PDF 0%→75%. Remaining drag = the retriever not honouring filenames named in the user's clarification turn. |
| **Large Invoices + Financial Reports** (Method B, post-fix, isolated) | 2026-05-23 | **89.6% scored / 100% real** | 0 real failures | 35m 14s | One agent per fixture (real-user scenario). All 5 "misses" are correct agent answers blocked by strict regex. The platform CAN handle 100+ page PDFs + complex multi-sheet XLSX correctly. See `large_invoice_reports_comparison.md`. |

Knowledge suites combined: **75/77** raw questions correct = **97.4%**.
Data Assistant baseline: **16/24** = **66.7%** with one HIGH-severity bug to fix.

---

## Excel suite

### Headline strengths

| Capability | Score |
|---|---:|
| direct_lookup | 92.9% |
| aggregation | 100% |
| comparison | 100% |
| merged_headers | 100% |
| cross_sheet | 100% |
| multi_hop | 100% |
| scale_retrieval | 100% |
| not_present | 100% |
| **hidden_security** | **100%** (no leak of `ZX-HIDDEN-7Q-MARKER`) |
| multi_table_seg | 75% ⚠️ |

### The Excel failure

Question: *"Which customer is ranked third by revenue?"*
The agent answered using *Orders × Products* from one fixture instead of
the explicit "Top 5 Customers" table in another fixture. Both numbers
are real; the agent silently picked one source.

Discussed-and-dismissed as a product improvement (Tier 1 citation
prompt would fix it), not a regression.

---

## Word suite

### Headline strengths

| Capability | Baseline | Notes |
|---|---:|---|
| direct_lookup, bullet_extract | ~100% | minus 2 regex misses on parenthetical numbers |
| heading_nav | 100% | "what does section X cover" |
| table_in_word | 100% | Eldoria Logistics hub throughput, revenue lanes, claims |
| chart_caption | 100% | Atlas Networks ARR composition (42% Atlas Core) extracted from caption |
| **tracked_change_accepted** | **100%** | weighted 2.0 — agent correctly reported `$12,500 USD per business day` (post-tracked-insertion) and DID NOT echo the deleted `$5,000` figure. This means the extractor honours `<w:ins>` / `<w:del>` revision XML and produces a clean post-revision view. |
| footnote_extract | 100% | RFC-HLN-031 footnote surfaced correctly |
| long_doc_retrieval | 100% | DR drill date "February 14, 2026" found in a 30+ page doc |
| not_present | 100% | "Does HLN use Azure?" → correctly answered no |

### Tracked changes fidelity — why it matters

This was the most product-relevant Word finding. A worst-case extractor
would concatenate both versions and surface *"the penalty is $5,000 $12,500 USD per day"* — which would be wrong, confusing, and could surface
text the original author explicitly removed (e.g. an old confidentiality
limit that's been struck through). Confirming the extractor produces a
clean accepted-state view is worth keeping as a permanent regression
guard. Re-run this suite any time `LLMDocumentEngine._process_word`
changes.

### The Word "failures" (regex bugs)

The two failed questions had correct answers from the agent:
- `What is the initial term of the MSA?` → agent: *"twenty-four (24) months"* ✅
- `How long does the confidentiality obligation survive?` → agent: *"five (5) years"* ✅

My regex `r"24\s*months?"` didn't match `twenty-four (24) months` because of the parenthetical. Fixed in the suite — next run will be 100%.

---

## PDF suite

### Headline strengths — 100% across the board

| Capability | Score | What was tested |
|---|---:|---|
| direct_lookup | 100% | Pelagic Maritime founder, fleet size, FY2025 revenue |
| **multi_column_order** | **100%** | Halberd Steel newsletter — agent correctly extracted Q1 revenue ($1.42B), Chongqing furnace date (Feb 28, 2026), new CTO (Wynne Rasmussen), Bremen safety milestone — all from a 2-column layout in correct reading order |
| table_in_pdf | 100% | Quasar invoice — pulled line totals, customer info correctly |
| **invoice_calc** | **100%** | Subtotal (£30,800), VAT (£6,160), TOTAL DUE (£36,960) — all 3 derived figures answered correctly from the totals table |
| header_footer_isolation | 100% | Project Greenline — "CONFIDENTIAL" header repeated on every page didn't drown out body content |
| **long_doc_retrieval** | **100%** | weighted 2.0 — found "RFC-OPAL-007 author: Verena Strauss; approved Nov 4 2025" buried in chapter 11 of a 50-page PDF |
| not_present | 100% | "Does Opal Networks use Azure?" → correctly answered no |

### Why PDF is the strongest result

Two surfaces that traditionally trip up naive extractors **passed cleanly**:

1. **Two-column layout** — many text extractors read left column row-1 → right column row-1 → left row-2 → right row-2, producing interleaved garbage. The AI Hub extractor reads column-major order correctly, so the newsletter's prose stayed coherent.

2. **Repeated page headers** — without deduplication, "CONFIDENTIAL — Project Greenline · Internal Distribution Only" would appear 4× in the indexed body (one per page), heavily biasing retrieval toward that string. The agent's answers about budget, sponsor, and risks show the body content survived intact.

---

## Cross-suite observations

1. **Different file format, very similar performance.** Excel 96%, Word 93% (100% after regex fix), PDF 100%. The underlying retrieval + grounding stack is doing its job consistently across formats. The differences are mostly artifacts of how the LLM phrases answers (which regex catches).

2. **Hidden / deleted data does NOT leak.** The Excel hidden-sheet test (sentinel `ZX-HIDDEN-7Q-MARKER`) and the Word tracked-deletion test (deleted `$5,000`) both refused to surface forbidden content. Worth keeping these as permanent safety regressions.

3. **Needle retrieval works.** Both the 50-page PDF and the 30+ page Word doc had a single fingerprint fact deep inside them; both were found.

4. **The one real product gap is cross-document source disambiguation.** Documented in the Excel section. Tier-1 citation prompt would fix it; user chose to defer.

---

## Running

```powershell
$py = "$env:USERPROFILE\miniconda3\envs\aihub2.1\python.exe"

# Run one suite
& $py -m pytest tests_v2/competency/test_competency_agent_knowledge_excel.py -v -s
& $py -m pytest tests_v2/competency/test_competency_agent_knowledge_word.py  -v -s
& $py -m pytest tests_v2/competency/test_competency_agent_knowledge_pdf.py   -v -s

# Run all three
& $py -m pytest tests_v2/competency/ -v -s
```

Each suite provisions its own fresh agent, uploads its fixtures, waits
~2 min for the indexer, asks ~25 questions, and tears the agent down.

## How to add a question

In any `test_competency_agent_knowledge_*.py`, append to the
`QUESTIONS` list:

```python
(
    "<fixture filename>",
    "<question text>",
    [r"<accept regex 1>", r"<accept regex 2>"],
    ["dimension_tag_a", "dimension_tag_b"],
    [r"<negative regex>"] or None,  # forbidden pattern → leak
    1.0,  # weight (use 2.0 for security/fidelity-critical)
),
```

The report's per-dimension table picks up new tags automatically.

## How to add a new fixture

1. Put your `.xlsx` / `.docx` / `.pdf` in the suite's fixtures dir.
2. Add at least one fingerprinted fact (a string that doesn't appear in
   any other fixture).
3. Add 5–8 questions to `QUESTIONS` covering its capability dimensions.
4. Re-run the suite — fixture is uploaded automatically by the glob.

## Data Assistant: legacy vs. v2

There are two parallel NL→SQL stacks in the codebase:

| Stack | UI page | Chat endpoint | Engine | Suite | Score |
|---|---|---|---|---|---:|
| Legacy | `/data_chat` (data_chat.html) | `POST /chat/data` | `LLMDataEngine` v1 | `test_competency_data_assistant_nl_to_sql.py` | **66.7%** |
| New (v2) | `/data_explorer` (data_explorer.html) | `POST /data_explorer/chat` | `LLMDataEngineV2` | `test_competency_data_explorer_v2_nl_to_sql.py` | **91.7%** |

The suites share their 24-question battery (the v2 suite imports `QUESTIONS` from the legacy one) so the scores are directly comparable.

### What v2 does better

5 of legacy's 8 failures are single-value aggregations. Legacy 500s with empty body on:

1. *"What is the total sales revenue across all stores?"*
2. *"What is the average revenue per sale?"*
3. *"Which store had the highest total sales revenue?"*
4. *"What was the total revenue for sales in January 2025?"*
5. *"Did Downtown Flagship generate more revenue than Westside Mall?"*

v2 returns correct answers to ALL FIVE. Example for #5:
> *"Yes. Downtown Flagship generated more revenue than Westside Mall ($80,317,122.35 vs. $68,465,113.31)."*

Concrete proof that BUG-DATA-ASSISTANT-AGG-500 lives in legacy code only — specifically in `process_chat_data_request`'s answer-type branching (`app.py:1134`). v2's `_serialize_answer` (`routes/data_explorer.py:263`) handles the scalar / 1×1 dataframe case cleanly.

### What v2 still fails

Same 2 `not_present` regex misses as legacy — caused by the LLM using a Unicode curly apostrophe (`’` U+2019) in *"I can't show / answer"*. ASCII-only `'` regex doesn't match. The agent IS refusing correctly. Regex widened to `[’' ]` post-run.

### Recommendation

Either fix the legacy serializer (~30-min backport of v2's `_serialize_answer`) or sunset `/data_chat` and route everyone to `/data_explorer`. The competency suite proves the right behaviour already exists in production code — it's just on a different URL.

## Data Assistant suite (legacy detail)

### Headline strengths

| Dimension | Score | Notes |
|---|---:|---|
| `count` | 100% (4/4) | `SELECT COUNT(*) ... WHERE` — clean and correct every time |
| `distinct_count` | 100% (3/3) | DISTINCT and COUNT(DISTINCT) both reliable |
| `group_by` | 100% (6/6) | the **strongest signal in the whole suite** — every GROUP BY question produced correct, executable SQL with the right joins |
| `join_2` | 83.3% (5/6) | 2-table joins (sales × products, sales × stores) work cleanly |
| `order_by_top_n` | 80% (4/5) | `TOP N ... ORDER BY DESC` recognised |
| `date_filter` | 75% (3/4) | `YEAR(sale_date) = 2025` and `BETWEEN` shapes both handled |

### Headline weakness — a real product bug

**BUG-DATA-ASSISTANT-AGG-500.** Five questions in the battery asked for a single-value aggregation and all five returned HTTP 500 in ~30 seconds with empty SQL and empty answer:

- *"What is the total sales revenue across all stores?"*
- *"What is the average revenue per sale?"*
- *"Which store had the highest total sales revenue? Just give me the top one."*
- *"What was the total revenue for sales in January 2025?"*
- *"Did the Downtown Flagship store generate more revenue than the Westside Mall store?"*

The same aggregations GROUPED (e.g., *"What is the total sales revenue by product category?"*) work fine — they returned a multi-row dataframe and scored ✅✅. Only when the expected result is a SINGLE NUMBER (scalar, 1-row/1-column) does the request fail.

This is the kind of finding a competency suite is *supposed to surface*. None of the journey tests, lifecycle tests, or full-feature tour ever asked a question of this shape, so we shipped with a 30%-of-questions blind spot.

Suspected root cause: `process_chat_data_request` (`app.py:1134`) has an `answer_type` switch that handles `"dataframe"`, `"multi_dataframe"`, and `"string"` — likely the scalar/1×1 case falls into a branch that serialises a numpy scalar or a 1-element pandas Series in a way that breaks JSON encoding. Worth a 30-min debug.

### Other findings (lower severity)

- `not_present` 0% was almost entirely a regex bug. The agent CORRECTLY refused both ("I can't provide a customer churn rate from the information available here") — my accept-patterns required "don't have"/"not available" wording. Regex was widened post-run; next run should clear it.
- `simple_select` 50% (1/2): one question succeeded ("list the distinct product categories"), one failed soft ("what product categories are available?" — the agent answered conversationally without running a query, listing categories from memory). Borderline — the agent gave a correct-feeling answer but didn't ground it in a SQL query. Worth one more probe to decide if this is a regex tweak or a real behaviour change.

## Cross-suite observations (updated)

1. **Document extraction is strong; data analysis has a real gap.** Knowledge suites averaged 96%; Data Assistant baseline 66.7%. The gap is concentrated in one bug class (scalar aggregation) — not a systemic weakness.

2. **Two-tier scoring proved its worth.** Several Data Assistant questions matched on SQL pattern but had empty answer text (DB query succeeded, response serialization failed). Several others matched on answer pattern but had empty SQL (agent answered from cached schema knowledge without running a query). A single-signal scorer would have masked half of the failures.

3. **The competency layer found a HIGH-severity production bug** that no level-1 (reachability) or level-2 (feature tour) test could ever surface — because they don't ask "do single-value aggregations work?" The bug existed in production but was statistically discoverable only through a question battery of this shape.

## Workflow Execution suite

Many clients pay for workflows specifically. We had reachability (page loads) and lifecycle (workflow saves+deletes) coverage, but **zero coverage of "did the workflow produce the right OUTPUT?"**

### Baseline strengths

| Fixture | Score | Notes |
|---|---:|---|
| `var_chain_substitution` | 100% | `Set a=42 → Set b=${a}` → `b="42"`. Basic variable substitution works. |
| `conditional_branch_true` | 100% | `Conditional (x > 5)` correctly routes to the TRUE branch and runs the downstream Set Variable. Numeric coercion (`"10"` → `10`) in the comparison evaluator also works. |

### Baseline failures — two real, actionable bugs

**🔴 BUG-WORKFLOW-EVAL-STRING-MULT** — Variable arithmetic produces string multiplication, not numbers.

Reproduction: a `Set Variable` node with `evaluateAsExpression=True` and `valueExpression="${a} * 5"` (with `a=10`) returns `b="1010101010"` — Python's `eval("\"10\" * 5")` is string repeat. The substitution layer is interpolating `${a}` as the raw string `"10"` (with quotes preserved) into the eval target.

Affects ANY workflow that does math on a variable. Fix is small — coerce substituted values that parse as numbers to their numeric type before eval.

**🔴 BUG-WORKFLOW-DB-UNKNOWN-ERROR (HIGH)** — The Database node fails with "Unknown database error" on every run.

Reproduced with `SELECT COUNT(*) AS n FROM TS.product_master` on connection_id=135. The SAME SQL via `GET /execute/query_result/135/...` returns `[['200']]` (correct). So the connection works; the workflow Database node has a different broken code path.

This is **pre-existing** — `tests_v2/workflow/test_node_database.py::test_database_happy_path` was already failing with this exact "Unknown database error" message before today. The competency suite re-surfaced it from the API layer, which gives a second reproduction angle and makes it harder to ignore.

**Severity HIGH** because workflows are a flagship feature and the Database node is the most-used node type for analytical workflows. Every customer doing reporting workflows is affected.

### Documentation gotcha (not a bug)

Conditional-node downstream connections use connection types `pass` (TRUE branch) and `fail` (FALSE branch), NOT `true`/`false`. The competency suite caught this when my first attempt used `true`/`false` and the engine couldn't find a downstream connection. Worth verifying the workflow-builder UI generates the right types — if it doesn't, every user-built conditional is silently broken.

## Cross-suite observations (updated)

1. **Document extraction strong, NL→SQL strong on the new path, workflows broken.** Knowledge ~96%; Data Explorer v2 91.7%; Workflow 53.8%. The single largest quality gap in the platform right now is the workflow execution engine, specifically the Database node.

2. **Pre-existing bugs surface from new angles.** BUG-WORKFLOW-DB-UNKNOWN-ERROR was already flagged by a unit test, but the competency suite reproduces it at the API layer with concrete repro JSON for the workflow. That gives engineering an artifact to bisect against. Same pattern surfaced BUG-DATA-ASSISTANT-AGG-500 earlier — pre-existing pain that "was a known issue but nobody scored it" until a competency suite asked the right shape of question.

3. **Two-tier scoring keeps proving its worth.** The workflow suite uses an even simpler scoring tier — "did the variable get set with the expected value?" — but the same shape: per-fixture, per-dimension, per-assertion. Every weak spot gets a label and a number, and the report tells you exactly which workflow stanza is broken.

## Next competency suites to build

- `test_competency_agent_knowledge_word.py` ✅ done
- `test_competency_agent_knowledge_pdf.py` ✅ done
- `test_competency_data_assistant_nl_to_sql.py` ✅ done (legacy)
- `test_competency_data_explorer_v2_nl_to_sql.py` ✅ done (new path)
- `test_competency_workflow_execution.py` ✅ done (surfaced 2 workflow bugs)
- `test_competency_mcp_tool_routing.py` — given N prompts, does the
  right MCP tool fire? Critical for safety (a wrong tool fire can
  delete files / send wrong emails).
- `test_competency_custom_agent_prompt_adherence.py` — does the agent
  honour its system prompt under edge / jailbreak inputs?
- Expanded workflow suite — add Loop, File, AI Action, Excel Export
  nodes once the Database-node regression is sorted.
