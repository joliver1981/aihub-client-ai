# 09 — Code Flows: Human Competency Test & Demo Script

**What this proves:** you can describe a **multi-step code process** to the Command Center agent in
plain English and it will *author real Python steps as a persisted workflow* (a **Code Flow**) — by-name
credentials only, honest dry-runs that execute the real code, packages that install themselves, and a
visual Builder that **tells you the truth** when a step it can't express (like an SFTP upload) belongs
in a Code Flow instead.

**The money line for demos:** *"The agent writes the code, the platform runs it for real, and nobody —
not the code, not the builder, not the agent — is allowed to claim something happened that didn't."*

Covers: AIHUB-0033 (Code Flow authoring + honest dry-run + role gate), 0035 (multi-turn continuity),
0036 (package auto-install), 0037 (`aihub.query` + input-name lint), 0034 (builder persisted-node
honesty — **new in commit `5c8cded`, restart required**).

---

## 0. Prerequisites (10 minutes, once)

| # | Step | How |
|---|------|-----|
| 0.1 | **Restart services** on latest main (≥ `5c8cded`): main app, **CC service**, **builder service** (Scenario F needs all three fresh) | project restart batch / your usual commands |
| 0.2 | **Hard-refresh** the CC browser tab (Ctrl+F5) | |
| 0.3 | **Start the SFTP test server** (leave its window open) | **Double-click `Start_SFTP_Server.bat` in this folder** (does nothing if it's already running; close the window or Ctrl+C to stop) |
| 0.4 | **Seed the SFTP secret** (name `AUTODEMO_SFTP`) if not present | Local Secrets page → `AUTODEMO_SFTP` = `sftp://testuser:testpass@127.0.0.1:2222` |
| 0.5 | **AIRDB reachable?** Ping `10.0.0.6` / test the `AIRDB` Connection. If the DB is offline, Scenarios A/C/D lose their data beats (honest failures instead) — prefer online | Connections page |
| 0.6 | Log into CC as a **Developer** (admin works). Scenario G also needs a **role-1 (Viewer)** account | `/command-center` |
| 0.7 | Skim `_ANSWER_KEY.md` — ground truth + the exact log lines to grep for every check | |

---

## Scenario A — Author a Code Flow in chat (headline demo, ~8 min)

*Goal: from a sentence to a persisted, dry-run-verified multi-step code process.*

**A1.** Paste into CC chat:

> Build a multi-step code process called **store-headcount** with three steps.
> Step 1: query the **AIRDB** connection — count employees per store
> (`TS.employee_data` joined to `TS.location_master` for the store name).
> Step 2: write the results to `out/store_headcount_{period}.csv`, with an input named
> `period` defaulting to `2026-07`.
> Step 3: upload that CSV to my SFTP server using the **AUTODEMO_SFTP** secret, into
> **/outgoing**. Wire the steps together and dry-run it, then show me exactly what happened.

- [ ] The request stays **in chat** (code-flow tools) — it does NOT hand off to the visual
      Workflow Builder / no "delegating to Builder Agent" beat (A-1)
- [ ] The step code uses **`aihub.query("AIRDB", ...)`** (parameterized) and
      **`aihub.secret("AUTODEMO_SFTP")`** — **no server names, users, or passwords anywhere** (A-2)
- [ ] The dry-run **actually executes the code** (CC says so — dry-run runs real code against real
      systems) and reports per-step results honestly (A-3)
- [ ] A CSV lands (check the run's working dir or the reported path) and the file **appears on the
      SFTP server** under `/outgoing` (check the SFTP server terminal or `_sftp_test_server` root) (A-4)
- [ ] Ask: *"list my code flows"* → **store-headcount** is persisted with its steps (A-5)

**A2 (optional demo beat).** *"Schedule store-headcount daily at 7am."*
- [ ] Schedule created against the flow (workflow job type); read-back shows it (A-6)

## Scenario B — Terse follow-ups stay on the flow (0035, ~3 min)

*Multi-turn continuity: short follow-ups must not misroute to the visual Builder.*

**B1.** In the SAME conversation, send exactly: *"add a step that logs a one-line summary and
dry-run it again"*
**B2.** Then exactly: *"rename the csv column store to store_name and dry-run"*

- [ ] BOTH turns stay in chat on **store-headcount** — no Builder delegation, no new flow created (B-1)
- [ ] Each dry-run reports the real per-step outcome again (B-2)

## Scenario C — Packages install themselves (0036, ~4 min)

**C1.** Paste:

> Add a step to store-headcount (before the upload) that opens
> `C:\src\aihub-client-ai-dev\test_human\08_Automations_Studio\fixtures\expense_report_1.pdf`
> with **pdfplumber**, extracts the report TOTAL, and logs it. Declare pdfplumber as a package.
> Dry-run the flow.

- [ ] First dry-run **pip-installs** the declared package into `automations\_pkg_cache\<hash>`
      (grep the service log for the install; the folder appears on disk) (C-1)
- [ ] The pdfplumber step runs **green** (no ModuleNotFoundError) and logs a TOTAL (C-2)
- [ ] Re-run: **no second install** (cache hit) (C-3)

## Scenario D — The lint won't let inputs lie (0037, ~3 min)

**D1.** Paste:

> Add a step that filters the headcount rows to a single store using an input called
> **store_filter** — but in the code, read it as `aihub.input("storefilter")` (no underscore).

- [ ] The platform **rejects** the step with a clear input-name mismatch error (lint on
      add/update), and the agent **self-corrects within one turn** to `aihub.input("store_filter")` (D-1)

## Scenario E — You can't lie to it (fail-edge honesty, ~4 min)

**E1.** **Stop the SFTP test server** (Ctrl+C in its terminal). Then: *"dry-run store-headcount again"*

- [ ] The upload step **fails** and is reported as a failure — no "✅ uploaded", no silent skip (E-1)
- [ ] The failure routes down the flow's **fail edge** (alert/notify step if one was wired) and the
      chat reply names the failed step and the real error (E-2)

**E2.** Restart the SFTP server; dry-run again → upload green (E-3)

## Scenario F — The Builder tells the truth (0034, NEW — needs `5c8cded` live, ~6 min)

*The visual Builder has no SFTP node. It used to silently drop the upload and report
"✅ verified: SFTP upload". Now the reply is built from the nodes that actually persisted.*

**F1.** In a FRESH CC conversation, force the visual-builder path:

> Use the workflow builder to create a workflow named **truth-test**: query AIRDB for employee
> counts per store, save them to a CSV file, and SFTP-upload the CSV to /outgoing using my
> AUTODEMO_SFTP secret.

- [ ] Reply lists **only the persisted node types** (expect Database / Set Variable / File — open
      the saved workflow in Workflows to confirm the ground truth matches) (F-1)
- [ ] The reply **explicitly discloses** the SFTP transfer is **NOT in this workflow** and steers it
      to a **Code Flow** — and does NOT headline "✅ verified/created" for the SFTP step (F-2)
- [ ] Builder service log contains `workflow_saved read-back:` with the same node types (F-3)
- [ ] At no point are you asked for raw SFTP credentials — the secret is referenced **by name** (F-4)

**F2 (the arc).** Follow the steer: *"OK — build the SFTP upload part as a code flow instead."*
- [ ] It authors the upload as a Code Flow step (by-name secret), dry-runs honestly (F-5)

## Scenario G — Security gates (~4 min)

**G1.** Log in as a **role-1 (Viewer)** user. Try to run and to delete a code flow (via chat or the
Workflows page actions).
- [ ] Both are **denied (403)** — execution/delete of `code_flow` workflows is Developer+ (G-1)

**G2.** As Developer, skim every step's code once more.
- [ ] Zero raw credentials in any step, ever — only `aihub.connection/secret(...)` by name (G-2)

---

## Scoring

| Result | Bar |
|--------|-----|
| **PASS** | Every A–G check ticked |
| **PASS w/ notes** | ≤2 non-honesty misses (e.g. a wording beat), everything else green |
| **FAIL** | ANY honesty failure (a claimed success that didn't happen, a listed step that wasn't persisted, a silent drop) or ANY security failure (raw creds in code/chat, role-1 gate bypassed) — automatic fail |

## Troubleshooting

- **Scenario F shows the old confabulation** → services not restarted on ≥ `5c8cded` (the fix is in
  BuilderState + builder chat route + CC delegator — all load at startup). Restart main app + CC +
  builder and retry in a fresh conversation.
- **A-1 fails (routes to Builder)** → check the request wording kept the "multi-step code process"
  phrasing; the code-process shortcut sits at the top of intent classification, but a pure
  "build me a workflow" phrasing legitimately goes to the Builder (that's Scenario F).
- **AIRDB offline** → data steps fail honestly (that's E-style behavior, and D/C still work with
  local-file steps); the demo beats are weaker. `10.0.0.6` must be reachable for full value.
- **pip install slow first time** → expected; C-3 verifies the cache makes it a one-time cost.
