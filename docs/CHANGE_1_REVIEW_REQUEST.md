# Review request: give Command Center a read-only "inspect my environment" path

You are reviewing a **proposed change that has not been written yet**. Another agent
investigated a defect, formed a plan, and wants the findings and the plan independently
verified before any code is touched. Please **verify or refute** — do not implement.

Repo: `C:\src\aihub-client-ai-dev` (AI Hub platform). The relevant service is
`command_center_service` (the "Command Center" / CC agent, a LangGraph agent on port 5091).

---

## 1. The observed defect

Asked directly, in a fresh CC chat:

> **"What tables are in the ERPDB connection? Just list them."**

CC replied:

```
📊 AIRDB5_Wizard (Agent #260):
- TS.calendar_master
- TS.cost_of_products
- TS.employee_data
- TS.Inventory
- TS.location_master
...
[Source: AIRDB5_Wizard (Agent #260), status: completed]
```

Two things are wrong:

1. **Those are not ERPDB's tables.** ERPDB contains `Invoices`, `InvoiceLineItems`,
   `CG_ARCustomers`, `CG_CollectionActivity`, `CG_DunningLog`, `CustomerPayments`,
   `EKKO`/`EKPO`/`EKBE`, and 29 others. It contains **none** of the `TS.*` tables above —
   those belong to a different database (AIRDB). The answer is confidently wrong, and the
   `[Source: ...]` attribution makes it look grounded.
2. **No schema tool was called.** Instrumenting the CC log for that turn showed
   `discovery tools called: NONE`.

## 2. Root cause as diagnosed

CC routes a message through a mini-LLM "capability router" before its main intent
classifier.

- `_CAPABILITY_ROUTER_PROMPT` — `command_center_service/graph/nodes.py:767`
- capability list — same file, lines ~769-777:
  `document_search · web_search · map · image_generation · run_tool · portal · build · none`
- `classify_intent` — `nodes.py:1535`

**There is no capability for questions about the platform's own configuration.** The prompt
additionally routes anything data-shaped to `none` explicitly:

- line ~777: `"none: ... includes database queries, delegations to data/general agents ..."`
- line ~790: `"Database/data-agent queries (sales, revenue, orders, headcount, inventory metrics) → none."`

So `"what tables are in ERPDB"` → `none` → main classifier → `delegate` → CC picks a data
agent → it picked an unrelated one (AIRDB5_Wizard) → returns that agent's tables.

**CC already has the correct tools and never reaches them:**

| tool | location | note |
|---|---|---|
| `list_data_connections` | `nodes.py:6072` | |
| `get_connection_schema` | `nodes.py:6094` | docstring: *"ALWAYS ground table/column/join names AND filter values in this. NEVER invent them."* |
| `probe_connection_query` | `nodes.py:6231` | |

This is a **routing** defect, not a missing-capability defect.

### Why it matters beyond a wrong answer

The same blindness appears in multi-turn build conversations: CC gathers a complete business
spec, then stalls asking the user for schema/connection/secret names the platform already
knows. In a scripted 12-turn test the simulated user gave up. (Two related fixes have already
shipped — a repeat-reply stall guard in CC, and feeding the *builder service* the data
dictionary — commits `839de15` and `c60407f`. This change is the third and was deliberately
left until last as the riskiest.)

## 3. The proposed change

### 3a. A new capability, defined by READ vs CREATE

Add to `_CAPABILITY_ROUTER_PROMPT`:

> **`inspect_environment`** — READ-ONLY questions about *this platform's own setup and
> contents*: what connections, tables, columns, agents, workflows, automations, code flows,
> schedules, secrets or MCP servers exist; how something is configured; what a table or
> column means. **This path never creates, modifies, runs or deletes anything.** Use it to
> answer a question about the environment, **or to gather facts before planning a build.**

The read-only framing is stated up front deliberately, so the router distinguishes it from
`build` (which mutates) by *intent to change* rather than by subject matter.

### 3b. The disambiguation rule (the part most likely to break something)

> Ask about the **container** (tables, columns, agents, configs) → `inspect_environment`.
> Ask about the **contents** (rows, totals, values — *"how many invoices are overdue"*) →
> data-agent query (`none`).

### 3c. Handler

Route `inspect_environment` to a handler that answers from CC's **own deterministic tools**
(`list_data_connections`, `get_connection_schema`, …) rather than delegating to a data agent.

### 3d. New read-only tools

CC has 69 tools but cannot currently enumerate:

| gap | consequence |
|---|---|
| agents (data + general) | can't pick the right agent — root of the AIRDB5_Wizard mis-pick |
| the **documented** data dictionary | `get_connection_schema` reads *live* schema; the curated table descriptions are unreadable by any tool |
| secrets (names only, never values) | asks the user to confirm `AUTODEMO_SFTP` exists |
| MCP servers + their tools | can't report what integrations exist |

### 3e. Kill switch

`CC_INSPECT_ENVIRONMENT`, **default on**, so the path can be disabled in production without a
deploy. (Matches `CC_STALL_GUARD` and `BUILDER_TABLE_CONTEXT`, both already shipped.)

## 4. Known risks

1. **Highest risk: mis-routing data questions.** If *"how many invoices are overdue"* starts
   going to `inspect_environment`, a working analysis path breaks. The container/contents
   rule is the mitigation; `pack 16 b12_numeric_grounding` is the regression guard.
2. Mitigating factor: the path is read-only, so a mis-route yields an unhelpful answer, never
   damage.
3. The capability router is a mini-LLM, so routing is probabilistic — a single passing test
   is not proof.

## 5. What to verify

Please confirm or refute, with evidence:

1. **Is the root cause right?** Is the absence of an environment capability at `nodes.py:767`
   really why this becomes `delegate`, or is something else (the main classifier at
   `nodes.py:1535`, or agent selection) the true cause?
2. **Is the container/contents boundary sound**, or are there realistic phrasings it
   mis-sorts? Specifically: *"what's in the invoices table"*, *"show me the sales data"*,
   *"what does the ERPDB connection have"*.
3. **Is adding a router capability the right layer?** Would a deterministic pre-check, or
   fixing agent selection, be safer or more targeted?
4. **Is anything in section 3d already implemented** somewhere the investigating agent missed?
   (It previously reported "the builder has no schema capability" and was wrong —
   `connections.discover_tables` existed; it had searched `builder_service/` when the code
   lives in `builder_agent/`. Please check for that class of error here.)
5. **What existing behaviour could this break** beyond the mis-routing risk above?

## 6. Ground rules

- **Do not implement.** Verification only.
- Verify claims against the code — several earlier findings in this effort were retracted
  after measurement contradicted them.
- Line numbers were confirmed on commit `c60407f`.
- If you disagree with the plan, say so plainly and propose the alternative.
