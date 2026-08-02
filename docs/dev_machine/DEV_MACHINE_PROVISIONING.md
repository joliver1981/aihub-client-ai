# AI Hub Dev Machine — Backup, Clone & Rebuild Plan

**Goal:** (1) a working backup dev machine if this one dies, (2) a repeatable way to spin up
*working* dev machines, (3) a safe way to fork/experiment without risking the primary machine.

> **Privacy split:** this file is committed to a PUBLIC repo, so it contains no machine-specific
> identifiers. All concrete names (VM/resource group, DB hosts, storage share, subnet, exact
> az commands) live in `MACHINE_FACTS.local.md` in this folder, which is **gitignored** and is
> captured by the backup script instead.

---

## 0. TL;DR — the strategy

| Tier | What | Time to a working machine | Protects against |
|------|------|---------------------------|------------------|
| **T0 – Insure now** | Azure VM backup + seed-package backup to the file share + push discipline | n/a (insurance) | disk loss, fat-fingered `git clean`, accidental deletes |
| **T1 – Clone on demand** | Snapshot → disk → new VM in the same VNet, then run the neuter script | **~1 hour** | machine loss; also the sandbox for experiments |
| **T2 – Rebuild from parts** | Fresh Windows + `bootstrap_dev_machine.ps1` + seed package | 0.5–1 day, supervised | Azure-account-level loss; also documents *what* the machine is |

The fastest path to "hit the ground running" is **T1 (disk clone)** — it captures every modified
python library, NSSM config, registry key, and installed tool with zero reverse-engineering.
T2 exists so the machine is *understood*, not just copied, and because images rot.

**Fork/experiment = T1 clone + `sandbox_neuter.ps1` + (ideally) a copy of the config DB.**
Never experiment on the primary machine.

---

## 1. What actually makes this machine "the dev machine"

Analysis of the live system found six layers. Anything not listed under "in git" exists
**only on this machine** until backed up.

### 1.1 Code (safe in git, keep it that way)
- This repo — GitHub remote, **public by design** (see §7). Stays pushed within ~1 commit.
- Sibling repos in `C:\src` with remotes: the email service, the cloud API (auto-deploys to
  Azure on push — careful), installer, marketplace, forum, langsmith variant, ai-memory.
- Sibling repos **without any remote** (bundle these in backups): `ai-dca`,
  `aihub-accelerator`, `aihub-llmml`.
- **Not in git at all:** `C:\src\ai-colab` (the multi-agent collaboration hub — boards,
  protocol, guidelines), `C:\src\dummy-service`, `C:\scripts` (feeds the AIRDB
  data-generation scheduled tasks). The backup script copies all three.

### 1.2 Machine-only files inside this repo (gitignored but load-bearing)
| Path | What it is |
|------|-----------|
| `.env` | **Full runtime config incl. secrets** (API_KEY, DB creds, encryption secret/salt, WINTASK/WINRM/SMTP creds, proxy URLs). The "non-sensitive only" doctrine is aspirational — treat as secret material. |
| `data/secrets/` | Encrypted LocalSecretsManager store + `.machine_id` |
| `data/portal_registry.json`, `data/portal_workflows.json`, `data/model_overrides.json` | Runtime-modified config |
| **11 untracked PyInstaller `.spec` files** | `*.spec` is gitignored; only `app_onedir.spec` + `wsgi_executor_service_onedir.spec` were force-added. Untracked: 6 root specs (`wsgi_agent_api`, `wsgi_knowledge_api`, `wsgi_doc_api`, `wsgi_vector_api`, `app_jss_main`, `app_doc_job_q` — all `_onedir`) + 5 service-dir specs (MCP gateway, builder_service, builder_data, cloud gateway, command_center_service). **They exist nowhere else.** |
| `dist/` seed set: top-level `.env`, `core_tools.yaml`, `user_config.py`, `user_prompts.py`, `GeneralAgent.pyd` **+ `dist/python-bundle/`, `dist/python-bundle-requirements/`, `dist/static/icons/`** | Hand-maintained installer inputs — **no build script regenerates any of these** (verified in Appendix D). The 13 PyInstaller trees in `dist/` are the only regenerable part. |
| `_build_config.py`, `_build_config_client.py` | Build-time credential modules |
| `tools/` | Runtime-generated custom agent tools |
| `run_regression.py`, `llm_unit_test.py`, `e2e_app_tests/`, `tests/e2e/` | Tests with embedded test creds, deliberately excluded from the public repo |
| `knowledge_files/`, `data/chroma_knowledge/`, `chroma_db/` | Ingested corpora + vectors (re-ingestable but ~100 min per large corpus) |
| `agent_sessions/`, `workflows/`, `uploads/`, `exports/`, `builder_service/data/`, `command_center_service/data/`, `agent_environments/tenant_*`, `automations/tenant_*` | Tenant/runtime working data — this is "the configured dev tenant" |
| `shortcuts/*.lnk` | Desktop launchers (the `.bat` files themselves are tracked) |

### 1.3 Python environments — the part `pip install` cannot rebuild
The live app is 14 manually-launched service windows (`shortcuts\00_Start-Restart_AIHub_Services_V3.bat`)
plus the email NSSM service, spread across **9 conda envs** under the user's miniconda:

| Env | Runs |
|-----|------|
| `aihub2.1` | Main app, Agent API, Knowledge API, Executor (+ code-interpreter python in dev) |
| `aihubbuilder` | Builder, Builder Data, Command Center |
| `aihubant` | Document API, Doc Job Queue |
| `aihubvector2` | Vector API |
| `jss` | Job Scheduler engine |
| `aihubmcp` | MCP Gateway |
| `aihubcloudgateway` | Cloud Storage Gateway |
| `aihub-browseruse` | Browser Use service (bundled Chromium/Playwright) |
| `aihubemail` | Email service (NSSM, from the email repo) |
| `testftp` | Local SFTP/FTP/FTPS test server (`test_human\_sftp_test_server`) |

Some site-packages are **locally patched — confirmed by hash audit, not folklore**. The
headline: `aihub2.1`'s `openai 2.22.0` carries a ~205-line hand patch in
`openai/_base_client.py` that intercepts every SDK request and relays it to the AI Hub
cloud proxy (driven by `AI_HUB_API_URL`/`AI_HUB_PROXY_OPENAI`, escape hatch `BYPASS_PROXY`,
per-user context attached from Flask). A plain `pip install openai` **silently drops the
proxy**. The full diff is preserved as `openai_2.22.0_proxy.local.diff` in this folder
(gitignored; captured by backups). Legacy `aihub2`-era envs carry the same idea patched
into `openai 0.27.5` (`api_requestor.py`). Per-env audit results: **Appendix C**
(regenerate any time with `audit_site_packages.py`, kept in this folder).

Because of these patches + native deps + version pinning, **envs are backed up/restored as
whole directories** (they work when restored to the identical path), with
`pip freeze`/conda manifests captured alongside as documentation. Longer-term hygiene
(open decision): maintain such patches as versioned diff files applied by a script, so env
rebuilds stop depending on copying bytes forward.

### 1.4 Windows-level state
- **Machine environment variables** — real secret store: Anthropic/OpenAI/Azure OpenAI keys,
  SQL creds, Mailgun/LangSmith/Tavily/PostHog/Sentry, mail connection strings, model defaults
  (names enumerated in Appendix B; values only in backups).
- **Registry** `HKLM\Software\AI Hub\Config` (ApiKey).
- **NSSM services** — installed-app services under `C:\Program Files\AIHub` (stopped; used for
  installer testing) + dev services (`AIHubEmail` running/auto from the email repo;
  legacy `AIHubDev`/`AIHubDocs`/`AIHubFC` point at retired trees).
- **Scheduled tasks** — `\AI\*` (app Quick-Jobs + AIRDB test-data generators driven by
  `C:\scripts`), plus the OpenClaw gateway task.
- **Mapped drive** to an Azure Files share (backup target; UNC in MACHINE_FACTS).
- Windows itself: Win10 Enterprise from a Visual Studio marketplace image (see §8 re: EOL).

### 1.5 Installed toolchain (rebuildable, but must be present)
Git (+LFS), Miniconda, **Inno Setup 6** (`ISCC.exe`), NSSM 2.24 (`C:\src\nssm-2.24`),
PyInstaller (per-env), Nuitka/pyd compile chain (`compile_*_pyd*.bat`), **UPX on PATH**
(every spec sets `upx=True`), OpenSSL (`setup_ssl.bat`), **ODBC Driver 17 for SQL Server**
(pyodbc breaks without it), Node.js (npx-launched MCP servers, docx tooling), Azure CLI,
Chocolatey/winget, VS Code, browsers, and the **Playwright Chromium cache**
(`%LOCALAPPDATA%\ms-playwright`, revision-pinned by the build bat). Full 361-program
inventory is captured by each backup run. Build-chain externals: **Appendix D**.

### 1.6 External dependencies (nothing to copy, but the machine must reach them)
- **App config DB = Azure SQL** — users/tenants/jobs/schedules live off-machine (good), and it
  is **shared** — see the sandbox warning in §5.
- **Test SQL host** (AIRDB/ERPDB retail+ERP datasets) — a peer VM on the same VNet/subnet.
  A clone must join **the same VNet** to reach it. This host is its own single point of
  failure (§8).
- Cloud API (the deployed api repo), shared mailbox for inbound email, LLM provider accounts,
  LangSmith/PostHog/Sentry, the Azure Files share.

---

## 2. T0 — Do this week (insurance on the current machine)

1. **Enable Azure Backup on the VM** (daily restore points). One command — in MACHINE_FACTS.
   This alone converts "machine dies" from catastrophe to inconvenience.
2. **Run the seed-package backup:** `.\backup_dev_machine.ps1 -Tier Core,Data`
   → writes to the mapped Azure Files share: all §1.2 files, env-var/registry/NSSM/task
   exports, conda manifests, git bundles (incl. the no-remote repos + ai-colab + C:\scripts).
   Schedule it weekly (§6).
3. **Force-add the 11 orphan `.spec` files** (mirrors how the credential tests were
   force-added): the 6 root `*_onedir.spec` + the 5 service-dir specs listed in §1.2 —
   they contain no secrets (relative paths only, verified) and the build is not
   reproducible without them. (Open decision #1 — recommended yes.)
4. **Put `ai-colab` under git** with a **private** remote (it holds internal process docs).
   Until then the backup script covers it.
5. Verify **Azure SQL PITR retention** on the config DB, and decide backups for the test SQL
   host (§8).
6. Keep push discipline: the repo stays ≤ a few commits ahead; push after each session.

## 3. T1 — Clone on demand (backup machine and/or sandbox, ~1 hour)

Runbook (concrete commands with real names: MACHINE_FACTS.local.md):

1. (Optional, for consistency) Deallocate the VM, or just accept crash-consistent.
2. **Snapshot the OS disk** → create a managed disk from it → **create a specialized VM**
   from that disk, **same VNet/subnet** (test-SQL reachability), same or smaller size
   (burstable B-series; deallocate when idle).
   - Marketplace-image note: if creation complains about purchase plan, pass the image's
     plan name/product/publisher (recorded in MACHINE_FACTS).
   - Windows activates via Azure KMS automatically. Do NOT sysprep — specialized clone is
     exactly what we want (identical paths, users, installs).
3. First boot: **rename the computer**, verify the mapped drive reconnects (stored SMB creds
   clone with the profile; remap command in MACHINE_FACTS if not).
4. **Immediately run `sandbox_neuter.ps1`** (before starting anything) — see §5 for why this
   is not optional: the clone wakes up with an auto-start email service and scheduler config
   pointed at the SHARED mailbox/DB.
5. Acceptance test ("is it a working dev machine?"):
   - `shortcuts\00_Start-Restart_AIHub_Services_V3.bat` → all 14 windows up, UI on the main port.
   - Log in, run one agent chat, one document search.
   - `test_human\15_Platform_Regression\runner.py` (the all-areas gate) — expect scheduler
     /email tests to be affected if those services are neutered; that is correct sandbox
     behavior.
   - Build check: run the executables build bat for one service + compile the `.iss` with ISCC.
6. **Refresh cadence:** re-snapshot monthly and before/after any risky platform change, keep
   the last 2–3 snapshots. Snapshots of a 512 GB disk are incremental-billed; cost noted in
   MACHINE_FACTS.

## 4. T2 — Rebuild from parts (cold rebuild, 0.5–1 day)

For when there is no usable disk/snapshot, or to build a *clean* machine deliberately.
Driven by `bootstrap_dev_machine.ps1` (skeleton — run phase by phase, not fire-and-forget):

0. Windows box (see §8 re: Win10 vs Win11), **same username** — absolute paths bake the
   user profile path into bats/.env/env configs. Different username = find/replace pass.
1. Toolchain via winget/choco: git, miniconda, Inno Setup 6, Node LTS, Azure CLI, VS Code,
   7zip, **ODBC Driver 17 for SQL Server**; copy `nssm-2.24` from seed.
2. Restore repos: `git clone` the remoted ones into `C:\src\<same names>`; unbundle the
   no-remote ones; copy `ai-colab`, `dummy-service`, `C:\scripts` from seed.
3. Overlay the machine-only repo files from the seed package (§1.2 list, preserved paths).
4. **Copy conda envs wholesale** from the seed/backup into the identical miniconda path.
   Do NOT rebuild from requirements — patched packages (Appendix C) and native pins won't
   reproduce. The captured `pip freeze` manifests are for documentation/diffing, not install.
5. Import machine env vars (JSON from seed) → registry import (AI Hub key) → recreate NSSM
   services (from captured definitions) → import `\AI\*` scheduled tasks (XML).
6. Map the Azure Files share; restore `C:\temp` fixture sets (leases, demo assets, client docs).
7. Claude-layer: restore `~\.claude\skills`, `settings.json`, project memory dirs (agent
   re-auths itself; credentials are not restored).
8. Run the §3 step-5 acceptance tests. Fix what fails; update this doc with what was missing.

## 5. Fork & experiment workflow (the sandbox story)

**Pattern: experiments run on a T1 clone, never on the primary.**

1. Snapshot → clone VM (§3) — you get the *entire* working platform including patched envs.
2. `sandbox_neuter.ps1` on first boot. **Why this matters — shared-state hazards found in
   analysis:**
   - The **job scheduler polls the shared Azure SQL config DB** → a second machine running it
     **double-executes every scheduled job**.
   - The **email service auto-starts** (NSSM) and polls the **shared mailbox** → double
     processing/replies.
   - `\AI\*` scheduled tasks re-seed shared test databases; demo rows persist in the shared
     ERP DB.
   - Same LLM API keys (quota/billing mixing — usually acceptable), same LangSmith project
     (trace pollution — switch project env var on the clone).
3. For real isolation, **copy the config DB** (single `az sql db copy` — command in
   MACHINE_FACTS) and repoint the clone's `.env` DATABASE_* at the copy. Then everything can
   run, including scheduler/email tests against the copy.
4. Code changes on the clone: branch → push to GitHub → review/merge on the primary
   (primary-machine workflow stays commit-to-main; branches are for experiment machines).
   Env/library experiments (new packages, lib upgrades) don't round-trip through git — if an
   experiment graduates, apply the same change on the primary deliberately and note it in
   the env manifests.
5. Discard clones freely; re-clone from a fresh snapshot for the next experiment.

## 6. Cadence & restore drill

| When | What |
|------|------|
| After each work session | `git push` (repo + email repo if touched) |
| Weekly (scheduled task) | `backup_dev_machine.ps1 -Tier Core` |
| Monthly | `-Tier Core,Data,Envs` + fresh VM snapshot |
| Before risky change | Ad-hoc snapshot + `-Tier Core` |
| Quarterly | **Restore drill:** clone from latest snapshot → neuter → §3 step-5 acceptance. A backup that hasn't restored is a hypothesis. |

## 7. Public-repo hygiene

The GitHub remote is **publicly visible** (deliberate — the .gitignore has an explicit
open-source exclusions section). Consequences for this plan:
- Machine identifiers (VM/RG/subscription, DB hostnames, storage account, subnet, mailbox)
  stay ONLY in `MACHINE_FACTS.local.md` (gitignored) and in backups on the private share.
- Never commit: `.env*` (except tracked templates), specs are fine but review before adding,
  anything under §1.2 marked secret, the `*.local.*` files in this folder.
- The backup DESTINATION is a private Azure Files share; the seed package contains plaintext
  secrets by design (it must, to restore a machine) — treat the share's access keys as the
  perimeter, and consider a password-protected 7z for the `secrets` subfolder (supported by
  the script via `-ArchivePassword`).

## 8. Open decisions & risks (for James)

1. **Force-add the ~12 untracked `.spec` files?** Recommended — build is otherwise
   unreproducible from git. (They're plain build config; quick review for anything odd first.)
2. **`ai-colab` → private git repo?** Recommended (process docs + boards deserve history).
3. **Test SQL host backups** — AIRDB/ERPDB VM is outside this plan's scope but is a real
   SPOF for the test/demo ecosystem (reseed scripts exist but drift). Decide: Azure Backup on
   that VM too, or scripted DB `.bacpac` exports to the share.
4. **Azure Backup enablement + snapshot budget** — a few $/month per retained snapshot tier;
   exact numbers in MACHINE_FACTS.
5. **Prune before you preserve?** ~40 of the 49 conda envs and several `DO NOT USE`/backup
   source trees are stale. Archiving them (one last backup, then delete) shrinks every future
   image/backup and removes footguns. Candidate list in Appendix E.
6. **Legacy patched envs** (`aihub2`-era, patched `openai 0.27.5`): nothing live launches
   from them today; keep one archived copy for archaeology, don't migrate them.
7. **Win10 EOL**: the base image is Win10 Enterprise (support ended Oct 2025 absent ESU).
   T1 clones inherit it — fine for private dev. For T2, consider building on Win11 and
   budgeting a half-day for toolchain re-validation.
8. **This plan's own drift**: re-run `audit_site_packages.py` + a backup after any env
   surgery; update Appendix C/D when the build chain changes.

---

## Appendix A — Service → env → port map
See §1.3 for the env map. Ports follow the base+offset scheme (base 5001: doc +10, vector
+30, agent +40, knowledge +50, executor +60, MCP gateway +70, cloud gateway +80, browser-use
+100), Command Center 5091, Builder 8100, Builder Data 8200, plus the email service and the
SFTP test server. Resolve programmatically via `CommonUtils.get_*_api_base_url()`.

## Appendix B — Machine env var names (values in backups only)
AIHUB_GITHUB_REPO, AIHUB_VERSION, ANTHROPIC_API_KEY, ANTHROPIC_API_THROTTLE_CALLS,
ANTHROPIC_API_THROTTLE_DELAY, ANTHROPIC_MAX_TOKENS, ANTHROPIC_MODEL, APP_LOG_PATH,
APP_MAIL_CONN_STRING, APP_MAIL_DEFAULT_SENDER, AUTH_MIDDLEWARE_DRY_RUN, AZURE_MAIL_CONN_STR,
AZURE_MAIL_DEFAULT_SENDER, AZURE_OPENAI_API_KEY(_MINI), AZURE_OPENAI_API_VERSION_MINI,
AZURE_OPENAI_BASE_URL_MINI, AZURE_OPENAI_DEPLOYMENT_NAME_MINI, EMAIL_DISPLAY_NAME,
EMAIL_PROVIDER, EMAIL_SENDER, ENABLE_API_KEY_SWAP, GIT_LFS_PATH, LANGSMITH_*, LOG_*,
MAILGUN_*, OPENAI_API_KEY, OPENAI_LOG, PORTAL_API_KEY, POSTHOG_*, SECRET_KEY,
SECURITY_PASSWORD_SALT, SENTRY_DSN, SQL_DB_*, TAVILY_API_KEY, TELEMETRY_RELAY_ENABLED
(+ user-level: OPENROUTER_API_KEY).

## Appendix C — Locally modified / unreproducible packages (audit results)
_2026-08-02 run of `audit_site_packages.py`: sha256 verification of every pip RECORD across
11 envs (~1,080 packages / ~124,000 files). Rerun after any env surgery._

**Real local patches in LIVE envs (pip cannot reproduce these):**

| Env | Package | File(s) | What the patch does | Preserved diff |
|-----|---------|---------|---------------------|----------------|
| `aihub2.1` | `openai 2.22.0` | `openai/_base_client.py` | Cloud-proxy relay: intercepts every SDK request, wraps method/URL/headers/base64-body as JSON and POSTs it to the AI Hub cloud API (`AI_HUB_API_URL` + `AI_HUB_PROXY_OPENAI`, `api_key` param, Flask per-user context); `BYPASS_PROXY` escape hatch; reconstructs an `httpx.Response`. ~205 diff lines. | `openai_2.22.0_proxy.local.diff` |
| `aihubvector2` | `chromadb 0.6.3` | `chromadb/utils/embedding_functions/__init__.py` | Default-embedding-function fallback chain: try ONNX MiniLM → SentenceTransformer → OpenAI instead of hard-failing. 54 diff lines. | `chromadb_0.6.3_embedding_fallback.local.diff` |

**Legacy env `aihub2`** (nothing live launches from it; keep one archived copy, do not migrate):
`openai 0.27.5` (`api_requestor.py` — the original proxy relay), `langchain 0.0.348`
(`chains/base.py`), `pandasai 1.5.12` (8 files: agent/cache/code_manager/response_parser/…).

**Clean (no real patches):** `aihubbuilder`, `aihubant`, `jss`, `aihubmcp`,
`aihubcloudgateway`, `aihub-browseruse`, `aihubemail`, `testftp`. Anthropic SDK is unpatched
everywhere — Anthropic proxying is app-level config (`AI_HUB_PROXY_ANTHROPIC_*`), not a lib patch.

**Noise to ignore when rerunning:** `pip`/`wheel`/`packaging` INSTALLER files and
`Scripts\*.exe` launcher hash mismatches appear in every env — pip/conda self-management,
not hand edits.

**Consequences:** (1) envs restore by directory copy only; (2) any deliberate lib upgrade in
`aihub2.1` (openai) or `aihubvector2` (chromadb) must re-apply the preserved diff or
consciously retire it; (3) longer-term, apply these as versioned patch files in a script so
the knowledge lives in the repo, not in env bytes.

## Appendix D — Build-chain external dependencies

**Sequence:** (occasional) Nuitka pyd compile → `scripts\generate_build_config.py` (PRE step;
**aborts unless repo-root `.env` is populated** — emits `_build_config.py` baked into exes +
`_build_config_client.py` shipped loose) → 13 × PyInstaller (`Build_AIHub_Executables_OneDir_Dev_v3.bat`)
→ Browser Use "Strategy B" robocopy (source + whole `aihub-browseruse` conda env + Chromium
into `dist\`) → **manual** ISCC compile of `AIHub_Setup_Script_v4_OneDir_Dev.iss` (no script
invokes ISCC; no code signing anywhere).

**Spec → env map:** `aihub2.1` (app, agent, knowledge, executor) · `aihubant` (doc API, doc
job queue) · `jss` (job scheduler) · `aihubvector2` (vector) · `aihubmcp` (MCP gateway) ·
`aihubbuilder` (builder, builder data, command center) · `aihubcloudgateway` (cloud gateway)
· `aihub-browseruse` (copied, not compiled).

**External inputs the build/installer needs (outside the repo):**
- Miniconda at the user profile path (activate.bat + all envs above) — username is
  hardcoded in the build bats.
- Playwright Chromium at `%LOCALAPPDATA%\ms-playwright\chromium-<pinned-rev>` (revision
  pinned in the build bat; drifts on browser-use/Playwright upgrades).
- `C:\src\nssm-2.24\win64\nssm.exe` (copied into the install by the .iss).
- UPX on PATH; OpenSSL for `setup_ssl.bat` (v4 .iss ships no certs).
- Inno Setup 6 itself.
- **Live license check at install time** against the cloud API (`/validate_license`) —
  installs on an offline machine abort.

**Installer creates 14 NSSM services** (AIHub, DocAPI, DocQueue, JobScheduler, VectorAPI,
AgentAPI, KnowledgeAPI, ExecutorService, MCPGateway, CloudGateway, BuilderService,
BuilderData, CommandCenter, BrowserUse) with restart-on-failure recovery, writes
`HKLM\Software\AI Hub\Config\ApiKey`, and seeds `{app}\.env`.

**Hardcoded machine assumptions:** repo at `C:\src\aihub-client-ai-dev` (every `Source:` line),
NSSM at `C:\src\nssm-2.24\win64`, username-jamesed miniconda paths in 4 bats, port 5001
default in 3 places (BrowserUse service env does not follow a user-chosen port), version
string duplicated (`AppVersion` + `OutputBaseFilename`), `run_service.bat` still points at a
retired tree (`C:\src\aihub-client`) — legacy, do not use.

## Appendix E — Size survey (backup planning, measured 2026-08-02)

| What | Size | Notes |
|------|------|-------|
| Repo working tree | 22.4 GB | includes the three below |
| — `dist\` | 5.8 GB | 13 PyInstaller trees regenerable; **`python-bundle` 0.6 GB + seed files are NOT** |
| — `Output\` | 4.6 GB | built installers — regenerable |
| — `agent_environments\` | 5.1 GB | mostly python-bundle (Envs tier) + `tenant_*` (Data tier) |
| — `data\` | 0.35 GB | Core/Data tiers |
| 10 live conda envs combined | **5.5 GB** | biggest: `aihubvector2` 1.9, `aihub2.1` 1.2, `aihubant` 0.6 — small in GB, huge in file count (why full-tree scans crawl) |
| Playwright Chromium cache | 2.4 GB | Envs tier |
| `C:\Program Files\AIHub` | 3.4 GB | regenerable by running the installer |
| Fixtures (`C:\temp`: leases / demo / client docs) | ~0.6 GB | Data tier |
| ai-colab + scripts + nssm + email repo + dummy-service | <0.1 GB | Core tier |

**Implication:** a FULL seed package (Core+Data+Envs) is **≈ 15 GB** — trivial for the
100 TB share. Weekly Core runs are ≈ 1–2 GB (dominated by the main-repo git bundle).
The ~40 stale conda envs and retired source trees are *excluded* by design; archive-then-
delete them at leisure (plan §8.5).
