# The missing `fields` envelope — issue write-up and self-healing fix

**Date:** 2026-08-29
**Status:** analysis + design. No code changed.
**Found in:** Five Below vendor-agreement extraction, Haiku run 2026-08-29
(see [haiku-vs-sonnet-five-below-extraction.md](docs/haiku-vs-sonnet-five-below-extraction.md))

---

## Bottom line

A document was extracted correctly and then thrown away. The workflow reported
**"52/52 steps completed, 0 failed"** and the file simply vanished from the summary tab.

The model returned valid JSON in the wrong shape — it omitted the `fields` wrapper. Nothing
checked, so every consumer read `{}` and wrote zero rows.

**This is recoverable without a human.** The data was present and intact in the payload; only
the container was wrong. The fix is a deterministic reshape at one chokepoint, with an
automatic re-ask behind it, and a guarantee that **every input file gets a row in the output
no matter what happens**.

**Design constraint driving this document:** the process runs unattended at client sites.
Clients will review the *output*; they will not troubleshoot the pipeline. So a hard stop is
not an acceptable outcome — it converts a self-fixable hiccup into a support ticket nobody
picks up. Recovery must be automatic, and the only thing that ever reaches a human is a
reviewable marker in the workbook they already open.

---

## 1. What happened

`Vendor Agreement_Section 2 of 3_Compliance` produced no `Customer_Summary` row. From the
workflow log:

**Working file (12:14:20)**
```
extractedCustomerReq_full = {"chunk_count":1,"chunked":false,"fields":{"customer":{"assumptions":[],
                             "confidence":"HIGH","sources":...
```

**Failing file (12:15:20)**
```
extractedCustomerReq      = {}
extractedCustomerReq_full = {"chunk_count":1,"chunked":false,"customer":"Five Below",
                             "document_title":"Vendor Agreement Section ...
```

The data is **present and correct** in the failing payload — customer, document title, all
of it. Only the `"fields": {...}` wrapper is missing. The node then emitted:

```
{ "extraction": {}, "mode": "document",
  "excel": { "rows_written": 0, "success": true } }
```

Worth stating plainly: **nothing was actually wrong with the extraction.** This was a
packaging error that cost a document.

## 2. The prompt was not at fault

`build_extraction_instructions` ([AppUtils.py:3053](AppUtils.py:3053)) prints the literal
required shape and adds:

> Do NOT include any keys other than "fields" and "global_assumptions".

The model ignored a correct, unambiguous instruction. Smaller/cheaper models do this more
often. **Tightening the prompt is not the fix** — it is already right, and no prompt makes
this impossible. Plan for drift instead of trying to eliminate it.

## 3. Where our code lets it through

`populate_schema_with_claude` ([AppUtils.py:3858](AppUtils.py:3858)) ends at
[AppUtils.py:4109](AppUtils.py:4109):

```python
    return populated
```

`populated` is whatever `json.loads()` produced, returned unvalidated. Malformed JSON raises
(there is even a quote-repair path just above) — but JSON that *parses* with the wrong
structure passes through untouched. `populate_schema_with_claude_chunked` then stamps
`chunked` / `chunk_count` / `total_pages` onto it, producing the `_full` shape above.

Downstream, every consumer does the same thing:

```python
fields = result.get("fields", {}) or {}      # compliance_engine.py:476
if "fields" in extracted: ...                # compliance_engine.py:446
```

No `fields` key ⇒ `{}` ⇒ zero rows ⇒ nothing raised anywhere.

---

## 4. The fix: a recovery ladder that never stops the line

The decisive fact: **`populate_schema_with_claude` receives `schema_fields: Dict[str, str]`**
— the exact field keys expected, known *before* the call. Detection is therefore a
comparison against a known key set, not pattern-guessing.

### Tier 0 — conformant (the fail-safe gate)

```python
if isinstance(populated, dict) and isinstance(populated.get("fields"), dict):
    return populated          # unchanged. one isinstance check.
```

Nothing below this line executes on a healthy response. Zero overhead, zero behaviour
change when things work.

### Tiers A–D — deterministic reshape

`expected = set(schema_fields)`. Ordered, each pure code, microseconds, free.

| Tier | Drift shape | Repair |
|---|---|---|
| **A** | Flat scalars: `{"customer": "Five Below", ...}` — **the observed case** | wrap each value as `{"value": v, "assumptions": [], "sources": []}` under `fields` |
| **B** | Flat but per-field enveloped: `{"customer": {"value": ..., "confidence": ...}}` | add the `fields` wrapper only |
| **C** | Renamed wrapper: one dict-valued key (`data`, `result`, `extraction`, …) whose inner keys match `expected` | lift it to `fields` |
| **D** | List form: `[{"field": "customer", "value": "Five Below"}, …]` | rebuild the dict |

**Tier C matches by key-overlap, not by a wrapper-name allowlist.** Any single wrapper key
qualifies if its contents match the expected fields — self-deriving, nothing to maintain.

Expected coverage: **Tier A alone almost certainly handles the observed failure and most
future ones.**

### Tier E — automatic re-ask (no human, no document re-read)

If A–D cannot place the payload, send the malformed JSON **text** back to the model with the
schema keys and "return this same data in the required envelope."

This is the highest-yield automatic recovery, and it is cheap because **the PDF is not
re-sent** — only a few KB of JSON the model already produced. It is a pure reformatting
task, so it can go to the mini/cheap model regardless of which model did the extraction.

Constraints:
- One attempt. No retry storms.
- The re-ask reshapes **keys and structure only**; it is instructed not to change values.
- Its output goes back through Tiers 0–D for validation before use.

### Tier F — last resort: write the row anyway

If everything above fails, **still write a `Customer_Summary` row** carrying the filename,
whatever values were recoverable, and a status marker (e.g. `extraction_status = "PARTIAL —
review"`).

This is the part that most directly fixes the incident. Today the file **silently
disappears** from the summary — invisible. A present row with a status is reviewable in the
artifact the client already opens, costs them nothing to notice, and keeps the run green.

**Never raise, never abort the workflow.** A stopped run is a worse outcome than a flagged
row.

---

## 5. The one hard line: reshape, never fabricate

Recovering *shape* is always safe — the content is already there and untouched.
Recovering *content* is not.

So every tier is gated on overlap with the known key set:

```python
overlap = len(found_keys & expected) / max(1, len(expected))
```

Low overlap does not mean "reshape harder." It means the model answered a different
question, and the correct response is Tier F — write the row with what exists and mark it —
**not** to invent plausible values. A repair that guesses is worse than the bug, because a
wrong value in a compliance workbook is not detectable by review, whereas a blank marked
`PARTIAL` is.

## 6. Put the signal where they already look

Since clients review output and not logs, a repair should surface **in the workbook**:

- `extraction_status` on the `Customer_Summary` row — `OK` / `REPAIRED` / `PARTIAL — review`
- optionally the tier that fired, for your own diagnosis

Keep the log line and a telemetry counter too — those are for **you**, so you can see which
models drift and how often (e.g. if Haiku needs repair on 30% of calls, that is decision-grade
information about whether to run Haiku at all). But the operational path requires no one to
read them.

---

## 7. Where the code goes

| Change | Location | Why there |
|---|---|---|
| Tiers 0–D + overlap gate | the `return populated` at [AppUtils.py:4109](AppUtils.py:4109) | one chokepoint; covers the compliance engine, the workflow AI Extract node, and every other caller |
| Tier E re-ask | same function, behind A–D | needs the raw text and `schema_fields`, both in scope here |
| Tier F row-anyway + `extraction_status` | AI Extract node result assembly / Excel export | the guarantee belongs where the row is written |

Fixing this in each consumer instead would be wrong — that is how there came to be two
separate `if "fields" in ...` checks that both silently return empty.

---

## 8. Test plan

Unit, no API calls — feed the coercion function canned payloads:

1. Conformant → returned **identical object**, no status change. (Proves fail-safe-only.)
2. **The real captured Haiku payload** → Tier A, correct values, `REPAIRED`.
3. Per-field enveloped without wrapper → Tier B.
4. `{"data": {...}}` → Tier C.
5. List-of-objects → Tier D.
6. `{"unrelated": 1}`, `{}`, `[]` → Tier F: a row is still produced, marked `PARTIAL`,
   **no exception**, and **no invented values**.
7. Overlap just under / just over threshold → boundary behaves as documented.

Tier E mocked in unit tests; exercised once live.

Then re-run the Section 2 Compliance PDF on Haiku and confirm the summary row appears.

---

## 9. What not to do

- **Do not just tighten the prompt.** It is already explicit; the model ignored it.
- **Do not repair in the consumers.** One chokepoint, or the problem regrows.
- **Do not raise or abort.** Unattended runs must finish; a flagged row beats a dead workflow.
- **Do not let repair invent content.** Overlap gate → Tier F, never a guessed value.
- **Do not re-send the PDF on the re-ask.** Reshape the JSON only; keep it cheap.
