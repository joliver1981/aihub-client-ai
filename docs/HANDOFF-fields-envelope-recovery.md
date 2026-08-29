# HANDOFF — Recover from a model omitting the `fields` envelope

**Written:** 2026-08-29
**For:** the agent implementing this fix
**Status:** spec only. No code has been written. All line numbers below were verified
against the working tree on 2026-08-29.

This document is self-contained. Background (not required reading):
[haiku-vs-sonnet-five-below-extraction.md](docs/haiku-vs-sonnet-five-below-extraction.md).

---

## 1. The bug, in one paragraph

`populate_schema_with_claude` asks the model for
`{"fields": {"<key>": {"value":…, "confidence":…, "assumptions":[], "sources":[]}}}`.
On 2026-08-29 a Haiku run returned valid JSON with the **values at the top level and no
`fields` wrapper**. The function returned that unvalidated, every downstream consumer did
`result.get("fields", {}) or {}` → `{}`, zero rows were written, and the workflow reported
`success: true` / "52/52 steps completed, 0 failed". A production document silently
disappeared from the client's output workbook.

**The extraction itself was correct.** Customer, document title, everything — present and
accurate in the payload. Only the container was wrong. This is recoverable in pure code.

### Evidence (verbatim from the workflow log)

Working file:
```
extractedCustomerReq_full = {"chunk_count":1,"chunked":false,"fields":{"customer":{"assumptions":[],
                             "confidence":"HIGH","sources":...
```

Failing file:
```
extractedCustomerReq      = {}
extractedCustomerReq_full = {"chunk_count":1,"chunked":false,"customer":"Five Below",
                             "document_title":"Vendor Agreement Section ...
```

(Both log lines are truncated by the logger. The shape is the point: no `fields` key,
values hoisted to the top level.)

---

## 2. Goal and non-negotiables

**Goal:** when the model omits or renames the envelope, recover the data automatically and
let the run continue. This process executes unattended at client sites; nobody is watching
the pipeline. A hard stop is not an acceptable recovery.

**Non-negotiables:**

1. **Reshape only. Never fabricate a value.** Moving existing data into the right container
   is always safe. Inventing a value that was not returned is not — a wrong value in a
   compliance workbook is undetectable by human review. If the payload cannot be confidently
   mapped, leave the field absent; do not guess.
2. **Zero cost and zero behaviour change on the happy path.** A conformant response must
   take one `isinstance` check and be returned byte-identical.
3. **Do not change the prompt.** `build_extraction_instructions`
   ([AppUtils.py:3053](AppUtils.py:3053)) is already explicit — it prints the literal
   required shape and says *"Do NOT include any keys other than 'fields' and
   'global_assumptions'."* The model ignored a correct instruction. Prompt edits are out of
   scope.

---

## 3. Scope

**IN — Tiers 0 through E** (defined in §5).

**OUT — deliberately deferred, do not build:**
- Any change to what gets written into the client Excel workbook (no new status column, no
  placeholder rows for failed extractions). Judged too risky for now.
- Consequence to accept: if Tiers 0–E all fail, behaviour is **unchanged from today** — the
  document produces no row and the run stays green. That residual gap is known and accepted
  for this pass.
- Prompt changes (see §2.3).
- Any change to the existing `failed_chunks` / `allowPartialExtraction` mechanism (§4.3).

---

## 4. The code, as it stands today

### 4.1 The defect site

`populate_schema_with_claude` — [AppUtils.py:3858](AppUtils.py:3858)
Signature (abridged):

```python
def populate_schema_with_claude(
    pdf_path: str,
    schema_fields: Dict[str, str],     # <-- field_key -> description. THE EXPECTED KEY SET.
    ...
) -> Dict[str, Any]:
```

Its final statement, **[AppUtils.py:4108](AppUtils.py:4108)**:

```python
    return populated
```

`populated` is whatever `json.loads()` produced. Note the function *already* has a JSON
recovery path immediately above (`_escape_unescaped_inner_quotes` for unescaped inner
quotes) — so repairing model output here is established practice in this function. What is
missing is **structural** validation.

`schema_fields` being a parameter is the key enabler: **the expected key set is known before
the call.** Detection is a set comparison, not a heuristic.

### 4.2 The wrapper that adds the metadata you saw in the log

`populate_schema_with_claude_chunked` — [AppUtils.py:3501](AppUtils.py:3501).
For documents under the page limit it calls `populate_schema_with_claude` and then stamps
`chunked` / `chunk_count` / `total_pages` onto the returned dict
([AppUtils.py:3630](AppUtils.py:3630) onward). That is why the log shows `chunk_count` and
`chunked` sitting beside the hoisted field values.

### 4.3 Precedent — there is already a normalizer for a *different* drift shape

`_execute_document_extraction` in workflow_execution.py contains, at
**[workflow_execution.py:2507](workflow_execution.py:2507)**:

```python
        if isinstance(extraction_result, list):
            primary_field = next(...)
            self.log_execution(execution_id, node_id, "warning",
                f"Extraction returned a bare top-level array ({len(extraction_result)} item(s)); "
                f"wrapping under field '{primary_field}' so it isn't lost")
            extraction_result = {primary_field: extraction_result} if primary_field else {}
```

**Read this before writing anything.** It is the same class of fix, it establishes the house
style (normalize + `warning` log + "so it isn't lost"), and it means the bare-array case
(Tier D) is *already handled at the node level*. Decide consciously whether your Tier D
duplicates it — see §7.

Just below it, from ~[workflow_execution.py:2535](workflow_execution.py:2535), is the
`failed_chunks` / `allowPartialExtraction` mechanism. **Do not modify it.** Be aware it
exists so your changes do not conflict with or duplicate its behaviour.

### 4.4 The consumers that silently swallow the bad shape

- [compliance_engine.py:446](compliance_engine.py:446) — `if "fields" in extracted:`
- [compliance_engine.py:476](compliance_engine.py:476) — `fields = result.get("fields", {}) or {}`
- `_execute_ai_extract_node` — [workflow_execution.py:902](workflow_execution.py:902);
  reads `extraction_result_full.get('fields', {})` at ~[:1000](workflow_execution.py:1000)

**Do not patch these individually.** Two of them already independently return empty on a
missing envelope; adding a third patch site is how this problem regrows. Fix upstream.

---

## 5. What to build

Add one helper and call it at the single `return` in `populate_schema_with_claude`.

```python
def _coerce_fields_envelope(populated, schema_fields, logger):
    """Return (result, repair_tier_or_None). Reshapes only; never invents values."""
```

### Tier 0 — conformant. The fail-safe gate.

```python
if isinstance(populated, dict) and isinstance(populated.get("fields"), dict):
    return populated, None
```

Return the **same object**, unmodified. Nothing below runs. This is what satisfies
non-negotiable #2 — verify it with test 1 in §6.

### The overlap gate — applies to every tier below

```python
expected = set(schema_fields)
overlap = len(found_keys & expected) / max(1, len(expected))
```

A tier may only fire if `overlap >= DRIFT_MIN_OVERLAP` (start at `0.5`; make it a module
constant, not a magic number). Below threshold the model answered a different question —
fall through, return the payload untouched, and let today's behaviour apply. **Never
"reshape harder" to force a match.**

Only keys present in `expected` are carried into `fields`. Unknown keys are dropped, not
invented into fields.

### Tier A — flat scalars *(the observed failure — highest priority)*

Input `{"customer": "Five Below", "document_title": "…"}` where keys overlap `expected` and
values are scalars/lists/dicts that do **not** themselves contain a `value` key.

```python
{"fields": {k: {"value": v, "confidence": None, "assumptions": [], "sources": []}
            for k, v in populated.items() if k in expected}}
```

Preserve any non-field top-level keys the pipeline expects (`global_assumptions`,
`cell_formatting`, `chunked`, `chunk_count`, `total_pages`) rather than dropping them.

### Tier B — per-field enveloped, wrapper missing

Keys overlap `expected` and values are dicts containing `value`. Add the `fields` wrapper
only; do not touch the inner dicts.

### Tier C — renamed wrapper

`populated` has exactly one dict-valued key whose **inner** keys clear the overlap gate
(e.g. `{"data": {...}}`, `{"result": {...}}`, `{"extraction": {...}}`). Lift it to `fields`.

**Match by key-overlap, not by a wrapper-name allowlist.** Any single wrapper key qualifies
if its contents match the expected fields. This is self-deriving and needs no list
maintained as models change.

### Tier D — list form

`[{"field": "customer", "value": "Five Below"}, …]` → rebuild the dict. See §7 first: the
node already handles a bare top-level array differently.

### Tier E — one automatic re-ask

Only if A–D cannot place the payload **and** the payload is non-empty.

Send the malformed JSON **as text** back to the model with `schema_fields` and an
instruction to return the same data in the required envelope.

- **Do NOT re-send the PDF.** Only the few KB of JSON the model already produced. This is a
  pure reformatting task, so it is cheap and fast.
- Route it to the mini/cheap model (`cfg.ANTHROPIC_MINI`) regardless of which model did the
  extraction — reformatting does not need the expensive one.
- Instruct it to change **keys and structure only, never values**.
- **One attempt.** No retry loop.
- Feed the reply back through Tiers 0–D for validation. Never trust it directly.
- On any failure (exception, timeout, still-unplaceable): log a warning and return the
  original payload untouched. Tier E must never be able to make things worse.

### Observability (for you, not for the client)

On any repair: a `warning` log naming the tier, the model, and the field count recovered —
matching the house style at [workflow_execution.py:2516](workflow_execution.py:2516). Add
`"_envelope_repaired": "<tier>"` to the returned dict so drift stays visible and you can
measure how often a given model needs it. Do not surface this in the Excel output (§3).

---

## 6. Tests

Unit tests, no API calls. Put them in `tests/unit/test_fields_envelope_recovery.py`.

1. **Conformant** → returns the **identical object** (assert `is` identity or deep-equality
   plus absence of `_envelope_repaired`). This is the fail-safe-only proof.
2. **The observed failure** — `{"chunk_count":1,"chunked":false,"customer":"Five Below",
   "document_title":"Vendor Agreement Section 2 of 3: Compliance"}` with
   `schema_fields={"customer":…,"document_title":…,"program_type":…,"record_id":…}` →
   Tier A, values correct, `chunk_count`/`chunked` preserved.
3. Per-field enveloped, no wrapper → Tier B, inner dicts untouched.
4. `{"data": {...}}` → Tier C.
5. List of `{field, value}` → Tier D.
6. **Overlap gate**: `{"unrelated": 1, "other": 2}` → **no repair**, payload returned
   unchanged, no exception, **no invented fields**.
7. `{}` and `[]` → no repair, no exception, no invented fields.
8. Overlap just below / just above `DRIFT_MIN_OVERLAP` → documented boundary behaviour.
9. Tier E mocked: assert the PDF is **not** re-sent, exactly one call, mini model used, and
   that a bad re-ask reply leaves the original payload unchanged.

### Test-environment gotcha (this will cost you an hour otherwise)

**No conda env has both `pytest` and `anthropic`.** Document processing runs under
`aihubant`; pytest lives in `aihub2.1`. Existing tests solve this by stubbing the
`anthropic` import — see `tests/unit/test_schema_extraction.py` for the established pattern.
Follow it. Keep `_coerce_fields_envelope` free of Anthropic imports at module scope so it is
testable in isolation.

---

## 7. Decision to make before coding

**Where does Tier D live?** The node already normalizes a bare top-level array at
[workflow_execution.py:2507](workflow_execution.py:2507), using the field *list* (with
`repeated_group` preference) rather than `schema_fields`. Options:

- **(a)** Implement Tiers 0–C and E in AppUtils; leave the array case to the existing node
  code. Smallest change, no duplication. **Recommended.**
- **(b)** Implement Tier D in AppUtils too, and leave the node normalizer alone as a
  belt-and-braces second line. Harmless but redundant.

Do **not** move or rewrite the node normalizer — it also serves the text-extraction path.

---

## 8. Other gotchas

- **Restart required.** `AppUtils` is imported by many services. Document extraction in this
  workflow runs through the workflow executor service (127.0.0.1:5061) and the document API;
  changes are not live until those restart. Use the project's restart script — **never pipe
  the launcher from an agent shell**.
- **`AppUtils` ships compiled (`AppUtils.pyd`) in client builds.** A client release needs the
  compile step; a source-only change will not reach a packaged install.
- **`.gitignore` hides `test*.py`.** New test files need `git add -f`.
- **Do not branch.** House rule: commit to `main`, promptly, and ask before large pushes.

---

## 9. Definition of done

- [ ] Conformant responses provably unchanged (test 1).
- [ ] The observed Haiku payload recovers to correct values (test 2).
- [ ] Low-overlap / empty payloads produce **no invented fields and no exception**.
- [ ] Tier E never re-sends the PDF and cannot worsen a failure.
- [ ] Nothing in `compliance_engine.py` or the AI Extract node was patched individually.
- [ ] Nothing written to the Excel output changed.
- [ ] Full unit suite green in `aihub2.1`.
- [ ] Live re-run of `Vendor Agreement_Section 2 of 3_Compliance_ Compliance 2025.pdf` on
      Haiku produces a `Customer_Summary` row.
