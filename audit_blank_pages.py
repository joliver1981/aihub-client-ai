"""
Blank-page audit for the document store
=======================================
Finds every stored page with no usable text and, where the source PDF is still
available, classifies each one as GENUINELY BLANK (no ink) vs CONTENT LOST (the
page visibly has content but nothing was extracted — the flattened/outlined-text
class that the hybrid extractor's blank-page rescue now routes to AI vision;
see DOC_HYBRID_BLANK_PAGE_RESCUE in config.py).

Read-only: no writes, no re-processing. Safe to run against any install.

Usage (run from the repo root, any env with pyodbc; PyMuPDF enables stage 2):
    python audit_blank_pages.py                  # console report
    python audit_blank_pages.py --csv report.csv # also write per-page CSV
    python audit_blank_pages.py --blank-max 20   # chars threshold for "no usable text"

Exit codes: 0 = clean or only genuinely-blank pages · 2 = CONTENT LOST pages found
(usable for monitoring). Documents whose source file no longer exists cannot be
classified and are listed for re-upload; ~95% of documents keep no re-processable
source (original_path is a transient upload location; archived_path is populated
on a small minority), so treat missing-source blanks as re-upload candidates.
"""
import argparse
import collections
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pyodbc  # noqa: E402
from CommonUtils import get_db_connection_string  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit DocumentPages for blank-stored pages")
    ap.add_argument("--blank-max", type=int, default=20,
                    help="pages with <= this many stored characters count as blank (default 20)")
    ap.add_argument("--csv", metavar="PATH", default=None,
                    help="write a per-page CSV report to PATH")
    ap.add_argument("--detail-rows", type=int, default=40,
                    help="max detail rows to print per section (default 40)")
    args = ap.parse_args()
    blank_max = args.blank_max

    conn = pyodbc.connect(get_db_connection_string(), timeout=30)
    cur = conn.cursor()
    tenant_key = os.getenv("API_KEY")
    if tenant_key:
        try:
            cur.execute("EXEC tenant.sp_setTenantContext ?", tenant_key)
        except Exception as e:
            print(f"tenant context not set: {str(e)[:100]}")

    blank_pred = (f"(dp.full_text IS NULL OR LEN(LTRIM(RTRIM(dp.full_text))) <= {int(blank_max)})")

    # ------------------------------------------------------------------ stage 1: SQL
    print("=" * 100)
    print(f"STAGE 1 — stored pages with no usable text (<= {blank_max} chars)")
    print("=" * 100)
    cur.execute(f"""
        SELECT COUNT(*),
               SUM(CASE WHEN {blank_pred} THEN 1 ELSE 0 END)
        FROM DocumentPages dp
    """)
    total_pages, blank_pages_n = cur.fetchone()
    blank_pages_n = blank_pages_n or 0
    print(f"  pages stored           {total_pages:,}")
    print(f"  blank-stored           {blank_pages_n:,}  ({blank_pages_n / max(1, total_pages) * 100:.2f}%)")

    cur.execute(f"""
        SELECT d.document_id, d.filename, d.document_type, d.is_knowledge_document,
               d.original_path, d.archived_path,
               COUNT(dp.page_id),
               SUM(CASE WHEN {blank_pred} THEN 1 ELSE 0 END)
        FROM Documents d JOIN DocumentPages dp ON dp.document_id = d.document_id
        GROUP BY d.document_id, d.filename, d.document_type, d.is_knowledge_document,
                 d.original_path, d.archived_path
        HAVING SUM(CASE WHEN {blank_pred} THEN 1 ELSE 0 END) > 0
        ORDER BY 8 DESC
    """)
    docs = cur.fetchall()
    wholly = sum(1 for r in docs if r[6] == r[7])
    print(f"  documents affected     {len(docs)}  ({wholly} wholly blank, {len(docs) - wholly} partially)")

    if docs:
        print(f"\n{'filename':<50} {'type':<20} {'pg':>4} {'blank':>6} {'source?':>18}")
        for row in docs[:args.detail_rows]:
            _, fn, dtype, _, opath, apath, pages, blank = row
            src = ("original" if opath and os.path.exists(str(opath))
                   else "archived" if apath and os.path.exists(str(apath))
                   else "MISSING (re-upload)")
            print(f"{str(fn)[:50]:<50} {str(dtype)[:20]:<20} {pages:4} {blank:6} {src:>18}")
        if len(docs) > args.detail_rows:
            print(f"   ... {len(docs) - args.detail_rows} more")

    # ------------------------------------------------------------- stage 2: classify
    print(f"\n{'=' * 100}")
    print("STAGE 2 — re-open available sources: genuinely blank, or content lost?")
    print("=" * 100)
    try:
        import fitz
        from fast_pdf_extractor import classify_page_needs_ai
        have_fitz = True
    except Exception as e:
        print(f"  PyMuPDF unavailable in this env ({e}) — skipping classification")
        have_fitz = False

    csv_rows = []
    lost = genuinely_blank = checked = 0
    missing_source_docs = 0
    lost_detail = []

    if have_fitz:
        cur.execute(f"""
            SELECT d.document_id, d.filename, d.document_type, d.is_knowledge_document,
                   d.original_path, d.archived_path, dp.page_number,
                   LEN(LTRIM(RTRIM(ISNULL(dp.full_text, ''))))
            FROM Documents d JOIN DocumentPages dp ON dp.document_id = d.document_id
            WHERE {blank_pred}
            ORDER BY d.filename, dp.page_number
        """)
        rows = cur.fetchall()

        by_doc = collections.defaultdict(list)
        for r in rows:
            by_doc[r[0]].append(r)

        for did, doc_rows in by_doc.items():
            _, fn, dtype, is_kb, opath, apath, _, _ = doc_rows[0]
            src_path = None
            for cand in (opath, apath):
                if cand and os.path.exists(str(cand)) and str(cand).lower().endswith(".pdf"):
                    src_path = str(cand)
                    break
            if not src_path:
                missing_source_docs += 1
                for r in doc_rows:
                    csv_rows.append([did, fn, dtype, is_kb, r[6], r[7], "missing",
                                     "unknown", "", "", ""])
                continue
            try:
                pdf = fitz.open(src_path)
            except Exception:
                missing_source_docs += 1
                continue
            for r in doc_rows:
                page_no = int(r[6])
                if not (1 <= page_no <= len(pdf)):
                    continue
                page = pdf[page_no - 1]
                draws = len(page.get_drawings())
                imgs = len(page.get_images(full=True))
                would_rescue = classify_page_needs_ai(page)
                checked += 1
                if draws > 50 or imgs > 0:
                    lost += 1
                    verdict = "CONTENT_LOST"
                    lost_detail.append((fn, page_no, draws, imgs, would_rescue))
                else:
                    genuinely_blank += 1
                    verdict = "genuinely_blank"
                csv_rows.append([did, fn, dtype, is_kb, page_no, r[7],
                                 "original" if src_path == str(opath) else "archived",
                                 verdict, draws, imgs, would_rescue])
            pdf.close()

        print(f"  blank pages in SQL                    {blank_pages_n}")
        print(f"  documents with no readable source     {missing_source_docs} (re-upload to remediate)")
        print(f"  pages re-checked against source       {checked}")
        print(f"    genuinely blank (no ink)            {genuinely_blank}")
        print(f"    CONTENT LOST (ink, no text stored)  {lost}")
        if lost_detail:
            print(f"\n{'filename':<52} {'pg':>4} {'draws':>6} {'imgs':>5} {'rescued by current build?':>26}")
            for fn, pno, draws, imgs, resc in lost_detail[:args.detail_rows]:
                print(f"{str(fn)[:52]:<52} {pno:4} {draws:6} {imgs:5} {str(resc):>26}")

    if args.csv and csv_rows:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["document_id", "filename", "document_type", "is_knowledge_document",
                        "page_number", "chars_stored", "source", "classification",
                        "draw_ops", "images", "would_rescue_now"])
            w.writerows(csv_rows)
        print(f"\n  per-page CSV written: {args.csv} ({len(csv_rows)} rows)")

    conn.close()

    print(f"\n{'=' * 100}")
    if lost:
        print(f"RESULT: {lost} CONTENT-LOST page(s) confirmed. Re-process those documents "
              f"(re-upload where no source exists) — the blank-page rescue now prevents new occurrences.")
        return 2
    print("RESULT: no confirmed content loss among classifiable pages. "
          "Blank pages with a missing source remain unverifiable — prefer re-upload.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
