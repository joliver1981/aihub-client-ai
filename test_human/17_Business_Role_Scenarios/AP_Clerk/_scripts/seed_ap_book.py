"""
seed_ap_book.py -- write (or remove) the Continental Goods AP book in ERPDB.

    seed                                   python seed_ap_book.py
    seed anchored to a fixed date          python seed_ap_book.py --anchor 2026-08-24
    stress book (~2,000 invoices)          python seed_ap_book.py --scale 8
    show what is there now                 python seed_ap_book.py --status
    remove every CG* AP row                python seed_ap_book.py --teardown
    ...and drop the CG_ AP tables too      python seed_ap_book.py --teardown --drop-tables

Idempotent: seeding always tears down first, so re-running gives the same book.

The data itself lives in ap_book.py -- this file only does SQL.

SAFETY. Nothing outside the AP namespace is ever touched:
  * vendors      LIFNR LIKE 'CGV%'          (stock V001..V005 untouched)
  * POs          EBELN LIKE 'CGPO-%'        (stock 45000000xx untouched)
  * receipts     EKBE rows for those POs
  * terms        ZTERM IN ('2T15','1T10','NT60')   (2T10/NT30/NT45 untouched)
  * GL           created_by='seed_ap_book' (AR pack uses 'seed_ar_book')
  * invoices     the three CG_Vendor* / CG_APPaymentRuns tables, which this pack owns
The AR pack's CG-INV-* / CG_ARCustomers rows are in different tables and are
never referenced here.

Interpreter: C:\\Users\\james\\miniconda3\\envs\\aihub2.1\\python.exe (pyodbc + ODBC 17).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from decimal import Decimal
from pathlib import Path

import pyodbc

sys.path.insert(0, str(Path(__file__).parent))
import ap_book as B  # noqa: E402

D = Decimal
CONN_STR = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=10.0.0.6;DATABASE=ERPDB;UID=ai_user;PWD=Bradynov11;"
    "TrustServerCertificate=yes"
)
MANIFEST = Path(__file__).resolve().parents[1] / "SEED_MANIFEST.json"

AP_GL_ACCOUNT = "2000-CG"       # AP control
GRIR_ACCOUNT = "1910-CG"        # goods received / invoice received clearing
TAX_ACCOUNT = "1450-CG"         # input tax recoverable

DDL = {
    "CG_VendorInvoices": """
        CREATE TABLE dbo.CG_VendorInvoices (
            vendor_invoice_id  NVARCHAR(20)  NOT NULL,
            source_channel     NVARCHAR(10)  NOT NULL,
            lifnr              NVARCHAR(10)  NULL,
            vendor_name        NVARCHAR(100) NULL,
            invoice_date       DATE          NULL,
            due_date           DATE          NULL,
            po_ref             NVARCHAR(10)  NULL,
            zterm              NVARCHAR(10)  NULL,
            subtotal           DECIMAL(15,2) NULL,
            freight            DECIMAL(15,2) NULL,
            tax                DECIMAL(15,2) NULL,
            total_amount       DECIMAL(15,2) NULL,
            currency           NVARCHAR(3)   NULL,
            status             NVARCHAR(20)  NULL,
            posted_on          DATE          NULL,
            paid_on            DATE          NULL,
            document_file      NVARCHAR(200) NULL,
            received_at        DATETIME      NULL
        )""",
    "CG_VendorInvoiceLines": """
        CREATE TABLE dbo.CG_VendorInvoiceLines (
            vendor_invoice_id  NVARCHAR(20)  NOT NULL,
            line_number        INT           NOT NULL,
            matnr              NVARCHAR(18)  NULL,
            description        NVARCHAR(200) NULL,
            quantity           DECIMAL(13,3) NULL,
            uom                NVARCHAR(3)   NULL,
            unit_price         DECIMAL(13,4) NULL,
            extended_price     DECIMAL(15,2) NULL,
            is_charge          BIT           NULL
        )""",
    "CG_APPaymentRuns": """
        CREATE TABLE dbo.CG_APPaymentRuns (
            run_id             NVARCHAR(20)  NOT NULL,
            created_on         DATETIME      NULL,
            created_by         NVARCHAR(50)  NULL,
            status             NVARCHAR(20)  NULL,
            invoice_count      INT           NULL,
            total_amount       DECIMAL(15,2) NULL,
            approved_by        NVARCHAR(50)  NULL,
            approved_on        DATETIME      NULL
        )""",
}

AP_TABLES = list(DDL)


def connect():
    return pyodbc.connect(CONN_STR, timeout=25)


def table_exists(cur, name: str) -> bool:
    cur.execute("SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME=?", name)
    return cur.fetchone() is not None


def ensure_tables(cur):
    for name, ddl in DDL.items():
        if not table_exists(cur, name):
            cur.execute(ddl)
            print(f"  created dbo.{name}")


# --------------------------------------------------------------------------
def teardown(cur, drop_tables: bool = False):
    """Remove every AP-namespace row. Never touches anything else."""
    steps = [
        ("EKBE  (CGPO POs)", "DELETE FROM dbo.EKBE WHERE EBELN LIKE 'CGPO-%'"),
        ("EKPO  (CGPO POs)", "DELETE FROM dbo.EKPO WHERE EBELN LIKE 'CGPO-%'"),
        ("EKKO  (CGPO POs)", "DELETE FROM dbo.EKKO WHERE EBELN LIKE 'CGPO-%'"),
        ("LFA1  (CGV vendors)", "DELETE FROM dbo.LFA1 WHERE LIFNR LIKE 'CGV%'"),
        ("T052  (AP terms)", "DELETE FROM dbo.T052 WHERE ZTERM IN ('2T15','1T10','NT60')"),
        ("GeneralLedger (AP)", "DELETE FROM dbo.GeneralLedger WHERE created_by = 'seed_ap_book'"),
    ]
    for label, sql in steps:
        try:
            cur.execute(sql)
            print(f"  cleared {label:24} {cur.rowcount if cur.rowcount > 0 else 0} rows")
        except Exception as e:                                    # noqa: BLE001
            print(f"  skipped {label:24} ({type(e).__name__}: {str(e)[:80]})")
    for t in AP_TABLES:
        if table_exists(cur, t):
            if drop_tables:
                cur.execute(f"DROP TABLE dbo.{t}")
                print(f"  dropped dbo.{t}")
            else:
                cur.execute(f"DELETE FROM dbo.{t}")
                print(f"  cleared dbo.{t:24} {max(cur.rowcount, 0)} rows")


# --------------------------------------------------------------------------
def seed(book: B.Book, cur):
    counts = {}

    # --- payment terms -----------------------------------------------------
    for zterm, ztext, ztag1, zprz1, ztag2 in B.TERMS_DEFS:
        cur.execute(
            "INSERT INTO dbo.T052 (ZTERM, ZTEXT, ZTAG1, ZPRZ1, ZTAG2) VALUES (?,?,?,?,?)",
            zterm, ztext, ztag1, zprz1, ztag2)
    counts["T052"] = len(B.TERMS_DEFS)

    # --- vendor master -----------------------------------------------------
    for v in book.vendors:
        cur.execute("""INSERT INTO dbo.LFA1
            (LIFNR, NAME1, STRAS, ORT01, REGIO, PSTLZ, LAND1, TELF1, SMTP_ADDR,
             STCD1, ZTERM, WAERS, ERDAT, LOEVM)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            v.lifnr, v.name1, v.street, v.city, v.region, v.postcode, "US",
            f"1-{v.postcode[:3]}-555-{v.postcode[-4:]}", v.email,
            f"TAX{v.lifnr[-3:]}9911", v.zterm, "USD", book.anchor, 0)
    counts["LFA1"] = len(book.vendors)

    # --- purchase orders ---------------------------------------------------
    for po in book.pos:
        cur.execute("""INSERT INTO dbo.EKKO
            (EBELN, BUKRS, BSTYP, BSART, LIFNR, EKORG, EKGRP, WAERS, BEDAT,
             ZTERM, INCO1, NETWR, ERNAM, ERDAT, STATU, FRGKE)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            po.ebeln, "CG01", "F", "NB", po.lifnr, "CG01", "AP1", po.waers,
            po.bedat, po.zterm, po.incoterm, po.netwr, "MARCUS.BELL",
            po.bedat, "B", "R")
    counts["EKKO"] = len(book.pos)

    n_lines = 0
    for po in book.pos:
        for ln in po.lines:
            cur.execute("""INSERT INTO dbo.EKPO
                (EBELN, EBELP, MATNR, TXZ01, MENGE, MEINS, NETPR, PEINH, NETWR,
                 MWSKZ, WERKS, LGORT, EINDT, ELIKZ, LOEKZ, ERDAT)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ln.ebeln, ln.ebelp, ln.matnr, ln.txz01, ln.menge, ln.meins,
                ln.netpr, ln.peinh, ln.netwr, ln.mwskz, "CG01", "0001",
                ln.eindt, 0, None, po.bedat)
            n_lines += 1
    counts["EKPO"] = n_lines

    # --- goods receipts ----------------------------------------------------
    for r in book.receipts:
        cur.execute("""INSERT INTO dbo.EKBE
            (EBELN, EBELP, ZEKKN, VGABE, GJAHR, BELNR, BUZEI, BEWTP, MENGE,
             WRBTR, WAERS, BUDAT, BLDAT, XBLNR, ERNAM, ERDAT)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            r.ebeln, r.ebelp, r.zekkn, r.vgabe, r.gjahr, r.belnr, r.buzei,
            "E" if r.vgabe == "1" else "Q", r.menge, r.wrbtr, "USD",
            r.budat, r.budat, r.xblnr, "WMS.AUTO", r.budat)
    counts["EKBE"] = len(book.receipts)

    # --- posted history invoices ------------------------------------------
    n_hist_lines = 0
    for inv in book.history:
        cur.execute("""INSERT INTO dbo.CG_VendorInvoices
            (vendor_invoice_id, source_channel, lifnr, vendor_name, invoice_date,
             due_date, po_ref, zterm, subtotal, freight, tax, total_amount,
             currency, status, posted_on, paid_on, document_file, received_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            inv.inv_no, "history", inv.lifnr, inv.vendor_name, inv.inv_date,
            inv.due_date, inv.po_ref, inv.zterm, inv.subtotal, inv.freight,
            inv.tax, inv.total, "USD", "Paid", inv.inv_date,
            inv.due_date, None, None)
        for l in inv.lines:
            cur.execute("""INSERT INTO dbo.CG_VendorInvoiceLines
                (vendor_invoice_id, line_number, matnr, description, quantity,
                 uom, unit_price, extended_price, is_charge)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                inv.inv_no, l.line_no, l.matnr, l.description, l.qty, l.uom,
                l.unit_price, l.extended, 1 if l.is_charge else 0)
            n_hist_lines += 1
    counts["CG_VendorInvoices"] = len(book.history)
    counts["CG_VendorInvoiceLines"] = n_hist_lines

    # --- GL: AP control postings for the posted history --------------------
    # The AR pack owns CG-GL-*/created_by='seed_ar_book'. This pack uses its own
    # CGAP-GL-* ids and created_by='seed_ap_book' so the two can never collide.
    n_gl = 0
    for i, inv in enumerate(book.history):
        desc = f"AP invoice {inv.inv_no} - {inv.vendor_name}"[:255]
        for acct, name, dr, cr in (
            (GRIR_ACCOUNT, "GR/IR Clearing (CG)", inv.subtotal + inv.freight, D("0.00")),
            (TAX_ACCOUNT, "Input Tax Recoverable (CG)", inv.tax, D("0.00")),
            (AP_GL_ACCOUNT, "Accounts Payable - Continental Goods", D("0.00"), inv.total),
        ):
            n_gl += 1
            cur.execute("""INSERT INTO dbo.GeneralLedger
                (gl_entry_id, transaction_date, posting_date, gl_account,
                 gl_account_name, debit_amount, credit_amount, transaction_type,
                 reference, document_id, description, department, created_by, created_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                f"CGAP-GL-{n_gl:05d}", inv.inv_date, inv.inv_date, acct, name,
                dr, cr, "Vendor Invoice", inv.inv_no, inv.inv_no, desc,
                "AP", "seed_ap_book", inv.inv_date)
    counts["GeneralLedger"] = n_gl

    # CG_APPaymentRuns is created EMPTY on purpose -- U05 must leave it empty.
    counts["CG_APPaymentRuns"] = 0
    return counts


# --------------------------------------------------------------------------
def status(cur):
    q = [
        ("LFA1  CGV vendors", "SELECT COUNT(*) FROM dbo.LFA1 WHERE LIFNR LIKE 'CGV%'"),
        ("EKKO  CGPO POs", "SELECT COUNT(*) FROM dbo.EKKO WHERE EBELN LIKE 'CGPO-%'"),
        ("EKPO  PO lines", "SELECT COUNT(*) FROM dbo.EKPO WHERE EBELN LIKE 'CGPO-%'"),
        ("EKBE  goods receipts", "SELECT COUNT(*) FROM dbo.EKBE WHERE EBELN LIKE 'CGPO-%' AND VGABE='1'"),
        ("T052  AP terms", "SELECT COUNT(*) FROM dbo.T052 WHERE ZTERM IN ('2T15','1T10','NT60')"),
        ("PO value", "SELECT COALESCE(SUM(NETWR),0) FROM dbo.EKKO WHERE EBELN LIKE 'CGPO-%'"),
    ]
    for label, sql in q:
        cur.execute(sql)
        print(f"  {label:24} {cur.fetchone()[0]}")
    for t in AP_TABLES:
        if table_exists(cur, t):
            cur.execute(f"SELECT COUNT(*) FROM dbo.{t}")
            print(f"  {t:24} {cur.fetchone()[0]}")
        else:
            print(f"  {t:24} (table not present)")
    cur.execute("SELECT COUNT(*) FROM dbo.LFA1 WHERE LIFNR NOT LIKE 'CGV%'")
    print(f"\n  untouched non-CGV vendors: {cur.fetchone()[0]}  (must stay 5)")


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Seed the AP book into ERPDB.")
    ap.add_argument("--anchor", help="YYYY-MM-DD; default today")
    ap.add_argument("--scale", type=int, default=1, help="multiply the book (stress run)")
    ap.add_argument("--seed", type=int, help="vary the book — a genuinely different batch")
    ap.add_argument("--day", type=int, default=0,
                    help="run-day: 0 = first drop; 1+ posts the receipts that land later")
    ap.add_argument("--teardown", action="store_true")
    ap.add_argument("--drop-tables", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    cn = connect()
    cn.autocommit = False
    cur = cn.cursor()

    if a.status:
        print("AP book — current state in ERPDB\n")
        status(cur)
        return

    if a.teardown:
        print("Tearing down the AP book...")
        teardown(cur, a.drop_tables)
        cn.commit()
        print("\nDone. Nothing outside the AP namespace was touched.")
        return

    anchor = _dt.date.fromisoformat(a.anchor) if a.anchor else _dt.date.today()
    print(f"Building the book (anchor {anchor}, scale {a.scale}, day {a.day})...")
    book = B.build(anchor, a.scale, a.seed, a.day)
    s = book.summary()

    print("Tearing down any previous AP book first...")
    teardown(cur, drop_tables=False)
    ensure_tables(cur)

    print("Seeding...")
    counts = seed(book, cur)
    cn.commit()

    for k, v in counts.items():
        print(f"  {k:26} {v}")
    print(f"\n  run day                      {s['day']}")
    print(f"  documents arrived so far     {s['documents_arrived']} of {s['batch_documents']}"
          f"   by day {s['documents_by_arrival_day']}")
    print(f"  exceptions / parked / decoys {s['exceptions']} / {s['parked']} / {s['decoys']}")
    print(f"  parked right now             {s['parked_now']}"
          f"   (auto-cleared so far: {s['parked_auto_cleared']})")
    print(f"  batch value                  ${float(s['batch_total_value']):,.2f}")
    print(f"  variance exposure            ${float(s['variance_exposure']):,.2f}")
    print(f"  blocked invoice value        ${float(s['blocked_invoice_value']):,.2f}")

    MANIFEST.write_text(json.dumps({
        "anchor": anchor.isoformat(),
        "seeded_at": _dt.datetime.now().replace(microsecond=0).isoformat(),
        "scale": a.scale,
        "seed": a.seed if a.seed is not None else B.SEED,
        "day": a.day,
        "row_counts": counts,
        "book": s,
    }, indent=2), encoding="utf-8")
    print(f"\nWrote {MANIFEST.name}")
    print("Next: python make_fixtures.py --distribute")


if __name__ == "__main__":
    main()
