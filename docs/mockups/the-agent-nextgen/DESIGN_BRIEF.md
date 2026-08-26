# The Agent — Next-Gen UI Concept Brief (shared spec)

These are LOOK-AND-FEEL mockups only. The information architecture, flow, and copy
below are FIXED — identical across all twelve concepts so they can be compared
side by side. Only the visual language changes per concept.

## Ground rules (every mockup)

- ONE self-contained `.html` file. No external requests of any kind: no CDN, no
  webfonts, no images. System font stacks only (e.g. "Segoe UI", Georgia,
  "Cascadia Mono", Consolas). Inline SVG for icons/charts. Must work from `file://`.
- Desktop-first, designed for 1280–1920px wide. No horizontal page scroll.
- Working navigation: the six sections switch via vanilla JS class toggles.
  Hover/focus states styled. At least one signature motion moment (e.g. agent
  "thinking" presence), wrapped in `@media (prefers-reduced-motion: reduce)` guards.
- All data is static demo data (below). Buttons that would mutate (Approve, Send)
  should give a tiny non-destructive visual acknowledgment (e.g. brief state
  change) or do nothing — never navigate away or error.
- No console errors. No `alert()`.
- A small, discreet fixed badge in a corner: `#NN · <Concept Name>` linking to
  `./index.html` (the gallery). Style it to fit the concept.
- Put a proper `<title>`: `The Agent · <Concept Name> concept`.

## App identity

Product: **The Agent** (AI Hub's next-gen agent surface). Signed in as **James**
(admin). Brain model note: `brain: claude-fable-5`. Footer mantra pair:
`draft → dry-run → promote → schedule` and `humans stay in the loop · My Work`.

## Navigation (fixed six sections + platform escape)

1. **Assistant** (default) — the chat
2. **My Work** — badge count **4**
3. **Views** — deterministic dashboards
4. **Playbooks** — workflows / code flows / automations
5. **Email** — the agent's personal address
6. **Skills** — procedural memory

Plus a compact "Platform" group of escape links (can be collapsed/secondary):
Data Explorer, Workflow Designer, Mission Control, Command Center, Documents,
Classic app. These are decorative links (href="#").

## Section content (use EXACTLY this demo data)

### 1) Assistant (HIGH fidelity)
Empty-state hello: "Hi James — what do you need?" + sub "Explore your data, build
automations through conversation, and keep humans in the loop with My Work."
Three starter prompts: "What data connections do we have?" / "Build me an
automation that checks ERPDB daily" / "What's waiting in My Work?"

Render an in-progress conversation (skip the empty state OR show conversation
with the hello scrolled away — your choice):

- **You:** "Which vendors drove August invoice spend, and are any invoices stuck?"
- **The Agent** runs tools (render as tool-activity chips with status):
  - `list_connections {}` · ok · 0.4s
  - `query_database {"connection":"ERPDB","sql":"SELECT vendor, SUM(total)…"}` · ok · 1.8s
- **The Agent:** "August invoice spend is **$482,910** across 41 vendors. The top
  five drive 68% of it:" followed by a small table:

  | Vendor | Invoices | August total | vs July |
  |---|---|---|---|
  | Meridian Supply Co | 14 | $128,400 | +22% |
  | Northgate Logistics | 9 | $96,150 | +4% |
  | Corvid Materials | 11 | $64,020 | −8% |
  | Atlas Packaging | 7 | $41,310 | +13% |
  | Helix Industrial | 5 | $38,900 | +2% |

  then: "Two Meridian invoices (**INV-20841**, **INV-20857**) are pending approval
  past 14 days — $61,200 combined. Want me to raise them in My Work?"
- **You:** "Yes — raise them and save this as a view."
- **The Agent** runs tools: `create_work_item` · ok · 0.6s, `save_view {"name":"Vendor Spend — August"}` · ok · 0.9s
- **The Agent:** "Done. Two approval items are in **My Work**, and I saved
  **Vendor Spend — August** as a view with 4 tiles — it refreshes with zero AI."
- Show the agent "thinking" presence (animated) OR a streaming cursor as your
  signature motion moment, plus the input row (textarea + send + New + history).

### 2) My Work (HIGH fidelity) — queue + detail split
Filter chips with counts: All 4 · Approval 1 · Email / Edit 1 · Question 1 · FYI 1.
Queue items (verb-typed):
1. **Approval** · "Approve payment run — 2 overdue Meridian invoices ($61,200)"
   · from automation `invoice-chaser` · 09:14 · dry-run
2. **Email / Edit** · "Re: Q3 renewal pricing — reply to Dana Whitfield"
   · from email · 08:47
3. **Question** · "Which fiscal calendar should the vendor report use — 4-4-5 or calendar month?"
   · from agent · shared, unclaimed
4. **FYI** · "Nightly ERPDB health check completed — 0 anomalies, 312k rows scanned"
   · from automation `erpdb-daily-health` · 06:00

Detail pane shows item 2 selected (the email): label "Draft — body is editable;
what you approve is what sends", rows TO `dana@meridiansupply.com`, FROM
`james-agent@mail.aihub.dev`, SUBJECT `Re: Q3 renewal pricing`; an editable
textarea body (2–3 sentence courteous draft holding at current pricing pending
the vendor-spend review, mentioning the two stuck invoices); optional comment
box; buttons **Approve & send** (primary) / **Deny**; hint "Nothing sends
without you". Below: "Thread with the agent" — one exchange (You: "Is Dana on
the approved vendor contact list?" / The Agent: "Yes — primary commercial
contact for Meridian Supply Co since March.") + an input "Ask the agent about
this item…".

### 3) Views (HIGH fidelity) — list + tile board
Views list: **Vendor Spend — August** (selected · user scope · 4 tiles · v3),
**Ops Pulse** (group scope · 6 tiles · v11 · has automation tiles),
**Warehouse Throughput** (tenant scope · 5 tiles · v2).
Header meta: `user · v3 · refreshed 16:42` + buttons Arrange / Rename /
✎ Edit with AI / ↻ Refresh.
Tiles for the selected view:
- **stat**: "August invoice total" → **$482,910** (sub: `SUM(total) · as of 16:42`)
- **bar**: "Spend by vendor (top 5)" — bars matching the table above
- **line**: "Daily invoice volume" — ~14 gently rising points
- **table**: "Open invoices > 14 days" — 6 rows: INV-20841 Meridian $38,700 21d ·
  INV-20857 Meridian $22,500 17d · INV-20872 Corvid $9,340 16d ·
  INV-20881 Atlas $7,120 15d · INV-20889 Northgate $5,480 15d ·
  INV-20896 Helix $4,210 14d
- Optional 5th: **ticker** strip "Latest invoices" scrolling.

### 4) Playbooks (MEDIUM fidelity)
Filter chips: All 6 · Workflows 2 · Code Flows 1 · Automations 3 + search box +
links "Open Workflow Designer ↗ / Open Mission Control ↗".
List: **Invoice Intake — OCR + 3-way match** (workflow · id 1218) ·
**Vendor Onboarding Checks** (workflow · id 1327) ·
**Monthly Close Journal Prep** (code flow · id 214) ·
**invoice-chaser** (automation · v7 · pinned v6) ·
**dayforce-doc-upload** (automation · v3) ·
**erpdb-daily-health** (automation · v12).

### 5) Email (MEDIUM fidelity)
"Your address" card: prefix `james-agent` + suffix `@mail.aihub.dev`, state chip
**ACTIVE**, "Inbound enabled" toggle, Save. Sub-copy: "Mail sent to this address
runs a headless agent session as you and lands its result in My Work."
"Sending & behavior" card: toggles Outbound enabled (on) · Auto-send replies
(off — "every reply waits for your approval in My Work") · Notify on inbound
(off); cooldown minutes field; standing-instructions textarea with placeholder.
"Recent inbound activity": 3 rows — `08-09 08:47 · reply_drafted · dana@meridiansupply.com — Re: Q3 renewal pricing` ·
`08-08 16:02 · processed · alerts@dayforce.com — Document batch ready` ·
`08-08 09:15 · processed · noreply@erpdb — Weekly digest`.

### 6) Skills (MEDIUM fidelity)
List + detail. Skills: **erpdb-reconciliation-recipes** (product · 4.2 KB) ·
**vendor-naming-conventions** (tenant · 1.1 KB) ·
**james-report-format** (user · 0.8 KB, selected).
Detail: title + scope + a short mono body ("Reports for James: lead with the
number, then the table. Currency to the dollar, no cents. Always note the
as-of time…") + a Delete skill (danger) button.

## Fidelity bar

Assistant, My Work, Views must feel real and complete. Playbooks, Email, Skills
can be simpler but must exist and match the concept. Every concept must restyle
— not merely recolor — chat bubbles, tool chips, verb tags, tiles, buttons, and
the nav. The concept should have an OPINION: shape language, type hierarchy,
motion identity, and one memorable signature element nobody else ships.
