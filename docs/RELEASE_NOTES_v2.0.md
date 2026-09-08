# AI Hub v2.0

Our biggest release since launch. v2.0 adds **The Agent** — a conversational front door to the
whole platform — along with **Playbooks**, personal **Views** dashboards, **My Connections**, a
rebuilt document stack, and a broad round of speed, accuracy and reliability work.

Every classic screen is still there and still supported.

---

## 🌟 The Agent (new)

Talk to AI Hub instead of navigating it. The Agent has five surfaces:

| Surface | What it's for |
|---|---|
| **Assistant** | Ask, analyze, build — the conversation |
| **My Work** | Every approval, question and draft waiting on you, in one queue |
| **Playbooks** | Everything you've built — automations, workflows, code flows and recorded portal workflows — in one inventory |
| **Views** | Your dashboards |
| **Schedules** | Every recurring job, in one place |

**What it can do**

- **Build by conversation.** Automations, workflows, dashboards and chat agents get drafted,
  dry-run against real data, and promoted only when you approve.
- **Build and manage chat agents** — create a General Agent, pick its tools, give it knowledge
  documents, share it with groups, rename or delete it, all by asking.
- **Your data and documents** — query connections, search documents with cited sources, and run
  real calculations rather than estimates.
- **Files in, answers out.** Drag or paste a spreadsheet, PDF or screenshot into the chat and it
  gets analyzed for real, using a built-in sandboxed Python environment for exact math.
- **Rich answers.** Charts, KPI cards, maps and generated images appear inline in the
  conversation, not as a wall of text. It can read charts and screenshots you send it, export
  results to Excel or CSV, and split, merge or fill PDFs.
- **Vendor portals.** It logs in, navigates and downloads. When a site asks for a 2FA code it
  pauses and offers a **Take over the browser** button — you finish the step, and the
  conversation picks up exactly where it left off.
- **Email.** Per-user agent mailboxes: it reads incoming mail and attachments, and sends results
  out as formatted HTML email with a live dashboard embedded
  ("email me this dashboard every weekday at 9am").
- **Schedules.** Set up recurring work in conversation, in your own time zone, and see, run,
  pause or cancel any scheduled job from one screen.
- **Skills.** Reusable know-how — shipped with the product, plus what your organization teaches
  it — so procedures your team relies on get followed the same way every time.
- **Every classic screen** is one click away from a role-aware Platform directory.

**Built to be honest.** It won't claim it did something unless the action actually verified,
permissions are enforced in the tools rather than left to the model, it tells you what it did and
did not check, and every step shows as a live chip you can click into.

---

## 🔗 My Connections (new)

Connect your own accounts — starting with Microsoft 365 / Outlook — and let AI Hub work with
**your** mail and calendar as **you**, with your own permissions. Sign-in happens at the provider;
AI Hub never sees your password. Connections are per-user and isolated: nobody else's agent can
read your mailbox. Administrators choose which connections are published to users, and
write actions are off by default.

This works on standard on-premise installs — no public HTTPS endpoint or firewall changes needed.

---

## 📊 Views (new)

Dashboards you arrange yourself — drag to reorder, resize, rename in place. Tiles are backed by
your automations, so the numbers are current rather than a snapshot. Scope a View to yourself, a
group or the whole tenant, edit a tile by describing the change, and email it on a schedule.

---

## 📄 Documents

- **Audit questions get a real count.** "How many leases allow early termination?" sweeps the
  whole set and reports a genuine number with evidence, instead of answering from the top few
  hits.
- **Better answers.** Full pages as context instead of fragments, your specific terms preserved
  through the search, and ambiguity surfaced when similar documents compete for an answer.
- **Rebuilt Document Search page.** A browsable category tree, type-ahead on the fields your
  documents actually contain, one unified search box, and safer view/delete.
- **Document category access control.** Access is granted to groups by document category and is
  enforced everywhere — the search page, the Documents manager, the APIs and every agent — so
  people only see the categories they're entitled to.
- **Governance screens.** Review and recategorize incoming filings, manage extraction schemas,
  and grant category access from the Groups page.
- **Word documents paginate properly** on ingest, which noticeably improves search and citations.
- **Roughly 4× cheaper search planning**, and honest retryable "busy" responses under heavy load
  instead of hanging.

---

## 🖥️ Command Center

- Answers questions about your own connections, tables, agents, workflows and schedules from your
  actual configuration.
- Exact math on uploaded spreadsheets, with hidden worksheets disclosed rather than read silently.
- The Builder sees your real schema and validates generated queries before saving them.
- When it can't find something, it says what it's waiting on and offers options instead of
  repeating itself.

---

## 📈 Data and analytics

- **Excel exports are roughly 10× faster** — a 1,000-row export takes 2–3 minutes instead of ~25.
- **Data Explorer: pin to dashboard works.** Pinning a table or chart from the toolbar had no
  effect; it now pins, and you're warned before navigating away from unsaved pins.
- Charts arrive inline reliably, and empty results no longer render as a bare "No data to
  display".
- Generated SQL is tested before it's saved, and the generator sees real column values instead of
  guessing at status codes.
- Data Explorer works correctly on current OpenAI models, and database driver selection is
  automatic.

---

## ⏰ Scheduling

- **Day-of-week schedules run on the right day.** A long-standing off-by-one is fixed at the root,
  and existing schedules were corrected along with it.
- Times display in your own time zone, and editing a schedule no longer shifts it.
- On-premise SQL Server date-binding failures fixed; the scheduler is quieter and cleans up jobs
  whose target was deleted.

---

## 🔁 Workflows, automations and code flows

- Valid workflows are no longer flagged with false configuration warnings, and conditional steps
  report the correct status.
- Code Step nodes are visible on the canvas instead of being invisible ghost nodes.
- **Code flows can send email** directly from a step, and deleting a flow now removes its
  schedules with it.
- Recorded portal workflows get real, meaningful names and no longer overwrite each other.
- Approvers can correct extracted field values in place instead of rejecting and starting over.
- **One-off jobs stay one-off.** Work built for a single run is marked as such and cleaned up
  automatically, so your automation library doesn't fill with throwaways.
- Approval items left undecided when a run finishes are closed out instead of lingering in your
  queue.
- Solution bundles install without owner errors.

---

## ⚙️ Administration and security

- **Email Settings screen** — configure outbound email from the UI, live, with no restart.
- Current OpenAI model family available throughout; a stale model alias that was silently
  absorbing traffic is gone.
- Customers using their own Anthropic key have it take precedence over the platform default.
- Uploads are attributed to your signed-in identity, and document search enforces category access
  inside the engine rather than in the UI.
- Portal credentials stay in platform secrets and never enter workflow definitions.
- Signing out clears a remembered "classic mode" choice, so shared machines start clean.

---

## ⬆️ Upgrade notes

1. Run the v2.0 installer over your existing installation.
2. **Restart the AI Hub services and hard-refresh your browser** (Ctrl+Shift+R).
3. **The Agent is enabled on upgrade for Developers and Administrators**, and becomes their
   landing page. Regular users continue to see the classic experience until an administrator sets
   `AGENT_ALLOW_ALL_USERS=true` in the `.env` in your install folder and restarts. Everyone can
   switch back to the classic navigation at any time.
4. **Word documents ingested before this release should be re-ingested** to pick up proper page
   splits — search quality and citations improve noticeably.
5. **My Connections** requires a one-time administrator setup (register the redirect address with
   your identity provider and publish the connection to users) before it appears for staff.

Your existing configuration, connections and customizations are preserved.

---

## ⚠️ Known issues

- Empty database values may export as the text `None`.
- A build that fails part-way can leave an empty automation behind; it can be deleted safely.
- Two sample MCP server entries ship enabled but are not reachable — remove them if unused.
