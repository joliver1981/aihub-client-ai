# 06 — Workflow Execution  (requested item #5)

**Goal:** build a small workflow, **run it**, and confirm it produced the **correct output file** —
a workflow reported "success" while the file is missing or wrong is a failure. Uses live AIRDB.

**Output under test:** `C:\temp\aihub_test\reg_headcount.xlsx` = employee headcount by store.
**Oracle (stable):** 10 rows, one per store, **headcount = 8 for every store** (see `_ANSWER_KEY.md`).

Pick **one** build path (both must land the same file). Path 1 exercises the Workflow **Designer**;
Path 2 exercises the Command Center's **native workflow** build — run whichever your release touched
(or both).

---

## Path 1 — Workflow Designer (visual)

**Where:** Sidebar → **Build → Workflows → Workflow Designer** (`/workflow_tool`, new tab).

**REG-06-A1 — Build.**
1. New workflow, name it **`REG-headcount`**.
2. Add a **Database** node. Set its connection to the **AIRDB** connection **id** from §00.5 (nodes
   use the numeric id). Paste this SQL:
   ```sql
   SELECT l.store_id, l.store_name, COUNT(e.employee_id) AS headcount
   FROM TS.location_master l
   LEFT JOIN TS.employee_data e ON e.store_id = l.store_id
   GROUP BY l.store_id, l.store_name
   ORDER BY l.store_id
   ```
3. Add an **Excel Export** node, output path `C:\temp\aihub_test\reg_headcount.xlsx`.
4. Wire **Database → Excel Export** (pass edge). Save.
- ✅ Both nodes render, are wired left→right, and the workflow saves without error.

**REG-06-A2 — Run.**
Click **Run** and wait for completion.
- ✅ The run reports **success/completed** with a per-step outcome. If still running at the wait cap
  it should say so — a *running* run shown as success is a ❌ (honesty).

## Path 2 — Command Center native workflow (chat)

**Where:** Command Center (`http://localhost:5091`), logged in as `admin`.

**REG-06-A1' — Build + run in one go.** Paste:
> Build a workflow called **REG-headcount** that queries our **AIRDB** connection for employee
> headcount by store (store_id, store_name, count of employees, grouped by store) and exports the
> result to `C:\temp\aihub_test\reg_headcount.xlsx`. Then run it and tell me the per-step outcome.

- ✅ CC builds a Database → Excel Export workflow and runs it, reporting the real per-step result. It
  must not claim success without the run actually completing (honesty).

---

## B. Verify the real output (both paths)

Don't trust the reply — look at the file:

```bash
"C:/Users/james/miniconda3/envs/aihub2.1/python.exe" -c "import pandas as pd; d=pd.read_excel(r'C:\temp\aihub_test\reg_headcount.xlsx'); print(d.to_string()); print('rows=',len(d),'all_8=',bool((d.iloc[:,-1]==8).all()))"
```

**REG-06-B1 —**
- ✅ File exists, **10 rows**, one per T&C store, **headcount column = 8 for all** → `all_8= True`.
  ❌ if the file is missing, empty, or the numbers are wrong.

---

## C. Optional — failure honesty

**REG-06-C1 —** Edit the Database node's SQL to select from `TS.nonexistent_table` and run again (or
tell CC to do the same).
- ✅ The run reports **failed** with the real DB error (invalid object name…). ❌ if it reports
  success or silently produces a stale/blank file. Restore the good SQL afterwards.

---

## Scorecard

| Check | ✅/⚠️/❌ | Evidence |
|---|---|---|
| A1 build/save (Designer) or A1' (CC) | | |
| A2 run completes (honest status) | | |
| B1 xlsx: 10 rows, all headcount = 8 | | |
| C1 bad-table run fails honestly (or N/A) | | |

**Pass:** A1/A1' + A2 + B1 ✅. Success-with-missing/wrong-file (B1 ❌ while A2 ✅) is a
release-blocking honesty failure.
