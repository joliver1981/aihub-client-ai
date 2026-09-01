# Handoff: search knows when several documents could answer the question, and never says so

**Status: BUILT 2026-08-31**, after re-measuring per the "Do item 3 first" rule.

**The re-measurement:** with entity loss fixed (`293eb8b`), 3 GA runs of the ambiguity
case gave 2 ideal answers and 1 run that answered S350 alone as definitive while all
three Boston leases sat at the top of the window — the residual this doc predicted, so
the signal was built.

**The archaeology:** `git log -L` on the commented-out `search_execution` block shows it
commented out since the repo's initial commit — imported that way; no in-repo reverted
decision to respect.

**The discriminator** (`DocUtils._competing_documents_hint`): fire only when several
documents OF THE SAME TYPE match EVERY entity the user named (from `question_entities`,
which item 3's fix added to the strategy), within a count band
(2..`DOC_AMBIGUITY_HINT_MAX_DOCS`, default 8; `DOC_AMBIGUITY_HINT_ENABLED` gates it).
Each rule kills a false-positive class: joint entities select the three Boston leases
out of six Summit Centers; same-type-only keeps the S317 lease + roof warranty + fire
inspection trio silent (this exact trio exists in the corpus — a bare shared-entity
count would have over-fired on the control case); the band keeps breadth silent. Every
firing prints `[search]     ambiguity hint: …` so precision is measurable. Additive
only — retrieval, ranking and filtering untouched; the note never suppresses an answer.

**Plumbing** (the records_hint shape, as prescribed): appended inline to the semantic
blob for the direct GA lane; `document_search_wrapper` lifts it OUT of the raw blob
before normalization (the last `[Source …]` block swallows trailing text, so extraction
must precede parsing) and re-attaches it to `result["text"]` + `result["ambiguity_hint"]`;
the JSON lane carries it as a response field; The Agent's renderer
(`agent_service/document_tools.py`) appends it beside records_hint.

**Verified:** engine level 3/3 fires on the Summit question (always exactly the three
Boston files) and 3/3 silent on the S317 control; GA e2e 6/6 — Summit 3/3 with all
three Boston leases, "there are multiple", and a which-store offer (the pre-hint
single-store run did not recur), control 3/3 direct $18,000/month answers with zero
hedging. Facade verified: hint in `text` and as its own field, no leakage into
passages, coexists with records_hint. 13 unit tests in `tests/unit/test_ambiguity_hint.py`.

---

Original write-up follows.

**Status (original):** analysed 2026-08-31. Not fixed.
**Component:** `DocUtils.document_search_super_enhanced_debug` — Step 8.5 and the response payload
**Severity:** medium. Not a wrong answer on its own; it is what turns a retrieval near-miss into
a confident wrong answer instead of a useful clarifying question.
**Sequencing note: read the "Do item 3 first" section before building anything.** This may be
substantially unnecessary once entity loss is fixed.

---

## In plain English

The corpus holds six Summit Center leases — three in Boston, three in Chicago. A user asked for
"the Summit Center, Boston lease" as though there were one.

- **Run 1** returned a table of all three Boston leases and said: *"If you mean a particular
  store, please provide its store identifier."* Genuinely excellent — it noticed the question
  presumed a single lease, and there were three.
- **Run 2**, same question, answered about **one Chicago store** as though it were the answer.
  Correct term, correct dates, correct citation, wrong city, and no hint that alternatives
  existed.

Both behaviours came from the same code. The difference was which passages happened to reach the
model. **Run 1's behaviour was luck, not design** — nothing in the pipeline told the model that
several documents were competing, so whether it noticed depended entirely on what it happened to
be handed.

## The engine already computes the number and throws it away

`DocUtils.py:4489`:

```python
distinct_document_ids = {result['document_id'] for result in combined_results if 'document_id' in result}
count_distinct = len(distinct_document_ids)
```

`count_distinct` is used for one console line and then discarded. It never reaches the model.

More telling — the response payload once carried it, and it is **commented out**
(`DocUtils.py:4508`):

```python
# "search_execution": {
#     "total_page_results_found": len(combined_results),
#     "total_document_results_found": count_distinct,
#     ...
# },
```

So the count was surfaced at some point and someone disabled it. Worth a quick `git log -L` on
that block before reinstating it — there may have been a reason (payload size, or the model
over-reacting to it), and repeating a reverted decision would be a waste.

## Where a signal would go

The payload already has two precedents for handing the model guidance alongside results:

1. **`special_instructions`** (`DocUtils.py:4496`) — currently only populated from
   `cfg.DOC_KNOWLEDGE_SPECIAL_INSTRUCTIONS` when `ENABLE_AGENT_KNOWLEDGE_MANAGEMENT` is on;
   otherwise an empty string.
2. **`note`** — set from `check_document_completeness` when a document lacks sufficient
   information.

And in the wrapper there is a third, which is the closest match and the pattern to copy:
`document_search_wrapper.document_search_unified` builds a **`records_hint`**, appends it to
`result["text"]`, and exposes it as its own field. Its comment describes the same class of
problem this defect belongs to:

> *"a which/how-many question answered by counting passages is the confident-wrong-number
> failure … passages are a relevance sample, not a census."*

**Follow the `records_hint` shape.** It is already proven in this codebase, both consumers
already read it, and it keeps the `[Source …]` parsing contract untouched.

## The hard part: the signal must be discriminating, or it is noise

The naive implementation — *"N distinct documents matched, mention it"* — **fires on virtually
every search**, because most searches legitimately return passages from many documents. A hint
that appears every time is one the model learns to ignore, and it would make answers hedge on
questions that are perfectly answerable. That would be a net loss.

The signal has to distinguish *"several documents contributed evidence"* (normal, healthy) from
*"several documents are competing alternative answers to a question that presumed one"* (the
failure). Some candidate discriminators, roughly in order of how well they target the real case:

- **No clear winner in the re-ranker.** `rank_search_results` already scores every candidate.
  If the top documents cluster within a narrow band, there is no winner; if one dominates, there
  is no ambiguity. This is the cheapest strong signal and uses data already computed.
- **Documents sharing the entity the user named.** Six documents all called "Summit Center" are
  alternatives; six documents about different properties are just breadth. Needs the entity —
  see the sequencing note below.
- **A singular question against plural matches.** "*the* Summit Center lease" presuming one
  where several exist. Per house directive this is a natural-language judgement, so a mini-LLM
  classifier rather than a regex — but that adds a call to the hot path, which may not be worth
  it if the score-band signal is sufficient.

Whichever is chosen, **make the threshold config-driven** so it can be tuned without a deploy,
and log when it fires so its precision can be measured before anyone trusts it.

## Do item 3 first — this may shrink or disappear

Item 3 (`docs/search-entity-loss-handoff.md`) is the defect where the strategy LLM rewrites
*"Summit Center, Boston lease"* into `['lease term', 'term length', ...]` — dropping both the
property and the city before search runs.

That matters here for two reasons:

1. **Run 1's good behaviour needed no instruction.** The model produced the ideal answer on its
   own the moment it was handed three competing Boston leases. The problem was never that it
   lacks the judgement — it was that the evidence reached it only by luck. **Fix retrieval and
   the model may do this reliably without any signal at all.**
2. **The best discriminator needs the entity.** "Documents sharing the entity the user named" is
   only available if the entity survived into the pipeline, which today it does not.

So: fix item 3, re-measure with the test below, and only build this if the model still answers
from one document while alternatives sit in the result set. Building it first risks adding a
permanent instruction to compensate for a bug that is about to be fixed — and instructions added
to paper over retrieval problems are hard to remove later.

## Scope and cautions

- **This must not change retrieval, ranking, or filtering.** It is additive text on a payload.
  If a proposed change alters which documents come back, it has left this item's scope.
- **Never let it suppress an answer.** The goal is "here is the answer, and note that N similar
  documents also matched", not "I cannot answer, please clarify". A pipeline that starts
  refusing answerable questions is worse than the bug.
- **Do not append to `special_instructions` unconditionally.** That field is currently gated on
  `ENABLE_AGENT_KNOWLEDGE_MANAGEMENT`; overloading it couples two unrelated features. A separate
  field following `records_hint` is cleaner.
- **Both consumers must be checked.** Command Center reads `result["text"]`; The Agent renders
  hints explicitly. A hint added to only one is a hint that silently does nothing for the other.
- **Measure precision before trusting it.** Log every firing for a while and check what fraction
  are real ambiguity. A signal that fires on 80% of searches is noise wearing a useful label.

## How to verify

Ground truth, from the pack-23 corpus:

| store | city | initial term |
|---|---|---|
| S300 | Boston, MA | 15 years |
| S350 | Boston, MA | 15 years |
| S400 | Boston, MA | 5 years |
| S325 | Chicago, IL | 10 years |
| S375 | Chicago, IL | 7 years |
| S425 | Chicago, IL | 10 years |

**Ambiguity case** — ask a General Agent with the `document_super_search` tool:
*"what length is the term of Summit Center, Boston lease?"*
Pass: all three Boston stores covered, and the answer states they differ / asks which is meant.
Fail: one store answered as though definitive, or any Chicago store appears.

**The control case that matters more** — an unambiguous question that also touches many
documents, e.g. *"what is the base rent for store S317?"*
Pass: a direct answer with **no** ambiguity hint. If the signal fires here, it is over-firing and
is not ready, however well it does on the case above.

Run each at least three times; current behaviour is unstable, so a single pass proves nothing.
Watch the console — `2c94c27` added `[search] 6/6 RESULT: N passage(s) from M distinct
document(s)` plus the document list, which shows exactly what the model was handed.

Corpus and generator: `test_human/23_Doc_Corpus_250/`; ground truth in
`C:\temp\doc_corpus_250\ground_truth.json`.
