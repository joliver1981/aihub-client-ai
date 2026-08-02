# 17 — Business Role Scenarios

**Purpose.** Everything in packs 01–16 asks *"does this feature work?"* This pack asks a different
question: **"could someone actually do their job with this?"**

Each scenario takes one real business role, walks a real day, and grades whether the platform
assisted, automated, or got in the way — with an exact oracle behind every number so the answer
isn't a vibe.

**Company under test:** *Continental Goods Co.*, a ~$180M omnichannel seller of home goods. Retail
stores, wholesale/B2B accounts on terms, and a DTC/marketplace channel. It buys from vendors on POs
with goods and invoice receipts, and runs a SAP-shaped procurement stack beside an app-side AR
system, a GL, and a WMS.

That company is not invented for this pack — it's the shape of the data already on the test box:
`AIRDB` (retail sales, inventory, stores, staff, traffic, plan) and `ERPDB` (invoices, payments,
POs, vendors, GL, WMS), plus the document fixtures and the Meridian 2FA vendor portal.

---

## Scenarios

| Role | Folder | Status |
|---|---|---|
| **AR Specialist / Collections** | [`AR_Clerk/`](AR_Clerk/) | **built** — 6 of 10 beats, foundation + oracles complete |
| AP Clerk | — | next candidate: 3-way match, portal invoice download, SFTP drop |
| Inventory / Demand Planner | — | AIRDB side: stock-outs, weeks-of-supply, forecast accuracy |
| FP&A Analyst | — | plan-vs-actual (⚠️ AIRDB2 plan data is known-broken) |
| Store Manager | — | daily flash, staffing vs traffic |
| Buyer / Category Manager | — | reorder, vendor scorecard, cost-change detection |

See [`ROLE_MATRIX.md`](ROLE_MATRIX.md) for the full department/role map and which roles the existing
data can already support.

---

## What makes a scenario in this pack

A role scenario is not a feature test. To count, it needs all five:

1. **A persona with a login** — run as the business user, not `admin`. Half of what's being tested
   is whether a non-admin can actually do the job
2. **A real day, in order** — beats with times on them, not a feature checklist
3. **An oracle per beat** — a number derived from live data or a fixture answer key. "Looks right"
   is not a result
4. **Honesty probes** — questions the data can't answer, false premises, and instructions planted
   in the data. Every scenario needs these
5. **A one-command reset** — a seeder and a teardown, so the second run is as clean as the first

Each scenario also tags every beat **Assist** (human asks, platform answers) / **Augment** (platform
drafts, human approves) / **Automate** (scheduled, human sees only exceptions). A role's value story
is how much of the day moves rightward — and which beats *shouldn't* move, because a human decision
is the point.

---

## Conventions

- **Namespace everything you seed.** The AR pack uses `CG-*` and its own GL control account. Shared
  ERPDB already carries `INV-DEMO-*` rows from the demo seeder — collisions there cost a debugging
  session
- **Never repair the stock demo data.** Its inconsistencies are useful: they're what a real user hits
  on day one, and "did the platform notice?" is a fair test
- **Seeded contact addresses are `@example.com`** (RFC 2606 reserved). Scenarios deliberately try to
  make the platform send things; a guardrail failure must be a log line, not a real message
- **Generated answer keys only.** A key is derived from the live database and cross-checked against
  the seed definition, and is not written if the two disagree. A stale oracle invalidates every
  result under it
- Check ids are `<ROLE>-<beat>-<step>` — e.g. `AR-06-B1`

---

## Relationship to the other packs

| Pack | Asks |
|---|---|
| 11 Regression Suite | did any basic feature regress? |
| 14 Workflow Node Matrix | did any engine node stop working? |
| 15 Platform Regression | is the build shippable? |
| 12 / 13 / 16 | how good is NLQ / document retrieval / the CC agent? |
| **17 (this)** | **can a person do their job with it?** |

Run 11 and 15 before a release. Run 17 before telling a customer what the platform does for their
AR desk — it's a value-and-trust test, not a smoke test.
