---
name: aihub-portals
description: Use when a user wants a file downloaded from — or uploaded to — a
  website/web portal that needs a login (vendor portals, customer portals,
  statement/invoice sites), wants a portal login remembered, or asks about
  recorded portal workflows. Covers the portal_fetch / save_portal /
  run_portal_workflow flows, 2FA take-over etiquette, and delivery rules.
---

# Web portals (browser RPA)

You CAN sign into web portals with a real server-side browser and download or
upload files. The browser runs headless in the platform's Browser Use service;
credentials resolve server-side from the encrypted store — they never pass
through you after storage.

## Decision path (always in this order)

1. **`lookup_portal` first.** Never ask for a URL or login before checking
   what's saved. A saved portal runs on its name alone.
2. **Saved portal** → `portal_fetch(portal_name, task)`. Done.
3. **Nothing saved** → ad-hoc: ask for the login URL + credentials in chat,
   then `portal_fetch(portal_name, task, start_url, username, password[, totp])`.
   Act immediately — don't stall or over-confirm. After a successful run,
   **offer** `save_portal` (only with the user's yes).
4. **Repeatable job** → check `list_portal_workflows`: a recorded workflow
   replays deterministically via `run_portal_workflow(name)` — prefer it over
   auto-mode when one matches. `describe_portal_workflow` shows its steps when
   the match is loose. Successful ad-hoc runs are auto-recorded as draft
   workflows when possible — mention the recorded name so the user knows the
   repeatable path exists.

Portal workflows are NOT the platform's regular workflows/playbooks
(`list_playbooks`) — different system, never confuse them.

## 2FA / verification take-over

When a run pauses for a human step the tool returns a **take-over link**:
relay it VERBATIM, tell the user to finish the step there and click *Hand
back*, and KEEP the `run_id` line in your reply. The link opens in a new tab;
this conversation stays where it is.

**Automatic follow-up.** The service keeps watching the run after your turn
ends. When it finishes — after the hand-back, or on its own if it merely
outlived the in-tool wait — you are WOKEN in this same conversation with a
`[PORTAL RUN UPDATE]` turn: call `check_portal_run(run_id)`, deliver the
links (or the honest failure), keep it short ("Done — here's the file: …").
So when you hand over a link, tell the user the result will appear **here**
automatically; they don't have to come back and say "done" (if they do, just
call `check_portal_run`). Never start another run or re-ask for the hand-back
in that wake-up turn. A run that hasn't finished is still not a delivered
file: give the honest "still running" status with the run_id.

## Delivery (non-negotiable)

- Downloads come back from the tools as `/api/files/…` markdown links, already
  staged for this user — include each link VERBATIM (FILES rules). Never quote
  a server path, never invent a link, never claim delivery when the tool
  reported 0 files.
- Uploads: pass `upload_file` with a server path (`list_server_files` helps
  find one). Report exactly what the tool said — "uploaded" only when it says
  so.

## Reading what's ON the page (no download)

"What's my balance?" / "what's the order status?" when the answer is shown on
screen: `portal_fetch` with a task that says what to **report** ("open the
account summary and report the current balance shown"). The tool returns the
browser agent's reading of the page — relay it, and say it came from reading
the portal page (an interpretation of on-screen text), not from a document.
If the user needs something durable or auditable, prefer downloading the
statement instead.

## Reading / using a downloaded file

To answer questions about a file you delivered ("what's the balance on the
statement?"): pass its `/api/files/…` link straight to `import_documents` —
it resolves to this user's staged copy — then `search_documents` /
`query_document_records` and answer with citations. The portal tool result
also lists **Server copies** paths; those work anywhere a server path does,
including `upload_file` (and `upload_file` accepts the `/api/files` link
directly too). Never go hunting with `list_server_files` for a file you
already delivered.

## Recurring portal downloads

`schedule_portal_workflow(name, cron_expression+timezone | every_hours/every_days,
email_after_run)` on a SAVED workflow — a headless, deterministic replay with
zero AI per run. Each run lands an FYI in the user's My Work with working
download links (plus the file by email if asked). Re-scheduling the same
workflow REPLACES its schedule; `describe_portal_workflow` shows "ALREADY
SCHEDULED" with the job id; `cancel_portal_workflow_schedule` (two-step
confirm) stops it. Unattended 2FA needs a TOTP-seeded portal; if a scheduled
run still pauses for a person, a take-over item appears in their My Work.
Report only the job id the tool returns. (The Portal Workflows page at
`/portal-workflows` and the Run Monitor at `/portal-workflows/runs` remain the
visual surfaces.)

## Files the user attaches in chat

When the user attaches a file with the 📎 button, your turn begins with an
"[Attached files from the user …]" line listing server paths. Use those paths
directly — `upload_file` for a portal upload, `read_file` to read it,
`run_python` to compute over it, `list_server_files` to
inspect — and never echo the paths back. (`import_documents` only when the
user explicitly asks to store it in the document repository.)

## Credentials

`save_portal` stores the login encrypted server-side and read-back-verifies;
the registry keeps only key NAMES. Never echo a credential back, never write
one into a skill, work item, view, or automation. If the user prefers not to
paste a login in chat, point them to the classic Local Secrets page with the
`PORTAL_U(their user id)_(PORTAL SLUG)_USERNAME/_PASSWORD/_TOTP` naming, then
the portal works by name once the registry entry exists (save_portal with the
same name records it).
