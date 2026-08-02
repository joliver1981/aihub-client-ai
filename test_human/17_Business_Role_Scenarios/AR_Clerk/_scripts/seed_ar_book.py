"""
seed_ar_book.py -- write (or remove) the Continental Goods AR book in ERPDB.

    seed                                  python seed_ar_book.py
    seed anchored to a fixed date         python seed_ar_book.py --anchor 2026-08-01
    remove every CG-* row and CG_ table   python seed_ar_book.py --teardown
    show what's there now                 python seed_ar_book.py --status

Idempotent: seeding always tears down first, so re-running gives you the same book.

The data itself lives in ar_book.py -- this file only does SQL. Nothing outside the
CG-* namespace is ever touched; the stock INV-DEMO-*/INV-724xx rows are left exactly
as they are.

Interpreter: the project's own conda env --
C:\\Users\\james\\miniconda3\\envs\\aihub2.1\\python.exe (pyodbc 5.0.1 + ODBC Driver 17).
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
import ar_book as B  # noqa: E402

D = Decimal
CONN_STR = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=10.0.0.6;DATABASE=ERPDB;UID=ai_user;PWD=Bradynov11;"
    "TrustServerCertificate=yes"
)

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "SEED_MANIFEST.json"

# Short-pay payment timing. SP2's 26-day offset is load-bearing: it is what puts the
# payment outside the 2/10 discount window and makes the discount unearned.
SP2_DAYS_AFTER_INVOICE = 26
SP1_PAYMENT_DAYS_AGO = 30
SP3_PAYMENT_DAYS_AGO = 14


def connect():
    return pyodbc.connect(CONN_STR, timeout=20)


# ---------------------------------------------------------------------------
# Schema for the three CG_ tables. ERPDB has no customer master, no collection
# activity log, and no dunning history -- all three are required for an AR desk.
# ---------------------------------------------------------------------------
DDL = [
    ("CG_ARCustomers", """
CREATE TABLE dbo.CG_ARCustomers (
    customer_id      varchar(20)   NOT NULL PRIMARY KEY,
    customer_name    varchar(100)  NOT NULL,
    contact_name     varchar(100)  NULL,
    contact_email    varchar(255)  NULL,
    phone            varchar(30)   NULL,
    payment_terms    varchar(20)   NOT NULL,
    credit_limit     decimal(15,2) NOT NULL,
    risk_rating      varchar(20)   NOT NULL,
    sales_rep        varchar(100)  NULL,
    on_credit_hold   bit           NOT NULL DEFAULT 0
)"""),
    ("CG_CollectionActivity", """
CREATE TABLE dbo.CG_CollectionActivity (
    activity_id      int IDENTITY(1,1) PRIMARY KEY,
    customer_id      varchar(20)   NOT NULL,
    invoice_id       varchar(20)   NULL,
    activity_type    varchar(20)   NOT NULL,
    activity_date    date          NOT NULL,
    promised_date    date          NULL,
    promised_amount  decimal(15,2) NULL,
    status           varchar(20)   NOT NULL,
    notes            varchar(1000) NULL,
    created_by       varchar(50)   NULL
)"""),
    ("CG_DunningLog", """
CREATE TABLE dbo.CG_DunningLog (
    dunning_id       int IDENTITY(1,1) PRIMARY KEY,
    customer_id      varchar(20)   NOT NULL,
    stage            int           NOT NULL,
    stage_label      varchar(40)   NULL,
    sent_date        date          NOT NULL,
    channel          varchar(20)   NOT NULL,
    recipient        varchar(255)  NULL,
    status           varchar(20)   NOT NULL,
    invoice_ids      varchar(500)  NULL,
    balance          decimal(15,2) NULL,
    approved_by      varchar(100)  NULL
)"""),
]

# Deletes, children before parents. Everything is prefix-scoped.
TEARDOWN_SQL = [
    "DELETE FROM dbo.PaymentApplications WHERE invoice_id LIKE 'CG-%' OR payment_id LIKE 'CG-%'",
    "DELETE FROM dbo.InvoiceLineItems    WHERE invoice_id LIKE 'CG-%'",
    "DELETE FROM dbo.InvoiceAddresses    WHERE invoice_id LIKE 'CG-%'",
    "DELETE FROM dbo.InvoiceDocuments    WHERE invoice_id LIKE 'CG-%'",
    "DELETE FROM dbo.Invoices            WHERE invoice_id LIKE 'CG-%'",
    "DELETE FROM dbo.CustomerPayments    WHERE payment_id LIKE 'CG-%'",
    "DELETE FROM dbo.SalesOrderLineItems WHERE order_id LIKE 'CG-%'",
    "DELETE FROM dbo.SalesOrderAddresses WHERE order_id LIKE 'CG-%'",
    "DELETE FROM dbo.SalesOrderDocuments WHERE order_id LIKE 'CG-%'",
    "DELETE FROM dbo.SalesOrders         WHERE order_id LIKE 'CG-%'",
    "DELETE FROM dbo.GeneralLedger       WHERE gl_entry_id LIKE 'CG-%'",
]


def table_exists(cur, name: str) -> bool:
    cur.execute("SELECT 1 FROM sys.tables WHERE name = ?", name)
    return cur.fetchone() is not None


def ensure_tables(cur, verbose=True):
    for name, ddl in DDL:
        if not table_exists(cur, name):
            cur.execute(ddl)
            if verbose:
                print(f"  created dbo.{name}")
        elif verbose:
            print(f"  dbo.{name} already present")


def teardown(cur, drop_tables=False, verbose=True):
    for name in ("CG_DunningLog", "CG_CollectionActivity", "CG_ARCustomers"):
        if table_exists(cur, name):
            cur.execute(f"DELETE FROM dbo.{name}")
            if drop_tables:
                cur.execute(f"DROP TABLE dbo.{name}")
                if verbose:
                    print(f"  dropped dbo.{name}")
    total = 0
    for sql in TEARDOWN_SQL:
        cur.execute(sql)
        total += max(cur.rowcount, 0)
    if verbose:
        print(f"  removed {total} CG-* rows from stock ERPDB tables")


def so_id(invoice_id: str) -> str:
    return invoice_id.replace("CG-INV-", "CG-SO-")


def po_for(customer_id: str, seq: str) -> str:
    initials = "".join(w[0] for w in B.CUSTOMERS_BY_ID[customer_id].name.split()[:2]).upper()
    return f"{initials}-PO-{seq[-5:]}"


class GLWriter:
    """Accumulates balanced GL pairs so the control account is genuinely derived."""

    def __init__(self):
        self.rows = []
        self._n = 0

    def entry(self, date, dr_acct, cr_acct, amount, ttype, reference, document_id, description):
        for acct, dr, cr in ((dr_acct, amount, D("0.00")), (cr_acct, D("0.00"), amount)):
            self._n += 1
            self.rows.append((
                f"CG-GL-{self._n:04d}", date, date, acct, B.GL_NAMES[acct],
                dr, cr, ttype, reference, document_id, None, description, "AR", "seed_ar_book",
                date,
            ))

    def planted(self, date):
        """The unsupported entry beat 8 has to find."""
        self.rows.append((
            B.PLANTED_GL_ENTRY_ID, date, date, B.GL_AR, B.GL_NAMES[B.GL_AR],
            B.PLANTED_GL_DIFFERENCE, D("0.00"), "Journal", "CG-JE-0042", None, None,
            B.PLANTED_GL_DESCRIPTION, "AR", "j.kowal", date,
        ))
        self.rows.append((
            "CG-GL-9002", date, date, B.GL_UNAPPLIED, B.GL_NAMES[B.GL_UNAPPLIED],
            D("0.00"), B.PLANTED_GL_DIFFERENCE, "Journal", "CG-JE-0042", None, None,
            B.PLANTED_GL_DESCRIPTION, "AR", "j.kowal", date,
        ))


def seed(cur, anchor: _dt.date, verbose=True):
    gl = GLWriter()
    counts = {}

    # --- customers -------------------------------------------------------
    cur.executemany(
        "INSERT INTO dbo.CG_ARCustomers (customer_id, customer_name, contact_name, "
        "contact_email, phone, payment_terms, credit_limit, risk_rating, sales_rep, "
        "on_credit_hold) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(c.customer_id, c.name, c.contact_name, c.contact_email, c.phone, c.payment_terms,
          c.credit_limit, c.risk_rating, c.sales_rep, 1 if c.on_credit_hold else 0)
         for c in B.CUSTOMERS])
    counts["CG_ARCustomers"] = len(B.CUSTOMERS)

    orders, order_lines, invoices, invoice_lines = [], [], [], []
    payments, applications = [], []

    # --- open invoices (+ their sales orders) ----------------------------
    for inv in B.OPEN_INVOICES:
        cust = B.CUSTOMERS_BY_ID[inv.customer_id]
        issued, due = B.dates_for_open_invoice(inv, anchor)
        total = B.invoice_total(inv)
        due_amt = B.amount_due(inv)
        order = so_id(inv.invoice_id)
        po = po_for(inv.customer_id, inv.invoice_id)
        so_extended = (inv.unit_price * inv.so_qty).quantize(D("0.01"))

        orders.append((order, issued - _dt.timedelta(days=3), inv.customer_id, cust.name, po,
                       "Invoiced", cust.payment_terms, cust.sales_rep, so_extended,
                       D("0.0000"), D("0.00"), inv.shipping, so_extended + inv.shipping,
                       "USD", None))
        order_lines.append((order, 1, inv.item_id, inv.description, inv.so_qty,
                            inv.unit_price, "EA", so_extended, "EXEMPT"))

        invoices.append((
            inv.invoice_id, issued, due, inv.customer_id, cust.name, po, order,
            B.invoice_status(inv), None, None, None, cust.payment_terms,
            inv.subtotal, inv.shipping, D("0.0000"), D("0.00"), D("0.00"),
            total, inv.amount_paid, due_amt, "USD", B.GL_AR, "AR",
            inv.note or None))
        invoice_lines.append((inv.invoice_id, 1, inv.item_id, inv.description, inv.inv_qty,
                              inv.unit_price, "EA",
                              (inv.unit_price * inv.inv_qty).quantize(D("0.01")),
                              "EXEMPT", B.GL_REVENUE))

        gl.entry(issued, B.GL_AR, B.GL_REVENUE, total, "Invoice",
                 inv.invoice_id, inv.invoice_id, f"Invoice {inv.invoice_id} - {cust.name}")

    # --- short-pay payments ---------------------------------------------
    sp_dates = {
        "CG-INV-10007": anchor - _dt.timedelta(days=SP1_PAYMENT_DAYS_AGO),
        "CG-INV-10016": anchor - _dt.timedelta(days=SP3_PAYMENT_DAYS_AGO),
    }
    sp_memos = {
        "CG-INV-10007": "Payment per remittance advice RD-REM-88213",
        "CG-INV-10016": "Payment less freight - FOB destination per our PO",
        "CG-INV-10021": "Payment less 2% terms discount",
    }
    sp_seq = 1
    for inv in B.OPEN_INVOICES:
        if inv.amount_paid <= 0:
            continue
        cust = B.CUSTOMERS_BY_ID[inv.customer_id]
        issued, _ = B.dates_for_open_invoice(inv, anchor)
        if inv.invoice_id == "CG-INV-10021":
            pay_date = issued + _dt.timedelta(days=SP2_DAYS_AFTER_INVOICE)
        else:
            pay_date = sp_dates[inv.invoice_id]

        pid = f"CG-PAY-{sp_seq:04d}"
        sp_seq += 1
        payments.append((pid, f"FCB-CG-{pid[-4:]}", inv.customer_id, cust.name, pay_date,
                         pay_date, "ACH", f"{po_for(inv.customer_id, inv.invoice_id)}-PMT",
                         sp_memos[inv.invoice_id], inv.amount_paid, "USD",
                         "9283-5671-4450-8821", "Cleared", "bank_import", pay_date,
                         B.GL_CASH, "AR"))
        # discount_taken stays 0.00 on purpose: nothing here was an accepted discount,
        # it is an unresolved short-pay. Beat 3 exists to determine why.
        applications.append((pid, inv.invoice_id, issued, B.invoice_total(inv),
                             inv.amount_paid, D("0.00"), None, None))
        gl.entry(pay_date, B.GL_CASH, B.GL_AR, inv.amount_paid, "Payment",
                 pid, inv.invoice_id, f"Payment {pid} applied to {inv.invoice_id}")

    # --- paid history ----------------------------------------------------
    for p in B.PAID_INVOICES:
        cust = B.CUSTOMERS_BY_ID[p.customer_id]
        issued = anchor - _dt.timedelta(days=p.issued_days_ago)
        due = issued + _dt.timedelta(days=B.term_days(p.customer_id))
        pay_date = issued + _dt.timedelta(days=p.days_to_pay)
        order = so_id(p.invoice_id)
        po = po_for(p.customer_id, p.invoice_id)
        qty = int((p.amount / p.unit_price).to_integral_value())

        orders.append((order, issued - _dt.timedelta(days=3), p.customer_id, cust.name, po,
                       "Closed", cust.payment_terms, cust.sales_rep, p.amount,
                       D("0.0000"), D("0.00"), D("0.00"), p.amount, "USD", None))
        order_lines.append((order, 1, p.item_id, p.description, qty, p.unit_price, "EA",
                            p.amount, "EXEMPT"))

        pid = f"CG-PAY-{sp_seq:04d}"
        sp_seq += 1
        invoices.append((
            p.invoice_id, issued, due, p.customer_id, cust.name, po, order,
            "Paid", pay_date, pid, f"FCB-CG-{pid[-4:]}", cust.payment_terms,
            p.amount, D("0.00"), D("0.0000"), D("0.00"), D("0.00"),
            p.amount, p.amount, D("0.00"), "USD", B.GL_AR, "AR", None))
        invoice_lines.append((p.invoice_id, 1, p.item_id, p.description, qty,
                              p.unit_price, "EA", p.amount, "EXEMPT", B.GL_REVENUE))
        payments.append((pid, f"FCB-CG-{pid[-4:]}", p.customer_id, cust.name, pay_date,
                         pay_date, "ACH", f"{po}-PMT", f"Payment for {p.invoice_id}",
                         p.amount, "USD", "9283-5671-4450-8821", "Cleared", "bank_import",
                         pay_date, B.GL_CASH, "AR"))
        applications.append((pid, p.invoice_id, issued, p.amount, p.amount,
                             D("0.00"), None, None))

        gl.entry(issued, B.GL_AR, B.GL_REVENUE, p.amount, "Invoice",
                 p.invoice_id, p.invoice_id, f"Invoice {p.invoice_id} - {cust.name}")
        gl.entry(pay_date, B.GL_CASH, B.GL_AR, p.amount, "Payment",
                 pid, p.invoice_id, f"Payment {pid} applied to {p.invoice_id}")

    # --- unapplied cash --------------------------------------------------
    for u in B.UNAPPLIED_PAYMENTS:
        cust = B.CUSTOMERS_BY_ID[u.customer_id]
        pay_date = anchor - _dt.timedelta(days=u.days_ago)
        payments.append((u.payment_id, f"FCB-CG-{u.payment_id[-4:]}", u.customer_id,
                         cust.name, pay_date, pay_date, u.method, u.reference, u.memo,
                         u.amount, "USD", "9283-5671-4450-8821", "Cleared", "bank_import",
                         pay_date, B.GL_CASH, "AR"))
        # Deliberately hits unapplied cash, NOT the AR control account -- so the
        # subledger and the control account still tie except for the planted entry.
        gl.entry(pay_date, B.GL_CASH, B.GL_UNAPPLIED, u.amount, "Payment",
                 u.payment_id, None, f"Unapplied receipt {u.payment_id} - {cust.name}")

    gl.planted(anchor - _dt.timedelta(days=9))

    # --- write everything ------------------------------------------------
    cur.executemany(
        "INSERT INTO dbo.SalesOrders (order_id, order_date, customer_id, customer_name, "
        "customer_po, status, payment_terms, sales_rep, subtotal, tax_rate, tax_amount, "
        "shipping_amount, total_amount, currency, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        orders)
    counts["SalesOrders"] = len(orders)

    cur.executemany(
        "INSERT INTO dbo.SalesOrderLineItems (order_id, line_number, item_id, description, "
        "quantity, unit_price, uom, extended_price, tax_code) VALUES (?,?,?,?,?,?,?,?,?)",
        order_lines)
    counts["SalesOrderLineItems"] = len(order_lines)

    cur.executemany(
        "INSERT INTO dbo.Invoices (invoice_id, invoice_date, due_date, customer_id, "
        "customer_name, customer_po, order_id, status, payment_date, payment_ref, "
        "transaction_id, payment_terms, subtotal, shipping_amount, tax_rate, tax_amount, "
        "discount_amount, total_amount, amount_paid, amount_due, currency, gl_account, "
        "department, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        invoices)
    counts["Invoices"] = len(invoices)

    cur.executemany(
        "INSERT INTO dbo.InvoiceLineItems (invoice_id, line_number, item_id, description, "
        "quantity, unit_price, uom, extended_price, tax_code, gl_account) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        invoice_lines)
    counts["InvoiceLineItems"] = len(invoice_lines)

    cur.executemany(
        "INSERT INTO dbo.CustomerPayments (payment_id, transaction_ref, customer_id, "
        "customer_name, payment_date, posting_date, payment_method, reference_number, memo, "
        "amount, currency, bank_account, status, created_by, created_date, gl_account, "
        "department) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        payments)
    counts["CustomerPayments"] = len(payments)

    cur.executemany(
        "INSERT INTO dbo.PaymentApplications (payment_id, invoice_id, invoice_date, "
        "invoice_amount, applied_amount, discount_taken, discount_reason, discount_gl_account) "
        "VALUES (?,?,?,?,?,?,?,?)",
        applications)
    counts["PaymentApplications"] = len(applications)

    cur.executemany(
        "INSERT INTO dbo.GeneralLedger (gl_entry_id, transaction_date, posting_date, "
        "gl_account, gl_account_name, debit_amount, credit_amount, transaction_type, "
        "reference, document_id, transaction_id, description, department, created_by, "
        "created_date) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        gl.rows)
    counts["GeneralLedger"] = len(gl.rows)

    # --- collection activity + dunning history ---------------------------
    activity = []
    for a in B.COLLECTION_ACTIVITY:
        promised = (anchor + _dt.timedelta(days=a.promised_date_offset)
                    if a.promised_date_offset is not None else None)
        activity.append((a.customer_id, a.invoice_id, a.activity_type,
                         anchor - _dt.timedelta(days=a.days_ago), promised,
                         a.promised_amount, a.status, a.notes, a.created_by))
    cur.executemany(
        "INSERT INTO dbo.CG_CollectionActivity (customer_id, invoice_id, activity_type, "
        "activity_date, promised_date, promised_amount, status, notes, created_by) "
        "VALUES (?,?,?,?,?,?,?,?,?)", activity)
    counts["CG_CollectionActivity"] = len(activity)

    dunning = []
    for h in B.DUNNING_HISTORY:
        label = next((lbl for _, _, s, lbl in B.DUNNING_STAGES if s == h.stage), None)
        dunning.append((h.customer_id, h.stage, label,
                        anchor - _dt.timedelta(days=h.days_ago), h.channel,
                        h.recipient, h.status, None, None, "dana.reyes"))
    cur.executemany(
        "INSERT INTO dbo.CG_DunningLog (customer_id, stage, stage_label, sent_date, channel, "
        "recipient, status, invoice_ids, balance, approved_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
        dunning)
    counts["CG_DunningLog"] = len(dunning)

    return counts


def status(cur):
    checks = [
        ("CG_ARCustomers", "SELECT COUNT(*) FROM dbo.CG_ARCustomers"),
        ("CG_CollectionActivity", "SELECT COUNT(*) FROM dbo.CG_CollectionActivity"),
        ("CG_DunningLog", "SELECT COUNT(*) FROM dbo.CG_DunningLog"),
        ("Invoices (CG)", "SELECT COUNT(*) FROM dbo.Invoices WHERE invoice_id LIKE 'CG-%'"),
        ("CustomerPayments (CG)", "SELECT COUNT(*) FROM dbo.CustomerPayments WHERE payment_id LIKE 'CG-%'"),
        ("PaymentApplications (CG)", "SELECT COUNT(*) FROM dbo.PaymentApplications WHERE invoice_id LIKE 'CG-%'"),
        ("SalesOrders (CG)", "SELECT COUNT(*) FROM dbo.SalesOrders WHERE order_id LIKE 'CG-%'"),
        ("GeneralLedger (CG)", "SELECT COUNT(*) FROM dbo.GeneralLedger WHERE gl_entry_id LIKE 'CG-%'"),
    ]
    print("\nCurrent CG book:")
    for label, sql in checks:
        try:
            cur.execute(sql)
            print(f"  {label:28s} {cur.fetchone()[0]}")
        except Exception as e:
            print(f"  {label:28s} -- {type(e).__name__}: {str(e)[:60]}")

    cur.execute("SELECT ISNULL(SUM(amount_due),0) FROM dbo.Invoices WHERE invoice_id LIKE 'CG-%'")
    sub = cur.fetchone()[0]
    cur.execute("SELECT ISNULL(SUM(debit_amount - credit_amount),0) FROM dbo.GeneralLedger "
                "WHERE gl_account = ?", B.GL_AR)
    ctrl = cur.fetchone()[0]
    print(f"\n  AR subledger      {sub:>12,.2f}")
    print(f"  GL {B.GL_AR:<14s} {ctrl:>12,.2f}")
    print(f"  difference        {ctrl - sub:>12,.2f}  (planted: {B.PLANTED_GL_DIFFERENCE})")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--anchor", help="anchor date YYYY-MM-DD (default: today)")
    ap.add_argument("--teardown", action="store_true", help="remove the CG book and exit")
    ap.add_argument("--drop-tables", action="store_true",
                    help="with --teardown, also DROP the CG_ tables")
    ap.add_argument("--status", action="store_true", help="report what is currently seeded")
    args = ap.parse_args()

    anchor = (_dt.date.fromisoformat(args.anchor) if args.anchor else _dt.date.today())

    cn = connect()
    cn.autocommit = False
    cur = cn.cursor()
    try:
        if args.status:
            status(cur)
            return

        if args.teardown:
            print("Tearing down the CG AR book...")
            teardown(cur, drop_tables=args.drop_tables)
            cn.commit()
            if MANIFEST_PATH.exists():
                MANIFEST_PATH.unlink()
                print(f"  removed {MANIFEST_PATH.name}")
            print("Done.")
            return

        print(f"Seeding the Continental Goods AR book (anchor {anchor})...")
        ensure_tables(cur)
        teardown(cur, verbose=False)
        counts = seed(cur, anchor)
        cn.commit()

        for k, v in counts.items():
            print(f"  {k:28s} {v}")

        manifest = {
            "anchor": anchor.isoformat(),
            "seeded_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "row_counts": counts,
            "aging": B.expected_aging(),
            "dunning_plan": B.expected_dunning_plan(),
            "short_pays": B.expected_short_pays(),
            "unapplied_cash": B.expected_unapplied_cash(),
            "days_to_pay": B.expected_days_to_pay(),
            "gl_reconciliation": B.expected_gl_reconciliation(),
        }
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"\n  manifest -> {MANIFEST_PATH}")
        status(cur)

    except Exception:
        cn.rollback()
        raise
    finally:
        cn.close()


if __name__ == "__main__":
    main()
