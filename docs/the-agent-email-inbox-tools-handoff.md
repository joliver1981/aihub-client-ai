# The Agent can now OPEN its email

**Repo:** `C:\src\aihub-client-ai-dev` · **Service:** `agent_service/` (The Agent, :5111)
**Status:** BUILT + live-verified 2026-08-24. This doc started as another agent's spec; the build
applied five corrections found in review (kept below — they are the interesting part).

## What was built

`agent_service/email_tools.py` (exported `EMAIL_TOOLS`, registered in `brain.py` behind
`AGENT_EMAIL_TOOLS`, default **true**):

| Tool | Signature | Notes |
|---|---|---|
| `list_my_email` | `(limit=20, offset=0, since, sender, subject_contains, include_skipped=False, include_pending=True)` | Pages/searches the whole ledger (`email_store.search`, new) **and merges PENDING mail from the live cloud feed** (first page) — the cooldown-deferred blind spot, answered |
| `read_email` | `(event_id)` | Full body + attachment list inline; `message_key` recovery for pre-column rows; honest `Body NOT RETAINED` after the cloud's ~3-day expiry; HTML-only mail falls back to stripped HTML |
| `list_email_attachments` | `(event_id)` | Filenames, types, sizes, attachment_ids |
| `read_attachment` | `(event_id, attachment_id, max_chars)` | Extracted text via the main app's extract route (OCR fallback included). Client-side clamp `min(max(1000, x), AGENT_EMAIL_ATTACH_MAX_CHARS=500k)`, default `AGENT_EMAIL_ATTACH_READ_CHARS=100k` |
| `save_attachment` | `(event_id, attachment_id, filename?)` | Original bytes → `data/agent/users/<uid>/email/<event_id>/<attachment_id>__<name>`; ext-gated to `document_tools._ALLOWED_EXTS`; `AGENT_EMAIL_SAVE_MAX_MB=50` cap; traversal-proof; save-only (import stays explicit) |

Authz chokepoint: `email_tools.ledger_entry_for` / `_owned_event` / `_owned_attachment` —
ownership = the caller's own ledger row **or** (tools only) a live-feed event addressed to their
address; attachment membership = the attachment appears in `attachments_for(event_id)`. main.py's
`_own_ledger_row` (the `/api/email/log` viewer routes) now delegates to the same chokepoint,
deliberately ledger-only.

Also wired: `brain.py` `_READ_TOOL_NAMES` (+4 reads, side-threads can read mail), `MUTATING_TOOLS`
(+`save_attachment`), SYSTEM_PROMPT EMAIL doctrine rewritten (it used to say old bodies were
unreadable), `get_agent_email_status` now points past itself, and the poller prompt carries
`Email event_id: N` + per-attachment `attachment_id` lines so an email-triggered turn can open the
very mail it is handling. Poller pre-extraction default halves to 10k when the tools are on
(`AGENT_EMAIL_ATTACH_CHARS` still overrides).

## Corrections applied vs the original spec (why they mattered)

1. **`read_attachment`/`save_attachment` needed `(event_id, attachment_id)`, not bare
   `attachment_id`.** The cloud attachment routes are tenant-scoped and there is no
   attachment→event reverse lookup, so the spec's own membership check (C3) was unimplementable as
   signed — and skipping it would let any user read any tenant attachment by id-guessing.
2. **"Reuse `agent_email_attachments._resolve_cap`" contradicted C1** — that module imports
   `CommonUtils` (cloud DB) + main-app `config`. No cap library needed: the main-app extract route
   does NOT clamp a requested `max_chars` (`agent_email_routes.py:1407` passes it straight through),
   so the tool clamps client-side in two lines.
3. **`data/agent_files/{agent}/...` was the wrong save path** — that's the legacy GeneralAgent's
   agent-id-scoped area in the main app. A6 is per-user: files live beside `uploads/` and
   `downloads/` under `data/agent/users/<uid>/email/<event_id>/`.
4. **The flagship use case (emailed "file the attached invoice") failed as specced**: the poller
   prompt carried no event_id, and the ledger row is written only AFTER the turn, so ledger-based
   authz refused the in-flight email. Fixed by the prompt context lines + the live-feed ownership
   fallback (which also covers cooldown-deferred mail and pre-`message_key` rows — three gaps, one
   helper).
5. **Two registration points the spec missed**: `_READ_TOOL_NAMES` (side-threads) and the
   SYSTEM_PROMPT EMAIL block (which actively told the model it couldn't do this). Kill-switch
   default corrected to **true**, matching the `AGENT_DOCUMENT_TOOLS`/`AGENT_PORTAL_TOOLS` mirrors
   the spec cited (both default true); the inbound loop stays gated by `AGENT_EMAIL_ENABLED`.

Ledger caveats from the spec, resolved: cooldown-deferred mail now SURFACES (pending merge +
live-feed ownership) without touching poller record semantics (pre-recording would break
crash-retry). `reply_drafted`-with-auto-send ambiguity: stated in tool output ("with auto-send ON
that reply went out immediately"); a true sent/filed outcome split needs poller result capture —
separate ticket.

## Verification (2026-08-24)

- `tests_v2/unit/test_agent_email_reading_tools.py` — **28/28** (authz-adversarial: foreign event,
  wrong-event pairing, traversal, `.exe`, size caps, clamp, retention honesty, pending ownership,
  pagination/filters).
- `test_agent_brain_tool_lists.py` 8/8 (drift heuristics cover the new names), `test_agent_email_log_detail.py`
  9/9, email body/html suites green.
- Pack 20: new **A6-9** (deterministic, cloud stubbed at the `email_client` seam) and **A6-10**
  (real LLM turn: `read_email` registered, expired body reported honestly with real metadata) —
  both PASS against the restarted live service.
- **Real-mail live-fire (user 13):** one turn ran `get_agent_email_status → list_my_email (13 real
  rows) → read_email(76)` and returned the genuine cloud-retained body of James's "Portal" email,
  with the correct no-attachments and auto-send reasoning.
- Still owed: the PDF end-to-end (list → save → import → answer from inside the PDF) needs a real
  inbound email with an attachment — nothing currently in the cloud's 3-day retention has one, and
  the notifications API can't send attachments. Send any email with a PDF to an active agent
  address and run that journey.

## Out of scope (unchanged)

Legacy `EmailAgentDispatcher` and `agent_email_tools.py` (classic path untouched) · moving A6 onto
`AgentEmailAddresses` · **any tool that sends mail without human approval — `draft_email_reply`'s
approval contract stays exactly as it is.**
