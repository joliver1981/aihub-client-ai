# Haiku vs Sonnet — Five Below vendor agreement extraction

**Date:** 2026-08-29
**Corpus:** 3 production PDFs, 107 pages, 242,283 chars
(`Vendor Agreement part 3 of 3 Supply Chain 2025.pdf`, `_Section 1 of 3_Legal 2025.pdf`,
`_Section 2 of 3_Compliance_ Compliance 2025.pdf`)
**Outputs:** `C:\temp\Horizon_Customer_Onboarding\output\latest\5 Below_Template_{Haiku,Sonnet}.xlsx`

---

## Bottom line

**Do not ship Haiku on this workflow yet.** Not because of volume — Haiku extracted
2.7× more requirements than Sonnet and is *not* padding — but because it silently
missed the single most legally significant requirement in the corpus and rated
everything it did find HIGH confidence.

Separately: **the missing summary row is a platform bug, not a model quality issue.**
Haiku extracted the data correctly; the AI Extract node threw it away and reported
success. That bug can bite Sonnet too.

---

## 1. The missing summary row — root cause found

Sonnet's `Customer_Summary` has 3 rows (all files). Haiku's has 2 — missing
`Vendor Agreement_Section 2 of 3_Compliance`.

The workflow log contains the proof. Compare the two variable states:

**Working file (12:14:20):**
```
extractedCustomerReq      = {"customer":"Five Below","document_title":"...Section 1 of 3: Legal",...}
extractedCustomerReq_full = {"chunk_count":1,"chunked":false,"fields":{"customer":{"assumptions":[],
                             "confidence":"HIGH","sources":...
```

**Failing file (12:15:20):**
```
extractedCustomerReq      = {}
extractedCustomerReq_full = {"chunk_count":1,"chunked":false,"customer":"Five Below",
                             "document_title":"Vendor Agreement Section ...
```

`_full` on the failing file **has the extracted data** — customer, document_title, all of
it. What it does not have is the `"fields": {...}` envelope. Haiku returned the flat
shape; the parser looks for a `fields` key, found none, produced `{}`.

The node then reported:
```
Output: { "extraction": {}, "mode": "document",
          "excel": { "rows_written": 0, "success": true, ... } }
```

**Zero rows written, `success: true`, workflow "52/52 steps completed, 0 failed".**

### Exact defect site

The envelope is produced by `populate_schema_with_claude`
([AppUtils.py:3858](AppUtils.py:3858)). Its final statement is
[AppUtils.py:4109](AppUtils.py:4109):

```python
    return populated
```

`populated` is whatever `json.loads(output_text)` produced, **returned unvalidated**.
Malformed JSON raises loudly (good) — but JSON that parses yet has the *wrong structure*
passes straight through. `populate_schema_with_claude_chunked`
([AppUtils.py:3630](AppUtils.py:3630)) then stamps `chunked` / `chunk_count` /
`total_pages` onto it, which is exactly the `_full` shape seen in the log.

Every downstream consumer then does `result.get("fields", {}) or {}` — e.g.
[compliance_engine.py:476](compliance_engine.py:476), guarded by
`if "fields" in extracted:` at [compliance_engine.py:446](compliance_engine.py:446) —
so a missing envelope becomes `{}` / `[]` with no error anywhere.

**Fix belongs at the single `return populated`**, not in each consumer: either coerce a
flat `{key: scalar}` reply into `{"fields": {key: {"value": scalar}}}`, or raise. One
change covers the compliance engine, the workflow AI Extract node, and every other caller.

**This is model-agnostic.** Haiku drifts from the requested envelope more often, but any
model can, and when it does the data is discarded invisibly. Worth fixing regardless of
which model you ship: accept the flat shape as a fallback, and make an empty extraction
from a non-empty document a loud failure rather than `success: true`.

---

## 2. Requirements_Notes — the volume difference is real, not padding

| | Haiku | Sonnet |
|---|---|---|
| Rows | **259** | 96 |
| Distinct `requirement` strings | 259 (0 exact dups) | 93 (6 dup rows) |
| Near-duplicate pairs (jaccard ≥ 0.6) | 5 | 3 |
| Reused excerpts | 0 | 0 |
| Avg `value` length | 231 | 212 |
| Avg `excerpt` length | 245 | 169 |

Haiku is genuinely more granular, not fragmenting or repeating itself. On raw
completeness metrics Haiku looks *better*.

---

## 3. But Haiku missed real content — verified against the source PDFs

Keyword presence, counted in the actual PDF text:

| Term | In source PDFs | Sonnet rows | Haiku rows |
|---|---|---|---|
| **Proposition 65 / Prop 65** | **32** | 8 | **0** |
| three-way match | 1 | 3 | 0 |
| DC10 / DC11 | 1 each | 2 each | 0 |
| "sixty (60) days" | 1 | 1 | 0 |
| "ninety (90) days" | 1 | 3 | 0 |
| APSCA | 1 | 0 | **3** ← Haiku won this one |

**Prop 65 is the headline.** 32 mentions across the corpus, and it carries order-rejection
rights. From the source:

> Vendor must notify Five Below that Goods require Proposition 65 warning at the time of
> item set up. Five Below has the right to reject or cancel orders for Goods where a
> Proposition 65 warning is required

Haiku produced **zero of 259 rows** mentioning it. Sonnet produced 8.

Two caveats in Sonnet's direction, for fairness:
- `FOB Point of Destination` (3 Sonnet rows) has **0** hits in the source even allowing
  `F.O.B.`/`FOB Destination` variants — that phrasing is Sonnet's, and is worth spot-checking.
- `Treasury` is Sonnet's own topic label, not source text. That's categorization, not a claim.

---

## 4. Confidence calibration

| | HIGH | MED |
|---|---|---|
| Haiku | **259** | 0 |
| Sonnet | 92 | 4 |

Haiku's `confidence` column carries **zero information** — it cannot tell a reviewer what
to check. For a human-review workflow that removes the triage mechanism entirely.

Also: Haiku wrote `document_version_date = 2025-08-29` (the run date) on both summary rows.
Sonnet wrote `2025`, which is correct.

---

## 5. The lesson that generalizes

**Row count would have picked the wrong model.** Haiku: 259 rows, zero duplicates, longer
excerpts, 100% HIGH confidence — it wins every surface metric while missing the most
legally consequential requirement in the corpus.

Any cheap-model evaluation on this platform needs a **keyword-recall check against the
source document**, not just output volume and shape. That applies directly to the
records-lane Haiku proposal (`DocumentRecords` ingest) — this result is a caution flag for
it, not a verdict, since it is a different lane and a different prompt.

---

## 6. Recommendation

1. **Keep Sonnet** on this workflow's Extract Summary / Extract Notes.
2. **Fix the envelope silent-success** — independent value, protects Sonnet runs too.
3. **Re-run Haiku once** before concluding the Prop 65 miss is systematic. One run cannot
   distinguish a deterministic weakness from run-to-run variance, and that distinction
   changes whether Haiku is usable anywhere in this pipeline.
4. If cost is the driver, the **records lane** (a different prompt, with a `__manifest`
   honesty ledger and an exact 316-row baseline to test against) remains the better first
   Haiku candidate — but gate it on a source-keyword recall check, not row counts.
