# Workflow Builder — Human Test Plan

The scenarios below test the **Workflow Builder** end-to-end. The tester builds a small workflow in the UI, runs it, and verifies the output. The fictional company context is preserved — these scenarios are framed as work a retail/wholesale/ecommerce employee might genuinely need to automate.

---

## How to run

1. Open the AI Hub UI and navigate to **Workflow Builder**.
2. For each scenario:
   1. Build the described workflow node-by-node.
   2. Save with the suggested name (so cleanup is easy later).
   3. Run it.
   4. Compare the output to the **Expected Result** column.
3. Score PASS / PARTIAL / FAIL.

---

## Scenario W1 — Daily Sales Summary (3-node)

**Persona:** Finance analyst building a daily revenue digest.

**Workflow name:** `HUMAN-TEST-W1-DailySales`

**Nodes:**
1. **Database query node** — query the Orders table for "orders placed in the last 24 hours", return `SUM(order_total)`, `COUNT(*)`, and channel breakdown.
2. **LLM transform node** — take the query result and format a 3-sentence executive summary: total revenue, order count, channel mix.
3. **Output / log node** — print the summary to the workflow log.

**Expected result:**
- Workflow saves without validation errors.
- Run completes in < 30 seconds.
- Log shows a clean 3-sentence summary with real numbers from the DB.
- Variables from node 1 are correctly referenced in node 2 (no `{{var}}` literals leaking through).

**Common failure modes:**
- Variable substitution fails → check syntax in the transform node.
- Database node hangs → check DB connection ID matches a live connection.

---

## Scenario W2 — Inventory Low-Stock Alert (4-node, with branching)

**Persona:** Ops analyst building an inventory monitoring workflow.

**Workflow name:** `HUMAN-TEST-W2-LowStock`

**Nodes:**
1. **Database query** — return all SKUs where `on_hand < 50` across all warehouses.
2. **Condition / branch** — if count > 0, continue to node 3; else continue to node 4.
3. **LLM transform** — produce a bulleted list of low-stock SKUs grouped by warehouse, plus a one-sentence recommendation.
4. **No-op / terminate** — log "Inventory healthy" if branch went here.

**Expected result:**
- Workflow saves and validates (no dangling connections).
- Run takes the correct branch based on real DB state.
- If branched to node 3, output is a structured list, not a generic LLM response.

**Common failure modes:**
- Condition node syntax — confirm the condition is evaluated against the node-1 output variable, not the raw input.

---

## Scenario W3 — Customer Win-back Email Drafting (3-node)

**Persona:** Marketing analyst drafting a win-back campaign.

**Workflow name:** `HUMAN-TEST-W3-Winback`

**Nodes:**
1. **Database query** — return customers whose `last_order_date` is between 60 and 120 days ago, limit 5 for testing.
2. **LLM transform (looped)** — for each customer in the result, generate a short personalized email subject + body.
3. **Output** — log the 5 generated emails.

**Expected result:**
- The looped node executes 5 times (once per customer).
- Each generated email contains the customer's first name (real variable substitution).
- No identical emails (LLM should vary the wording).
- No leakage of system prompts or PII not in the input.

**Common failure modes:**
- Loop only runs once if the loop binding is misconfigured.
- Variable for customer name resolves to the literal `{{first_name}}` if the path is wrong.

---

## Scenario W4 — Approval-gated workflow

**Persona:** Procurement analyst kicking off a PO above the auto-approval threshold.

**Workflow name:** `HUMAN-TEST-W4-Approval`

**Nodes:**
1. **Input node** — accept a PO amount as input.
2. **Condition** — if amount > $10,000, route to approval node; else auto-approve.
3. **Approval node** — pause the workflow until a human approver (you) approves or rejects via the Approvals UI.
4. **Output** — log "Approved" or "Rejected".

**Expected result:**
- Submitting $5,000 → auto-approves and logs "Approved".
- Submitting $25,000 → workflow pauses; an entry appears in the **Approvals** UI.
- After approving via the UI, workflow resumes and logs "Approved".
- After rejecting in a second run, logs "Rejected".

**Common failure modes:**
- Approval UI doesn't show the pending request → check permissions / role of the running user.
- Workflow auto-completes without pausing → condition evaluated wrong.

---

## Scoring table

| Scenario | Built OK? | Ran OK? | Output matches expected? | Pass/Partial/Fail | Notes |
|---|---|---|---|---|---|
| W1 — Daily Sales Summary |   |   |   |   |   |
| W2 — Low-Stock Alert |   |   |   |   |   |
| W3 — Win-back Emails |   |   |   |   |   |
| W4 — Approval-gated |   |   |   |   |   |

**Pass criteria:** all four scenarios should pass end-to-end. W4's approval pause/resume is the most likely failure point — pay extra attention there.

---

## Cleanup

After scoring, delete the four workflows from the Workflow list so they don't clutter the workspace for the next tester.
