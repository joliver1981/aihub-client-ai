# Amendment blindness: the engine answers from the original lease and ignores the amendment

**Status:** confirmed 2026-08-31 against the pack-23 corpus. Not fixed.
**Component:** `doc_search_v3/enumerate_engine.py` (per-document verdicts + roll-up)
**Severity:** high — silent and confidently wrong. The answer looks authoritative and cites a
real document; it is simply the superseded one.

---

## In plain English

A commercial lease is rarely one document. There is the original lease, and then over the
years there are amendments: "as of March 2025, Section 6.3 is deleted and replaced with the
following." The amendment wins. Any lawyer, any lease administrator, reads the lease *and*
its amendments together and answers from the combination.

Our engine reads one document at a time. It opens the original lease for store S303, sees
"Tenant shall maintain the HVAC," and records **tenant**. It then separately opens the
First Amendment for S303, which moves HVAC to the landlord — and records that as a
completely unrelated document's answer.

When it counts up "which stores put HVAC on the landlord," S303 is filed under **tenant**,
because that is what the document called "the lease" said. The amendment is either counted
as its own separate row or contributes nothing.

**The engine is not misreading anything. It is reading each document correctly and has no
concept that two documents can be about the same thing, with one overriding the other.**

## The evidence

Pack 23 contains 130 store leases. 18 of them have amendments; 8 of those amendments
deliberately change the HVAC answer, and 10 change rent, expiry and the CAM cap. Ground
truth records both the original value and the effective value.

Verdicts are persisted per document (`_write_back` → `DocumentFields`), so each lease could
be scored individually against ground truth:

| dimension | leases with NO amendment | leases WITH an amendment |
|---|---|---|
| HVAC responsibility | **110 / 111 correct (99%)** | **9 / 17 correct (53%)** |
| CAM cap | **111 / 111 correct (100%)** | **12 / 18 correct (67%)** |

The 8 HVAC errors are S303, S311, S318, S325, S337, S344, S352, S369 — *exactly* the eight
stores whose amendments flip the HVAC clause, with no others. The 6 CAM errors (S314, S329,
S341, S377, S384, S396) are all money-amendment stores.

In every single case the engine returned the **original lease's** value:

| store | original lease says | amendment says | engine answered |
|---|---|---|---|
| S303 | tenant | landlord | tenant |
| S318 | split | landlord | split |
| S344 | silent | split | not addressed |
| S352 | landlord | tenant | landlord |

Related: the `multihop` question class, which exists to test exactly this, scored **0 / 8**.

This matters more than the headline accuracy number suggests. On documents read in
isolation the engine is essentially perfect (221/222). **Every measurable error in the
fan-out lane traces to this one missing concept.**

## Why the aggregate counts hid it

The reported totals were only 6–8 documents off, which reads like ordinary noise. It is not
noise — it is 14 specific, individually wrong answers, partly masked because the scope also
contained extra documents pulling the count the other way. Anyone reading only the count
would conclude the engine was roughly fine.

## Proposed fix

Three options, cheapest first. **Option B is the recommendation.**

### Option A — group documents into families before reading them
Give each amendment a pointer to the lease it amends, then send the whole family to the
model as one unit: "here is the lease and its two amendments; what is the HVAC
responsibility today?"

*Accurate, and closest to how a person does it. But it makes the expensive per-document
pass bigger — a family of three documents is three times the tokens in one call — and the
fan-out lane is already the most expensive thing in the product.*

### Option B — reconcile AFTER the per-document pass  ← recommended
Leave the per-document reading exactly as it is. Add a reconciliation step at roll-up:
group the verdicts by family, order them by document date, and let the newest document that
actually speaks to the question win. Silent documents do not override anything.

*Costs no additional model calls at all — it is a grouping rule over verdicts already
stored in `DocumentFields`. Given that a single uncached fan-out question already costs
around a million tokens, a fix that adds zero inference cost is worth a lot. It also
fixes the historical verdicts already in the table, not just future ones.*

The per-document verdict would need to carry two extra things it does not carry today:
- **which family the document belongs to**
- **whether the document actually addressed the question**, so a silent amendment does not
  overwrite a good answer from the base lease

### Option C — teach the roll-up to subtract
Keep counting per document, but when a document is an amendment, remove its parent from the
tally. *Least invasive, and wrong as soon as a lease has two amendments that touch different
clauses. Not recommended.*

## The hard part is identifying the family

This is where the real design work is, and it should not be hand-waved.

In pack 23 it is easy — every document carries a store identifier. Real client documents
will not be so tidy. Options, roughly in order of reliability:

1. **An explicit link captured at ingest.** The document-records / schema work already
   extracts structured fields per document type. Add an `amends` / `parent_document`
   field to the amendment document types and populate it during extraction. Most reliable,
   because it is decided once with the full document in front of the model.
2. **Match on the identifiers already extracted** — property address, original lease date,
   tenant and landlord names. Amendments restate these in their recitals precisely so they
   can be tied back. Good, but fuzzy matching on addresses will need care.
3. **Filename convention.** Works on tidy corpora, fails on real ones. Fine as a fallback,
   never as the primary.

Whatever is chosen, the family link belongs in the data model, not in the question-answering
path — every lane benefits from it, and it should be computed once at ingest rather than
re-derived on every question.

## Scope and cautions

- **Do not change the per-document verdict prompt to "consider amendments."** The model
  cannot see the other documents; asking it to account for them invites invention.
- **A silent amendment must not overwrite a good answer.** An amendment that changes only
  the rent says nothing about HVAC. The reconciliation has to distinguish "says landlord"
  from "does not address it", which is why the verdict needs an explicit addressed/not-addressed
  flag rather than a free-text value that happens to read as empty.
- **Estoppel certificates are not amendments.** Pack 23 contains 8, three of which state
  figures that contradict the executed lease, and one says on its face that it was never
  reconciled against the lease. They restate; they do not amend. If the family logic treats
  a later-dated estoppel as superseding, it will make accuracy worse. There is a ready-made
  test for this: pack-23 questions Q067 and the estoppel stores S334, S372, S408.
- **Ordering must use the document's own effective date, not `processed_at`.** Ingest order
  is arbitrary; an amendment loaded before its lease is normal.

## How to verify a fix

The corpus and its ground truth already encode the answer:

```bash
python test_human/23_Doc_Corpus_250/analyze_verdicts.py --out C:\temp\doc_corpus_250
```

It prints per-dimension accuracy split by whether a lease has an amendment. **Today that
reads 99% unamended / 53% amended for HVAC. A correct fix brings the amended column up to
match the unamended one without moving the unamended column.**

Then re-run the `multihop` class, which is currently 0/8:

```bash
python test_human/23_Doc_Corpus_250/run_docstore.py --cls multihop --out C:\temp\doc_corpus_250
```

Ground truth for each amended store is in `ground_truth.json` under `dims` (the original
lease value) and `dims_effective` (the value after amendments) — the two are deliberately
different for exactly these stores, so a fix that reads amendments cannot pass by accident.
