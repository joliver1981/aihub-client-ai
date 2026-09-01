# Handoff: document search ships 25K tokens of field metadata on every question, and cannot be pointed at a cheaper model

**Status:** researched 2026-08-31. Not fixed.
**Component:** `DocUtils.document_search_super_enhanced_debug` — Step 2 (field selection) and
Step 3 (search strategy), plus the model plumbing in `AppUtils` / `api_keys_config`.
**Severity:** medium-high on cost, low on risk. Nothing here is a correctness bug; it is spend.
**Production relevance:** this is the lane General Agents use (`document_super_search`), and the
same function serves Command Center and The Agent through the facade's LOOKUP branch. **Both
changes below benefit all three surfaces.**

Two separate pieces of work, in the order they should be done:

1. **Trim the field-metadata block** — bigger saving, lower risk, provider-independent
2. **Add a doc-search-specific OpenAI model knob** — so Luna can be tested without touching the
   system-wide model

---

# Part 1 — the field-metadata block

## In plain English

Before it searches anything, the engine hands the strategy model a catalogue of **every field
name in the document universe** — 500 of them — so the model can decide which fields to filter
on. That catalogue is **102,423 characters, about 25,600 tokens**, and it is sent on **every
single search**, before the user's question is even considered. A near-identical catalogue goes
to the field-selection step one stage earlier.

It is also mostly packaging. The six keys across all 500 entries contain about 31,400 characters
of actual data. The rest — roughly **69%** — is JSON punctuation and `indent=2` whitespace.

## Measured

`get_document_universe(document_types=["lease_agreement"])` on the pack-23 corpus:

| what is sent to the strategy model | chars | ~tokens | vs today |
|---|---:|---:|---:|
| **as sent today** (`indent=2`, 6 keys × 500 fields) | 102,423 | 25,605 | 100% |
| same objects, compact JSON | 75,422 | 18,855 | 74% |
| **`name` + `document_count` only, compact** | 16,440 | 4,110 | **16%** |
| field names only | 9,955 | 2,488 | 10% |

Per-key contribution across all 500 entries:

| key | bytes | assessment |
|---|---:|---|
| `document_types` | 9,500 | Largest key — but the search has **already filtered** to the chosen types, so it is the same value repeated 500 times |
| `name` | 9,454 | **Needed** |
| `display_name` | 9,454 | Mechanically derived from `name` (`name.replace('_',' ').title()`, `DocUtils.py:2565`) — pure duplication |
| `count` | 1,028 | Near-duplicate signal to `document_count` |
| `sample_values` | 1,000 | **Empty on all 500 entries** |
| `document_count` | 985 | Useful — lets the model prefer common fields |

`sample_values` is empty by configuration, not accident: `DOC_INCLUDE_FIELD_SAMPLES_VALUES = False`
(`config.py:630`), commented *"takes time and might not be used"*. The key is still emitted 500
times, and the console still prints "(sampling values)" while sampling is off.

## Where it is sent

`DocUtils.py:4135`, inside the Step-3 strategy prompt:

```
Detailed field metadata with usage statistics:
{json.dumps(universe_data.get('field_metadata', []), indent=2)}
{json.dumps(attribute_metadata, indent=2)}
```

## Why trimming is low-risk — four independent pieces of evidence

1. **The trimmed shape is already proven in production, one step earlier.** Step 2
   (`ai_select_relevant_fields`) receives *exactly* `{field_name, document_count}` and nothing
   else — built at `DocUtils.py:4080` — and returns confident, well-reasoned selections. The
   reasoning that correctly identified *"city and state help confirm that the property is the
   Boston location"* came from that trimmed input. **If the trimmed shape were insufficient,
   step 2 would already be failing.**

2. **Step 3 already receives step 2's answer.** `ai_strategy_prompt = "AI Suggested Fields: " +
   str(ai_selected_fields)` is in the same prompt. So the strategy model gets the 12-field
   shortlist **and** the 500-field haystack it was distilled from. The narrowing work is done and
   then handed back the raw input.

3. **A second full field list was already removed from this same prompt.** Directly above the
   strategy prompt (`DocUtils.py` ~4127) sits a commented-out block:
   `# CRITICAL FIELD VALIDATION RULE: Only use field names that appear EXACTLY in this list…`
   with a note: *"This WAS in the below prompt but removed b/c it was thought to be
   unnecessary."* Someone already deleted one copy of the field list and nothing broke.

4. **There is no validation that trimming could break.** `available_field_names` is computed at
   `DocUtils.py:4075` and referenced **nowhere afterwards** except inside that comment. It is a
   dead variable. Nothing checks the field names the strategy returns.

## Recommended change

Send `{name, document_count}` as compact JSON, **and keep `sample_values` when it is non-empty**.

That last clause matters: sample values are the one key that would genuinely help a model build
a filter (knowing a `city` field contains "Boston, MA" vs "Boston" changes the operator you
pick). They cost nothing today because they are disabled — but a blanket drop would silently
remove the benefit if anyone re-enables `DOC_INCLUDE_FIELD_SAMPLES_VALUES` later, and the trim
would then look like the cause of a regression it did not create.

**Expected saving: ~86,000 chars / ~21,500 tokens per search**, on the more expensive model, for
every surface.

## Scope and cautions

- **Do not also trim step 2's input.** It is already minimal (`DocUtils.py:4080`). Only the
  Step-3 embed at `DocUtils.py:4135` is the problem.
- **Verify field filters still build.** The strategy emits `field_filters` with
  `"field_name": "exact_field_name_from_available_list"`. Names survive the trim, so filters
  should be unaffected — but the hybrid path is worth exercising explicitly, especially since
  `8b6eb51` recently made the `4b/6` field branch reachable again.
- **Consider the two disagreeing caps.** `DOC_TOP_N_FIELDS_INCLUDED_IN_RESULTS = 200`
  (`config.py:820`) but the universe returns **500**, and the trace confirms "planner sees top
  500 of 500". Something upstream is capping at 500 independently. Worth understanding before
  tuning either number — not part of this change.
- **The `document_types` key becomes provably redundant** once the search has filtered to
  specific types. If a future caller passes no type filter, it stops being redundant. Keep the
  trim conditional on that rather than deleting the key outright.

---

# Part 2 — pointing document search at a different OpenAI model

## The current state, measured

GA document search makes four model calls. **Three of the four are already on Luna:**

| step | function | model slot | actual model today |
|---|---|---|---|
| 1 · pick document types (`DocUtils.py:4031`) | `azureMiniQuickPrompt` | mini | **gpt-5.6-luna** |
| 2 · pick relevant fields (`DocUtils.py:3488`) | mini, gated by `DOC_USE_MINI_MODEL_FOR_AI_FIELD_SELECTION=True` | mini | **gpt-5.6-luna** |
| **3 · search strategy + terms (`DocUtils.py:4203`)** | `azureQuickPrompt` | **main** | **gpt-5.6-terra** |
| 4 · re-rank (`DocUtils.py:545`) | `claudeQuickPrompt` | — | Anthropic Haiku |

**Only Step 3 is on the expensive model.** "Point document search at Luna" is therefore a
change to exactly one call site.

The model slots available (`api_keys_config.get_openai_config`, `config.py:124-147`):

| slot | Azure var | Direct-OpenAI var | default |
|---|---|---|---|
| main | `AZURE_OPENAI_DEPLOYMENT_NAME` | `OPENAI_MODEL` | gpt-5.6-terra |
| mini | `AZURE_OPENAI_DEPLOYMENT_NAME_MINI` | `OPENAI_MODEL_MINI` | gpt-5.6-luna |
| alternate | `AZURE_OPENAI_DEPLOYMENT_NAME_ALTERNATE` | — | = main |

Setting either of the first two changes the model **system-wide** — which is exactly what must
not happen here.

## Option A — mirror the toggle that already exists *(smallest, recommended for the test)*

`DOC_USE_MINI_MODEL_FOR_AI_FIELD_SELECTION` (`config.py:640`) already does precisely this for
Step 2. Its comment states the intent: *"Uses a mini model instead of core model for ai field
selection (reduces token usage on core model)."*

Add the equivalent for Step 3 — e.g. `DOC_USE_MINI_MODEL_FOR_SEARCH_STRATEGY`, defaulting to
`False` so today's behaviour is unchanged — and branch at `DocUtils.py:4203` the same way
`DocUtils.py:3488` already branches.

*One `if/else`, no new model plumbing, uses machinery already proven three lines away in the
same file. Since mini = Luna, it delivers exactly the A/B being asked for.*

**Limitation:** it can only select "the mini model", not an arbitrary model. Fine for this test,
insufficient if the goal is a permanent per-lane model choice.

## Option B — a real per-lane model override *(better long-term)*

Mirror what was done on the Anthropic side. `DOC_SWEEP_MODEL` (`config.py:984`) exists precisely
so the document sweep can run a different model from `ANTHROPIC_MODEL`; `enumerate_engine._llm`
takes an optional `model=` parameter, defaulting to the system model.

The OpenAI equivalent: add an optional `model=None` parameter to `azureQuickPrompt`
(`AppUtils.py:445`) that overrides the value resolved from `get_openai_config`, and a
`DOC_SEARCH_OPENAI_MODEL` config that Step 3 passes.

*More flexible — any deployment, not just "the mini one" — and gives one consistent pattern
across both providers. Slightly more surface area, since `azureQuickPrompt` has ~15 callers and
the new parameter must default to today's behaviour for all of them.*

**Recommendation: A now to unblock the measurement, B if per-lane model choice becomes a
standing requirement.** They are compatible — A is a special case of B.

## The risk that matters

Step 3 is the call that was **just fixed** (`293eb8b`). It now emits `question_entities` and
entity-bearing search terms, and its failure is what produced the Summit Center wrong-city
answer. It is the most reasoning-heavy step in the pipeline. Moving it to a smaller model is
exactly where a regression would appear.

**Do Part 1 before testing Part 2.** A 6× smaller strategy prompt is a materially easier task;
judging Luna against the current bloated prompt tests it on the harder version of the job and
may reject a model that would have been fine.

---

# How to verify

Both parts share one test, because both touch Step 3.

**The regression case** — ask a General Agent with `document_super_search`:

> *"what length is the term of Summit Center, Boston lease?"*

Ground truth: three Boston Summit Center leases — **S300 (15 yrs), S350 (15 yrs), S400 (5 yrs)**.
Three more in Chicago (S325, S375, S425) exist as the trap.

- **Pass:** all three Boston stores, no Chicago store, and ideally the ambiguity note.
- **Fail:** any Chicago store, or fewer than three Boston stores.
- **Watch the console** for `[search] 4/6 semantic search · N term(s): [...]`. "Summit Center"
  and "Boston" must survive into the terms. A right answer with stripped terms is luck, not a
  pass.

**Run three times.** Steps 1 and 3 run at `temperature=1.0` whenever `reasoning_effort` is
configured, so single runs prove nothing.

**Measuring the saving:** the trace already prints `[search] universe: N distinct fields`. Log
`len(json.dumps(...))` for the block before and after; the number to beat is **102,423 chars**
on a `lease_agreement` search against the pack-23 corpus.

Corpus and generator: `test_human/23_Doc_Corpus_250/`; ground truth in
`C:\temp\doc_corpus_250\ground_truth.json`.

---

# Related finding, not part of this work

**Field names returned by the strategy are never validated.** The check was written, commented
out (`DocUtils.py` ~4127), and its variable `available_field_names` (`DocUtils.py:4075`) left
behind unused. A hallucinated `field_name` in `field_filters` flows straight into the SQL filter
builder. This is independent of both changes above — but whoever trims the metadata will be
looking directly at the dead validation, and it is worth deciding deliberately rather than
leaving it in place.
