# Handoff: DOCX ingest stores every Word document as a single page

**Status:** FIXED 2026-08-30 — `_docx_extract_pages` paginates on real break signals
(`w:br type="page"`, `w:pageBreakBefore`, section starts via the FOLLOWING sectPr's
`w:type`, `w:lastRenderedPageBreak` — including inside table rows) with synthetic
block-packing fallback (`DOC_DOCX_SYNTHETIC_PAGE_CHARS`, default 2800, pages flagged
`synthetic_pagination`) for documents carrying no signal. Live-verified: probe below
reports DOCX 5 rows = PDF control; real Word-authored docs (13 found on this box)
paginate to their rendered page counts; extraction breadth (tables, headers/footers,
text boxes) covered by 41 unit checks. Existing DOCX rows in `DocumentPages` remain
single-page until re-ingested — that decision is still open. Original write-up kept
below for the record.
**Component:** `LLMDocumentEngine._process_word_document`
**Severity:** medium-high — silent. Nothing errors; text is complete; only pagination is lost,
so every downstream page-level behaviour degrades without a signal.

---

## The defect

`LLMDocumentEngine.py:4271` `_process_word_document()` concatenates the whole document into one
string and returns exactly one page, regardless of length:

```python
# LLMDocumentEngine.py:4322-4328
# Handle as a single page document
pages.append({
    "page_number": 1,
    "text": full_text.strip()
})

return pages
```

Explicit `<w:br w:type="page"/>` breaks present in the file are never consulted.

Two call sites, both in the same file:
- `LLMDocumentEngine.py:994` — the main `process_document` dispatch (the knowledge/ingest path)
- `LLMDocumentEngine.py:1478` — `_get_text_from_other_documents`

## Evidence

A 5-page DOCX with 4 explicit page breaks and a 5-page PDF with **identical content**, both sent
through the live `POST /document/process` on 127.0.0.1:5011 (probe script at the bottom):

| | `Documents.page_count` | `DocumentPages` rows | chars per row |
|---|---|---|---|
| **DOCX** | **1** | **1** | `[1891]` |
| PDF (control) | 5 | 5 | `[385, 385, 389, 387, 387]` |

Each page carried a unique token (`PAGEMARKER_ONE` … `PAGEMARKER_FIVE`). In the PDF each token
landed in its own row. In the DOCX all five landed in the single row.

Reproduced independently on a real 7-page fixture during the pack-23 corpus load:
`SKY-LEASE-S303-BelmontRow.docx`, 6 explicit page breaks, stored as **1 row / 11,629 chars**,
while its 7-page PDF siblings stored 7 rows each at comparable total length.

## This was not fixed recently

Checked because it was thought to have been resolved a couple of weeks ago, possibly dropped
when the document-records work took over:

- `git log -L 4265,4340:LLMDocumentEngine.py` → last touched by `de2c2c1` (*"Many many changes
  related to 1.7.1"*) and the initial commit. **Nothing in the last 60 days.**
- The recent document-records cluster (`0dc9ab1`, `59e1081`, `cc6ee6a`, `6d06392`, `ab291ef`,
  `a7d3db9`, `7a97509`, `f2e96af`) touched extraction shapes, schemas and page *references* —
  `ab291ef` normalises page refs to bare numbers — but none changed Word page extraction.
- No config toggle exists. `grep "DOCX\|WORD_\|word_page" config.py` returns only
  `DOC_MAX_UPLOAD_SIZE_MB`.

The likely source of the "we fixed this" memory is `c13f798` (*"The Agent chat-attachment lane:
… Word battery false positive"*). That touched `attachment_text_extractor.extract_docx_text`,
which is a **different function on a different path** — and it legitimately returns a flat
string, because the attachment lane is whole-document by design. The knowledge-ingest path was
never changed.

## Why it matters

1. **Page-level retrieval cannot locate a fact inside a Word document.** Vector chunks resolve
   to a page; for DOCX there is only ever page 1, so a citation points at the whole file.
2. **Context bloat.** With `DOC_INCLUDE_FULL_PAGE_IN_CHUNK_RESULTS=true` (set in this tree), a
   page hit injects the full page. For DOCX that is the entire document, on every hit.
3. **Routing is miscounted.** `KNOWLEDGE_BRUTE_FORCE_PAGE_THRESHOLD=999` (`dist\.env:53`) gates
   on page count. A DOCX-heavy knowledge base undercounts by roughly its true page count, so it
   stays on the brute-force branch far past the intended size and then blows the companion
   `KNOWLEDGE_BRUTE_FORCE_CHAR_BUDGET=400000` instead — the two gates disagree about the same
   corpus.
4. **Page-reference features are unreliable for DOCX.** Anything built on stored page numbers —
   including the records work's page refs — can only ever say "page 1" for a Word source.

Measured on the pack-23 corpus: 31 DOCX documents worth 139 real pages store as 31 rows. The
corpus stores 1,045 pages instead of 1,153.

## The part that makes this more than a one-liner

**Do not just split on `<w:br w:type="page"/>` and call it done.** That fixes generated
documents and does almost nothing for real client files.

Explicit page breaks are the minority case. In a normal Word document, pagination is computed by
the renderer from flow — most `.docx` files contain no explicit break at all. A fix validated
only against generated fixtures (including the pack-23 corpus, which *does* use explicit breaks)
will pass its tests and change nothing for a client.

Three viable strategies, roughly in order of cost:

| approach | accuracy | notes |
|---|---|---|
| `w:lastRenderedPageBreak` | good on Word-saved files | Word writes these on save, reflecting its last layout. **Absent** in python-docx-generated files, so combine with `w:br type="page"`. Zero new dependencies. |
| Convert → PDF, reuse `_process_pdf` | highest | LibreOffice headless or Word COM. Accurate, but adds a heavyweight dependency and real latency; a converter is not currently in the stack. |
| Synthetic pagination | approximate | Chunk to ~2,500–3,000 chars on paragraph boundaries into pseudo-pages. Restores retrieval granularity and honest page counts; page numbers will not match what a human sees in Word. |

A reasonable landing point: honour `w:br type="page"` **and** `w:lastRenderedPageBreak` when
either is present, and fall back to synthetic pagination when neither is — never emitting a
single page for a document over some threshold. If pages are synthetic, mark them so
(a flag on the page dict, or a distinguishable `page_number` scheme) rather than letting
downstream code believe they are true Word pages.

## The pattern to follow is in the same file

`_process_excel` already does this correctly — it maintains a running `page_number` and appends
one page per sheet (`LLMDocumentEngine.py`, search `page_number += 1`). DOCX is the outlier, not
the house style. Match that shape: build a list, increment, never hardcode.

## Scope and cautions

- **Keep the existing extraction breadth.** The current function deliberately reaches
  paragraphs, tables, headers/footers, and `w:txbxContent` text boxes — the comment at
  `LLMDocumentEngine.py:4311` records that text boxes were the reason "modern" Word docs
  extracted empty. Any rewrite must keep pulling all four, and must keep them in a sensible
  reading order. Losing text-box content while fixing pagination would be a straight
  regression.
- `.doc` (legacy) falls through to `_process_generic_file` / Claude Vision and is out of scope.
- `Documents.page_count` is written from the returned page list, so it corrects itself once
  pagination is right — do not patch it separately.
- Existing DOCX documents already in `DocumentPages` will keep their single row. Decide whether
  a re-ingest is needed; `/document/reprocess-vectors` alone will not fix them, because the
  page rows themselves are wrong.
- Check whether `_get_text_from_other_documents` (`LLMDocumentEngine.py:1478`) wants paginated
  output at all — it may be flattening the result anyway, in which case leave its behaviour
  alone and only change what the ingest path consumes.

## How to verify

Drop this in a scratch directory and run it under the `aihub2.1` conda env with the doc API up
on 127.0.0.1:5011. It builds a 5-page DOCX and an identical 5-page PDF control, ingests both,
prints the row counts, and cleans up after itself. **Before a fix, DOCX reports 1 row; after a
fix it should report 5, matching the control.**

```python
"""DOCX pagination probe — builds a 5-page DOCX + identical PDF control, ingests both,
reports DocumentPages rows, cleans up. Run under aihub2.1 with the doc API on :5011."""
import os, sys, time
REPO = r"C:\src\aihub-client-ai-dev"
sys.path.insert(0, REPO)
import requests, pyodbc
from docx import Document
from docx.enum.text import WD_BREAK
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas as rl_canvas
from CommonUtils import get_db_connection_string

KEY = "DB27D555-03A8-446E-9C23-8DAAA95EAD21"     # local dev tenant key
os.environ.setdefault("API_KEY", KEY)
HERE = os.path.dirname(os.path.abspath(__file__))
WORDS = ["ONE", "TWO", "THREE", "FOUR", "FIVE"]
PAGES = [f"PROBE DOCUMENT - PAGE {i+1}\nPAGEMARKER_{w}\nThis is page {i+1} of five. "
         f"The unique token for this page is PAGEMARKER_{w}. Filler so the page is not "
         f"trivially short: the quick brown fox jumps over the lazy dog, repeatedly."
         for i, w in enumerate(WORDS)]

def make_docx(path):
    doc = Document()
    for i, text in enumerate(PAGES):
        for line in text.split("\n"):
            doc.add_paragraph(line)
        if i < len(PAGES) - 1:
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    doc.save(path)

def make_pdf(path):
    c = rl_canvas.Canvas(path, pagesize=letter)
    for text in PAGES:
        y = 720
        for line in text.split("\n"):
            for k in range(0, max(len(line), 1), 90):
                c.drawString(72, y, line[k:k+90]); y -= 14
        c.showPage()
    c.save()

def process(path):
    r = requests.post("http://127.0.0.1:5011/document/process",
                      headers={"X-API-Key": KEY, "Authorization": f"Bearer {KEY}"},
                      data={"filePath": path, "is_knowledge_document": "true",
                            "extract_fields": "false", "detect_document_type": "false"},
                      timeout=300)
    r.raise_for_status(); return r.json()

d = os.path.join(HERE, "PROBE_DOCX_PAGINATION.docx")
p = os.path.join(HERE, "PROBE_PDF_CONTROL.pdf")
make_docx(d); make_pdf(p)
conn = pyodbc.connect(get_db_connection_string(), timeout=30); cur = conn.cursor()
cur.execute("EXEC tenant.sp_setTenantContext ?", KEY)
for label, path in [("DOCX", d), ("PDF (control)", p)]:
    process(path)
    fn = os.path.basename(path)
    cur.execute("SELECT TOP 1 document_id, page_count FROM Documents WHERE filename = ? "
                "ORDER BY processed_at DESC", [fn])
    doc_id, pc = cur.fetchone()
    cur.execute("SELECT page_number, LEN(full_text) FROM DocumentPages "
                "WHERE document_id = ? ORDER BY page_number", [doc_id])
    rows = cur.fetchall()
    print(f"{label:<15} page_count={pc}  rows={len(rows)}  sizes={[r[1] for r in rows]}")
for path in (d, p):                                    # cleanup
    fn = os.path.basename(path)
    cur.execute("SELECT document_id FROM Documents WHERE filename = ?", [fn])
    for (i,) in cur.fetchall():
        cur.execute("DELETE FROM AgentKnowledge WHERE document_id = ?", [i])
        cur.execute("DELETE FROM DocumentPages WHERE document_id = ?", [i])
        cur.execute("DELETE FROM Documents WHERE document_id = ?", [i])
    conn.commit()
conn.close()
print("cleaned up")
```

**Also test, beyond the probe:**

1. A **real Word-authored** `.docx` with no explicit page breaks (save one from Word). This is
   the case the naive fix misses — it is the important test, not the generated one.
2. A DOCX containing tables, a header/footer, and a text box, to confirm the extraction breadth
   at `LLMDocumentEngine.py:4286-4320` survived.
3. A single-page DOCX, which must still produce exactly one page.
4. `test_human/23_Doc_Corpus_250/` — regenerate the corpus (`python gen_corpus.py --prune`),
   load it, and check `load_corpus.py --status`. It compares stored pages against ground truth
   and warns when they diverge; after a fix, stored pages should approach the 1,153 that
   `ground_truth.json` describes rather than the current 1,045.
