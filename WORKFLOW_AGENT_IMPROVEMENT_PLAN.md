# Workflow Agent — Improvement Plan for Builder Delegations

## Problem Statement

When the Builder Agent delegates a workflow creation task to the Workflow Agent, the delegation often fails because:

1. **The task description is too complex for single-shot processing** — the Builder sends a detailed, multi-clause description as one message (e.g., "Create a workflow that checks inventory levels daily, queries the Products database for items below reorder point, and emails the purchasing team"). The Workflow Agent's LLM (max_tokens=2000) can hit content filtering or complexity limits trying to process this in one turn.

2. **Mismatch between Builder's expectations and Workflow Agent's design** — The Builder expects a "fire-and-forget" delegation where the Workflow Agent immediately builds the workflow. But the Workflow Agent is designed for **iterative, phase-based conversation** (DISCOVERY → REQUIREMENTS → PLANNING → BUILDING), working best with 3+ turns of back-and-forth.

3. **The retry mechanism is too simplistic** — When the first attempt fails, the Builder truncates the description to 500 chars and prepends "Please create a simple version:". This often loses critical context.

## Current Architecture

```
Builder generates plan → Step says "agent:workflow_agent"
→ Builder calls _execute_agent_delegation()
→ Sends FULL task description as initial message
→ WorkflowAgent processes ONE message
→ Response is either:
   a) Questions (agent asking for clarification) → conversation continues
   b) Workflow commands (agent built it) → success
   c) Fallback error (content filter) → Builder retries once → likely fails again
```

## Proposed Improvements

### Option A: "Builder Mode" System Prompt Injection (Recommended)
**Effort: Low | Impact: High**

Add a special instruction to the Workflow Agent's system prompt when it receives a delegation from the Builder (vs. a human user). This instruction would tell it:

1. **Don't ask questions on the first turn** — the Builder has already gathered requirements
2. **Treat the initial message as a complete specification** — go straight to PLANNING/BUILDING phase
3. **Use smaller, focused workflow scope** — don't try to build a 15-node workflow from one message

Implementation:
- Add a `is_builder_delegation` flag to the `/api/workflow/builder/guide` request payload
- When `is_builder_delegation=True`, inject a "Builder Mode" system prompt addendum:
  ```
  You are receiving a task from the Builder Agent. The user's requirements have
  already been gathered. DO NOT ask clarifying questions. Instead:
  1. Extract the key workflow steps from the description
  2. Look up required IDs (connections, agents, users) using your tools
  3. Create a focused workflow plan and generate commands immediately
  4. If the description is too complex, build the CORE workflow first
     (2-4 nodes max) and note what can be added in a refinement pass.
  ```
- In `_execute_agent_delegation()`, pass `is_builder_delegation=True` in the send_kwargs

### Option B: Task Description Decomposition
**Effort: Medium | Impact: High**

Have the Builder pre-decompose complex workflow descriptions into simpler atomic descriptions before delegating:

1. Before delegation, use a small LLM call to extract the **core workflow concept** (2-3 nodes max)
2. Strip out secondary concerns (email notifications, scheduling, approvals) that can be added later
3. Send only the core concept to the Workflow Agent
4. If the core workflow succeeds, send follow-up messages to add the secondary features

Example:
- Original: "Create a workflow that checks inventory levels daily, queries the Products database for items below reorder point, generates a PO, emails the purchasing team, and archives completed orders"
- Decomposed seed: "Create a workflow that queries inventory levels from a database and sends email alerts for low stock items"

### Option C: Increase Token Limits & Reduce Temperature
**Effort: Low | Impact: Medium**

The current settings are:
- `max_tokens=2000` — quite low for complex workflow generation
- `temperature=0.7` (or 1.0 with reasoning) — high for structured output

Proposed changes:
- Increase `max_tokens` to 4000-6000 for Builder delegations
- Lower `temperature` to 0.3-0.5 for more deterministic output
- These changes would reduce content filter triggers

### Option D: Multi-Turn Delegation with Auto-Answers
**Effort: High | Impact: Very High**

Make the Builder Agent capable of auto-answering the Workflow Agent's questions:

1. Builder delegates with the full context (task description + system context)
2. If the Workflow Agent asks questions, the Builder uses its own LLM to generate answers from the original user request and system context
3. This creates an automated multi-turn conversation between Builder and Workflow Agent
4. Continue until workflow commands are generated or a max turn count is reached

This would preserve the Workflow Agent's iterative design while making delegation seamless.

## Recommended Approach

**Phase 1 (Quick wins):** Options A + C together
- Add "Builder Mode" prompt injection — low risk, high impact
- Increase max_tokens to 4000 — straightforward config change
- Lower temperature to 0.4 for builder delegations

**Phase 2 (If Phase 1 insufficient):** Option B
- Add task decomposition before delegation
- Extract core concept, send simplified seed message

**Phase 3 (Full solution):** Option D
- Auto-answer loop for multi-turn delegations
- Most robust but most complex to implement

## Remaining Retest Issues Analysis

### Issue 1: Workflow agent can't handle complex delegations
- **Root cause**: Single-shot delegation + 2000 max tokens + content filtering
- **Fix**: Options A + C above
- **Status**: PLAN ONLY — needs approval before implementation

### Issue 2: Schedule creation fails on permissions
- **Root cause**: The Builder plans a `schedules/create` step without checking if the user has scheduling permissions
- **Fix**: Add role pre-check in `execute()` before scheduling steps, OR add scheduling permission info to the planning context so the LLM knows not to plan schedule steps for users without the right role
- **Note**: The permission check at lines 914-933 of nodes.py already checks domain-level permissions — need to verify `DOMAIN_ROLE_REQUIREMENTS` includes the `schedules` domain

### Issue 3: Knowledge step executes without file (FIXED)
- **Root cause**: No file upload pre-check
- **Fix applied**: Added `FILE_REQUIRED_CAPABILITIES` check in `execute()`
- **Retest confirms**: Error message now says "No file attached — requires file upload" ✅
- **Possible enhancement**: Skip/defer the step instead of failing it, with a "waiting for file" status

### Issue 4: Data agent creation doesn't validate connection (FIXED)
- **Root cause**: No connection_id validation
- **Fix applied**: Added connection validation after `validate_and_correct_parameters()`
- **Retest confirms**: Error now says "Invalid connection_id: Products" with available connections listed ✅
- **Note**: "Products" as connection_id is a planning issue — the LLM is using a descriptive name rather than matching to actual system connections. The validation correctly catches this.

### Issue 5: Tools assigned to wrong agent (cascading failure)
- **Root cause**: No step dependency checking — when Step 1 (create agent) fails, Step 2 (assign tools) still runs with its pre-planned `agent_id` which may reference an existing agent
- **Fix options**:
  a) Add dependency resolution: if a step was supposed to use output from a failed previous step, skip it
  b) Add "abort on failure" option for steps with explicit dependencies
  c) Track created resource IDs and only allow tool assignment to agents created in the current plan
- **Recommended**: Option (a) — skip dependent steps when their prerequisite fails
