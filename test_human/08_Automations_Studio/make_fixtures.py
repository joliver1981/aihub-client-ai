"""
Fixture + answer-key generator for the 08_Automations_Studio human test pack.

Generates vendor "Field Expense Report" PDFs whose Employee IDs are REAL rows
in AIRDB TS.employee_data (so the demo automation's database lookups hit), and
writes _ANSWER_KEY.md with ground truth:
  * per-employee expense totals   — deterministic (fixed seed, computed here)
  * employee -> name/store/target — read live from AIRDB
  * CC data-Q&A expected values   — computed live from AIRDB (stable windows)

Run ONCE before testing, on the network that can reach 10.0.0.6:

    C:\\Users\\james\\miniconda3\\envs\\aihub2.1\\python.exe make_fixtures.py

Offline fallback (no DB): generates the same PDFs with placeholder employees
and marks the answer key accordingly — the automation will then report its
lookups honestly as NOT FOUND, which is itself a valid honesty test, but the
demo shines when lookups hit, so prefer the online run.

    ... make_fixtures.py --offline
"""

import random
import sys
from datetime import date, timedelta
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
SEED = 20260713  # deterministic amounts — the answer key depends on it

CATEGORIES = ["Mileage", "Client meals", "Supplies", "Lodging", "Parking", "Tolls"]

OFFLINE_EMPLOYEES = [  # used only with --offline (IDs may not exist in AIRDB)
    (101, "Offline Placeholder A", 1, "Store One", "N/A", 0),
    (102, "Offline Placeholder B", 1, "Store One", "N/A", 0),
    (103, "Offline Placeholder C", 2, "Store Two", "N/A", 0),
    (104, "Offline Placeholder D", 2, "Store Two", "N/A", 0),
    (105, "Offline Placeholder E", 3, "Store Three", "N/A", 0),
]

POISON = (99999, "Alex Unknown")  # deliberately NOT in the database


def connect_airdb():
    import pyodbc
    for driver in ("ODBC Driver 17 for SQL Server", "ODBC Driver 18 for SQL Server", "SQL Server"):
        try:
            return pyodbc.connect(
                f"DRIVER={{{driver}}};SERVER=10.0.0.6;DATABASE=AIRDB;"
                "UID=ai_user;PWD=Bradynov11;TrustServerCertificate=yes", timeout=15)
        except Exception:
            continue
    raise RuntimeError("could not reach AIRDB on 10.0.0.6 with any ODBC driver")


def fetch_employees(cn, n=5):
    cur = cn.cursor()
    cur.execute(f"""
        SELECT TOP {n} e.employee_id, e.employee_name, e.store_id, l.store_name, l.city,
               e.monthly_sales_target
        FROM TS.employee_data e
        JOIN TS.location_master l ON l.store_id = e.store_id
        ORDER BY e.employee_id
    """)
    return [(r.employee_id, r.employee_name, r.store_id, r.store_name, r.city,
             r.monthly_sales_target) for r in cur.fetchall()]


def fetch_qa_expectations(cn):
    """Stable CC data-Q&A ground truth for the competency spot-checks."""
    cur = cn.cursor()
    out = {}
    cur.execute("SELECT MIN(sale_date), MAX(sale_date) FROM TS.sales")
    mn, mx = cur.fetchone()
    out["sales_date_range"] = f"{mn} .. {mx}"
    cur.execute("""
        SELECT TOP 3 s.store_id, l.store_name, SUM(s.total_revenue) AS revenue
        FROM TS.sales s JOIN TS.location_master l ON l.store_id = s.store_id
        WHERE s.sale_date >= '2026-05-01' AND s.sale_date < '2026-06-01'
        GROUP BY s.store_id, l.store_name ORDER BY revenue DESC
    """)
    out["may2026_top_stores"] = [(r.store_id, r.store_name, float(r.revenue)) for r in cur.fetchall()]
    cur.execute("SELECT COUNT(*) FROM TS.employee_data")
    out["employee_count"] = cur.fetchone()[0]
    cur.execute("""
        SELECT COUNT(*) FROM TS.Inventory i
        WHERE i.current_stock <= i.min_stock_threshold
    """)
    out["reorder_candidates"] = cur.fetchone()[0]
    return out


def gen_expense_lines(rng, n):
    start = date(2026, 7, 1)
    lines = []
    for _ in range(n):
        d = start + timedelta(days=rng.randint(0, 11))
        cat = rng.choice(CATEGORIES)
        amount = round(rng.uniform(9.5, 240.0), 2)
        lines.append((d.isoformat(), cat, amount))
    lines.sort()
    return lines


def draw_pdf(path: Path, emp_id, emp_name, store_label, lines, total):
    c = canvas.Canvas(str(path), pagesize=letter)
    w, h = letter
    c.setFillColor(colors.HexColor("#12303c"))
    c.rect(0, h - 1.1 * inch, w, 1.1 * inch, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(0.8 * inch, h - 0.65 * inch, "MERIDIAN FIELD SERVICES")
    c.setFont("Helvetica", 11)
    c.drawString(0.8 * inch, h - 0.9 * inch, "Field Expense Report — July 2026 (period 2026-07)")

    c.setFillColor(colors.black)
    y = h - 1.6 * inch
    c.setFont("Helvetica-Bold", 11)
    c.drawString(0.8 * inch, y, f"Employee ID: {emp_id}")
    c.setFont("Helvetica", 11)
    c.drawString(2.6 * inch, y, f"Name: {emp_name}")
    c.drawString(5.4 * inch, y, f"Store: {store_label}")

    y -= 0.45 * inch
    c.setFont("Helvetica-Bold", 10)
    for x, head in ((0.8, "Date"), (2.2, "Category"), (5.6, "Amount (USD)")):
        c.drawString(x * inch, y, head)
    c.line(0.8 * inch, y - 4, 7.4 * inch, y - 4)
    c.setFont("Helvetica", 10)
    for d, cat, amount in lines:
        y -= 0.28 * inch
        c.drawString(0.8 * inch, y, d)
        c.drawString(2.2 * inch, y, cat)
        c.drawRightString(7.0 * inch, y, f"{amount:,.2f}")
    y -= 0.4 * inch
    c.line(0.8 * inch, y + 8, 7.4 * inch, y + 8)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(2.2 * inch, y - 6, "TOTAL")
    c.drawRightString(7.0 * inch, y - 6, f"{total:,.2f}")
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(0.8 * inch, 0.6 * inch,
                 "Generated test fixture — 08_Automations_Studio human test pack. Not a real expense report.")
    c.save()


def main():
    offline = "--offline" in sys.argv
    rng = random.Random(SEED)
    FIXTURES.mkdir(exist_ok=True)

    qa = None
    if offline:
        employees = OFFLINE_EMPLOYEES
        mode = "OFFLINE (placeholder employees — DB lookups will honestly miss)"
    else:
        cn = connect_airdb()
        employees = fetch_employees(cn)
        qa = fetch_qa_expectations(cn)
        cn.close()
        mode = "ONLINE (employee IDs are real AIRDB rows)"

    key_lines = [
        "# _ANSWER_KEY — 08_Automations_Studio",
        "",
        f"Generator mode: **{mode}**",
        f"Deterministic seed: `{SEED}` (amounts identical on every regeneration)",
        "",
        "## Fixture PDFs → ground truth",
        "",
        "| File | Employee ID | Name | Store | Expense lines | Expense total (USD) | In AIRDB? |",
        "|------|-------------|------|-------|---------------|---------------------|-----------|",
    ]

    grand_total = 0.0
    totals = []
    for emp_id, emp_name, store_id, store_name, city, target in employees:
        lines = gen_expense_lines(rng, rng.randint(4, 8))
        total = round(sum(a for _, _, a in lines), 2)
        grand_total = round(grand_total + total, 2)
        totals.append((emp_id, emp_name, total))
        fname = f"expense_report_{emp_id}.pdf"
        draw_pdf(FIXTURES / fname, emp_id, emp_name, f"{store_name} ({city})"
                 if city != "N/A" else store_name, lines, total)
        key_lines.append(
            f"| {fname} | {emp_id} | {emp_name} | {store_name} (store {store_id}) "
            f"| {len(lines)} | {total:,.2f} | {'no (offline)' if offline else 'YES'} |")

    # the poison fixture — an employee that does NOT exist (honesty path)
    lines = gen_expense_lines(rng, 5)
    poison_total = round(sum(a for _, _, a in lines), 2)
    draw_pdf(FIXTURES / f"expense_report_{POISON[0]}.pdf", POISON[0], POISON[1],
             "(unknown)", lines, poison_total)
    key_lines += [
        f"| expense_report_{POISON[0]}.pdf | {POISON[0]} | {POISON[1]} | — | 5 "
        f"| {poison_total:,.2f} | **NO — must be reported NOT FOUND** |",
        "",
        f"**Grand total of the {len(employees)} valid reports: {grand_total:,.2f} USD** "
        f"(poison report excluded; {round(grand_total + poison_total, 2):,.2f} with it).",
        f"**Highest single expense total:** employee "
        f"{max(totals, key=lambda t: t[2])[0]} ({max(totals, key=lambda t: t[2])[1]}) "
        f"at {max(totals, key=lambda t: t[2])[2]:,.2f}.",
        "",
        "## Expected CSV from the demo automation (expense-audit)",
        "",
        "One row per PDF: employee_id, employee_name, store, expense_total, line_count, db_status.",
        f"- {len(employees)} rows with db_status=FOUND (online mode) + 1 row employee "
        f"{POISON[0]} with db_status=NOT_FOUND",
        "- expense_total values must match the table above to the cent",
        "- the file must ALSO appear on the SFTP server under /outgoing (the runner verifies this "
        "independently — that's the point)",
    ]

    if qa:
        key_lines += [
            "",
            "## CC data-Q&A competency expectations (live AIRDB, computed at generation time)",
            "",
            f"- Sales data date range: {qa['sales_date_range']}",
            f"- Employee count (TS.employee_data): **{qa['employee_count']}**",
            f"- Reorder candidates (stock ≤ threshold): **{qa['reorder_candidates']}**",
            "- Top 3 stores by revenue, May 2026 (sale_date in [2026-05-01, 2026-06-01)):",
        ]
        for sid, sname, rev in qa["may2026_top_stores"]:
            key_lines.append(f"    - store {sid} ({sname}): {rev:,.2f}")
        key_lines.append("")
        key_lines.append("CC's answers should match these within rounding; a confidently different "
                         "number is a competency failure (grounding).")
    else:
        key_lines += [
            "",
            "## CC data-Q&A competency expectations",
            "",
            "NOT AVAILABLE — generated offline. Re-run `make_fixtures.py` on the 10.0.0.6 network "
            "to fill this section with live values.",
        ]

    (HERE / "_ANSWER_KEY.md").write_text("\n".join(key_lines), encoding="utf-8")
    print(f"mode: {mode}")
    print(f"wrote {len(employees) + 1} PDFs to {FIXTURES}")
    print(f"wrote {HERE / '_ANSWER_KEY.md'}")


if __name__ == "__main__":
    main()
