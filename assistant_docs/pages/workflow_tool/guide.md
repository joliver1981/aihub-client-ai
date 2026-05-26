# Workflow Designer (`/workflow_tool`)

The Workflow Designer is the visual builder for automations. Users drag node types onto a canvas, wire them together with pass/fail/complete connections, configure each node's inputs and outputs, and run the result. This is a **developer/builder tier** page — it requires the `workflows` feature flag and the developer role.

> **Companion pages:** Built workflows are watched and managed from **Workflow Monitor** (`/workflow_monitor`) — runs, schedules, approvals, history. Use this page to **build and edit** workflows; use Workflow Monitor to **observe and operate** them.

---

## Page Layout

### Top bar — workflow controls
- **New** / **Manage** — open the Workflow Manager modal to create, open, search, categorize, or delete saved workflows.
- **Variables** — open the workflow variables panel (declare named variables that nodes can read and write).
- **Export / Import / Copy** — JSON export of the current workflow, import from a JSON file, or duplicate the current one.
- **Delete** — remove the currently open workflow (only visible when a workflow is loaded).
- **Save** — persist the current canvas to the server (top of the left toolbar).
- **Run / Stop** — execute the workflow against the live engine. **Save first** — Run does not auto-save.

### Left toolbar — node palette (drag onto canvas)

| Category | Nodes |
|---|---|
| **Data Sources** | Database, File, Folder Selector, Integration |
| **AI & Documents** | Document, AI Action, AI Extract, Excel Export |
| **Flow Control** | Conditional, Start Loop, End Loop |
| **Variables & Processing** | Set Variable, Execute App |
| **Communication** | Alert, Human Approval |
| **Compliance** | Compliance Process, Compliance Excel Export |

### Canvas — center
- Drag nodes from the toolbar onto the canvas.
- Click a node to open its configuration panel.
- Drag from a node's connection point to another node to wire them. Each connection has a type: **pass**, **fail**, or **complete**.
- **Fit to View** and **Scroll to Start Node** controls (top-right of canvas) help when the workflow grows large.

#### Node right-click menu
Right-clicking a node on the canvas opens a context menu with:
- **Rename** — change the node's display label without changing its config.
- **Configure** — open the node's configuration panel (same as left-click).
- **Set as Start** — mark this node as the workflow's starting point. Each workflow has exactly one start node.
- **Duplicate** — make a copy of the node with the same configuration (placed nearby; rewire connections manually).
- **Delete** — remove the node and any connections to/from it.

#### Connection (arrow) right-click menu
Right-clicking a connection arrow opens a context menu with:
- **Set as Pass** — change this connection to a pass (success) edge.
- **Set as Fail** — change this connection to a fail (error / false-branch) edge.
- **Set as Complete** — change this connection to a complete (either-outcome) edge.
- **Delete Connection** — remove the connection.

This is how you fix a connection that was created with the wrong type — don't delete and redraw, just right-click and reassign.

### Debug Panel — bottom
A resizable panel at the bottom of the page that shows live execution state. Expand/collapse with the chevron button. Four tabs:

- **Execution** — live log stream during a run. Shows the running status badge ("Not Running" / "Running" / "Completed" / "Failed") and per-node events as they happen. **Clear** button wipes the log.
- **Variables** — live table of all workflow variables and their current values during a run. Updates in real time as nodes write to variables. The most useful tab for debugging "why is my variable empty?" issues.
- **Node Output** — pick a node from the dropdown to see its raw output (after it has run). Helpful for inspecting what a Database query returned, what an AI Extract produced, or what stdout came back from an Execute App.
- **Execution Path** — the ordered list of nodes that actually executed in the most recent run. Useful for verifying that a Conditional took the branch you expected, or that a Loop iterated the right number of times.

If a user asks "why didn't my workflow do what I expected?", the Debug Panel is almost always the first place to look — specifically the Execution tab for errors, and the Variables tab for empty/wrong values.

### Built-in AI assistant on this page
The Workflow Designer ships with a dedicated **Workflow Agent** that can walk users through building a workflow conversationally — discovery, requirements, planning, and even emitting the commands that build/modify the canvas for them. Users open it via the assistant panel on the page. If a user is asking how to build something complex, **point them at the Workflow Agent** rather than walking them through every node-by-node detail.

---

## Workflow Lifecycle

1. **Create** — New from the Workflow Manager, or start dragging onto the empty canvas.
2. **Build** — Drag nodes, configure each one, connect them.
3. **Set a start node** — Right-click a node and mark it as the start, or use the Workflow Agent's `set_start_node` command. Every workflow needs exactly one start node.
4. **Declare variables** — Open the Variables panel to declare any named values nodes will pass between each other.
5. **Save** — Top-toolbar Save button. Saves to the server; required before Run.
6. **Run** — Top-toolbar Run button. Executes against the workflow engine.
7. **Monitor** — Switch to `/workflow_monitor` to see live status, history, errors, approvals, and scheduled runs.

---

## Variable Syntax (Important)

Workflows pass data between nodes via named variables. Two syntactic rules trip users up most often:

- **Reading a variable** in a config value: use `${variableName}` (dollar-brace).
- **Declaring an output variable** (the `outputVariable` field on most nodes): use a plain name, **no braces**.

So a node that runs `SELECT * FROM customers WHERE id = ${customerId}` and stores results into `customerResults` is correct. Writing `${customerResults}` in the `outputVariable` field is wrong and the validator will reject it.

SQL queries inside the **Database** node must use `${variableName}` substitution — they do **not** support `?` positional placeholders.

---

## Node Reference (User-Facing Summary)

This is a quick reference. Each node has many more options visible in its config panel.

### Data Sources

**Database** — Run SQL queries or stored procedures against a configured connection. The connection field expects the connection's numeric ID (string), not the name. Use the Connections page to set up the connection first.

**File** — Read, write, append, delete, check existence, copy, or move a file. To read a file's contents into a variable, both `saveToVariable: true` **and** `outputVariable` must be set — missing either is the most common reason a downstream node sees an empty value.

**Folder Selector** — Pick files from a folder by pattern, latest/first/largest, or all. Outputs an array of paths — usually paired with a Loop downstream.

**Integration** — Call an operation on a connected SaaS integration (QuickBooks, Shopify, Stripe, Slack, SharePoint, etc.). The `integration_id` is numeric; the `operation` is a snake_case key from the integration's template, not the display name. See the Integrations page for what's connected.

> **SharePoint specifically** now supports a full document lifecycle from inside workflows: path-based browsing/downloading (`list_folder_by_path`, `download_file_by_path`, `download_folder`), uploads (`upload_file`, `upload_content`), folder/file management (`create_folder`, `move_file`, `copy_file`, `rename_file`, `delete_file`), and direct knowledge-base import (`download_to_knowledge`, `import_folder_to_knowledge`). All write operations require the `Files.ReadWrite.All` scope — existing connections from before this change must be reconnected (or token-refreshed) once to pick up the new scope.

### AI & Documents

**AI Action** — Send a free-form prompt to a configured AI agent and store the text response. Use for analysis, summarization, content generation, or open-ended reasoning. The output is the response **text** directly — reference it as `${aiResult}`, not `${aiResult.response}`.

**AI Extract** — Pull **structured** fields out of text or a document (PDF/DOCX) according to a field schema you define. Preferred over AI Action when downstream nodes need predictable field names. **Tip:** AI Extract can read PDF/DOCX files directly — no Document node needed first; it's faster and more accurate to extract in a single LLM call.

**Document** — Get the raw text of a PDF/DOCX as a single string. Use only when you need the full text (for display, logging, or feeding to AI Action). For structured extraction, use AI Extract instead.

**Excel Export** — Write workflow data to an Excel file. Supports create-new, template-based, append, and intelligent UPDATE (with AI-assisted key matching and change detection). Append/template/update operations require an `excelTemplatePath`. When appending, that's typically the same path as the output file.

### Flow Control

**Conditional** — Branch on a comparison or check. Has two outgoing paths: **pass** (true) and **fail** (false). Prefer `conditionType: comparison` for simple checks — it handles type coercion automatically (string "10" vs number 10 compares correctly). Use `expression` only for complex multi-condition logic.

**Start Loop / End Loop** — Iterate over an array (database rows, selected files, etc.). The Loop node defines an `itemVariable` (e.g. `currentFile`) and an `indexVariable`. **Critical rule:** nodes **inside** the loop body must reference the item variable (`${currentFile}`), not the original array. End Loop has exactly one outgoing **pass** connection back to its matching Loop node — this is the iteration-back edge.

### Variables & Processing

**Set Variable** — Compute or store a value in a workflow variable. The `evaluateAsExpression` toggle is the key switch:
- **Off (default):** value is stored as a literal string.
- **On:** value is evaluated as a Python expression — supports `len()`, arithmetic, string concatenation, list/dict comprehensions, ternaries, and access to `math`, `json`, `re` modules. Required whenever you do *any* computation.

**Execute App** — Run an external executable, script, or system command. Capture stdout, parse as text/json/csv/regex, configure timeout, decide success codes. Inside a Loop, pass the item variable as an argument to act on each file/item.

### Communication

**Alert** — Send email, SMS, or phone-call notifications. Only **simple** `${variableName}` references are allowed in `messageTemplate` and `emailSubject` — no property access, indexing, function calls, or arithmetic. To compose a formatted message with computed values, use a **Set Variable** node first (with `evaluateAsExpression: true`) to build the string, then reference that single variable in the Alert.

**Human Approval** — Pause the workflow until a user or group approves. Creates three paths: **pass** (approved), **fail** (rejected), **complete** (either outcome). Configure assignee (user, group, or unassigned), title, description, data shown to the approver, priority, due hours, and what happens on timeout (auto-approve or auto-fail).

### Compliance

These nodes are specialized for retailer-compliance workflows. Retailer and document-set IDs are managed in `/compliance_management` — if a user is asking about them and isn't already familiar with that surface, point them there first.

**Compliance Process** — Routes one or more compliance documents to the right retailer-compliance agent for extraction. Two routing modes:
- `fixed` — pick a specific retailer + document set at design time.
- `dynamic` — resolve the retailer and/or set from workflow variables at runtime.

Agent assignment is controlled by `agentMode` (`per_retailer` = one agent for all of a retailer's sets, or `per_set` = a dedicated agent per category) and by `onMissing` (`skip` or `auto_create` — when `auto_create`, provide an `agentObjectiveTemplate` so the auto-created agent has a sensible objective). Optional overrides: `agentOverrideId`, `retailerAgentOverrideId`.

**Compliance Excel Export** — Exports the extracted fields of a compliance run to Excel. Two source modes:
- `version` — caller passes a `versionVariable` resolving to a specific `version_id` (typical when paired with Compliance Process upstream).
- `latest_in_set` — pick a `retailerId` + `setId` and the node resolves the most recent version automatically (the easiest way to wire "export the latest extraction for this retailer/set").

---

## Connection Rules

- Connection types are lowercase: `pass`, `fail`, `complete`.
- Most nodes use **pass** for success-path forward motion.
- Conditionals use **pass** for true, **fail** for false.
- Human Approval emits **pass** / **fail** / **complete**.
- **A node can have only ONE outgoing connection per type.** A node can't have two `pass` edges going to two different places — use a Conditional or split into separate paths.
- Don't create duplicate connections (same from-node + same type to the same target).

---

## Common Tasks

### "I want to process every file in a folder"
Folder Selector (outputs array) → Loop (loops the array; defines `currentFile`) → File / AI Extract / etc. (use `${currentFile}`) → End Loop.

### "I want to extract data from PDFs and put it in Excel"
Folder Selector → Loop → AI Extract (with `inputVariable: ${currentFile}`, define your field schema) → Excel Export (append mode, with `excelTemplatePath` = your target file) → End Loop.

### "I need someone to approve this before it goes out"
Build the workflow up to the decision point → Human Approval (assign a user/group, set due hours) → on **pass** path, continue with the action; on **fail** path, send an Alert or set a variable noting rejection.

### "I want this to run every morning"
Build and save the workflow here. Then go to `/workflow_monitor` and configure a schedule for it from the Schedules tab.

### "I want my workflow to call Shopify / QuickBooks / Slack"
Make sure the integration is connected at `/integrations` first. Then drag an **Integration** node, pick the integration, pick the operation, pass parameters with `${variable}` syntax.

### "I want to push a generated report / file to SharePoint"
Build the workflow that produces the file (e.g. Excel Export, AI Extract, or any node that writes a file) → **Integration** node with `integration_id` = your SharePoint connection, `operation` = `upload_file`, pass the local path as `source_file_path` (e.g. `${reportPath}`), and set `folder_path` or `parent_item_id` for the destination. Use `conflict_behavior` to control overwrite vs. rename. For pulling files the other way, use `download_file_by_path` or `download_folder` instead.

---

## Troubleshooting

**"Validator rejected my workflow on save/run"**
The most common causes:
- A Database node has the connection's name instead of its numeric ID.
- A SQL query uses `?` placeholders instead of `${variable}` substitution.
- `saveToVariable` is true but `outputVariable` is empty (or vice versa) on a File or Database node.
- An Alert's `messageTemplate` uses `${count + 1}`, `${items[0]}`, `${file.name}`, or `${len(rows)}` — only bare `${name}` is allowed.
- An End Loop is missing its `pass` edge back to the matching Loop, or has more than one.
- A node has two outgoing connections of the same type.

**"My variable is empty in the next node"**
- Check that `outputVariable` is set on the upstream node and the name matches.
- For File reads, confirm `saveToVariable: true`.
- Inside a Loop, you must reference the loop's `itemVariable`, not the original array.
- Open the Debug Panel → **Variables** tab during/after the run to see exactly what value (if any) was written.

**"How do I see what went wrong in a run?"**
- Open the Debug Panel (bottom of the page).
- **Execution** tab — error messages and per-node events.
- **Node Output** tab — pick the failing node from the dropdown and inspect its raw output.
- **Execution Path** tab — confirms which nodes actually ran (especially useful when a Conditional took an unexpected branch).

**"I drew a connection but it's the wrong type (pass/fail/complete)"**
- Don't delete and redraw — right-click the arrow and pick **Set as Pass / Fail / Complete**.

**"Conditional always takes the wrong branch"**
- Switch from `expression` to `comparison` — it handles type coercion correctly.
- If you must use `expression`, wrap string-valued variables in quotes: `"'${status}' == 'active'"` — without the quotes, after substitution the raw string becomes an undefined Python name.

**"Run does nothing"** — Save the workflow first. Run does not auto-save.

**"I don't see my saved workflow"** — Open the Workflow Manager (Manage button). Check the category filter and search box.

---

## What This Page Doesn't Do

- It doesn't show **run history** — that's `/workflow_monitor`.
- It doesn't manage **approvals waiting on you** — that's the Approvals page (linked from Workflow Monitor).
- It doesn't manage **schedules** — that's the Schedules tab on Workflow Monitor.
- It doesn't create **integrations** or **connections** — set those up on `/integrations` and `/connections` first, then reference them here.
