# Pack 25 — The Agent: competency 2 (reconciliation, traps, injection)

Fixture pack for the second OpenClaw tester brief,
[`docs/openclaw-tester-brief-2.md`](../../docs/openclaw-tester-brief-2.md) — scenarios **TA-13 … TA-26**.
Pack 21 covers the first five judgment scenarios and the first brief covers TA-01 … TA-12; this pack
goes after reconciliation across two sources of truth, units/hidden-sheet/draft-document traps, vision
and OCR, and prompt injection in both a PDF and a database row.

```
_scripts\make_fixtures.py     deterministic generator (reportlab + openpyxl + Pillow)
_fixtures\
  ap_batch\        5 vendor-invoice PDFs mirroring ERPDB CG_VendorInvoices rows      TA-13
  remittance\      customer remittance CSV with planted discrepancies                TA-14
  excel\           3-sheet targets workbook: $000s header, hidden sheet, TOTAL row   TA-15
  messy\           dirty POS export: banner, 4 date/money formats, dup rows, footer TA-16
  contracts\       MSA + executed Amendment 1 + unsigned DRAFT Amendment 2            TA-17
  images\          receipt photo PNG (vision) + image-only PDF (OCR)                  TA-18
  injection\       vendor memo with visible + hidden AI instructions                  TA-19
  _ANSWER_KEY.md   the grading oracle — closed until grading
```

Regenerate (idempotent, same bytes every run):

```
C:\src\aihub-apps\.venv\Scripts\python.exe test_human\25_The_Agent_Competency_2\_scripts\make_fixtures.py
```

Live-database facts in the key were verified on **2026-09-04**. AR aging drifts daily — the brief's
§2.3 oracle re-derives it. TA-20 and TA-21 rely on rows that already exist in ERPDB/AIRDB
(`CG_CollectionActivity` row 1053, the $200 GL imbalance, the `sale_id` collisions); the generator
does not touch the databases.
