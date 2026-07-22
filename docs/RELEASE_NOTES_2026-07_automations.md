# AI Hub Release Notes — Automations & Approvals Update (July 2026)

> Release: _\<version\>_ · July 2026
> Focus: human-in-the-loop Automations, approvals, live visibility, and safer operations.

This update turns Automations from "scripts that run" into a governed, human-in-the-loop
part of the platform: runs can pause for real approvals with the evidence attached,
exceptions flow to a work queue without stopping the batch, everything is visible live,
and common tweaks no longer require touching code.

---

## ✅ Human approvals for Automations

**Automation checkpoints now land in My Approvals** — the same queue your workflow
approvals use.

- When an automation pauses for a decision (`Review this batch before upload…`), a
  **Pending item appears in My Approvals**, assigned to the person who started the run
  by default — or to a **user or group you choose** (any group member can decide).
- **Attachments included.** The files the approver needs — the generated CSV, the
  exception report, the scanned source document — are attached to the approval item as
  download links. No hunting for files.
- **Decide anywhere.** Approve or reject from My Approvals, from Mission Control's live
  run card, or by replying in Command Center chat — all three stay in sync, and the
  first decision wins. Approving resumes the run; rejecting aborts it before anything
  leaves your environment.
- **Per-document review items.** In batch processes, individual problem documents
  (unknown employee, low confidence, unreadable scan…) are sent to the queue as their
  **own review items with the source file attached** — and the batch keeps going.
  Payroll works the exceptions one by one; the run never stalls per document.
- **My Approvals quality of life:** a **Type** column distinguishes Automation vs
  Workflow approvals, and **every column is click-to-sort** (click again to reverse).

## 🖥️ Automation Studio workbench (Command Center)

Watch your automation **being built and run, live**, in a panel that docks beside the
chat:

- Phase rail (Gather → Create → Write code → Dry-run → Confirm → Promote → Live),
  the code as it's saved, and a "contract" card showing exactly which connections,
  secrets, inputs, packages and outputs the automation declares.
- **Live run feed** with log lines, elapsed/timeout bar, outbound-connection chips, and
  approval gate buttons right in the panel.
- Runs always end with the **true final verdict** — including when you decide from My
  Approvals — with each verification check shown pass/fail.
- The panel is now **resizable** (drag its left edge; your width is remembered) and
  links you straight to My Approvals when a run is waiting on you.

## 🎛️ Mission Control upgrades

- **⚙ Settings panel per automation:** see versions, description, connections, secrets,
  packages and schedules at a glance — and **edit input defaults and the timeout in a
  form**. Saving creates a new version through the same pipeline chat uses (dry-run,
  then Promote — there's a button for that too). Every input shows its own help text.
- **Delete button** with a safe lifecycle: schedules are deactivated first so nothing
  keeps firing, an in-flight run blocks deletion, run history is preserved, and stale
  Scheduled Tasks entries clean themselves up.
- **🧹 Clear stale runs:** one click finalizes runs whose supervising process died
  (e.g. after a service restart). Genuinely live runs are never touched.

## 🤖 Platform-managed AI for automation scripts

Automation code can now call the platform's AI directly:

- `aihub.llm("…")` for plain-text prompts, `aihub.ai_extract("…", images=[…])` for
  structured JSON extraction — including **vision** (reading scanned documents,
  handwriting, rotated pages).
- **No API keys and no model names in your scripts.** The platform supplies the tenant
  key and resolves the model from central configuration, so upgrading models is a
  one-place change — nothing goes stale inside individual automations.

## 🧟 Reliability: no more ghost runs

- A **startup sweep** finalizes any run orphaned by a restart — honestly marked, its
  pending approvals cancelled — so Live Now always tells the truth.
- Heartbeat-based safety: a run supervised by *any* live service is never touched.
- Half-alive scripts from before a restart now shut themselves down instead of polling
  forever.

## 🧩 Workflow designer

- **New Automation node:** run any promoted automation as a workflow step. Pick it from
  a **dropdown** (no IDs to paste) and its inputs appear as **individual fields** with
  defaults and help text — no raw JSON authoring.
- Portal node now has proper styling in the palette and on the canvas.

## 💬 Command Center authoring

- CC **builds all of the above from plain English** — describe the process, and the
  generated automation uses platform AI, attaches evidence to its approval gates,
  routes exceptions to the queue, and exposes its tunables as described inputs.
- New chat tool: **delete an automation** by name, with a two-step confirmation.
- Scheduled automations now appear in the **Scheduled Tasks panel** (run-now / cancel
  work as usual), and a header button jumps straight to Mission Control.
- Paused-run messages include **direct links** to My Approvals and Mission Control.
- An audit that legitimately finds **nothing** is a success, not a failure — empty
  report batches no longer mark runs failed.

## 🔧 Portal Workflow fixes

- Scheduling a portal workflow no longer fails with a database conversion error.
- Saving with an existing name warns instead of silently overwriting.
- Invalid URLs and negative wait durations are rejected at save time.
- The builder now shows the actual error detail instead of a generic message.

---

## Upgrade notes

- **No database schema changes.** Restart the AI Hub services after updating, and do a
  hard refresh (Ctrl+Shift+R) in open browser tabs.
- Optional settings:
  - `AUTOMATIONS_CHECKPOINT_NOTIFY_EMAIL` / `…_SMS` — email/text the assignee when a
    run pauses for approval.
  - `AUTOMATIONS_AI_MODEL` — pin a specific model for automation AI calls (defaults to
    the platform's central model setting).
- Solutions exported from this version that use the new AI/approval capabilities
  require the target installation to be on this version or later.
