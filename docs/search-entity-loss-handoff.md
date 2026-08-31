# Handoff: the search strategy discards the entities in the user's question

**Status:** confirmed live 2026-08-31 on the pack-23 corpus. Not fixed.
**Component:** `DocUtils.document_search_super_enhanced_debug` — Step 3, the search-strategy call
**Severity:** high, and **silent**. The engine returns a confident, well-formatted answer with a
correct citation — about the wrong document. Nothing in the output signals it.
**Production relevance:** this is the lane General Agents actually use (`document_super_search`
tool). It is not the v3 enumerate lane.

---

## In plain English

A user asked:

> *"what length is the term of Summit Center, Boston lease?"*

Before searching, the engine asks an LLM to turn that question into search terms. It produced:

```
['lease term', 'term length', 'initial term', 'commencement date expiration date']
```

**"Summit Center" is gone. "Boston" is gone.** Every term is a generic phrase about lease
duration. The vector engine was then asked, in effect, *"find me anything about lease terms"* —
across all 185 lease documents.

The user's question had two constraints and a topic. The rewriter kept the topic and threw away
both constraints.

## The evidence

Live console trace from the exact question (the trace was added in `2c94c27`):

```
[search] ===== what length is the term of Summit Center, Boston lease?
[search] 1/6 selecting document types...
[search]     document types -> ['lease_agreement']
[search] 2/6 loading document universe...
[search]     universe: 500 distinct fields
[search]     field-selection confidence=high type=lookup
[search]     AI picked fields -> ['property_name', 'shopping_center_name', 'city', 'name',
                                  'tenant', ..., 'term_length_years', 'term_years', ...]
[search] 3/6 determining search strategy...
[search] 4/6 semantic search · 4 term(s): ['lease term', 'term length', 'initial term',
                                           'commencement date expiration date']
[search]     vector -> 999 chunks for 'lease term'
[search]     vector -> 836 chunks for 'term length'
[search]     vector -> 682 chunks for 'initial term'
[search]     vector -> 998 chunks for 'commencement date expiration date'
[search]     semantic total 3515 -> deduped 1568
```

**3,515 chunks retrieved, deduped to 1,568 pages, none of them selected for being the right
property or the right city.** Downstream, 30 of those 1,568 are handed to the re-ranker. Which
30 is decided by cosine ties between documents that are near-identical prose.

**The most damning detail:** the field-selection step one stage earlier *did* understand the
constraint. Its own recorded reasoning:

> *"The property_name, shopping_center_name, and name fields help identify the relevant lease…
> while **city and state help confirm that the property is the Boston location**."*

One stage understood "Boston" and selected the fields to enforce it. The next stage dropped it.

### What the user actually saw

The corpus has six Summit Center leases — three in Boston (S300, S350, S400) and three in
Chicago (S325, S375, S425), all near-identical prose.

- **Run 1:** Boston chunks happened to survive. Excellent answer — a table of all three Boston
  leases plus *"if you mean a particular store, provide its identifier."*
- **Run 2, same question:** the answer was about **S325, Summit Center, Chicago**. Term, dates,
  renewal options and citation were all **factually correct** — for a store in the wrong city.
  Boston was never mentioned.

Same question, same corpus, minutes apart. Both LLM calls in this path already run at
`temperature=0.0` (`AppUtils.py:445`, `DocUtils.py:384`), so this is not sampling noise — it is
near-tied vectors being resolved arbitrarily because nothing in the query distinguishes them.

## Where it happens

The strategy prompt (`DocUtils.py` ~4003–4010) specifies search terms in a single line:

```
"semantic_search": {
    "search_terms": ["term1", "term2"]  // Key terms for semantic search
}
```

That is the entire instruction. Nothing asks the model to preserve proper nouns, and **"key
terms" actively invites abstraction to the topic** rather than the subject. The model is doing
what it was asked.

The terms are consumed at `DocUtils.py` ~4116:

```python
semantic_terms = search_strategy.get("semantic_search", {}).get("search_terms", [])
if not semantic_terms:
    semantic_terms = [user_question]
for term in semantic_terms:
    search_result = vector_client.search_for_ai(term, filters={"document_type": {"$in": relevant_doc_types}})
```

**The only filter passed to the vector engine is `document_type`.** No city, no property, no
store. Note the fallback on the second line: when the model returns *no* terms, the raw question
is used — and would have worked. The failure mode is the model returning terms that are valid
but stripped.

## Why the fixes already committed do not cover this

`2c94c27` made the re-ranker window diverse (`DOC_RERANK_MAX_PER_DOC`, default 3), so the window
now spans 6 documents instead of 3 in this exact scenario. That is a real improvement to the
tie-break, and it makes the Boston leases *reachable*.

But it cannot fix a query that never mentioned the property. The re-ranker is choosing among
1,568 equally-generic "lease term" pages. Widening which 30 it sees improves the odds; it does
not restore the constraint. **This defect is upstream and needs its own fix.**

## Fix options

### Option A — instruct the rewriter to preserve entities *(smallest)*
Change the `search_terms` spec so at least one term must carry the proper nouns from the
question, e.g. *"At least one search term MUST include the specific names, places, or
identifiers the user mentioned verbatim. Do not generalise 'Summit Center, Boston' to 'lease'."*

*Cheapest change, no new call, no new failure path. But it is a prompt instruction — compliance
is probabilistic, and this is exactly the sort of instruction models drop on longer prompts.
Necessary, almost certainly not sufficient on its own.*

### Option B — assert entities survived, and repair if not *(recommended, pairs with A)*
After the strategy returns, check whether the proper nouns / capitalised multiword spans /
quoted strings in the user's question appear in at least one search term. If not, append the raw
user question as an additional search term.

*Deterministic, cheap, and fails safe: the worst case is one extra vector query using the
question as written, which is the behaviour the existing empty-terms fallback already relies on.
It does not depend on the model complying.*

**The natural home is `_normalize_search_strategy` (`DocUtils.py:3794`)** — it already exists to
coerce this exact structure, every branch flows through it, and it already has the
`search_attempts` list for recording what it changed. Adding the check there means one
chokepoint rather than a guard at each consumer.

### Option C — turn entities into real filters
The field-selection step already nominates `city`, `property_name`, `shopping_center_name`. Pass
them to the vector search as metadata filters alongside `document_type`.

*Strongest guarantee and the closest to what a person does. But significantly riskier: it
depends on those fields being reliably extracted and consistently named per document, and the
corpus shows 16,403 distinct field paths across 254 documents, so field naming is not
consistent. A filter on a field a document does not have will exclude it entirely — turning a
ranking problem into a zero-results problem. If attempted, it must fail open: no matches under
the filter should fall back to the unfiltered search, not return nothing.*

**Recommended sequence: A + B first** (small, safe, testable), measure, and only reach for C if
the entity-in-terms approach proves insufficient.

## Scope and cautions

- **Do not remove the rewriter.** Multiple terms genuinely help recall on vague questions. The
  goal is to keep the topic expansion *and* retain the constraints, not to search the raw string
  only.
- **Fail open, always.** Every guard added here must degrade to today's behaviour. This function
  is the production lookup path for every General Agent; a change that can return zero results
  is worse than the bug.
- **Entity detection needs care, not a regex over capitals.** "Boston" and "Summit Center" are
  easy; "the Q3 lease" and "our biggest store" are not. A conservative detector that only fires
  on clear proper nouns is better than an aggressive one that mangles ordinary questions. Per
  house directive, prefer a mini-LLM call over regex for interpreting natural language — regex
  is acceptable only as a fail-closed format check.
- **Check the `field_search` half too.** The observed run chose `search_approach: "hybrid"`, so
  a `field_filters` block was also produced. Whether it contained a city filter, and whether it
  matched anything, is unverified — the new trace prints `[search] 4b/6 field search · N
  filter(s)` when that branch runs. Worth confirming before assuming the semantic half is the
  whole story.
- **Related but separate:** `ai_select_relevant_fields` (`DocUtils.py:3412`) sorts candidate
  fields by `usage_count`, a key absent from the dicts it is given (they carry `document_count`,
  built at `DocUtils.py:3958`). The sort is a silent no-op. It is currently **inert** because
  the universe already caps at 500 and `DOC_TOP_N_FIELDS_INCLUDED_IN_RESULTS` is also 500 — but
  it will bite the moment those numbers diverge. Fix it while you are in the file; do not
  conflate it with this defect.

## How to verify

The corpus is built for exactly this. Six Summit Center leases, three per city, near-identical
prose — the discriminator case that a real lease portfolio has and a monoculture test set does
not.

**Ground truth:**

| store | city | initial term |
|---|---|---|
| S300 | Boston, MA | 15 years |
| S350 | Boston, MA | 15 years |
| S400 | Boston, MA | 5 years |
| S325 | Chicago, IL | 10 years |
| S375 | Chicago, IL | 7 years |
| S425 | Chicago, IL | 10 years |

**The test:** ask *"what length is the term of Summit Center, Boston lease?"* through a General
Agent with the `document_super_search` tool.

- **Pass:** the answer covers S300, S350 and S400 — and only those. Bonus if it notes the three
  differ and asks which store is meant.
- **Fail:** any Chicago store appears, or fewer than three Boston stores are found.
- **Watch the console** for `[search] 4/6 semantic search · N term(s): [...]`. If "Summit
  Center" or "Boston" is missing from every term, the fix has not taken, regardless of what the
  answer happens to say — a right answer with stripped terms is luck, not a fix.

**Run it at least three times.** The current behaviour is unstable; a single pass proves nothing
in either direction.

Corpus and generator: `test_human/23_Doc_Corpus_250/` (regenerate with
`python gen_corpus.py --prune`); ground truth in `C:\temp\doc_corpus_250\ground_truth.json`.
