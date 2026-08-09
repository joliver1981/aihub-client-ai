"""
Plant / clear an obvious data-quality anomaly in ERPDB for the watchdog test.

Everything is namespaced WATCHDOG-* so it never touches real or demo data.
Plants two catchable problems:
  - a DUPLICATE invoice (same id used twice is impossible via PK, so we plant
    two invoices with identical customer_po + amount + date = a dup-payment risk)
  - an invoice with a NULL/blank customer_po (missing PO)

Run:  python plant_anomaly.py           (insert the anomalies)
      python plant_anomaly.py --clear   (remove every WATCHDOG-* row)
"""
import sys
from datetime import date

import pyodbc

CS = "DRIVER={SQL Server};SERVER=10.0.0.6;DATABASE=ERPDB;UID=ai_user;PWD=Bradynov11"


def _cols(cur):
    cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME='Invoices'")
    return {r[0] for r in cur.fetchall()}


def plant():
    c = pyodbc.connect(CS, timeout=10); cur = c.cursor()
    clear(c, quiet=True)
    cols = _cols(cur)
    today = date.today()
    # order_id has an FK to SalesOrders — borrow a real one (the anomaly is in
    # the PO/duplicate/amount fields, not the order link).
    cur.execute("SELECT TOP 1 order_id FROM dbo.SalesOrders")
    real_order = (cur.fetchone() or ["SO-0001"])[0]
    rows = [
        # (id, date, customer_id, customer_name, po, status, subtotal, amount_due)
        # two invoices, same PO + amount + date = duplicate-payment smell
        ("WATCHDOG-DUP-1", today, "WD-CUST-01", "Contoso Supplies", "PO-DUP-555", "Open", 4200.00, 4200.00),
        ("WATCHDOG-DUP-2", today, "WD-CUST-01", "Contoso Supplies", "PO-DUP-555", "Open", 4200.00, 4200.00),
        # missing PO
        ("WATCHDOG-NOPO-1", today, "WD-CUST-02", "Fabrikam Freight", None, "Open", 1875.00, 1875.00),
    ]
    has_amt = "amount_due" in cols
    # Literal INSERT: the legacy {SQL Server} ODBC driver rejects bound
    # date/Decimal/None params (HYC00). Values are fully controlled + namespaced
    # (no external input), so a built statement is safe here.
    def q(s):
        return "'" + str(s).replace("'", "''") + "'"
    for i, (inv, d, cust_id, cust, po, status, subtotal, amt) in enumerate(rows):
        # every NOT-NULL/no-default column is supplied (order_id/total_amount/
        # currency required by the schema); order_id namespaced per row.
        f = ["invoice_id", "invoice_date", "due_date", "customer_id",
             "customer_name", "customer_po", "order_id", "status",
             "subtotal", "total_amount", "currency"]
        v = [q(inv), q(f"{d:%Y-%m-%d}"), q(f"{d:%Y-%m-%d}"), q(cust_id), q(cust),
             ("NULL" if po is None else q(po)), q(real_order),
             q(status), f"{subtotal:.2f}", f"{amt:.2f}", q("USD")]
        if has_amt:
            f.append("amount_due"); v.append(f"{amt:.2f}")
        cur.execute(f"INSERT INTO dbo.Invoices ({','.join(f)}) "
                    f"VALUES ({','.join(v)})")
    c.commit()
    print("planted 3 WATCHDOG-* anomalies (1 duplicate PO pair + 1 missing PO)")
    c.close()


def clear(conn=None, quiet=False):
    own = conn is None
    c = conn or pyodbc.connect(CS, timeout=10)
    cur = c.cursor()
    cur.execute("DELETE FROM dbo.Invoices WHERE invoice_id LIKE 'WATCHDOG-%'")
    n = cur.rowcount
    c.commit()
    if not quiet:
        print(f"cleared {n} WATCHDOG-* row(s)")
    if own:
        c.close()


if __name__ == "__main__":
    if "--clear" in sys.argv:
        clear()
    else:
        plant()
