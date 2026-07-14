# _ANSWER_KEY — 08_Automations_Studio

Generator mode: **OFFLINE (placeholder employees — DB lookups will honestly miss)**
Deterministic seed: `20260713` (amounts identical on every regeneration)

## Fixture PDFs → ground truth

| File | Employee ID | Name | Store | Expense lines | Expense total (USD) | In AIRDB? |
|------|-------------|------|-------|---------------|---------------------|-----------|
| expense_report_101.pdf | 101 | Offline Placeholder A | Store One (store 1) | 6 | 834.60 | no (offline) |
| expense_report_102.pdf | 102 | Offline Placeholder B | Store One (store 1) | 6 | 1,140.44 | no (offline) |
| expense_report_103.pdf | 103 | Offline Placeholder C | Store Two (store 2) | 8 | 790.13 | no (offline) |
| expense_report_104.pdf | 104 | Offline Placeholder D | Store Two (store 2) | 8 | 940.68 | no (offline) |
| expense_report_105.pdf | 105 | Offline Placeholder E | Store Three (store 3) | 6 | 616.36 | no (offline) |
| expense_report_99999.pdf | 99999 | Alex Unknown | — | 5 | 679.40 | **NO — must be reported NOT FOUND** |

**Grand total of the 5 valid reports: 4,322.21 USD** (poison report excluded; 5,001.61 with it).
**Highest single expense total:** employee 102 (Offline Placeholder B) at 1,140.44.

## Expected CSV from the demo automation (expense-audit)

One row per PDF: employee_id, employee_name, store, expense_total, line_count, db_status.
- 5 rows with db_status=FOUND (online mode) + 1 row employee 99999 with db_status=NOT_FOUND
- expense_total values must match the table above to the cent
- the file must ALSO appear on the SFTP server under /outgoing (the runner verifies this independently — that's the point)

## CC data-Q&A competency expectations

NOT AVAILABLE — generated offline. Re-run `make_fixtures.py` on the 10.0.0.6 network to fill this section with live values.