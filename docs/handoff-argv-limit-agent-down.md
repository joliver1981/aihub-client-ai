# Handoff — The Agent is down: system prompt overflows the Windows argv limit

**Status:** OPEN — root-caused, reproduced, not fixed
**Filed:** 2026-09-06
**Severity:** **Critical.** Every turn fails. `/health` still reports `ok`.
**Component:** `agent_service/brain.py` (`SYSTEM_PROMPT`, `_options()`)

---

## Symptom

Every `/api/chat` turn fails in ~0.5s with:

```
ERROR - turn error user=admin session=None:
Claude Code not found at: ...\aihub-agent\Lib\site-packages\claude_agent_sdk\_bundled\claude.exe
```

**The message is wrong.** `claude.exe` is present (284 MB) and runs fine
(`--version` → `2.1.224`, exit 0).

## Cause

The SDK passes the whole system prompt on the **command line**
(`subprocess_cli.py:570` → `cmd.extend(["--system-prompt", <32k string>])`).
Windows caps a command line at **32,767 characters**. We are over it:

| Component | chars |
|---|---|
| `SYSTEM_PROMPT` | 32,461 |
| `allowed_tools` (43 × `mcp__aihub__*`, comma-joined) | 1,251 |
| CLI path + fixed flags | ~350 |
| **total argv** | **~34,062** |
| Windows limit | 32,767 |

`CreateProcess` fails with **`WinError 206` — "The filename or extension is too
long"**, which Python raises as **`FileNotFoundError`**. The SDK catches
`FileNotFoundError`, sees the cwd exists, and relabels it as
"Claude Code not found at: <path>" (`subprocess_cli.py:864-871`). Hence a
length error reported as a missing file.

Reproduced directly on the box:

```
argv ~32,000 chars: OK
argv ~33,000 chars: FileNotFoundError: [WinError 206] The filename or extension is too long
```

## Why now

Prompt growth crossed the cliff:

| Commit | `SYSTEM_PROMPT` | |
|---|---|---|
| before `2782ba1` | 29,878 | worked |
| `2782ba1` scope honesty | 32,378 | +2,500 |
| `065b9f9`, `c46f0c6` | **32,461** | **every turn fails** |

Nothing about the install, the env, the account or the restart is at fault —
a fresh process in the same conda env spawns the CLI fine, because it isn't
carrying a 32k argument.

## Fix

**The SDK already supports a file form.** `SystemPromptFile` (`types.py:60`)
maps to `--system-prompt-file` (`subprocess_cli.py:572-573`). One line in
`brain.py:771`:

```python
# before
system_prompt=SYSTEM_PROMPT,

# after — write SYSTEM_PROMPT once at startup, pass the path
system_prompt={"type": "file", "path": SYSTEM_PROMPT_PATH},
```

That removes 32,461 chars from argv and leaves ~1,600 in use against a 32,767
limit — roughly 20× headroom.

Notes for whoever implements it:
- Write the file once at startup (e.g. `DATA_DIR/agent/system_prompt.md`),
  not per turn. It is static.
- The file must be readable by the service account and must survive restarts;
  regenerate on boot so a prompt edit always takes effect.
- Do **not** solve this by trimming words. The doctrine content is worth
  keeping, and trimming only moves the cliff a few hundred characters away.

## Guards worth adding at the same time

1. **Fail loudly at startup**, not per turn: assert the built command length is
   under ~30,000 and log the actual breakdown if not. A phantom
   "Claude Code not found" cost hours here; the real reason should be one log
   line.
2. **`/health` must reflect turn capability.** It reported `ok` throughout a
   total outage. A cheap check — resolve the CLI and assert the argv budget —
   would have caught this before any user did. On a client install nobody
   would know until a user complained.
3. `allowed_tools` is the other growth vector (1,251 chars, 43 tools) and grows
   with every tool added. Once the prompt moves to a file it is no longer
   urgent, but it is the next thing to hit a limit.

## Is it safe to fix?

**Yes — this is a low-risk change.**

- It uses a supported SDK path (`--system-prompt-file`), not a workaround.
- Behaviour is identical: same prompt text, delivered by file instead of argv.
- It is one line plus a startup file write, with a trivial rollback.
- It is independently verifiable: one chat turn either works or does not.

The genuine risks are small and all mitigable:

| Risk | Mitigation |
|---|---|
| File missing / unreadable at spawn | Write at startup, assert it exists, fail loudly |
| Stale file after a prompt edit | Regenerate on every boot |
| Frozen / installed builds may resolve `DATA_DIR` differently | Use the same `DATA_DIR` the workspace and skills dirs already use — a path the service already writes to |
| A future SDK drops the file form | Pin the SDK version; add the startup length assertion as a backstop |

**Do not ship the current build.** Any customer whose prompt sits near the
limit — or who upgrades into these commits — gets an agent that answers every
message with a missing-file error while reporting healthy.

## Broader lesson

Fixing agent behaviour by adding prompt text is not free, and the failure mode
is not the expected one. We were watching for attention dilution; what actually
happened is that the prompt grew large enough to break process spawn. Once the
prompt moves off argv this specific cliff is gone, but the cost of prompt
growth stays real — prefer code-side fixes (the resolver ladder, the file
staging) over doctrine where both are available.

---

## Resolution — 2026-09-06

Fixed in `agent_service/brain.py` + `main.py` (commit follows this note).

- **Prompt by file.** `SYSTEM_PROMPT` is written to `data/agent/system_prompt.md`
  (atomic write, regenerated on every boot) and the SDK options carry
  `system_prompt={"type": "file", "path": …}` → `--system-prompt-file`. The file
  is re-checked before every turn (`ensure_system_prompt_file`): missing or
  stale content is rewritten, so a prompt edit or a wiped data dir can never
  spawn against an old or absent file.
- **Measured, not estimated.** `measure_argv_chars` builds the exact command
  line through the SDK's own `_build_command` (CLI path resolved via
  `_find_cli`, joined with `subprocess.list2cmdline`). On this box: **723 chars**
  with the 9 product skills, against the 32,767 limit — the inline shape
  measured through the same path is > 33,000, which the unit pack pins as the
  regression case. A coarse estimator is the fallback if SDK internals move.
- **Fail loudly at startup.** `assert_spawn_budget()` runs at import: over the
  30,000-char budget the service logs the breakdown and refuses to start
  (`RuntimeError: The Agent cannot spawn turns: …`) instead of coming up and
  failing every turn with the phantom "Claude Code not found".
- **`/health` reflects turn capability.** It now carries a `spawn` block
  (prompt file present + current, measured argv chars, budget, limit) and
  reports `status: "degraded"` with the reason when a turn could not spawn.
  Liveness alone no longer reads as healthy.
- `allowed_tools` growth: in full scope the option is the `mcp__aihub__*`
  wildcard plus one `Skill(name)` rule per mounted skill, so the 43-name list
  only appears for read-only side threads. Both are covered by the same
  startup measurement.

Unit pack: `tests_v2/unit/test_agent_prompt_file.py` (5/5). Service 5111
restarted with the fix; live turn verified below in the commit message.
