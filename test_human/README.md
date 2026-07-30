# test_human/ — Human-runnable AI Hub test suite

A complete one-day test plan a human tester can execute against the AI Hub UI. All material is framed around a fictional omni-channel retail / wholesale / ecommerce business — **Northwind Outdoor Co.**

## Start here

**Open `MASTER_TEST_PLAN.docx` in the root of this folder.** It contains everything: prerequisites, run order, per-section instructions, scoring rubric, troubleshooting tips, and a cleanup checklist.

## Folder map

```
test_human/
├── MASTER_TEST_PLAN.docx       ← THE master document. Read this first.
├── README.md                   ← This file.
│
├── 01_Finance/
│   ├── fixtures/
│   │   ├── F1_monthly_sales_by_region.xlsx       (60-row sales sheet)
│   │   ├── F2_Q3_PnL_statement.pdf               (multi-page P&L, no repeating header)
│   │   └── F3_vendor_payment_terms.docx          (10-vendor reference)
│   └── test_plan.md                              (23 questions)
│
├── 02_Operations/
│   ├── fixtures/
│   │   ├── O1_inventory_turnover.xlsx            (3 tabs, 36 SKUs × 3 quarters)
│   │   ├── O2_carrier_manifest.pdf               (90 shipments, 5 pages)
│   │   └── O3_returns_SOP.docx                   (returns SOP)
│   └── test_plan.md                              (23 questions)
│
├── 03_IT/
│   ├── fixtures/
│   │   ├── I1_asset_inventory.xlsx               (70 assets, 5 sites)
│   │   ├── I2_security_audit.pdf                 (24 findings, multi-page table)
│   │   └── I3_integration_runbook.docx           (ERP-Shopify runbook)
│   └── test_plan.md                              (24 questions)
│
├── 04_Planning/
│   ├── fixtures/
│   │   ├── P1_demand_forecast.xlsx               (12-month forecast)
│   │   ├── P2_annual_SOP.pdf                     (S&OP plan, multi-page table)
│   │   └── P3_capacity_policy.docx               (capacity/replenishment policy)
│   └── test_plan.md                              (28 questions)
│
├── 05_Data_Assistant/
│   └── data_assistant_questions.md               (NL→SQL test set, 20 questions)
│
├── 06_Workflow/
│   └── workflow_scenario.md                      (4 build-and-run scenarios)
│
├── 07_Smoke_Tests/
│   └── compliance_integrations_mcp_smoke.md      (CRUD smoke on remaining modules)
│
├── 11_Regression_Suite/                          ← PRE-RELEASE regression pass (UI, human/agent)
│   ├── README.md                                 (start here — 9 basic features, ~2h)
│   ├── 00_Setup … 10_Extras_Smoke.md             (one file per feature, exact prompts + expected values)
│   ├── _ANSWER_KEY.md                            (live-DB + fixture ground truth)
│   ├── TEST_RUN_TEMPLATE.md                       (copy per run)
│   └── fixtures/                                  (self-contained artifacts)
│
├── 13_Document_Competency/                       ← DOCUMENT engine competency (ingestion + retrieval)
│   ├── README.md                                 (start here — phases A/B/C, automated runner)
│   ├── battery.py                                (single source of truth: fixtures, questions, grading)
│   ├── run_battery.py                            (upload → wait → question → grade → report)
│   ├── make_fixtures.py / fixtures/              (real lease corpus incl. flattened amendments)
│   └── _ANSWER_KEY.md                            (derived — regenerate via gen_answer_key.py)
│
├── 14_Workflow_Node_Matrix/                      ← WORKFLOW ENGINE node regression matrix (runner)
│   ├── README.md                                 (start here — per-node/pairing checks, tiers)
│   ├── runner.py                                 (build→run→verify each node via the real engine)
│   ├── UI_SPOT_CHECKS.md                         (browser-only legs: Designer/Monitor/CC lint)
│   ├── REPORT_LATEST.md / results_history/       (baseline-diffed reports — PASS→FAIL = REGRESSION)
│   └── fixtures/                                 (folder_probe files)
│
└── _scripts/
    ├── generate_fixtures.py                      (regenerates all 12 fixtures)
    ├── generate_master_doc.py                    (regenerates MASTER_TEST_PLAN.docx)
    └── verify_facts.py                           (audits fingerprint facts)
```

## Quick stats

| Section | Questions / Scenarios | Approx. time |
|---|---:|---|
| Finance | 23 | 45 min |
| Operations | 23 | 45 min |
| IT | 24 | 45 min |
| Planning | 28 | 45 min |
| Data Assistant (NL→SQL) | 20 | 45 min |
| Workflow Builder | 4 scenarios | 60 min |
| Smoke tests (Compliance / Integrations / MCP) | 4 modules | 30 min |
| **Total** | **122 + 4** | **~5–6 hours** |

## Regenerating fixtures

If you change the generator scripts and want fresh fixtures:

```powershell
$PY = "$env:USERPROFILE\miniconda3\envs\aihub2.1\python.exe"
& $PY _scripts\generate_fixtures.py
& $PY _scripts\generate_master_doc.py
```
