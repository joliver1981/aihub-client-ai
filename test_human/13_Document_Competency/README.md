# Pack 13 — Document Processing & Retrieval Competency

**What this is:** a human-grade competency suite for the document engine — upload real
documents of every class the system must handle, then ask the questions a real portfolio
owner asks, and grade against known ground truth. It regression-locks the 2026-07-25
document-engine fixes and doubles as the **legacy-engine baseline (Phase P1)** for the
planned doc-search re-core (`docs/document-search-recore-analysis.md`).

**Single source of truth:** [battery.py](battery.py) — fixtures, questions, expected
answers, grading rules. `_ANSWER_KEY.md` and run reports derive from it; edit battery.py,
never the derived files.

## What it exercises (and which fix each part locks)

| Phase | What | Locks |
|---|---|---|
| **A — Ingestion integrity** | Upload digital leases, flattened/vector-outlined amendments (0 images, 0 text layer), a merged digital+flattened PDF, and a 79-page lease; assert **zero blank-stored pages** and key phrases present in `DocumentPages` (SQL oracle) | Blank-page rescue (`DOC_HYBRID_BLANK_PAGE_RESCUE`); no-cost-added on digital PDFs |
| **B — Repository retrieval** | Human-phrased needle, meaning/synonym, amendment-supersede, and honesty questions through an agent with document tools | Semantic `search_documents_meaning`; reranker; super-search; "not stated" honesty |
| **C — Agent knowledge retrieval** | NEEDLE facts, a store-by-store FANOUT portfolio question graded per-document, amendment supersede, and a **delete-then-ask** probe | NEEDLE/FANOUT paths (dev threshold=5 forces retrieval, the path production barely exercises); `KNOWLEDGE_FILTER_INACTIVE_VECTORS` deleted-doc gate |

The corpus is the real 20-lease set from `C:\temp\leases` (7 amendments are genuinely
text-invisible flattened PDFs — the class that silently stored empty before the fix),
copied into `fixtures/` with a `DCT13_` prefix by `make_fixtures.py`.

## Running it

```
# 1. fixtures (idempotent)
python make_fixtures.py

# 2. full automated run against the LIVE app (main app + vector API + doc queue must be up)
python run_battery.py                 # upload -> wait -> question -> grade -> report
python run_battery.py --skip-upload   # re-grade questions against already-uploaded fixtures
python run_battery.py --teardown      # delete DCT13_* repository docs + the test agent
```

Interpreter: the main-app env `C:\Users\james\miniconda3\envs\aihub2.1\python.exe`.
The runner writes `TEST_RUN_<date>.md` next to this file. Uploads are prefixed `DCT13_`
so re-runs and teardown are safe and identifiable.

## Grading rules

- Deterministic first: dates, amounts, names, and class keywords decide PASS/FAIL.
- `honesty` items PASS only when the answer states the absence AND invents nothing.
- The FANOUT portfolio item (C2) reports **completeness** (stores mentioned) and
  **correctness** (right landlord/tenant/split class) separately — its score is a
  tracked number, not just a gate, because phrasing-dependent completeness is the
  measured weakness of the legacy engine.
- Anything a rule can't settle lands in **NEEDS_REVIEW**, never a silent pass.

## Human execution

Every Phase B/C question is a plain-English prompt — ask them verbatim in the agent chat
UI and compare with [\_ANSWER_KEY.md](_ANSWER_KEY.md). Phase A is verified with
`audit_blank_pages.py` (repo root) or the SQL in the runner.
