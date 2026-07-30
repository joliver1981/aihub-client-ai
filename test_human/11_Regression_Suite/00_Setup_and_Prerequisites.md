# 00 — Setup & Prerequisites

Do this once before a regression run. ~10 minutes. Tick each box; if one can't be met, note it in the
run report — several sections depend on it.

---

## 0.1 Services running on the build under test

Restart all services so you're testing the exact commit you intend to release:

```
shortcuts\00_Start-Restart_AIHub_Services_V3.bat
```

- [ ] **REG-00-1** Main app answers at `http://localhost:5001` (login page loads).
- [ ] **REG-00-2** Command Center answers at `http://localhost:5091`.
- [ ] **REG-00-3** Scheduler service (`AIHubJobScheduler` / jss) is running (needed for §06/§07/§10 scheduling).

> Record the commit under test in the run report: `git rev-parse --short HEAD`.

## 0.2 Logins

| Login | Password | Role | Used by |
|---|---|---|---|
| `admin` | `admin` | Developer (3) | most sections |
| `test` | (your test pw) | User (1) | role-gate checks (§07-F, §10) |

- [ ] **REG-00-4** Can log in as `admin`.

## 0.3 Database connection on the platform

Sections 03/06/07/10 need an **AIRDB** connection registered in the app.

1. Sidebar → **Build → Data & Connections → Database Connections** (`/connections`).
2. If no connection named **AIRDB** exists, create one:
   - Type: **SQL Server / ODBC**
   - Server `10.0.0.6`, Database `AIRDB`, User `ai_user`, Password `Bradynov11`
   - (`TrustServerCertificate=yes` if the form exposes it)
3. **Test** the connection in the form → success.

- [ ] **REG-00-5** A working **AIRDB** connection exists. Note its **numeric id** (visible on the
      Connections page) — workflow **Database** nodes reference the id, not the name: `AIRDB id = ____`.

## 0.4 A Data Assistant bound to AIRDB (for Data Explorer §03 and §10)

Data Explorer and the Data Assistant chat drive their queries through a **Data Assistant / data agent**.

1. Sidebar → **Build → Agent Builders → Data Assistant Builder** (`/custom_data_agent`).
2. If none exists for AIRDB, create one named **`REG-Data-AIRDB`** pointed at the AIRDB connection,
   granting the `TS` retail tables (sales, product_master, location_master, employee_data, Inventory).
3. Save.

- [ ] **REG-00-6** A Data Assistant on AIRDB exists and appears in the Data Explorer agent dropdown.

## 0.5 SFTP test server + secret (for §07 automation upload and §10 transfer)

1. Start the local SFTP server (leave it running in its own terminal):
   ```
   cd test_human\_sftp_test_server
   & C:\Users\james\miniconda3\envs\testftp\python.exe run_all.py
   ```
2. Sidebar → **Build → Data & Connections → Local Secrets** (`/local_secrets` / "Local Secrets").
   Add secret **`AUTODEMO_SFTP`** = `sftp://testuser:testpass@127.0.0.1:2222`.

- [ ] **REG-00-7** SFTP server listening on `127.0.0.1:2222`.
- [ ] **REG-00-8** Secret `AUTODEMO_SFTP` exists (value is the `sftp://user:pass@host:port` URL).

## 0.6 Scratch output folder

- [ ] **REG-00-9** `C:\temp\aihub_test\` exists (create it): `mkdir C:\temp\aihub_test`.

## 0.7 Fixtures (already bundled — nothing to generate)

Everything this pack needs is in [`fixtures/`](fixtures/) and is **self-contained**:

| Fixture | Used by | Ground truth |
|---|---|---|
| `expense_report_1..5.pdf`, `expense_report_99999.pdf` | §04 doc processing, §07 automation | `_ANSWER_KEY.md` (per-PDF totals, seed-deterministic) |
| `Q3_PnL_statement.pdf` | §04 doc processing (multi-page PDF) | `_ANSWER_KEY.md` |
| `vendor_payment_terms.docx` | §05 agent knowledge upload | `_ANSWER_KEY.md` |
| `daily_sales_sample.csv` | §08 artifacts / code interpreter | `_ANSWER_KEY.md` |

- [ ] **REG-00-10** `fixtures/` contains the 9 files above.

---

## Quick pre-flight (optional, agent-friendly)

Confirm the live DB oracle is reachable (values in `_ANSWER_KEY.md` are computed from it):

```bash
"C:/src/aihub-apps/.venv/Scripts/python.exe" -c "import pyodbc;c=pyodbc.connect('DRIVER={ODBC Driver 17 for SQL Server};SERVER=10.0.0.6;DATABASE=AIRDB;UID=ai_user;PWD=Bradynov11;TrustServerCertificate=yes',timeout=10);cur=c.cursor();cur.execute('SELECT COUNT(*) FROM TS.location_master');print('stores=',cur.fetchone()[0])"
```

Expected: `stores= 10`. A connection error means "not on the 10.0.0.6 network / DB down," not a bad
recipe — the UI's data features will fail the same way, so fix this first.

---

**Prereqs done?** Proceed to [01_All_Pages_Open.md](01_All_Pages_Open.md).
