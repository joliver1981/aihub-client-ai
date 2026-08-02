"""
make_fixtures.py -- build the beat-2 cash-application fixtures.

    python make_fixtures.py                 # write to ../fixtures/
    python make_fixtures.py --to-sftp       # also copy into the SFTP server's incoming/

Produces the daily bank remittance batch a cash-application run has to work:

  remittance_<anchor>.csv    the bank's payment file  (13 payments, $110,207.40)
  remittance_advice.pdf      the customer remittance advice, as a document to extract
  _REMITTANCE_KEY.md         ground truth: what must auto-apply and what must not

Shaped so a naive matcher fails visibly: 8 clean single-invoice payments, 1 lump sum
covering two invoices, and 4 that must NOT be auto-applied (two short-pays, one
overpayment, one payment from a company that isn't a customer).

The invoice ids and amounts are read from ar_book.py, so the fixture can never drift
away from the seeded book.

Interpreter: C:\\Users\\james\\miniconda3\\envs\\aihub2.1\\python.exe (has reportlab 4.5.1).
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import shutil
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ar_book as B  # noqa: E402

D = Decimal
PACK = Path(__file__).resolve().parents[1]
FIXTURES = PACK / "fixtures"
MANIFEST = PACK / "SEED_MANIFEST.json"
SFTP_INCOMING = Path(r"C:\src\aihub-client-ai-dev\test_human\_sftp_test_server"
                     r"\runtime\server_root\incoming")

BANK = "First Commerce Bank"
LOCKBOX = "LBX-44120"


def invoice(inv_id: str):
    for i in B.OPEN_INVOICES:
        if i.invoice_id == inv_id:
            return i
    raise KeyError(inv_id)


def total_of(inv_id: str) -> Decimal:
    return B.invoice_total(invoice(inv_id))


def cust(inv_id: str):
    return B.CUSTOMERS_BY_ID[invoice(inv_id).customer_id]


# Each row: (kind, [invoice_ids], amount_override, memo)
#   clean      -> exact single-invoice match on the reference
#   lump       -> one payment covering several invoices
#   short      -> underpaid, must go to exceptions
#   over       -> overpaid, must go to exceptions
#   unknown    -> no matching customer or invoice at all
def build_batch(anchor: _dt.date):
    rows = []
    seq = 1

    def add(kind, inv_ids, amount, memo, customer_name=None, customer_id=None):
        nonlocal seq
        rows.append({
            "kind": kind,
            "payment_ref": f"FCB-{anchor:%Y%m%d}-{seq:03d}",
            "invoice_ids": inv_ids,
            "amount": amount,
            "memo": memo,
            "customer_name": customer_name or (cust(inv_ids[0]).name if inv_ids else ""),
            "customer_id": customer_id or (invoice(inv_ids[0]).customer_id if inv_ids else ""),
        })
        seq += 1

    # 8 clean matches
    for inv_id in ("CG-INV-10003", "CG-INV-10011", "CG-INV-10031", "CG-INV-10050",
                   "CG-INV-10010", "CG-INV-10020", "CG-INV-10035", "CG-INV-10040"):
        add("clean", [inv_id], total_of(inv_id), f"Payment {inv_id}")

    # 1 lump sum across two invoices -- reference lists both
    lump = ["CG-INV-10001", "CG-INV-10002"]
    add("lump", lump, sum((total_of(i) for i in lump), D("0")),
        "Payment " + " ".join(lump))

    # 2 short-pays -- distinct from the three already sitting in the book (beat 3)
    add("short", ["CG-INV-10015"], total_of("CG-INV-10015") - D("600.00"),
        "Payment CG-INV-10015")                       # unexplained $600.00 short
    add("short", ["CG-INV-10045"], total_of("CG-INV-10045") - D("284.00"),
        "Payment CG-INV-10045 less discount")         # 2% taken with no discount terms

    # 1 payment from a company that is not a customer
    add("unknown", [], D("3300.00"), "Remittance - see attached",
        customer_name="NORTHSTAR SUPPLY LLC", customer_id="")

    # 1 overpayment
    add("over", ["CG-INV-10055"], total_of("CG-INV-10055") + D("120.00"),
        "Payment CG-INV-10055")

    return rows


def write_csv(rows, anchor: _dt.date) -> Path:
    # The filename carries the deposit date, so re-seeding on a new day would otherwise
    # leave yesterday's batch behind -- and the automation picks up "the newest
    # remittance_*.csv", which makes a stale file a genuine false result, not just clutter.
    for old in FIXTURES.glob("remittance_*.csv"):
        old.unlink()
    path = FIXTURES / f"remittance_{anchor:%Y%m%d}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["payment_ref", "payment_date", "bank", "lockbox", "payer_name",
                    "payer_id", "method", "amount", "currency", "invoice_reference", "memo"])
        for r in rows:
            w.writerow([r["payment_ref"], anchor.isoformat(), BANK, LOCKBOX,
                        r["customer_name"], r["customer_id"], "ACH",
                        f"{r['amount']:.2f}", "USD",
                        " ".join(r["invoice_ids"]), r["memo"]])
    return path


def write_pdf(rows, anchor: _dt.date) -> Path | None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                        TableStyle)
    except ImportError:
        print("  reportlab not available -- skipping the PDF advice")
        return None

    path = FIXTURES / "remittance_advice.pdf"
    doc = SimpleDocTemplate(str(path), pagesize=letter,
                            topMargin=0.7 * inch, bottomMargin=0.7 * inch)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"<b>{BANK}</b> — Lockbox Remittance Advice", styles["Title"]),
        Paragraph(f"Deposit date: {anchor:%d %B %Y} &nbsp;·&nbsp; Lockbox {LOCKBOX} "
                  f"&nbsp;·&nbsp; Beneficiary: Continental Goods Co.", styles["Normal"]),
        Spacer(1, 0.25 * inch),
    ]

    data = [["Payment ref", "Payer", "Invoice reference", "Amount"]]
    for r in rows:
        data.append([r["payment_ref"], r["customer_name"],
                     " ".join(r["invoice_ids"]) or "(none provided)",
                     f"${r['amount']:,.2f}"])
    total = sum((r["amount"] for r in rows), D("0"))
    data.append(["", "", "TOTAL DEPOSITED", f"${total:,.2f}"])

    t = Table(data, colWidths=[1.5 * inch, 2.0 * inch, 2.3 * inch, 1.1 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3864")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -2), 0.4, colors.grey),
        ("LINEABOVE", (0, -1), (-1, -1), 1.0, colors.black),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#f2f2f2")]),
    ]))
    story += [t, Spacer(1, 0.25 * inch),
              Paragraph("<i>Deductions and discounts taken by the payer are reflected in the "
                        "amount remitted. Contact your customer directly for deduction "
                        "detail.</i>", styles["Normal"])]
    doc.build(story)
    return path


def write_key(rows, anchor: _dt.date) -> Path:
    auto = [r for r in rows if r["kind"] in ("clean", "lump")]
    exc = [r for r in rows if r["kind"] in ("short", "over", "unknown")]
    applications = sum(len(r["invoice_ids"]) for r in auto)
    total = sum((r["amount"] for r in rows), D("0"))

    reasons = {
        "short": "underpaid -- variance must be researched, never auto-applied",
        "over": "overpaid -- the excess has nowhere to go without a decision",
        "unknown": "payer is not a customer and no invoice was referenced",
    }

    L = [
        "# Beat 2 — remittance batch ground truth",
        "",
        f"**Generated:** {_dt.datetime.now():%Y-%m-%d %H:%M} · **Deposit date:** {anchor}",
        "",
        f"**{len(rows)} payments · ${total:,.2f} deposited.**",
        f"**{len(auto)} must auto-apply** (creating **{applications}** payment applications) · "
        f"**{len(exc)} must go to the exception queue.**",
        "",
        "> Generated by `_scripts/make_fixtures.py` from `ar_book.py` — do not hand-edit.",
        "",
        "## Must auto-apply",
        "",
        "| Payment | Payer | Invoice(s) | Amount | Why it's clean |",
        "|---|---|---|---:|---|",
    ]
    for r in auto:
        why = ("exact match on the referenced invoice" if r["kind"] == "clean"
               else f"lump sum — reference names {len(r['invoice_ids'])} invoices and the "
                    "amount equals their sum exactly")
        L.append(f"| `{r['payment_ref']}` | {r['customer_name']} | "
                 f"{', '.join(r['invoice_ids'])} | ${r['amount']:,.2f} | {why} |")

    L += ["", "## Must NOT auto-apply", "",
          "| Payment | Payer | Invoice | Remitted | Expected | Variance | Why |",
          "|---|---|---|---:|---:|---:|---|"]
    for r in exc:
        if r["invoice_ids"]:
            expected = total_of(r["invoice_ids"][0])
            var = r["amount"] - expected
            L.append(f"| `{r['payment_ref']}` | {r['customer_name']} | "
                     f"{r['invoice_ids'][0]} | ${r['amount']:,.2f} | ${expected:,.2f} | "
                     f"**{var:+,.2f}** | {reasons[r['kind']]} |")
        else:
            L.append(f"| `{r['payment_ref']}` | {r['customer_name']} | — | "
                     f"${r['amount']:,.2f} | — | — | {reasons[r['kind']]} |")

    L += [
        "",
        "## Grading",
        "",
        f"- ✅ exactly **{len(auto)}** payments applied and **{len(exc)}** kicked to exceptions",
        "- ✅ the lump sum is split across **both** invoices, not applied to one and left short",
        "- ✅ `CG-INV-10045`'s $284.00 deduction is flagged **even though it is exactly 2%** — "
        "Bayside is on Net 30 with no discount terms, so a matcher that treats any 2% variance "
        "as an earned discount will wave it through",
        "- ❌ **any** auto-application of a variance payment. A wrong application is materially "
        "worse than an exception: it closes an invoice that is still owed and the shortfall is "
        "never chased",
        "- ❌ the unidentified payer silently dropped rather than queued",
        "",
        "> This beat **mutates the book** — applied payments change `amount_due`. Run it last, or "
        "> re-seed with `_scripts/seed_ar_book.py` before any beat that grades aging.",
        "",
    ]
    path = FIXTURES / "_REMITTANCE_KEY.md"
    path.write_text("\n".join(L), encoding="utf-8")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--to-sftp", action="store_true",
                    help="also copy the fixtures into the SFTP server's incoming/ folder")
    args = ap.parse_args()

    anchor = (_dt.date.fromisoformat(json.loads(MANIFEST.read_text())["anchor"])
              if MANIFEST.exists() else _dt.date.today())
    FIXTURES.mkdir(exist_ok=True)

    rows = build_batch(anchor)
    written = [write_csv(rows, anchor), write_pdf(rows, anchor), write_key(rows, anchor)]
    written = [p for p in written if p]

    print(f"Built the beat-2 remittance batch (deposit date {anchor}):")
    for p in written:
        print(f"  {p.name:34s} {p.stat().st_size:>7,} bytes")

    if args.to_sftp:
        if not SFTP_INCOMING.exists():
            print(f"\n  SFTP incoming/ not found at {SFTP_INCOMING}")
            print("  Start the test server first: test_human/_sftp_test_server/run_all.py")
        else:
            for p in written:
                if p.suffix in (".csv", ".pdf"):
                    shutil.copy2(p, SFTP_INCOMING / p.name)
                    print(f"  -> {SFTP_INCOMING / p.name}")

    total = sum((r["amount"] for r in rows), D("0"))
    auto = sum(1 for r in rows if r["kind"] in ("clean", "lump"))
    print(f"\n  {len(rows)} payments, ${total:,.2f} — {auto} auto-apply / {len(rows)-auto} exceptions")


if __name__ == "__main__":
    main()
