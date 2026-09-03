# Defect — installed Command Center: `No module named 'command_center.artifacts.data_export'`

**Found:** 2026-09-02, on the installed build at `10.0.0.6`.
**Reproduces from source:** no. Install-only.
**Severity:** Command Center cannot return data from a data agent on a client
install. The user gets a 500 surfaced as agent failure text.
**Status (2026-09-02, later the same day):** root-caused — it is a PyInstaller
blind spot in `app.exe`, **not** the loose-package shadow the first write-up
blamed — and fixed in source (`app_onedir.spec` + build guard + tests + two
installed-smoke checks). A rebuilt `app.exe` has to reach the box before the
smoke checks go green.

---

## Symptom

Ask Command Center to query a data agent on the installed box:

```
POST http://10.0.0.6:5091/api/chat
{"message": "Ask agent 2 how many rows are in the largest table."}
```

CC answers 200, but the delegated turn inside it carries:

```
"Agent returned status 500: No module named 'command_center.artifacts.data_export'"
```

The same agent, called directly on the same box, is completely healthy:

```
POST http://10.0.0.6:5001/api/agents/2/chat   -> 200 in 22.8s
   table_name             row_count
   daily_store_inventory  278928
```

So: the agent works, CC is up (`/health` → `graph_ready: true`, and a plain
arithmetic turn returns the right answer). **Only the data-export path is
broken, and only when installed.**

## Root cause (corrected — not the loose-package shadow)

The first version of this document filed the defect under the
`command_center.orchestration` shadowing family (a partial loose
`{app}\command_center` outranking the CC exe's bundle, fixed in `c40abae`).
Three facts rule that out:

1. **The 500 is not raised in the Command Center process.** The text
   `Agent returned status 500: ...` is what
   `command_center/orchestration/delegator.py` returns when the HTTP call *it*
   makes comes back 500. For **data** agents that call goes to the main app:
   `POST {base}/data_explorer/internal/query` — not to `/api/agents/<id>/chat`.
   The direct agent call passing on the box therefore proves nothing about this
   path; it is a different endpoint in the same process.
2. **The importer is the main app.** `routes/data_explorer.py:689` runs
   `from command_center.artifacts.data_export import maybe_persist_result_artifacts`
   unconditionally at the end of every internal query. Nothing else in the main
   app imports `data_export`.
3. **PyInstaller never sees that file's imports.** `app.py` loads
   `routes/data_explorer.py` by file path (`importlib.util.spec_from_file_location`,
   `app.py` ~13976 — on purpose, so `routes/` never becomes a package that
   shadows `builder_service/routes`). `app_onedir.spec` accordingly ships the
   file as a raw **data file**. PyInstaller only walks imports through modules
   it can import by name, so every module that file imports is bundled only if
   some *other* module imports it by name. Six of its seven first-party imports
   are (`CommonUtils`, `app`, `config`, `engine_enhancements`,
   `nlq_engine_factory`, `role_decorators`). `command_center.artifacts.data_export`
   is imported nowhere else → never bundled.

Proof from the built `dist\app\app.exe` of the Latest5 chain (2026-09-01), PYZ
table of contents: 18 `command_center.*` modules are bundled, including
`command_center.artifacts` and its `artifact_manager`, `artifact_models`,
`delegated_capture`, `produced_sink` — and **not** `data_export`. Under `routes`
only `routes.portal_workflows_routes` exists; `routes.data_explorer` is absent
because it is the data file. That is also why the error names the full dotted
path: the `artifacts` package resolves from the bundle, its `data_export`
member does not exist there.

`app.exe` is built by PyInstaller 6.8 (`aihub2.1`), whose frozen importer sits
on `meta_path` ahead of the path finder, so the loose-folder shadow could never
have affected it — and on 10.0.0.6 the CC exe answers arithmetic turns, i.e.
its own bundle is intact.

Why it never showed before: `data_export.py` landed 2026-07-12 (`95e7230`).
Every client build since carries the hole; nobody drove CC → data agent on an
installed box until packs 16/24 ran against 10.0.0.6 on 2026-09-02.

Reproduction on the box without CC in the loop (needs the box's own API key —
the route validates `X-API-Key` itself, and the box does not share the dev
tree's key):

```
POST http://10.0.0.6:5001/data_explorer/internal/query
X-API-Key: <box key>
{"agent_id": 2, "question": "How many rows are in the largest table?", "session_id": "probe"}
-> 500 after ~26 s: No module named 'command_center.artifacts.data_export'
```

The query runs to completion first; the artifact step's import then fails.

## Why the existing guards did not catch it

- `scripts/stage_cc_tools_subset.ps1`'s closure check guards a different
  artifact: the service-local `command_center.tools` copy for The Agent and
  Browser Use. It never claimed to validate `app.exe`.
- `app_onedir.spec` had no notion that a data file can carry imports. Its
  hidden-import comments (jwt, paramiko, `doc_search_*`) describe the sibling
  trap — lazy imports inside function bodies of *bundled* modules — but that one
  only bites for string/`importlib` imports. A path-loaded file bites for
  **every** import in it, module-level included.

## Fix (landed 2026-09-02)

1. `scripts/dynamic_route_imports.py` — stdlib-only helper. AST-walks a
   path-loaded file and returns every absolute import (module level or nested)
   as dotted names; `verify_bundled()` reports the first-party names missing
   from an `Analysis`.
2. `app_onedir.spec` — `PATH_LOADED_SOURCES = ['routes/data_explorer.py']`.
   Every derived name is appended to `hiddenimports` (21 for `data_explorer`,
   7 first-party, `command_center.artifacts.data_export` among them). After
   `Analysis` the spec **fails the build** (`SystemExit`) if any first-party
   name is still absent from `a.pure` / `a.binaries`. `user_config` and
   `user_prompts`, shipped loose by design, are tolerated.
3. `tests_v2/unit/test_app_spec_path_loaded_imports.py` (7 tests): the
   derivation yields `data_export`; the guard flags exactly the 2026-09-02
   bundle; a drift test parses `app.py` for every `spec_from_file_location`
   load and asserts the spec lists it.
4. `test_human/24_Installed_Smoke` — two new checks that FAIL on the box today
   (red-to-green signal for the rebuilt box):
   - `cc_delegation_endpoint` hits `/data_explorer/internal/query` directly,
     the exact surface, and reports `FROZEN-BUILD PACKAGING DEFECT` on a
     `No module named` / `cannot import name` body. SKIPs (never FAILs) when the
     box's key is not supplied.
   - `cc_data_agent_turn` drives a CC turn that must delegate to a data agent
     and come back with a number, not `returned status 5xx`.

Left alone on purpose: the import at `routes/data_explorer.py:689` stays
unguarded. Wrapping it in `try/except` would turn a packaging defect into a
silent loss of the CSV-export handle on every client; the build-time guard is
the honest signal.

## Fix options considered

| Option | Verdict |
|---|---|
| Stage `artifacts\` into the service-local `command_center` copies | Irrelevant: those copies serve The Agent / Browser Use; `app.exe` never sees them. |
| One hidden import added by hand | Fixes this module, leaves the class open: the next lazy import in `routes/data_explorer.py` ships broken again. |
| **Derive + verify (done)** | Every import in every path-loaded file is bundled, and the build fails otherwise. |
| Make `routes/` a real package | PyInstaller would analyse it, but it reintroduces the `builder_service/routes` shadowing `app.py` deliberately avoids. |

## How to verify the fix

Build time — step `[1/13]` of `Build_AIHub_Executables_OneDir_Dev_v3.bat` prints
`path-loaded routes/data_explorer.py: 21 hidden imports, 7 first-party: ...` and
`path-loaded route imports verified in bundle: ...`. Then the PYZ of
`dist\app\app.exe` lists `command_center.artifacts.data_export` (aihub2.1
python: `PyInstaller.archive.readers.CArchiveReader(exe).open_embedded_archive('PYZ-00.pyz').toc`).

On the box, after reinstalling (or after replacing `{app}\app\` with the
rebuilt `dist\app\` and restarting the `AIHubApp` service):

```
cd test_human\24_Installed_Smoke
python runner.py --host 10.0.0.6 --only cc_d --api-key-file C:\Users\james\.secrets\aihub-10.0.0.6-api-key.txt
```

`cc_delegation_endpoint` and `cc_data_agent_turn` must both PASS. Pack 16's
`b3_ambiguous_multi_id` exercises the same path through CC.

Hot-fix on a client without a rebuild (**not verified**): copy
`command_center\artifacts\data_export.py` from the source tree to
`{app}\app\_internal\command_center\artifacts\data_export.py` and restart
`AIHubApp`. The frozen `command_center.artifacts` package's `__path__` points
into `_internal`, so the path finder picks up a source file there for a member
the archive lacks.

## Notes for whoever picks this up

- Do not "fix" this by testing CC only with arithmetic prompts. A plain
  `1875 / 25` turn passes on the installed box today — the failure needs a turn
  that delegates to a **data** agent and returns a dataset.
- The direct agent API is not a proxy for CC, and for a sharper reason than
  first thought: CC's delegator does not call `/api/agents/<id>/chat` for data
  agents at all.
- Same hole elsewhere: `wsgi_executor_service` does not bundle `app.py`'s
  path-loaded files at all (see the comment near `compliance_engine.py:765`);
  that is by design — only `app.exe` registers this blueprint. Any *new*
  `spec_from_file_location` load in `app.py` must be added to
  `PATH_LOADED_SOURCES`; the unit test fails if it is not.
- This is exactly the class of defect that only a post-install test path can
  find; see `docs/two-path-regression-testing.md`.
