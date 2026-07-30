# _ANSWER_KEY — 11_Regression_Suite

Ground truth for every check. **Live-DB facts** were captured from `10.0.0.6` on **2026-07-22**;
structural facts (store/employee counts, store names) are stable, and **closed-month** figures
(May 2026) are stable. The **current-day sales total is NOT pinned** here because `TS.sales` grows
daily — tests use structural/closed-month facts on purpose. The **live database is the final word**;
re-run the SQL below to confirm a value hasn't drifted.

---

## Live AIRDB facts (retail — used by §03, §06, §10)

| Fact | Value |
|---|---|
| Store count | **10** |
| Store names | T&C **Manhattan, Brooklyn, Chicago, Dallas, Houston, Atlanta, Miami, Denver, Seattle, Los Angeles** (all USA) |
| Employees total | **80** |
| Employees per store | **8** (every store) |
| Highest-revenue store, **May 2026** | **T&C Chicago — $800,476.86** (then Dallas $776,695.26, Brooklyn $758,149.42) |
| Total revenue, **May 2026** | **$6,665,039.95** |
| Non-US stores | **0** |
| Reorder candidates (stock ≤ threshold) | **0** |

> **Multiple AIRDB copies exist on `10.0.0.6`** — pin which one your data assistant uses (§03). The
> stock **"AIRDB Agent Demo"** assistant targets **AIRDB2**, whose facts differ (verified 2026-07-23):
> **15 stores** (Central Plaza, Southpoint Center, Hillside Mall, …), **75 employees** (5/store), all
> USA, top **May 2026** store **Central Plaza = $14,856,534.46**. The canonical **AIRDB** row above
> (10 stores / 80 employees / T&C names) applies only to assistants wired to `AIRDB`.

## Live ERPDB facts (finance — reference for §10 / ad-hoc)

| Fact | Value |
|---|---|
| Vendors (`dbo.LFA1`) | **5** |
| Invoices | **17** total — **9 Paid** ($2,355,212.14), **8 Open** ($121,625.50) |
| Open invoices (`amount_due <> 0`) | **8** |

---

## Fixture: `Q3_PnL_statement.pdf` (multi-page P&L — §04)

**Unambiguous, gradeable figures** (verified against the actual PDF; consistent across the document):

| Question | Answer | Where |
|---|---|---|
| Q3 FY2025 net revenue | **$12,840,200** | exec summary + table (page 1) |
| Total COGS | **$7,959,400** | table (page 2) |
| Gross profit | **$4,880,800** (margin **38.0%** per table) | table (page 2) |
| Total operating expenses (OpEx) | **$3,566,600** | table (**page 3** — good multi-page probe) |
| One-time inventory write-down | **$180,000**, in **August**, SKUs **SLP-1100** & **SLP-1102** | narrative + note 1 |
| Effective tax rate | **24.6%** | consistent everywhere |
| Highest revenue channel (net sales) | **Wholesale — $4,871,000** (Ecommerce $4,649,000, Retail $3,643,000) | table (page 1) |

> ⚠ **Do NOT grade on "net income" or "EBITDA" for this fixture.** The exec-summary **prose** and the
> detail **table** deliberately disagree: prose says EBITDA **$2,235,200 (17.4%)** / net income
> **$1,684,400**; the table computes EBITDA **$1,314,200 (10.2%)** and pre-tax **$984,400** − tax
> **$242,100** = net income **$742,300**. Both are "in the document," so either is defensible — a
> flaky check. Use the unambiguous rows above instead. (The "Ecommerce = 38% of revenue" claim is
> likewise prose-only; the table works out to ~36.2%.)

## Fixture: expense report PDFs (§04, §07)

Seed-deterministic; amounts identical on every regeneration. All 5 valid employees are at
**T&C Manhattan (store 1)** and are real AIRDB rows.

| File | Emp ID | Name | Expense total | In AIRDB? |
|---|---|---|---|---|
| expense_report_1.pdf | 1 | Alex Miller | **$834.60** | YES |
| expense_report_2.pdf | 2 | Drew Johnson | **$1,140.44** | YES |
| expense_report_3.pdf | 3 | Skyler Miller | **$790.13** | YES |
| expense_report_4.pdf | 4 | Jamie Johnson | **$940.68** | YES |
| expense_report_5.pdf | 5 | Quinn Miller | **$616.36** | YES |
| expense_report_99999.pdf | 99999 | Alex Unknown | $679.40 | **NO — must report NOT_FOUND** |

- **Sum of the 5 valid reports: $4,322.21** (poison report excluded).
- **Highest single expense total: employee 2 (Drew Johnson) — $1,140.44.**

## Fixture: `vendor_payment_terms.docx` (§05)

| Question | Answer |
|---|---|
| Longest payment terms | **Acme Textiles — Net 90** |
| Highest early-pay discount | **Cascade Down — 3.5% / 10** |
| Single-source vendor + supply | **Pacific Zipper Co. — zippers & sliders** |
| Non-USD vendors | **Alpenwerk GmbH (EUR)** and **Mountain Films Ltd. (GBP)** |
| Escalation contact | **Reilly Bauer, VP Finance** |
| Total vendors | **10** |

## Fixture: `daily_sales_sample.csv` (§08)

14 rows (2 stores × 7 days).

| Fact | Value |
|---|---|
| Total revenue | **$53,100.00** |
| Total units | **1,770** |
| Manhattan | **1,000 units / $30,000.00** |
| Brooklyn | **770 units / $23,100.00** |
| Highest single day | **2026-06-05, Manhattan, $6,000.00** |
| Average daily revenue (all 14 rows) | **$3,792.86** |

---

## Re-verify live values (read-only)

Known-good interpreter with `pyodbc` + ODBC 17: `C:\src\aihub-apps\.venv\Scripts\python.exe`.

```python
import pyodbc
def cn(db="AIRDB"):
    return pyodbc.connect(
        "DRIVER={ODBC Driver 17 for SQL Server};SERVER=10.0.0.6;"
        f"DATABASE={db};UID=ai_user;PWD=Bradynov11;TrustServerCertificate=yes", timeout=15)
c = cn().cursor()
c.execute("SELECT COUNT(*) FROM TS.location_master");            print("stores", c.fetchone()[0])          # 10
c.execute("SELECT COUNT(*) FROM TS.employee_data");             print("employees", c.fetchone()[0])         # 80
c.execute("""SELECT TOP 1 l.store_name, SUM(s.total_revenue)
             FROM TS.sales s JOIN TS.location_master l ON l.store_id=s.store_id
             WHERE s.sale_date>='2026-05-01' AND s.sale_date<'2026-06-01'
             GROUP BY l.store_name ORDER BY 2 DESC""")
print("top May-2026 store", c.fetchone())                        # ('T&C Chicago', 800476.86)
```

Headcount-by-store SQL (the §06 workflow output — expect 10 rows, headcount 8 each):
```sql
SELECT l.store_id, l.store_name, COUNT(e.employee_id) AS headcount
FROM TS.location_master l
LEFT JOIN TS.employee_data e ON e.store_id = l.store_id
GROUP BY l.store_id, l.store_name
ORDER BY l.store_id;
```

> If the connection errors, you're not on the `10.0.0.6` network / the DB is down — the UI's data
> features will fail the same way. Fix that before scoring §03/§06/§10.
