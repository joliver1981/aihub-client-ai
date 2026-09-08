# The Agent ↔ Command Center: full capability gap analysis + fix handoff

**Date:** 2026-09-02 · **Status:** ANALYSIS ONLY — no code was changed for this document · **Author:** Claude (Opus 5) session, read-only pass over both services
**Ask (James):** *"The Agent is the future but cannot do things CC agent can do."* → find every one of those things, and write a handoff another agent can execute.

**Method:** static source inventory of `agent_service/` (68 tools) and `command_center_service/graph/nodes.py` + `command_center/` (≈73 tools), plus both frontends, both system prompts, both renderer stacks, and the `aihub-agent` conda env's actual package list. Everything below cites a file and line. **Nothing was live-probed** — §9 lists the four claims worth a live confirmation before building.

---

## 1. Verdict in five sentences

1. **This is not a volume gap.** The Agent has 68 tools, CC has ~73. The difference is *which* nouns each one can touch.
2. **The single biggest hole is visual workflows.** CC has 13 tools that create, edit, wire, run, and inspect visual workflows; The Agent has **zero** — it can only *list* them read-only via `list_playbooks`. This is the **fourth instance of a known failure class** already documented three times in this repo (email → A6-5, documents → `document_tools.py`, portals → `portal_tools.py`): *every platform noun needs a first-class tool surface; prompts and skills cannot substitute for a missing tool.*
3. **The second biggest hole is output richness.** CC renders inline charts (Chart.js), maps (Leaflet + US-state choropleth), KPI cards, inline images, artifact chips and action buttons from a structured block protocol. The Agent renders markdown + interactive tables + file links, with charts only inside saved Views tiles. James himself set the bar in the A4 feedback batch ("CC chat's rich output is the BAR — we cannot lose that"); it is still the largest perceived-quality gap.
4. **Then a tail of eight discrete missing tools** — web search, arbitrary outbound email, platform-agent discovery/delegation, file export, artifact reading, PDF manipulation, SFTP/FTP, image generation — plus three *incomplete* code-flow editors (`unwire_steps`, `remove_code_step`, `update_step_code`) that make a code flow build-forward-only.
5. **The direction of travel is otherwise correct.** The Agent has eight substantial capabilities CC does not have at all (§5). Nothing here argues against The Agent being the future — it argues for four to six focused build passes.

---

## 2. Scope check — what each service actually is

| | **The Agent** (`agent_service/`) | **Command Center** (`command_center_service/`) |
|---|---|---|
| Port / env | :5111, conda `aihub-agent` (py311, deliberately thin) | :5011, conda `aihubbuilder` |
| Brain | Claude Agent SDK, `query(prompt, resume=session_id)`, one in-process MCP server `aihub`, `tools=[]` lockdown (`brain.py:132`, `:476`) | LangGraph state machine: intent classifier → converse / query / analyze / delegate / build / multi_step / create_tool (`cc_config.py:INTENT_CLASSIFICATION_PROMPT`) |
| Model | `AGENT_MODEL=claude-sonnet-5` (per-role: role<2 → haiku-4-5) | Azure/OpenAI via `get_llm()` |
| Tool count | **68** (`grep -c '^@tool(' agent_service/*.py`) | **≈73** bound at `nodes.py:6891–6994` |
| Output protocol | SSE events `text / tool / tool_result / guard / result` (`brain.py:494–607`); markdown rendered client-side | JSON array of typed blocks (`cc_config.py:528` `STRUCTURED_RESPONSE_FORMAT`) → `CCRenderers.renderBlocks` |
| Frontend | one 3,066-line `static/index.html`, 7 rails (Chat / My Work / Skills / Views / Email / Playbooks / Schedules) | `index.html` + `cc-renderers.js` (38 KB) + `command-center.js` + Studio + Ops Room + Inspector + Memory panel |

**Environment constraint that shapes every fix below** — the `aihub-agent` env is intentionally minimal. Verified by probing `C:\Users\james\miniconda3\envs\aihub-agent\python.exe`:

```
requests True   httpx True   pyodbc True
paramiko False  openpyxl False  pandas False  matplotlib False
reportlab False  PIL False  anthropic False  openai False
```

So: **anything needing the data-science or crypto-transport stack must either go over HTTP, go through `run_python` (which resolves `CODE_INTERPRETER_PYTHON` — a different, fat env, `code_exec/interpreter.py:47`), or add a dependency deliberately.** Precedent for the HTTP route already exists: `portal_tools._suggest_workflow_name` calls the Anthropic relay with raw `httpx` because the `anthropic` package is absent.

---

## 3. The gap table (CC can, The Agent cannot)

Ranked by user-visible impact. "Class" uses this repo's own vocabulary: **noun-without-tools** (the recurring failure class), **render** (capability exists, cannot be shown), **reach** (capability exists elsewhere, no tool routes to it).

| # | Missing in The Agent | CC seam | Class | Effort |
|---|---|---|---|---|
| **G1** | **Visual workflows — 13 tools.** `list_workflows` `get_workflow_structure` `create_workflow` `add_workflow_node` `update_workflow_node` `remove_workflow_node` `wire_workflow_nodes` `insert_workflow_node_between` `unwire_workflow_nodes` `set_workflow_start` `set_workflow_variable` `run_workflow` `check_workflow_run` | `nodes.py:6245–6910` over `command_center_service/graph/workflow_tools.py` | noun-without-tools | **L** (2–3 d) |
| **G2** | **Rich inline output** — charts, maps, KPI cards, inline images, artifact chips, action buttons | `cc_config.py:528` + `static/js/cc-renderers.js` (`_renderChart` :160, `_renderTable` :226, `_renderKPI` :265, `_renderMap` :344, `_renderArtifact` :537, `_renderAction` :566, `_renderImage` :601) | render | **M–L** (2–3 d) |
| **G3** | **`search_web`** — no internet access at all | `nodes.py:3856`, Tavily; key at `config.py:189` (`TAVILY_API_KEY_ENCRYPTED` → `decrypt_value`) | noun-without-tools | **S** (2 h) |
| **G4** | **`send_email`** — cannot compose outbound mail. Only `draft_email_reply` (reply to *inbound* A6 mail, approval-gated) and `schedule_view_email` | `nodes.py:4041` → cloud `POST /api/notifications/email`. The Agent **already talks to that endpoint** in `email_client.py:185` | noun-without-tools | **S** (3 h) |
| **G5** | **Agent discovery + general-agent delegation** — `list_platform_agents`, `query_general_agent`, `switch_active_agent`. The Agent's `ask_data_agent` (`platform_tools.py:263`) needs a numeric `agent_id` it has **no tool to discover** | `nodes.py:6672` (`GET /api/agents/summary`), `:3179`, `:3402` | reach | **S** (3 h) |
| **G6** | **Code-flow editing is one-way** — no `unwire_steps`, `remove_code_step`, `update_step_code`, `delete_code_flow`. A wrong step can be added but never fixed | `nodes.py:6035`, `:6061`, `:6079` over `graph/codeflow_tools.py` | noun-without-tools (partial) | **S** (3 h) |
| **G7** | **`export_data`** — no "give me this as Excel/CSV/PDF" tool | `nodes.py:3410` → `ArtifactManager` → `/api/artifacts/<id>/download` | reach (`run_python` can, model rarely thinks to) | **S–M** |
| **G8** | **Image understanding is OCR, not vision.** CC runs uploaded images through Claude Vision / Azure vision (`routes/upload.py:199 _analyze_image`). The Agent's `read_file` sends images to `/document/process` for text extraction only (`document_tools.py:900+`) — it cannot answer "what's happening in this screenshot/chart" | `routes/upload.py:188–308` | render/reach | **M** |
| **G9** | **`read_artifact`** + the shared artifact store — cannot read the contents of a file another agent produced | `nodes.py:4237`, `command_center/artifacts/artifact_manager.py` | reach | **S–M** |
| **G10** | **`manipulate_pdf`** — split/extract/rotate | `nodes.py:3615` → repo-root `pdf_tools.py` (pure functions, `pypdf`) | reach (`run_python`) | **S** |
| **G11** | **SFTP / FTP / FTPS** — `sftp_list_files` `sftp_download` `sftp_upload` | `nodes.py:5170/5201/5247` → `command_center/tools/sftp_transfer.py` (self-contained; `paramiko` lazy, FTP/FTPS on stdlib) | noun-without-tools | **S–M** |
| **G12** | **`generate_image`** (DALL·E) | `nodes.py:3800`, gated `CC_IMAGE_GENERATION_ENABLED` | noun-without-tools | **S** |
| **G13** | **`run_generated_tool`** + custom-tool packages — cannot run anything from the platform's custom-tools store | `nodes.py:3546` → `command_center/tools/tool_factory.py` + `tool_sandbox.py` | noun-without-tools | **M** |
| **G14** | **`list_mcp_servers`** — cannot answer "what integrations are set up". (Neither agent can *execute* MCP tools — see §6) | `nodes.py:6733` → `GET /api/mcp/servers` | reach | **S** (1 h) |
| **G15** | **User preference memory** — `save_user_preference` / `recall_all_memories` / `forget_preference`. The Agent has *Skills* (procedure) but no *preferences* (durable per-user facts like "always use the EDW agent") | `nodes.py:3213/3310/3357` → `command_center/memory/user_memory.py` | noun-without-tools | **M** |
| **G16** | `get_my_contact_info` — cannot answer "what's my email address on file" | `nodes.py:5140` | reach | **XS** |

### Also worth knowing (not tool gaps, but real deltas)

- **`run_python` output.** CC returns image/artifact **blocks** so a generated chart renders *in the chat* (`nodes.py:4194–4230`). The Agent stages produced files and returns `[⤓ chart.png]` **download links** (`code_tools.py:190–228`). Same engine, materially worse felt experience. Folded into **G2**.
- **`list_automations`.** The Agent's `list_playbooks` (`platform_tools.py:296`) covers automations + workflows + code flows in one read-only list. Adequate — not a gap.

---

## 4. Fix specs

The extension seam is proven and small. Every new tool module follows the same five-step recipe, and **the four hand-maintained lists in `brain.py` are the real drift hazard** — `tests_v2/unit/test_agent_brain_tool_lists.py` exists to catch exactly this, so run it after every addition.

**Registration checklist (do all five, every time):**
1. New module `agent_service/<x>_tools.py` exporting `<X>_TOOLS`.
2. `brain.py:25–34` — import; `brain.py:132–139` — concat into `create_sdk_mcp_server`, behind a kill switch following the `_DOCUMENT_TOOLS_ON` template (`brain.py:109–130`).
3. `brain.py:146` `MUTATING_TOOLS` — every write tool (or the mutation-claim honesty guard goes blind).
4. `brain.py:188` `_READ_TOOL_NAMES` — every read tool (or side-threads on work items silently lack it — this exact drift was fixed once in `bafa1e3`).
5. `brain.py:166` `SENSITIVE_TOOL_FIELDS` if any argument can carry a credential; `brain.py:203` `SYSTEM_PROMPT` section, **positively framed** — *twice-learned lesson in this repo: honest-agent framing plus a limitation line makes the model lead with disclaimers. Lead with the capability; put boundaries behind only-when-asked.*
6. A product skill under `agent_service/product_skills/` **and** full mode enumeration in the tool description itself — *tool descriptions outrank skills in-context* (the `save_view` ticker miss).
7. Pack-20 checks (`test_human/20_The_Agent/runner.py`) + a unit suite under `tests_v2/unit/` (**`git add -f`** — `.gitignore` hides `test*.py`).

---

### G1 — Visual workflow tools (do this first)

**Why first:** it is the last platform noun with no tool surface, and it is the one James's own product story ("build me a workflow") most obviously fails on today.

**The good news:** `command_center_service/graph/workflow_tools.py` is **fully self-contained** — its only imports are `json/logging/re/time/uuid/typing`, `requests`, and an optional fail-open import of repo-root `workflow_node_schemas`. It talks to the main app purely over HTTP (`_get` :125, `_post` :149) and encodes all the hard-won doctrine already:

- one write chokepoint through the guarded `POST /save/workflow` (same endpoint as the canvas UI and the builder — so the AIHUB-0039 code-flow kind-guard and AIHUB-0016 deterministic validation protect this caller too);
- true read-back after every save, re-resolving **by name** then by id, and loudly flagging a name→row mismatch (the AIHUB-0041 wrong-row bug class);
- competing edges are a hard error at wire time (AIHUB-0045);
- code-flow rows are refused client-side (AIHUB-0039);
- never raises for a remote failure — returns `{"ok": False, "error": ...}`.

**Import hazard:** `import command_center_service.graph.workflow_tools` executes `command_center_service/graph/__init__.py`, which imports **langgraph** — not present in `aihub-agent`. Do **not** add langgraph to the agent env. Load the module by file path instead:

```python
# agent_service/workflow_authoring_tools.py
import importlib.util, os
from agent_config import APP_ROOT
_spec = importlib.util.spec_from_file_location(
    "aihub_wf_tools",
    os.path.join(APP_ROOT, "command_center_service", "graph", "workflow_tools.py"))
_wt = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_wt)
```

(`APP_ROOT` is already on `sys.path` — `agent_config.py:41`. Do this lazily inside tool bodies, the `portal_tools.py` pattern, so an import failure is an honest tool error rather than a dead service.)

**Scope:** port all 13 as thin `@tool` closures (`nodes.py:6245–6910` are the reference bodies; each is ~15 lines over a `workflow_tools` call plus `_wf_mutate_and_save`). Keep CC's `_wf_json_obj` argument validation and its empty-workflow refusal in `run_workflow` ("EMPTY — nothing to run; do not report this as a successful run").

**Deltas to make while porting** (same discipline as the portal port):
- `run_workflow`'s wait is bounded at 300 s in CC; keep the bound but return an explicit `execution_id` + "still running — call `check_workflow_run`" handoff rather than a long silent block.
- Register `run_workflow`, `create_workflow`, and every mutator in `MUTATING_TOOLS`; `list_workflows` / `get_workflow_structure` / `check_workflow_run` in `_READ_TOOL_NAMES`.
- Gate: new `AGENT_WORKFLOW_TOOLS` (default true) **and** the existing Developer+ build gate (`AGENT_BUILD_ALLOW_ALL_USERS`) — visual workflows stay Dev+ exactly like CC's `_workflow_tools_allowed`.

**Verify with:** pack 14 (`test_human/14_Workflow_Node_Matrix/`) already owns per-node execution contracts — do not duplicate it; add pack-20 checks for *authoring* only (create → add node → wire → read-back → run → honest status → delete).

---

### G2 — Rich inline output

**The decision to make first** (flag it to James, don't guess): The Agent's stream is markdown text, not typed blocks. Two viable designs:

| | **A. Fenced-block convention** *(recommended)* | **B. New SSE event type** |
|---|---|---|
| How | Model emits ` ```aihub-chart {json}``` `; `renderMd` (`index.html:775`) intercepts the fence before sanitizing and mounts a renderer | New `{"type":"block", ...}` event from `brain.run_turn`; UI mounts it between text bubbles |
| Blast radius | **Zero on `run_turn`** — pure frontend + a prompt/skill section | Touches `brain.run_turn`'s hot loop — the same loop that carries the mutation-claim honesty guard (`all_text` / `mutation_succeeded`) |
| Reuse | `index.html` already has `renderChart` (:1263), `renderTicker` (:1311), `renderTileData` (:1327), `enhanceTables` (:677) built for Views tiles — a chat block renderer is mostly a re-mount | Same reuse, plus new plumbing |
| Downside | Model must emit well-formed JSON in a fence; needs a fallback that shows the raw table if parsing fails | — |

**Strong recommendation: A.** The parked streaming-progress handoff (`docs/the-agent-streaming-progress-handoff.md`) records the lesson verbatim: *for a presentational feature, the "cleaner" in-`run_turn` design is the wrong call — isolation beats elegance when blast radius is on the critical path.* Same reasoning applies here.

**Sub-items, in order:**
1. **`chart` / `kpi` blocks** — reuse `renderChart` + the `.tile` CSS already in `index.html:325–329`. Biggest ratio of felt improvement to work.
2. **Inline images.** Blocked today by design, not by rendering: `/api/files/<id>` is Bearer-authenticated, so `<img src>` cannot load it (this is the same doctrine that makes `.filelink` buttons fetch-with-header → blob). Fix: in `renderMd`, treat a markdown **image** whose target is `/api/files/<id>` the way `.filelink` treats an anchor — fetch with the auth header, `URL.createObjectURL`, set `src`. **Do not** add a token-in-URL escape hatch; that would regress the no-token-in-transcript rule.
3. **`run_python` charts inline** — once (2) lands, `code_tools.py:190–228` can emit a markdown image for produced `.png` files alongside the existing download link. Small change, big payoff.
4. **`map` blocks** — Leaflet + `command_center_service/static/data/us-states.geojson` (89 KB) for choropleth. ⚠ The Agent **vendors everything for offline installs** (that is why Views got spans-in-a-grid instead of Gridstack) — Leaflet and the GeoJSON must be vendored into `agent_service/static/vendor/`, not CDN-loaded. This is why maps are ranked last in G2.

---

### G3 — `search_web` (smallest high-value win)

Port `nodes.py:3856` almost verbatim. **One correction to make:** CC reads `os.environ.get("TAVILY_API_KEY")` — but on this box the key is stored **encrypted** (`TAVILY_API_KEY_ENCRYPTED` in `.env`; `config.py:188–189` decrypts it via `decrypt_value`). CC's plain-env read means CC's own web search is probably dead here unless something else exports the plaintext — **worth a live check (§9)**. The Agent should resolve it the same way `agent_config.ensure_anthropic_key()` already resolves the Anthropic key: `encrypt.decrypt_value(<encrypted>, key)`, falling back to plain env. Return an honest "web search is not configured" when absent. `httpx` is available; no new dependency.

---

### G4 — `send_email`

Do **not** re-implement transport. `agent_service/email_client.py:175–190` already POSTs to the cloud `/api/notifications/email` — the exact seam CC uses (`nodes.py:4119`). Add `send_email(to, subject, body, attach_file="")` where `attach_file` accepts the same reference forms `read_file` and `import_documents` already resolve (`file_tools.resolve_api_files_ref`) — server path, `/api/files/<id>` link, or chat-attachment id.

**Policy question for James — do not decide this unilaterally.** A6's whole design routes outbound mail through `draft_email_reply` → **My Work approval** → send. A free `send_email` tool bypasses that gate. Options: (a) approval-gated by default with an `auto_send` per-user option mirroring the existing A6 `auto_send` column; (b) Developer+ only; (c) unrestricted like CC. Recommend **(a)** — it preserves the platform's existing consent model and reuses machinery that already exists.

---

### G5 — Agent discovery + delegation

Three thin tools over `GET /api/agents/summary` (`nodes.py:6672` shows the shape: id, name, enabled, `is_data_agent`, `connection_names`):
- `list_platform_agents()` — read.
- `query_general_agent(agent_id, question)` — mirror `ask_data_agent`, hitting the same `/api/agents/<id>/chat`.
- Skip `switch_active_agent` — it is a CC-session concept (`ActiveDelegation` in `graph/__init__.py`) with no analogue in the SDK loop.

Then **update `ask_data_agent`'s description** to say "call `list_platform_agents` first if you don't know the id" — today it just says *"Requires the numeric agent id"*, which is a dead end for the model.

---

### G6 — Finish code-flow editing

Three ports from `nodes.py:6035/6061/6079` into `agent_service/authoring_tools.py`, over the same `/codeflows/api/internal/manage` seam the existing 8 code-flow tools use. Add `delete_code_flow` with the two-step confirmed-delete pattern already used by `delete_automation` (`authoring_tools.py:524`). Register the three mutators in `MUTATING_TOOLS`. This one is nearly free and removes a genuine dead end.

---

### G7 / G10 — `export_data` and `manipulate_pdf`

Both are **reach** gaps, not capability gaps: `run_python` resolves `CODE_INTERPRETER_PYTHON` (`code_exec/interpreter.py:47`), a fat env with pandas/openpyxl/matplotlib, and produced files already come back as `/api/files` links. Two ways to close them:

- **Cheap (recommended first):** a `SYSTEM_PROMPT` + skill line — *"to produce an Excel/CSV/PDF file, or split/rotate a PDF, write it with `run_python` and hand back the returned link."* Ship this in an hour and measure.
- **Proper:** first-class `export_data(format, name, rows_json)` and `manipulate_pdf(path, operation, ...)` tools that shell into the same interpreter (or, for PDFs, import repo-root `pdf_tools` — pure functions over `pypdf`, but `pypdf` is **not** in `aihub-agent`, so it must run in the interpreter env or add the dep).

⚠ **This repo's own recorded lesson cuts against the cheap option**: *"a skill can encode judgment but CANNOT substitute for a missing tool — the model recites the skill's worldview verbatim"* (the document-tools finding, commit `bfa8942`). Treat the prompt line as a stopgap, and measure whether the model actually reaches for it before deciding the tools are unnecessary.

---

### G8 — Vision on uploaded images

Today: a pasted screenshot goes through `read_file` → `/document/process` → **text extraction only** (`document_tools.py:900+`; `ocr=true` forces AI extraction but still returns text). CC instead base64s the image into a Claude Vision (or Azure vision) call and returns a description (`routes/upload.py:199–308`).

The Agent's brain **is** Claude and could see the image natively — but attachments reach the model as a server-path text block (`main.py` `/api/chat` `attachments` handling), never as an image content block, and the SDK's in-process MCP tool results are text. Two options:

- **Cheap:** a `describe_image(path, question="")` tool that raw-`httpx`-posts an image block to the Anthropic relay (`AGENT_RELAY_URL` is already configured; `portal_tools._suggest_workflow_name` is the working precedent for a raw relay call without the `anthropic` package). Returns a description into the transcript. ~half a day.
- **Right, but larger:** teach the chat envelope to attach true image content blocks so the brain sees pixels directly. Needs SDK support investigation — **flag as an open question, do not start here.**

---

### G11 / G12 / G13 / G14 / G15 / G16 — the tail

- **G11 SFTP** — `command_center/tools/sftp_transfer.py` is self-contained, synchronous, returns `{"ok": False, "error": ...}` and never logs passwords. FTP/FTPS work on stdlib; **SFTP needs `paramiko`, absent from `aihub-agent`** — either add the dep or ship FTP/FTPS first with an honest "SFTP not available in this environment" (the module already does exactly that for CC's env). Credentials are passed per call in CC — consider wiring The Agent's existing `local_secrets` seam instead, which would be a genuine improvement over CC.
- **G12 image generation** — `nodes.py:3800` uses the `openai` package (absent). Raw `httpx` to the OpenAI images endpoint with `api_keys_config.get_active_openai_key()`. Honor `CC_IMAGE_GENERATION_ENABLED` so one platform flag governs both agents. **Depends on G2 item 2** or the image only comes back as a download link.
- **G13 custom tools** — `tool_factory.get_generated_tool` + `tool_sandbox.test_tool_in_sandbox`; note `tool_factory` reads *both* the CC store and the platform `CUSTOM_TOOLS_FOLDER` store (`_get_platform_tools_dir`, the AIHUB-0020 F1 fix). Developer+ only, exactly as CC gates it (`_build_allowed`).
- **G14 MCP inspection** — one read tool over `GET /api/mcp/servers` and `/api/mcp/servers/<id>/tools_v1`. An hour's work; makes "what integrations do we have" answerable.
- **G15 preferences** — `command_center/memory/user_memory.py` is a real store with its own DB helper. **Design question for James:** does The Agent get its *own* preference store (mirroring `mywork.db`/`views_store` sidecar precedent) or share CC's? Sharing means both agents learn from one user model, which is probably what "The Agent is the future" wants — but it couples two services to one table. Recommend a short options note before building.
- **G16 contact info** — trivial; fold into whichever pass touches `platform_tools.py`.

---

## 5. What The Agent has that CC does not — do not regress these

Listed so the implementing agent doesn't "port CC over" and flatten the thing that makes The Agent better.

| Capability | Where |
|---|---|
| **My Work** — one approvals inbox unifying workflow `ApprovalRequests`, automation checkpoint sidecars, and email drafts, with read-only **side threads** per item | `work_tools.py`, `workitem_store.py`, `readthrough.py` |
| **Views** — saved dashboards: SQL + automation-backed tiles, user/group/tenant scopes, scheduled refresh + email delivery, drag-arrange/resize/rename, inline "Edit with AI" | `views_tools.py`, `views_store.py` |
| **Skills** — procedural memory with product/tenant/group/user precedence and approval-gated publishing | `work_tools.py:750`, `skills_mount.py`, `product_skills/` |
| **Personal agent email inbox (A6)** — per-user address, inbound polling, reading tools, attachment save, auto-send/approval options, per-address cooldowns | `email_*.py` (5 modules) |
| **Schedules surface** — view/run-now/pause/delete/create across every job type, with run history; plus the additive `POST /api/scheduler/jobs/<id>/run-once` engine route the old `/run/<id>` could not serve | `schedules_api.py` |
| **Deferred results → chat** — a scheduled run *resumes the originating conversation* instead of dead-ending in a notification | `brain.py:45–107`, `chat_history.py` |
| **Portal hand-back bridge** — after a 2FA takeover the completed run wakes the conversation on its own. CC's `_deliver_portal_result` still drops `final_result` on 0-file reads | `portal_watch.py` |
| **`read_file`** — one universal, non-storing reader for any common type | `document_tools.py:900` |
| Timezone contract (browser zone stamped into every turn), per-role model + daily turn caps, mutation-claim honesty guard, tool chips with result peek, BYOK/relay | `main._turn_envelope`, `usage_store.py`, `brain.py:146–183` |

---

## 6. Non-gaps — things that look like gaps and are not

- **MCP tool *execution*.** Neither agent can call an MCP server's tools; CC only *lists* them (`nodes.py:6733`). Not a Agent-vs-CC gap — a platform gap for a separate conversation.
- **Multi-agent orchestration.** CC's intent classifier / task decomposer / delegator (`command_center/orchestration/`) exists because a LangGraph state machine needs explicit routing. The SDK's own tool loop covers the same ground in one turn. Do **not** port the decomposer.
- **`switch_active_agent` / `delegate_to_builder_agent`.** CC delegates to the builder agent for things it cannot do; The Agent authors directly. Porting builder delegation would re-import the translation loss the native tools exist to avoid.
- **Session/thread persistence, side threads, schedules panel.** Both have these.
- **`list_automations`** — covered by `list_playbooks`.
- **Document search.** Both call the same `POST /api/internal/document-search-unified` (`document_search_wrapper.py`, commit `5ecc52d`). Parity already achieved.

---

## 7. Suggested sequencing

| Pass | Contents | Rough effort | Why here |
|---|---|---|---|
| **1 — quick wins** | G3 web search · G4 send_email · G5 agent discovery · G6 code-flow editors · G14 MCP list · G16 contact | ~2 days total | Six visible capabilities, all thin HTTP wrappers, no new deps, no architecture decisions except G4's approval policy |
| **2 — the missing noun** | G1 visual workflow tools (all 13) | 2–3 days | Largest structural gap; module is self-contained; closes the fourth instance of the noun-without-tools class |
| **3 — richness** | G2 items 1–3 (charts/KPI, auth-fetched inline images, `run_python` charts inline) | 2 days | Highest perceived-quality delta; frontend-only under design A |
| **4 — reach** | G7 export · G10 pdf · G8 `describe_image` · G9 read_artifact | 2 days | Mostly wrappers over machinery already reachable |
| **5 — optional** | G2 item 4 maps (vendored Leaflet) · G11 SFTP · G12 image gen · G13 custom tools · G15 preferences | as prioritized | Each carries a dependency or a design decision |

**Gate every pass** with the full pack 20 run (`test_human/20_The_Agent/runner.py`, ~72+ checks) plus `tests_v2/unit/test_agent_brain_tool_lists.py`. Before burning a 30-minute gate, check that **10.0.0.6 SQL is up** — the flake ledger in memory records several full-gate runs lost to that box being down, with the agent's honest refusals mis-scored as regressions.

---

## 8. Traps the implementing agent must know

Collected from this repo's own scar tissue — every one of these has already cost a session.

1. **A parallel Claude session often works in this same tree.** Verify `git diff` is 100% yours before staging; stage explicit files, never `git add .`; `git fetch` before pushing; never `git checkout` a shared file.
2. **`.gitignore` hides `test*.py`** — new test files need `git add -f`.
3. **`aihub-agent` is deliberately thin** (§2). Prefer HTTP or the code-interpreter env over adding dependencies; every added dep also has to survive `scripts/build_agent_service.ps1` conda-pack and the v5 installer's Service 15.
4. **Never put a helper function between `@tool()` and the function it decorates** — it breaks `create_sdk_mcp_server` registration at import.
5. **`@tool` returns an `SdkMcpTool`, not a callable** — smoke-test handlers via `.handler(args)`.
6. **`index.html` is served by `FileResponse`** — UI changes are live on browser reload, no :5111 restart. Python changes need the targeted restart: kill the PID owning 5111, then `Start-Process -FilePath agent_service\start_agent_service_dev.bat` (a cmd-with-redirect inherits stdout into the spawned window and blocks forever).
7. **Tool descriptions outrank skills in-context** — when a capability grows, update the description *and* the skill.
8. **Frame capabilities positively in the prompt.** Twice recorded: honest-agent framing plus a limitation line makes the model lead with what it *can't* do.
9. **Grade schedules by the engine's computed next run**, never the stored cron expression.
10. **Port a consumer's parsing verbatim from the working consumer** — the A6 empty-body bug came from reinventing an envelope unwrap from assumption.
11. **`8100` is the builder service; the vector API is `:5031`.** Map port → service via the V3 bat before killing anything.

---

## 9. Live checks worth doing before building

These four claims are source-derived and would be cheap to confirm on the running box:

1. **Does CC's `search_web` actually work here?** `nodes.py:3862` reads plain `TAVILY_API_KEY`, but `.env` only carries `TAVILY_API_KEY_ENCRYPTED`. If CC's web search is dead too, G3 becomes a *platform* fix, not a port.
2. **Does CC's chart/map rendering still work end-to-end** on this build, or has the block protocol drifted? The port target should be the behavior James actually sees, not the code.
3. **`workflow_tools.py` file-path import from the `aihub-agent` env** — one throwaway script proves the langgraph-free load and the `workflow_node_schemas` fail-open before G1 is scheduled.
4. **`/document/process` on a photo-of-a-chart** — confirms the vision gap is real (text-only) rather than partially covered by the engine's image handler.

---

## 10. Decisions needed from James

| # | Decision | Recommendation |
|---|---|---|
| D1 | **`send_email` policy** — free-send, Developer+, or approval-gated with a per-user `auto_send` opt-in? | Approval-gated with opt-in (reuses the A6 consent model) |
| D2 | **Rich output design** — fenced-block convention (frontend-only) vs a new SSE block event (touches `run_turn`) | Fenced-block; isolation beats elegance on the critical path |
| D3 | **User preferences** — The Agent's own sidecar store, or share CC's `user_memory`? | Options note before building; shared is probably right for "one front door" but couples the services |
| D4 | **Maps** — worth vendoring Leaflet + 89 KB GeoJSON into the offline-install bundle? | Defer to pass 5; charts and KPI cards deliver most of the felt gain |
| D5 | **Dependencies** — is adding `paramiko` (SFTP) and/or `pypdf` to `aihub-agent` acceptable, given each must survive conda-pack + installer? | Ship FTP/FTPS first without `paramiko`; decide on evidence of real SFTP demand |
| D6 | **Sequencing** — is G1 (workflows) or G2 (rich output) the true P0? | G1 — a missing noun is a hard "no I can't"; poor rendering is a soft "that looks worse" |

---

## 11. One-line summary for the fixing agent

> Port CC's 13 visual-workflow tools (self-contained module, file-path import to dodge langgraph), add six thin HTTP wrapper tools (web search, send email, agent discovery, general-agent delegation, MCP list, contact info), finish the three missing code-flow editors, and give the chat surface inline charts/KPIs/images via a fenced-block renderer that reuses the Views tile code — then gate on pack 20 and `test_agent_brain_tool_lists.py`. Do not port CC's orchestration layer, and do not regress My Work, Views, Skills, A6 email, Schedules, or the honesty guards.
