# Handoff — Three defects that stop a user getting their work done

**Status:** OPEN — root-caused from live transcripts, not fixed
**Filed:** 2026-09-05
**Model under test:** `claude-sonnet-5` (all three reproduce on the full-strength model, so they are
not a cheap-tier artifact)
**Framing:** these are the findings from competency packs 1 and 2 that a real user would actually
hit. Everything else in those packs is either cosmetic or a grading artifact against strict brief
wording.

Severity is judged by one question: **would the user act on a wrong answer, or fail to get the thing
they asked for?**

| # | Defect | Severity | Who fixes it |
|---|---|---|---|
| 1 | Absence asserted from a partial search ("no August data exists") | **High** | Product + prompt |
| 2 | Collections worklist silently narrowed; invoices vanish | **High** | Prompt/skill design |
| 3 | Code-flow output never offered as a download | Medium | Prompt (tool already works) |

---

## 1. "There's no sales data for August 2026" — absence asserted from two of six connections

**Scenario:** TA-26a. Prompt: *"How were sales last month?"*

**What the user was told:** *"There's no sales data for August 2026 yet… nothing has been recorded
for August."* Presented in bold, before any qualifier.

**The truth:** `AIRDB.TS.sales` holds **$5,406,701.82** of August 2026 revenue.

### Mechanism

Reading the chips in order:

1. `list_data_connections` returns all six: EDW (SQL Server), ERPDB, EDWDB (Postgres), **AIRDB**,
   AIRDB2, PHARMA. So AIRDB was on screen from the first call.
2. It probes **ERPDB** — `SalesOrders`, `SalesOrder`, `orders`, `erp_orders`. `SalesOrders` runs to
   `2026-07-21`.
3. It probes **EDW (id 5)** — `Sales`, `SalesByDay`. Both end `2024-05-04`.
4. It **never probes AIRDB, AIRDB2, PHARMA or EDWDB at all.**
5. It concludes the negative across *every* connection.

So the agent probed 2 of 6 sources and generalised the absence to all 6. The reasoning error is
specific and nameable: **a negative result was scoped to the whole platform on the strength of a
partial search.** A positive claim ("July was $18,150") is bounded by the query that produced it; a
negative claim is only as strong as the search's coverage, and nothing in the agent's reasoning
tracks that distinction.

### A contributing platform bug worth fixing on its own

Two of its schema calls failed on **exact-name matching**:

```
get_connection_schema("EDWDB") -> ok=false  "No connection named 'EDWDB'.
                                   Known connections: … EDWDB (Postgres) …"
get_connection_schema("EDW")   -> ok=false  "No connection named 'EDW'.
                                   Known connections: EDW (SQL Server), …"
```

Both strings are unambiguous prefixes of exactly one connection. The resolver demands the full
display name including the parenthetical, so `EDWDB` misses `EDWDB (Postgres)`. The agent recovered
by falling back to the numeric id (`connection: "5"`), but it burned two calls and — plausibly —
came away with the impression it had already "tried" EDWDB when it never read a row from it.

**Fix:** resolve a connection name by unique case-insensitive prefix / substring match, and only
error when the match is genuinely ambiguous (then list the candidates). This is a small change in
the same resolver that produces the "Known connections:" message.

### Fix for the reasoning half

The doctrine needs an explicit rule for negative claims: *before reporting that data does not exist,
either probe every connection that could plausibly hold it, or scope the statement to the ones you
actually queried* — "I checked ERPDB and EDW and found nothing after July; I have not checked AIRDB,
AIRDB2, PHARMA or EDWDB."

### Why it matters here

This is the worst of the three because the failure is invisible. The user asked a reasonable
question, got a confident, well-formatted answer, and the correct response was to go and load August
data that was already there. Nothing in the reply invites a second look.

---

## 2. Collections worklist silently narrowed — eight invoices disappear

**Scenarios:** TA-20 and TA-23b. Prompt: *"every customer invoice that's past due."*

**What the user was told:** 19 invoices, **$145,464.40** total.
**The truth for that moment:** 27 invoices, **$267,089.90**.
**Missing:** eight `INV-DEMO-*` / `CUST-*` invoices worth **$121,625.50** — including the single
largest exposure on the ledger, **Walmart INV-DEMO-1001 at $48,250.00**.

### Mechanism

Every probe in the scenario carries the same predicate, from the very first one:

```sql
WHERE customer_id LIKE 'CGC-%'
```

It appears in the discovery query, the count, the detail pull, all four bucket queries and the
final aggregate — twelve probes, one filter, applied before the agent ever sees the excluded rows.

The filter's origin is the `collections-triage` skill, which mandates reading `CG_CollectionActivity`
and `CG_ARCustomers` before recommending any call. Both tables only contain `CGC-*` customers. The
agent generalised *"the tables I must consult cover CGC-\*"* into *"collections means CGC-\* only"*
and applied it as a data filter rather than as an enrichment join.

**The structural problem is that the scope is self-confirming.** Because the narrowing happens in
the first query, every later query inherits it, every cross-check agrees, and no contradiction ever
surfaces. The agent has no way to notice the missing rows — they were filtered out before it looked.
An internally consistent, confidently presented, incomplete answer is the result.

### Confirmed by isolation

I re-ran TA-20 with the skill deleted and nothing else changed. It returned the **full** past-due
set with correct buckets. So the reasoning is sound; the skill's influence is what narrows it.

Two consequences worth separating:

- **The skill is doing this silently.** The KPI tile reads "Total past due $145,464" with no scope
  qualifier. Prose one line above mentions "the 12 CGC-* accounts", so it is disclosed in passing —
  but the headline number a user reads and forwards is wrong for the question asked.
- **A saved skill can reshape an unrelated question.** TA-20 never asked for triage; it asked for a
  worklist. The skill loaded because the topic matched and quietly changed the answer's scope.

### Related, same root, worse symptom

TA-23b took the same filter and went one step further, asserting *"Summit Provisions (CGC-010),
Clearwater Distributors (CGC-011): no open balance"*. Both have open past-due invoices —
CG-INV-10050 ($6,300) and CG-INV-10055 ($7,880). Here the narrowing stopped being an omission and
became a false positive statement about a customer.

### Fix

Three things, cheapest first:

1. **Skills must not silently change scope.** If a mounted skill causes a filter the user did not
   ask for, say so in the answer, at the number: "19 past-due invoices across the 12 CGC-\*
   collections accounts (8 further past-due invoices totalling $121,625.50 sit outside that
   population)."
2. **Rewrite the skill** so the required reads are an enrichment step (`LEFT JOIN` for hold/dispute
   flags), never a population filter. The current wording invites exactly this misreading.
3. **Doctrine:** when a question says "every", get the unfiltered total first, then narrow — so the
   number that was excluded is always known and reportable.

---

## 3. The workbook it built is never handed over

**Scenario:** TA-22. Prompt: *"…turn it into a formatted Excel workbook… and email the workbook to me."*

**What happened:** the flow ran, the workbook was built correctly (27 invoices, $267,089.90 — exact),
the email sent. But **no download link appeared in any of the three turns**, so within the chat the
user has no way to reach the file.

### Mechanism — and a correction to my first read

My initial diagnosis was that code-flow outputs land in the run workdir
(`automations/tenant_*/_codeflow_runs/<run>/<step>/`) and cannot be served, because `/api/files/<id>`
resolves out of `data/agent/users/13/downloads/`. **That is wrong, and the evidence is in the
previous run.**

In run 2, the agent called `offer_file_download` with the run-workdir path and it worked:

```
offer_file_download(server_path="…/_codeflow_runs/…/past_due_invoice_aging.xlsx")
  -> ok=true  "Download ready… [⤓ past_due_invoice_aging.xlsx (9.9 KB)](/api/files/…)"
```

The staged file is still on disk (`13063150-…__past_due_invoice_aging.xlsx`). So the bridge from a
code-flow artifact to a chat download **exists and works**.

In run 3 the agent called `offer_file_download` **zero times**. The tool was available, the file was
there, the path was in the walk summary in front of it. It simply did not offer it.

The most likely reason is a side effect of the send_email fix: email now genuinely works, so the
agent treats "emailed" as "delivered" and stops. In run 2, when the email silently failed, it
*did* offer the download. Fixing the delivery path appears to have removed the prompt to provide the
in-chat copy.

### Why it matters

Modest but real. The user asked for a workbook; the file exists; every artifact in the chat is a
link away — and they got prose. It also couples the deliverable to email working, on a platform
where email delivery to an external address is exactly the thing that was broken until yesterday.

### Fix

Doctrine, not code: **when a flow produces a file the user asked for, always offer it as an
`/api/files` download — in addition to any other delivery, never instead of it.** `run_python`
already does this automatically (`code_tools.py` returns `links` for produced files and the model
pastes them verbatim, which is why TA-16 gets a link with no explicit call). Code flows require the
explicit `offer_file_download` call, and that asymmetry is what the agent is falling through.

Worth considering the stronger version: have `dry_run_code_flow` auto-stage step output files and
return links the same way `run_python` does, so the behaviour stops depending on the model
remembering.

---

## Cross-cutting note

Defects 1 and 2 are the same shape: **a scope decision made early, never revisited, and never
disclosed.** In one it is which connections were searched; in the other it is which customers were
selected. Both produce internally consistent, confident, incomplete answers — the hardest kind for a
user to catch, because nothing in the reply looks uncertain.

If only one doctrine change is made, make it this: *state the boundary of any answer whose scope was
narrowed — by a filter, a skill, or an incomplete search — at the point where the number is given.*

---

## Resolution — 2026-09-05 (built, unit-tested; NOT yet restarted or live-verified)

The Agent service was deliberately **not restarted** (competency packs were running), so
every change below takes effect only after the next 5111 restart. Live re-verification of
TA-26a / TA-20 / TA-23b / TA-22 is the open step.

| # | What was built | Where |
|---|---|---|
| 1 | **Resolver ladder**: id → exact name → base name (`EDW` → `EDW (SQL Server)`, `EDWDB` → `EDWDB (Postgres)`) → unique prefix → unique substring; ambiguity lists candidates, unknown lists every connection. Tool output prefixes a `(connection 'EDW' resolved to 'EDW (SQL Server)', id 5)` line on a fuzzy hit, so an unresolved name is never mistaken for a checked source. `export_data` uses the same ladder. | `agent_service/platform_tools.py` (`match_connection`), `export_tools.py` |
| 1 | **Coverage guard** (deterministic, like the mutation-claim guard): the data tools record which connections exist and which ones a row-level query touched this turn; when a reply asserts absence after querying only some of them, the UI gets *"Coverage note: … only 2 of 6 connections were queried (…). Not queried: …"*. Silent when the user scoped the question themselves, when `run_python`/`ask_agent` ran (coverage unknowable), or when everything was queried. Kill switch `AGENT_COVERAGE_GUARD=false`. | `brain.py` (`coverage_warning`), `platform_tools.py` (coverage record) |
| 1+2 | **Doctrine**: new *SCOPE, COVERAGE AND NEGATIVE CLAIMS* prompt section (query every plausible connection or scope the negative; an entity negative needs the entity in the queried population; EVERY/ALL → unfiltered total first, narrowing stated AT THE NUMBER with what was excluded). `list_data_connections` ends with the coverage rule; the 0-rows probe text names the connection and says it proves nothing about the others. | `brain.py`, `platform_tools.py` |
| 2 | **Skills are enrichment, never silent scope**: SKILLS prompt section + `save_skill` description now say a "consult table X" step is a lookup/join for the population the user asked about, not a population filter, and that scope changes are disclosed at the number. The tester's user-scope `collections-triage*` skills on this box were left untouched (pack fixtures). | `brain.py`, `work_tools.py` |
| 3 | **Automatic file handoff for runs**: automation dry-run/run/check and code-flow dry-run/run now stage every produced output file and append *"Files produced — include these links VERBATIM (… never instead of email)"* with `/api/files/` links (and inline image lines) — the same contract `run_python` already had. FILES doctrine + tool descriptions + the playbook-lifecycle skill say email is an extra delivery, never a substitute. | `authoring_tools.py` (`_offer_run_files`), `brain.py`, `product_skills/aihub-playbook-lifecycle/SKILL.md` |

Unit pack: `tests_v2/unit/test_agent_scope_honesty.py` (15/15 in the aihub-agent env).
