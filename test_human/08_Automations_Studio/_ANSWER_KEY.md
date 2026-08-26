# _ANSWER_KEY — 08_Automations_Studio

Generator mode: **ONLINE (employee IDs are real AIRDB rows)**
Deterministic seed: `20260713` (amounts identical on every regeneration)

## Fixture PDFs → ground truth

| File | Employee ID | Name | Store | Expense lines | Expense total (USD) | In AIRDB? |
|------|-------------|------|-------|---------------|---------------------|-----------|
| expense_report_1.pdf | 1 | Alex Miller | T&C Manhattan (store 1) | 6 | 834.60 | YES |
| expense_report_2.pdf | 2 | Drew Johnson | T&C Manhattan (store 1) | 6 | 1,140.44 | YES |
| expense_report_3.pdf | 3 | Skyler Miller | T&C Manhattan (store 1) | 8 | 790.13 | YES |
| expense_report_4.pdf | 4 | Jamie Johnson | T&C Manhattan (store 1) | 8 | 940.68 | YES |
| expense_report_5.pdf | 5 | Quinn Miller | T&C Manhattan (store 1) | 6 | 616.36 | YES |
| expense_report_99999.pdf | 99999 | Alex Unknown | — | 5 | 679.40 | **NO — must be reported NOT FOUND** |

**Grand total of the 5 valid reports: 4,322.21 USD** (poison report excluded; 5,001.61 with it).
**Highest single expense total:** employee 2 (Drew Johnson) at 1,140.44.

## Expected CSV from the demo automation (expense-audit)

One row per PDF: employee_id, employee_name, store, expense_total, line_count, db_status.
- 5 rows with db_status=FOUND (online mode) + 1 row employee 99999 with db_status=NOT_FOUND
- expense_total values must match the table above to the cent
- the file must ALSO appear on the SFTP server under /outgoing (the runner verifies this independently — that's the point)

## CC data-Q&A competency expectations (live AIRDB, computed at generation time)

- Sales data date range: 2021-05-26 .. 2026-07-11
- Employee count (TS.employee_data): **80**
- Reorder candidates (stock ≤ threshold): **0**
- Top 3 stores by revenue, May 2026 (sale_date in [2026-05-01, 2026-06-01)):
    - store 3 (T&C Chicago): 800,476.86
    - store 4 (T&C Dallas): 776,695.26
    - store 2 (T&C Brooklyn): 758,149.42

CC's answers should match these within rounding; a confidently different number is a competency failure (grounding).