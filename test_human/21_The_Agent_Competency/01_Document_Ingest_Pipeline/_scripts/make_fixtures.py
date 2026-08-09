"""
Fixture generator — Document Ingest Pipeline competency test.

Produces a corpus of realistic single-page VENDOR INVOICE PDFs plus the
folder layout an ongoing ingest pipeline uses:

    _fixtures/vendor_invoices/           <- the initial corpus (12 PDFs)
    _fixtures/pipeline/input/            <- watched "drop" folder (starts empty)
    _fixtures/pipeline/archive/          <- where the pipeline moves processed files
    _fixtures/pipeline/_new_arrivals/    <- staged PDFs to drop into input/ mid-test

Deterministic (seeded) so the control panel can regenerate identically.
Run:  python make_fixtures.py            (build corpus + empty pipeline dirs)
      python make_fixtures.py --arrive 3 (drop 3 fresh invoices into input/)
      python make_fixtures.py --reset     (empty input/ + archive/, keep corpus)

An ANSWER_KEY.md is written alongside the corpus so a human can verify the
agent's answers about the documents.
"""
import argparse
import os
import random
import shutil
import sys
from datetime import date, timedelta

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "_fixtures"))
CORPUS = os.path.join(ROOT, "vendor_invoices")
PIPE = os.path.join(ROOT, "pipeline")
INPUT = os.path.join(PIPE, "input")
ARCHIVE = os.path.join(PIPE, "archive")
NEW = os.path.join(PIPE, "_new_arrivals")

VENDORS = [
    ("Acme Industrial Supply", "Net 30", "Chicago, IL"),
    ("Coastal Electronics Inc", "Net 60", "San Diego, CA"),
    ("Midwest Manufacturing Co", "Net 30", "Columbus, OH"),
    ("Premier Packaging LLC", "Net 45", "Atlanta, GA"),
    ("Global Parts Distributors", "Net 60", "Dallas, TX"),
    ("Northline Logistics", "Net 15", "Newark, NJ"),
]
ITEMS = [
    ("Steel bracket, 4in", 8.50), ("Copper wiring spool", 142.00),
    ("Hydraulic seal kit", 63.75), ("Corrugated boxes (bundle)", 24.90),
    ("Circuit breaker 20A", 31.20), ("Pallet, wood 48x40", 18.00),
    ("Safety gloves (case)", 47.50), ("LED panel light", 88.40),
    ("Freight & handling", 210.00), ("Adhesive, industrial (gal)", 55.30),
]


def _invoice_number(seq):
    return f"VINV-{2026}{seq:04d}"


def render_invoice(path, seq, vendor, rng, base_date):
    name, terms, city = vendor
    inv_no = _invoice_number(seq)
    inv_date = base_date - timedelta(days=rng.randint(0, 40))
    due = inv_date + timedelta(days=int(terms.split()[1]))
    po = f"PO-{rng.randint(20000, 99999)}"
    nlines = rng.randint(2, 5)
    lines, subtotal = [], 0.0
    for _ in range(nlines):
        desc, price = rng.choice(ITEMS)
        qty = rng.randint(1, 40)
        ext = round(qty * price, 2)
        subtotal += ext
        lines.append((desc, qty, price, ext))
    tax = round(subtotal * 0.07, 2)
    total = round(subtotal + tax, 2)

    c = canvas.Canvas(path, pagesize=LETTER)
    w, h = LETTER
    y = h - 0.9 * inch
    c.setFont("Helvetica-Bold", 20); c.drawString(1 * inch, y, "VENDOR INVOICE")
    c.setFont("Helvetica", 10)
    c.drawRightString(w - 1 * inch, y, inv_no)
    y -= 0.5 * inch
    c.setFont("Helvetica-Bold", 12); c.drawString(1 * inch, y, name)
    c.setFont("Helvetica", 9)
    c.drawString(1 * inch, y - 14, city)
    c.drawRightString(w - 1 * inch, y, f"Invoice date: {inv_date:%Y-%m-%d}")
    c.drawRightString(w - 1 * inch, y - 14, f"Due date: {due:%Y-%m-%d}  ({terms})")
    c.drawRightString(w - 1 * inch, y - 28, f"Customer PO: {po}")
    y -= 0.7 * inch
    c.setFont("Helvetica-Bold", 9)
    c.drawString(1 * inch, y, "Description")
    c.drawString(4.3 * inch, y, "Qty")
    c.drawString(5.1 * inch, y, "Unit")
    c.drawRightString(w - 1 * inch, y, "Amount")
    c.line(1 * inch, y - 4, w - 1 * inch, y - 4)
    y -= 20
    c.setFont("Helvetica", 9)
    for desc, qty, price, ext in lines:
        c.drawString(1 * inch, y, desc)
        c.drawString(4.3 * inch, y, str(qty))
        c.drawString(5.1 * inch, y, f"${price:,.2f}")
        c.drawRightString(w - 1 * inch, y, f"${ext:,.2f}")
        y -= 16
    y -= 8; c.line(4.6 * inch, y, w - 1 * inch, y); y -= 16
    for label, val in (("Subtotal", subtotal), ("Tax (7%)", tax), ("TOTAL DUE", total)):
        c.setFont("Helvetica-Bold" if label == "TOTAL DUE" else "Helvetica", 10)
        c.drawRightString(w - 2.1 * inch, y, label + ":")
        c.drawRightString(w - 1 * inch, y, f"${val:,.2f}")
        y -= 16
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(1 * inch, 0.8 * inch,
                 f"Remit to {name}, {city}. Terms {terms}. Reference {inv_no} on payment.")
    c.showPage(); c.save()
    return {"file": os.path.basename(path), "invoice_no": inv_no, "vendor": name,
            "terms": terms, "po": po, "date": f"{inv_date:%Y-%m-%d}",
            "total": total}


def build_corpus():
    os.makedirs(CORPUS, exist_ok=True)
    for f in os.listdir(CORPUS):
        os.remove(os.path.join(CORPUS, f))
    rng = random.Random(20260809)          # deterministic corpus
    base = date(2026, 8, 1)
    rows = []
    for seq in range(1, 13):               # 12 invoices
        vendor = VENDORS[(seq - 1) % len(VENDORS)]
        path = os.path.join(CORPUS, f"{_invoice_number(seq)}.pdf")
        rows.append(render_invoice(path, seq, vendor, rng, base))
    # stage 3 "future arrivals" (higher seq, distinct) for the ongoing-ingest test
    os.makedirs(NEW, exist_ok=True)
    for f in os.listdir(NEW):
        os.remove(os.path.join(NEW, f))
    rng2 = random.Random(77777)
    for seq in range(90, 93):
        vendor = VENDORS[seq % len(VENDORS)]
        path = os.path.join(NEW, f"{_invoice_number(seq)}.pdf")
        render_invoice(path, seq, vendor, rng2, date(2026, 8, 9))
    _write_answer_key(rows)
    for d in (INPUT, ARCHIVE):
        os.makedirs(d, exist_ok=True)
    print(f"corpus: {len(rows)} invoices in {CORPUS}")
    print(f"staged arrivals: {len(os.listdir(NEW))} in {NEW}")
    print(f"pipeline dirs ready: {INPUT} , {ARCHIVE}")


def _write_answer_key(rows):
    tot = sum(r["total"] for r in rows)
    by_vendor = {}
    for r in rows:
        by_vendor.setdefault(r["vendor"], []).append(r)
    lines = ["# Answer key — vendor invoice corpus", "",
             f"12 invoices, combined total **${tot:,.2f}**.", "",
             "| Invoice | Vendor | Terms | PO | Date | Total |",
             "|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: x["invoice_no"]):
        lines.append(f"| {r['invoice_no']} | {r['vendor']} | {r['terms']} | "
                     f"{r['po']} | {r['date']} | ${r['total']:,.2f} |")
    lines += ["", "## Handy verification questions",
              f"- Total of all invoices: **${tot:,.2f}**"]
    for v, rs in sorted(by_vendor.items()):
        s = sum(x["total"] for x in rs)
        lines.append(f"- {v}: {len(rs)} invoice(s), **${s:,.2f}**")
    net60 = [r["invoice_no"] for r in rows if r["terms"] == "Net 60"]
    lines.append(f"- Net-60 invoices: {', '.join(sorted(net60)) or 'none'}")
    biggest = max(rows, key=lambda r: r["total"])
    lines.append(f"- Largest single invoice: {biggest['invoice_no']} "
                 f"({biggest['vendor']}, ${biggest['total']:,.2f})")
    with open(os.path.join(ROOT, "01_ANSWER_KEY.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("answer key -> _fixtures/01_ANSWER_KEY.md")


def arrive(n):
    os.makedirs(INPUT, exist_ok=True)
    staged = sorted(os.listdir(NEW)) if os.path.isdir(NEW) else []
    moved = 0
    for fn in staged[:n]:
        shutil.copy(os.path.join(NEW, fn), os.path.join(INPUT, fn))
        moved += 1
    print(f"dropped {moved} new invoice(s) into {INPUT}")


def reset():
    for d in (INPUT, ARCHIVE):
        if os.path.isdir(d):
            for f in os.listdir(d):
                p = os.path.join(d, f)
                if os.path.isfile(p):
                    os.remove(p)
    os.makedirs(INPUT, exist_ok=True); os.makedirs(ARCHIVE, exist_ok=True)
    print("pipeline input/ and archive/ emptied (corpus kept)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--arrive", type=int, metavar="N",
                    help="drop N staged invoices into the input/ folder")
    ap.add_argument("--reset", action="store_true",
                    help="empty input/ and archive/ (keep the corpus)")
    a = ap.parse_args()
    if a.arrive:
        arrive(a.arrive)
    elif a.reset:
        reset()
    else:
        build_corpus()
