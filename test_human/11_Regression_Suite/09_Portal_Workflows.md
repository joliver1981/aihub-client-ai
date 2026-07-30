# 09 — Portal Workflows  (requested item #9)

**Goal:** the Portal Workflows builder loads, you can **author + save** a portal workflow (it
persists and reloads), the step palette and portal picker work, and a **Test run** either executes
against a reachable portal or **fails honestly** — never fakes a success.

**Where:** Sidebar → **Work → Portal Workflows** (`/portal-workflows`). Log in as `admin`.

**About this feature:** Portal Workflows drive a real browser to fetch from / act on an external web
**portal** (e.g. "log in and download the latest invoice"). Two modes: an **Auto-mode** natural-
language *Goal*, and explicit **steps** (deterministic + woven LLM steps). A workflow links to a
registered **Portal** for credentials.

> **Run dependencies:** a live **Test run** needs (a) the **Browser Use** service running (:5101) and
> (b) at least one registered **Portal** with credentials + a reachable target. If your environment
> has neither, do the **build/save/list** core (A) and the **honest-attempt** check (C-2); mark the
> live-fetch check (C-1) **N/A (no portal configured)**. Don't invent a portal just to force a pass.

---

## A. Build + persist (core — always testable)

**REG-09-A1 — Author.** On `/portal-workflows`:
1. **Name:** `REG-Portal-Test`.
2. **Goal:** `Log in and download the most recent invoice`.
3. **Add step** at least once (e.g. a **Navigate**/**Download** step, or a **Verify code (2FA)** step
   to confirm the palette works).
- ✅ The name/goal fields accept input and the added step appears in the step list.

**REG-09-A2 — Portal picker.** Open the **Portal (for credentials)** dropdown.
- ✅ It populates from registered portals (or shows an empty/"none" state cleanly — not a JS error).

**REG-09-A3 — Save + reload.** Save the workflow, reload the page, and reopen `REG-Portal-Test`.
- ✅ It's in the saved list and reopens with the same name, goal, and step(s) — **persistence works**
  (this is the WS3 / builder-hardening path most prone to regression). Confirm on disk if you like:
  ```bash
  grep -o "REG-Portal-Test" "C:/src/aihub-client-ai-dev/data/portal_workflows.json"
  ```

## B. Live runs monitor

**REG-09-B1 —** Click **Live runs** (or open `/portal-workflows/runs`).
- ✅ The runs monitor page loads and renders (empty list is fine).

## C. Test run (conditional on a portal being configured)

**REG-09-C1 — Real fetch (if a portal + Browser Use service are available).** Point `REG-Portal-Test`
(or an existing invoice workflow) at a reachable portal and click **Test run**.
- ✅ A run starts, a browser session executes the goal/steps, and the **Run result** panel reports a
  real outcome (e.g. a downloaded file appears under `data/browser_use_downloads/<run_id>/`, or a
  clearly-reported step result). The same run shows in **Live runs**.

**REG-09-C2 — Honest failure (always testable).** Click **Test run** with **no reachable portal**
(no portal linked, or an unreachable target).
- ✅ The run reports a **clear error / failure** (couldn't reach portal / no credentials / browser
  service unavailable). ❌ (release-blocking) if it reports **success** or a downloaded file that
  doesn't exist.

---

## Scorecard

| Check | ✅/⚠️/❌/N-A | Evidence |
|---|---|---|
| A1 author name/goal/step | | |
| A2 portal picker populates cleanly | | |
| A3 save + reload persists | | |
| B1 Live runs page loads | | |
| C1 real fetch (or N/A) | | |
| C2 honest failure on no portal | | |

**Pass:** A1–A3 + B1 + C2 ✅ (C1 ✅ or N/A). A faked run success (C2) is a release blocker.
