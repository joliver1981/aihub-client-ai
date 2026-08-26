# Code Interpreter Unification Plan

**Date:** 2026-08-26 · **Status: PLAN ONLY — no code changed.**
**Scope:** give every assistant surface (GeneralAgent, CC agent, The Agent) ChatGPT-style
"Advanced Data Analysis" over uploaded files. **OpenAI/Azure (legacy GeneralAgent) first** —
that is what every client runs today; The Agent picks it up nearly free.

---

## 1. The headline finding: we already built this

The spec ("Implementing a Multi-Format Code Interpreter API") reads as if we are starting
from zero. We are not. **Command Center already ships a working code interpreter that
matches the spec's target behavior end to end:**

| Spec requirement | Existing CC implementation |
|---|---|
| Model writes Python dynamically | `run_python` tool bound into the converse graph (`command_center_service/graph/nodes.py:4038`) with system-prompt doctrine at `nodes.py:2905`: *"the table in chat is a PREVIEW… for ANY row count/sum/group-by ALWAYS run_python against the actual file"* |
| Execute against the real file, isolated | `command_center/tools/code_interpreter.py` — per-call temp workdir, subprocess, timeout (`CODE_INTERPRETER_TIMEOUT`, 60 s default), stream truncation |
| File pipeline (upload → sandbox) | `prepare_workdir()` copies the session's uploads (ACL-checked via `_file_is_accessible_to`) **and** prior session artifacts into the workdir by original filename |
| Return program-generated results + artifacts | `harvest_outputs()` diffs the workdir and registers new files (charts, derived CSV/XLSX) as session artifacts |
| Runtime with pandas/numpy/matplotlib on a stock client | shipped `python-bundle` + `code_interpreter_env.py` background-provisions extras (scipy, sklearn, seaborn, statsmodels…) at CC startup — no conda, no Docker |
| Works when frozen/installed | interpreter resolution: `CODE_INTERPRETER_PYTHON` override → shipped bundle ([code-interpreter-client-deploy] memory: reuse the shipped bundle, never dev conda) |

This lane is live, gated per-user (`_code_interpreter_allowed`), and exercised by the
Code Flows / on-the-fly-automations work.

**The actual gap:** the two surfaces clients and the next-gen path use don't have it.

- **GeneralAgent (legacy app, Azure OpenAI — every client):** has hand-rolled per-format
  tools instead — `process_csv`/`show_csv`, `load_text_file`, whole-page budget readers
  (admit-or-deny P1/P2), and `analyze_excel_data` via **PandasAI**. Each of these was a
  patch on the same root problem (LLM doing math on tokenized text), and each has cost us
  bug rounds (dead temp paths → 429bd47; PandasAI param gaps → e5e6c85; terra
  tools+reasoning_effort → 25d160c; per-row Excel AI calls ~1.4 s/row).
- **The Agent (:5111, Anthropic sonnet-5):** file_tools read files; no execute lane.

So the project is **extraction and rewiring, not construction**: lift the CC executor into
a shared backend, bind it as a tool on the other two surfaces, then retire the hand-rolled
lanes it obsoletes. Roughly days, not weeks — because the hard 80 % (executor, file
staging, artifact harvest, bundle provisioning, installer story) is built and shipping.

---

## 2. Assessment of the spec's two approaches

### Approach A — Azure AI Foundry Agent Service (`CodeInterpreterTool`): **NO as written**

Correct that it's the successor to the retired Assistants API, and the sandbox itself is
enterprise-grade. But as written it makes the Foundry runtime own the conversation
(agent profile + thread + poll), which conflicts with our platform reality:

1. **It's a second agent harness.** Platform strategy verdict ([platform-strategy-analysis-2026-08])
   is *harness-on-top*: our loop owns tools, authz, session ledger, approvals, logging.
   Delegating analysis turns to a Foundry thread loses all of that — and re-opens the
   exact forensics gap the logging-research analysis just mapped (answers we can't trace).
2. **Per-client cloud provisioning.** Every client install would need an AI Foundry
   project, agent, and file store in *their* Azure tenant — a new provisioning and support
   surface for ~zero capability we don't already have locally.
3. **File egress + latency.** Every analysis uploads the file to Azure storage first
   (retention/purge governance, compliance questions per client) and pays cloud
   round-trips; the local lane answers a 100 MB-CSV row count in ~1–2 s for free.
4. **Model lock.** Foundry agents run Azure OpenAI models only → The Agent (Claude) is
   explicitly *not* free under this approach. The spec's "GPT-4o" target is also stale —
   clients are pinned `gpt-5.4-mini`, dev runs `gpt-5.6-terra/luna`.

Keep Foundry in the back pocket only as a *managed execution venue* if a specific client
ever demands MSFT-managed sandboxing in writing. Do not build toward it now.

### Approach B — `codeinterpreter-api` (open-source): **NO, firmly**

- The package is effectively **unmaintained** (2023-era LangChain wrapper); building a
  production lane on it is adopting abandonware.
- It executes in **Docker/Jupyter** — Docker Desktop on client Windows boxes (licensing,
  WSL2, IT policy) is a non-starter for our install base.
- Everything it actually does (LLM writes code → execute in a workdir → return
  stdout/files) is ~400 lines we already wrote, tuned to our frozen-onedir world, in
  `code_interpreter.py`.

### The option the spec missed (relevant later, not now)

**Azure Container Apps *dynamic sessions*** is Azure's model-agnostic sandbox-as-REST
primitive (Hyper-V-isolated session pools, file upload API, "execute this code" call —
no agent runtime attached). If a tenant ever wants hardened cloud isolation, it slots in
*behind our tool contract* without changing any agent surface, and it works with Claude
too. That — not Foundry-as-runtime — is the correct cloud story. Same shape applies to
Anthropic's server-side code-execution tool for The Agent someday. Both are Phase-4
options at most; verify current API versions during that phase, not now.

---

## 3. Recommended architecture

**One executor, three thin tool bindings.** Separate *who writes the code* (each
surface's own model, in its own loop) from *where it runs* (one shared backend).

```
GeneralAgent (Azure OpenAI, LangChain BaseTool catalog)   ┐
CC agent     (LangGraph converse node — already wired)    ├──►  shared executor
The Agent    (:5111, Anthropic, @tool registry)           ┘    (today: code_interpreter.py
                                                                mechanics — workdir + stage
                                                                files + subprocess + harvest)
                                                                     │
                                                        pluggable backends (later):
                                                        local bundle python (default, ships now)
                                                        → hardened local (job objects, low-priv)
                                                        → ACA dynamic sessions (opt-in cloud)
```

- **Extraction:** move the surface-neutral mechanics of
  `command_center/tools/code_interpreter.py` (interpreter resolution, workdir prep,
  run, truncate, harvest, cleanup) into a shared module (e.g.
  `common/code_exec/`). CC's file becomes a thin adapter over it — CC behavior unchanged.
  File *staging* and artifact *harvest* stay per-surface adapters, because each surface
  has its own upload store and artifact store (CC: `routes/upload` + shared artifact
  manager; GeneralAgent: `_resolve_uploaded_file_path` world from 429bd47; The Agent:
  its file/work stores).
- **Library first, service later.** Phase 1 reuses it in-process (like CC today): zero
  new ports, zero installer churn, fastest path to clients. A dedicated low-priv NSSM
  service (via the `aihub-new-service` recipe) is the Phase-3 *hardening* step if we want
  real privilege separation — not a prerequisite.
- **Provider-agnostic by construction:** the tool contract is
  `execute(code, staged_files) → {stdout, stderr, returncode, artifacts[]}`. Azure OpenAI
  writes pandas on the legacy side; sonnet-5 writes pandas on The Agent side; the
  executor doesn't care. **This is exactly the "solve The Agent for free" property** —
  and it's the property both spec approaches destroy.

---

## 4. Surface-by-surface wiring

### 4.1 GeneralAgent — Phase 1, the client-facing payoff

1. New `run_python` BaseTool in the legacy tool modules, registered in
   `core_tools.yaml` (the binding-guard chokepoint from 429bd47 protects the rollout).
2. Staging adapter: seed the workdir from the session's admitted uploads via the
   fresh `_resolve_uploaded_file_path` machinery — the admit-or-deny work (1d40773)
   already guarantees an admitted file has a live path and sidecars.
3. System-prompt doctrine (port CC's `nodes.py:2905` block): previews are previews;
   any count/sum/aggregate over an uploaded tabular file MUST go through `run_python`.
   This is a prompt directive, not regex routing ([feedback-minillm-over-regex]).
4. Demote (don't yet delete) the overlapping lanes: `process_csv` summaries and the
   whole-page readers become *preview/context* tools; math belongs to `run_python`.
5. Config: `GENERAL_AGENT_CODE_INTERPRETER=true|false` env gate, shipped per our
   legacy-first `dist\.env` pattern; per-user/tier gating can mirror CC's
   `_code_interpreter_allowed`.
6. Model notes: `gpt-5.4-mini` (client pin) is comfortably able to write pandas for
   these tasks; terra's tools+`reasoning_effort` 400 is already neutralized (25d160c).

### 4.2 The Agent — Phase 2, the free ride

One `@tool()` in `agent_service/` (likely alongside `file_tools.py`) calling the shared
executor, staging from The Agent's own file store, harvesting outputs into its
work/artifact flow. ⚠ Standing landmine: never put a helper between `@tool()` and its
function. Sandbox limits matter *more* here (agent runs unattended on schedules), so this
surface inherits whatever hardening tier is current.

### 4.3 CC — convergence only

Swap `command_center/tools/code_interpreter.py` internals to the shared module. No
behavior change intended; pack-09/Code-Flows suites are the regression gate.

### 4.4 The platform SDK inside the sandbox — `aihub_runtime` (James's ask, 2026-08-26)

**We already have the SDK, and it already solves the hard parts.** `automations/sdk/
aihub_runtime` (shipped by the v5 installer) is the in-script SDK automations and Code
Flow steps use today:

- **Verbs:** `connection(name)`, `secret(name)`, `query(conn, sql, params)` (with
  built-in dead-query detection), `input()/inputs()`, `log()`, `send_email()`,
  `checkpoint()` (human-in-the-loop → My Approvals), `review_item()/review_decisions()`,
  `llm()`, `ai_extract()`.
- **Stdlib-only on purpose** — PYTHONPATH-injected, zero pip footprint, works in any
  interpreter including our bundle.
- **Credential model already right:** code never sees creds in source/argv/env. The
  runner injects a signed, single-run `AIHUB_RUN_TOKEN` + `AIHUB_RUNTIME_URL`; the SDK
  POSTs the token to `/automations/api/runtime/resolve`, the server verifies signature +
  scope and resolves values server-side (process-memory cache only). The
  `AUTOMATIONS_ENV_CRED_INJECTION` env-var fast path is back-compat and stays OFF for
  chat runs.

**Wiring into `run_python` (all three surfaces):**

1. The shared executor PYTHONPATH-injects `automations/sdk` (same resolution the runner
   uses: source tree → `{app}\automations\sdk`) and mints a run token per execution.
2. **Scope = user parity.** An automation's token is scoped to its manifest-declared
   names; a chat turn has no manifest, so the token carries `user:<id>` scope and the
   resolve endpoint checks the requested connection/secret against **that user's
   existing platform authz**. Code can reach exactly what the user's session could
   already reach through tools — no new powers, nothing new to administer. (Optional
   tightening later: the model declares `connections=[...]` on the tool call for a
   narrower token — audit nicety, not required.)
3. Env-scrub interplay: the constructed child env *deliberately adds*
   `AIHUB_RUN_TOKEN`/`AIHUB_RUNTIME_URL`/`PYTHONPATH(sdk)` after the scrub — the scrub
   removes inherited accidents; this is an intentional, scoped, single-run grant.
4. Package-cache convergence: the runner already does cached runtime pip installs
   (`automations/_pkg_cache` + PYTHONPATH, cross-process safe). The `install()` helper
   from §5.1 should reuse that machinery rather than invent a second overlay.

**How the agents learn it — mostly already done:**

- CC and The Agent **already teach this exact SDK** in their automation-authoring
  prompts (`nodes.py:2560`/`:5268`, `brain.py:212` — "START EVERY SCRIPT WITH
  `import aihub_runtime as aihub`…"). The run_python doctrine block gets the same few
  lines, maintained once in the shared module and injected into all three surfaces
  (GA via the tool's SYSTEM addition).
- Add `aihub.help()` → prints the verb cheat sheet **plus the connection/secret NAMES
  this run's token can resolve** (names only, never values) — discoverability without
  leakage; models read what they print.
- The SDK's rich errors already self-teach (`AutomationRuntimeError` says exactly
  what's missing and why), and run_python returns stderr to the model, which
  self-corrects on the next call.

**What it unlocks** (the reason to bother): one script that loads the staged CSV,
`aihub.query("ERPDB", …)` for the matching invoices, reconciles in pandas, writes the
exceptions workbook as an artifact, and optionally `aihub.checkpoint()`s before
`aihub.send_email()` — replacing N tool calls (and the ~1.4 s/row per-row-AI class)
with one program. Side-effect verbs carry the same identity/authz as the session's
normal tools; `checkpoint()`/`review_item()` give code the same human-approval bridge
automations have.

Effort: rides Phase 1 (GA/CC) and Phase 2 (The Agent) — PYTHONPATH line + token mint +
resolve-endpoint scope extension + shared doctrine text; the SDK itself needs no changes
beyond `help()`.

---

## 5. Hardening ladder (honest isolation story)

Today's isolation is **process-level only** (subprocess + timeout + workdir), and one
finding needs fixing regardless of this project:

> **⚠ Finding:** `run_python` executes LLM-authored code with the **full parent
> environment inherited** (`code_interpreter.py:287` — `env={**os.environ, ...}`): Azure
> keys, DB connection strings, JWT/app secrets. Combined with unrestricted network, a
> prompt-injection payload *inside an uploaded file* could steer the model into
> exfiltrating creds. Same fail-closed class as the NLQ non-SELECT finding.

Tiers (each is independently shippable):

- **T0 (with Phase 1, cheap):** secret-scrub of the child env — a **denylist**, not an
  allowlist (drop the app's own `.env`-loaded vars + secret-pattern names; everything
  else passes — §5.2); path-fence doctrine (cwd only); output-size caps (exists);
  wall-clock timeout (exists).
- **T1:** Windows Job Object per run — memory cap, CPU cap, kill-on-timeout including
  child processes (plain `subprocess.timeout` doesn't kill grandchildren).
- **T2 (Phase 3):** dedicated low-priv NSSM executor service — separate Windows account,
  no read access to app config/secrets/DBs, firewall egress rule (network off by default,
  opt-in flag for "fetch this URL" use cases — note CC's extras provisioning needs net at
  *service* level, not per-run).
- **T3 (optional, per-tenant):** ACA dynamic sessions backend for true VM isolation.

Threat model context: single-tenant client box, user's own files, code authored by our
models under our prompts — T0+T1 covers the realistic risk; T2 is defense-in-depth.

### 5.1 Which Python the code runs in (packages ≠ env vars)

User code never runs in a service's own interpreter. Resolution
(`_resolve_interpreter`, `code_interpreter.py:102`):

1. `CODE_INTERPRETER_PYTHON` (operator override; skipped if the path is stale) —
   dev-box mode, deliberately outside `aihubbuilder`;
2. the shipped `{APP_ROOT}\agent_environments\python-bundle\python.exe` — client mode:
   a real standalone CPython 3.11, **not** the frozen service exe;
3. `sys.executable` last resort (frozen builds refuse it via `_interpreter_is_runnable`).

**Package policy — default-open, deny-by-observation (James's directive, 2026-08-26):**

- **Preinstalled seed** so common work is instant and offline-capable: CORE baked at
  build time (numpy / pandas / matplotlib / openpyxl —
  `prepare_python_bundle.py:CODE_INTERPRETER_STACK`); EXTRAS provisioned at startup by
  `code_interpreter_env.py` (scipy, sklearn, seaborn, statsmodels, requests, bs4, lxml).
  The requirements file is a **warm cache, not a ceiling**.
- **Runtime installs are allowed.** The script preamble provides an `install("pkg")`
  helper and the system prompt teaches the model to use it when an import is missing.
  Anything on PyPI works unless it's on the denylist.
- **Installs land in a persistent overlay folder next to the bundle**
  (`agent_environments\python-bundle-extras\`, via `pip --target` + `sys.path` in the
  preamble) — never the bundle's own site-packages, never any service environment.
  Delete the folder = factory reset. A pip **constraints file** pins the baked core so
  no install can ever move numpy/pandas/matplotlib out from under everyone else.
- **Denylist, not allowlist:** `code_interpreter_package_denylist.txt` starts (near)
  empty; a name is added only when observation proves it harmful. Every install is
  logged (package, version, session) so "observed" has data behind it. Zero curation
  burden until something actually misbehaves.
- Honest limit, stated once: with network open the helper is a steering chokepoint, not
  a jail — raw `pip` via subprocess remains possible. That matches the intent (guide
  normal behavior, block known-bad); T2 stays available as an opt-in strict mode for
  any tenant that wants a hard boundary.
- This resolves §9 Q2: **network egress stays ON by default** (installs need PyPI);
  the T2 egress block becomes opt-in-strict only.

> **⚠ Finding #2:** `ensure_async()` (`code_interpreter_env.py:194`) — the startup
> provisioning trigger — is **defined but never called anywhere in the tree**. On a
> stock client today the extras never install; the bundle has only the baked core.
> Phase 1 must wire `ensure_async()` into service startup (once, from the shared
> module's init — idempotent, so every surface can call it safely).

> **Tightening:** drop or dev-gate resolution step 3 (`sys.executable`). On a dev box
> with no env var it would execute user code inside the service's own conda env —
> exactly the parent-env pollution we're ruling out. Fail with the clear
> "not configured" error instead.

### 5.2 The env-var scrub — denylist edition (T0)

Same philosophy as packages: **pass everything through except our own secrets.**
Replace `env={**os.environ, ...}` with:

- **Dropped — and the list maintains itself:** every variable name the app itself
  loaded from `.env` / `dist_env` (record the key names at load time — that set IS the
  platform's config-and-secrets surface, derived automatically, nothing for anyone to
  curate), plus a fixed pattern net (`KEY|SECRET|TOKEN|PASSWORD|PWD|CONN`), plus
  `PYTHONHOME`/`PYTHONPATH` (correctness, not policy: a frozen parent's values corrupt
  the child interpreter's imports).
- **Set explicitly:** `TEMP`/`TMP` → the per-run workdir (temp files become harvestable
  and are cleaned with it), `MPLBACKEND=Agg`, `MPLCONFIGDIR=<workdir>\.mpl`,
  `PYTHONIOENCODING=utf-8`.
- **Everything else passes untouched** — full `PATH`, proxies, whatever a dev box has.
  No passthrough knob needed; exotic dev interpreters just work.

Nothing is lost: package availability comes from the interpreter's site-packages, not
env vars, and analysis code's contract is "compute over the files staged in cwd" —
platform data arrives as staged files via the agent's own (authz'd, logged) tools, so
creds-in-env was never a designed capability, only a `subprocess` default. Residual risk,
stated once: a secret living *only* in a machine-level var with an innocent,
pattern-missing name would slip through — accepted for the single-tenant threat model
(platform secrets live in `.env`; the machine-level vars on record are model pins, not
creds). Regression test: every `.env`-loaded name must be absent from the child env; a
smoke script (scipy import, chart render, tempfile write, `os.urandom`,
`install("tabulate")`) passes under the scrubbed env.

---

## 6. What this retires (the point of the exercise)

Once the gate is green, stop patching the hand-rolled class:

- **The GeneralAgent-side PandasAI lane only** — `analyze_excel_data` in
  `agent_excel_tools.py` (bound via `ExcelTool.get_tools()` whenever an assistant has
  Excel/CSV knowledge docs, `GeneralAgent.py:2844`): a nested per-call `PandasAIAgent`
  with its own param-compat scar tissue (e5e6c85) and failure modes. `run_python` + the
  assistant's own model subsumes it. **Data Explorer is NOT touched** — `LLMAnalyticalEngine`
  / `LLMDataEngineV2` keep PandasAI, and the shared `create_pandasai_llm` factory in
  `api_keys_config.py` stays for them.
- **CSV "count/aggregate from preview" bug class** — becomes structurally impossible when
  doctrine + tool make computation the default lane (this is the CC lesson: guard the
  chokepoint every caller converges on).
- **Per-row AI calls** (Excel-export style ~1.4 s/row) — one generated script replaces N
  model calls wherever this pattern lurks.
- P3/P4 of the CSV-lane roadmap ([general-agent-csv-tabular-gap]) largely collapse into
  this plan.

Keep: the document engine (PDF search/extract is retrieval, not math — though harvested
extractions can now be *cross-referenced* with tabular files inside one script, which is
the spec's "PDF × numbers" ask), and NLQ/SQL for database questions.

---

## 7. Test & verification plan

- **Rebuild pack 09's original mission** as a GeneralAgent Code Interpreter competency
  pack (the 6/28 loss finally repaid): row counts on big CSVs (beyond preview window),
  multi-sheet Excel aggregates, group-bys, two-file joins, chart generation, derived-file
  round-trip ("make me a cleaned CSV"), a PDF-table × CSV cross-reference, and an
  **adversarial fixture** (injection payload inside file content — assert no env/net
  reach; pack-21 plant style). Deterministic oracle scripts compute ground truth;
  existing kit at `C:\temp\csv_lane_test` seeds the fixtures.
- Unit level: staging adapter tests join the `test_legacy_csv_tools_resolution.py`
  family; env-scrub test asserts no secret-bearing vars cross into the subprocess.
- Wire into **pack 15** (platform regression) before any client build; CC pack-09 suites
  guard the convergence refactor.
- Live-fire on the dev tree per [live-agent-testing] recipes before calling any phase done.

---

## 8. Phasing

| Phase | Content | Effort |
|---|---|---|
| **0 — spike** | Run CC's `run_python` against the CSV kit for baseline latency/accuracy; confirm bundle resolution from app-5001 context (frozen APP_ROOT + `dist\` copy-drift traps); write the env-scrub allowlist | ~½ day |
| **1 — GeneralAgent** | Shared-module extraction + GA tool + staging adapter + prompt doctrine + T0 scrub + env gate + competency pack | 1–2 days |
| **2 — The Agent** | `@tool()` binding + staging/harvest adapters + agent-side doctrine | ~½ day |
| **3 — hardening** | T1 job objects; optional T2 low-priv executor service (aihub-new-service recipe: port, NSSM, .iss — installer only changes here) | 1–2 days |
| **4 — retire & extend** | Deprecate PandasAI lane + demote preview tools; optional ACA-dynamic-sessions backend evaluation for tenants that want it | as needed |

Installer impact through Phase 2: **near zero** — python-bundle already ships and CC's
provisioning already fattens it; only `.env` template lines change (in the HIGHEST .iss).

---

## 9. Phase 0 + Phase 1 execution log (2026-08-26) — DONE

**Phase 0 findings:** warm per-run overhead ≈ 1 s (cold OS cache ≈ 8 s, pandas
import); dev-tree python-bundle was BARE (no pandas — confirming the unwired
`ensure_async` finding; self-heal now covers core too); dev runs on
`CODE_INTERPRETER_PYTHON` = aihub2.1 (also the main app's own env); GA already
HAD a registered `run_python_code` (sys.executable + full inherited env + no
files) — Phase 1 became an in-place upgrade, so existing agent configs inherit
it with no migration.

**Shipped (commits `2644625`, `e129c3f`):** `code_exec/` shared backend
(resolver, denylist env-scrub, executor, install() preamble, SDK wiring,
doctrine); GA `run_python_code` upgraded (staging: conversation inputs +
agent-files tee incl. cross-user fallback + knowledge store; artifacts incl.
headless fallback + inline image blocks; SDK user-parity token); chat-lane
run-token (AUD_CODE_RUN) accepted by `runtime_resolve`; `aihub_runtime.help()`;
CC delegated to the shared backend (gains the scrub); `ensure_async` wired
(CC lifespan + lazy in-tool); context binding fixed where agents actually run
(`app_agent_api` /chat + `api_agent_chat` assertion-user resolve — this also
un-blinds manipulate_pdf/create_* on those paths); constraints + empty
denylist files; unit tests 31/31.

**Verified live:** pack `test_human/22_GA_Code_Interpreter` **9/9 PASS** on
gpt-5.6-terra over the agent-API execution path — exact rowcount/total (2,500 /
1,263,431), group-by, join, multi-sheet Excel, chart PNG artifact, derived-CSV
artifact, injection plant (correct 218,478, plant ignored), nested JSON, and a
live `aihub.query` against AIRDB2 graded by a direct-DB oracle.

**Follow-ups surfaced:** (a) `AgentAPIAdapter.chat` does not forward
`conversation_id`, so conversation-scoped staging is unavailable in UI+adapter
mode (agent-files/knowledge staging covers most cases); (b) `delete_agent`
leaves `data/agent_files/<id>/` tee residue; (c) knowledge ingest rejects
`.json` by design — chat attachment is the JSON lane; (d) client bundles should
be spot-checked post-install now that provisioning self-heals.

## 10. Open questions for James

1. **Default-on for clients** in the next installer build, or env-gated pilot first?
2. ~~Network egress~~ **RESOLVED 2026-08-26** by the package directive (§5.1): egress
   stays ON by default (runtime installs need PyPI); T2 egress block = opt-in strict
   mode only.
3. **PandasAI:** retire outright after the gate, or keep as fallback one release?
4. Any client with a **compliance posture** that would ever require the managed-cloud
   sandbox (Foundry/ACA)? If no, Phase 4's cloud branch drops off the roadmap entirely.
5. CC's per-user gating (`_code_interpreter_allowed`) — mirror the same tier/user policy
   on GeneralAgent and The Agent, or open to all?
