# Defect — installed Command Center: `No module named 'command_center.artifacts.data_export'`

**Found:** 2026-09-02, on the installed build at `10.0.0.6`.
**Reproduces from source:** no. Install-only.
**Severity:** Command Center cannot return data from a data agent on a client
install. The user gets a 500 surfaced as agent failure text.

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

## Root cause

This is the `command_center.orchestration` shadowing bug (client, 2026-09-01,
fixed in `c40abae`) reappearing on a different submodule.

`scripts/stage_cc_tools_subset.ps1` stages a **partial** loose `command_center`
package into `{app}` so the other services can import the portal tools. Its
closed set is six files:

```
command_center\__init__.py
command_center\tools\__init__.py
command_center\tools\portal_workflows.py
command_center\tools\portal_registry.py
command_center\tools\portal_fetch.py
command_center\tools\portal_workflow_run.py
```

There is **no `command_center\artifacts\` in that set.** Because `{app}` lands
at `sys.path[0]` for services built with PyInstaller 6.19, this partial package
shadows the CC executable's own complete bundled `command_center`. Any
`command_center.artifacts.*` import then resolves against the loose package,
finds no `artifacts` subpackage, and raises `ModuleNotFoundError`.

`command_center/artifacts/data_export.py` **does exist in the source tree** —
it is simply never staged, and the bundled copy is unreachable behind the
shadow.

## Why the existing guard did not catch it

The staging script already validates import closure and prints
`command_center imports that ESCAPE the shipped set`. It did not fire here, and
the reason matters for the fix:

**The closure check only walks imports found inside the six staged files.**
`data_export` is imported by CC's *own bundled* code, not by the staged portal
tools — so it is outside the set the script inspects. The check validates "can
the staged subset import itself", not "can everything that will now resolve
against this shadowing package still be found".

There is also an explicit tolerance list:

```powershell
# portal_fetch._register_artifacts: ArtifactType -> None, downloads are still returned.
"command_center.artifacts.artifact_models"
```

So `artifacts.*` was already known to escape — but `artifact_models` degrades
gracefully (artifact type becomes `None`, downloads still work), which is why it
was waved through. `data_export` does not degrade; it raises. The tolerance list
records a *known* escape without distinguishing "degrades" from "fatal".

## Fix options

| Option | Notes |
|---|---|
| **A. Stage the `artifacts` subpackage too** | Add `command_center\artifacts\__init__.py`, `artifact_models.py`, `data_export.py` (and whatever their closure pulls in) to `$Files`. Smallest change; keeps the current architecture. Risk: the shadow remains, so the next unstaged submodule fails the same way. |
| **B. Stop the loose package shadowing the bundle** | The real defect is that a partial package outsranks a complete one. If the services that need the portal tools imported them from a distinctly-named package (or `{app}` were not `sys.path[0]` for CC), this class of bug disappears. Larger change, permanent. |
| **C. Harden the closure check** | Make the guard walk the imports of the *shipped CC bundle* against the loose package, not just the staged files against themselves, and require every tolerated escape to be annotated "degrades" vs "fatal". Catches the next one at build time instead of at a client. |

**Recommendation:** A now (unblocks the release), C next (so this family stops
recurring silently), B when the packaging is next revisited.

## How to verify the fix

On the installed box after reinstalling:

```bash
curl -s http://<host>:5091/health          # expect graph_ready: true
```

then drive a delegated data turn through CC — the check now exists as
`test_human/24_Installed_Smoke`, and this specific path is what pack 16's
`b3_ambiguous_multi_id` exercises. A green result must contain the number, not
`Agent returned status 500`.

## Notes for whoever picks this up

- Do not "fix" this by testing CC only with arithmetic prompts. A plain
  `1875 / 25` turn passes on the installed box today — the failure needs a turn
  that delegates to a **data** agent and returns a dataset.
- The direct agent API is not a proxy for CC. Direct returned 200 while CC
  returned 500 on the same box, same agent, same question.
- This is exactly the class of defect that only a post-install test path can
  find; see `docs/two-path-regression-testing.md`.
