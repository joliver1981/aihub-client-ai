# Handoff — seed missing `.env` keys in the installer (v5 `.iss`)

**Status:** implemented in commit `237878b`. This document is the spec and the
reasoning; read it before changing or extending the key list.

**Owner decision needed on one item only:** whether to also track `dist\.env`
(see "Open question" at the end). Everything else is done.

---

## The problem in one line

`dist\.env` is installed with `onlyifdoesntexist`, so **an upgraded install
never receives any key added to `dist\.env` after that client first installed.**

```
AIHub_Setup_Script_v5_OneDir_Dev.iss:84
Source: "...\dist\.env"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
```

That flag is correct — it must not clobber a client's tuned `.env`. The
consequence is that new configuration silently never arrives, and the symptom is
whatever that key controlled. There is no error and nothing in a log that says
"you are missing a key".

Scale of the gap at the time of writing: **114 keys in `dist\.env`, 11 seeded on
upgrade.** Most of the remaining ~99 are inert (log paths, SMTP placeholders,
prompt text) and deliberately left alone.

## How it was found

The Agent on the installed box at `10.0.0.6` answered every turn with:

```
{"type":"error","error":"Claude Code returned an error result: success"}
```

`GET :5111/health` on that box explained it:

```json
{"anthropic_key_present": false, "anthropic_key_source": "none"}
```

`agent_service/agent_config.py::ensure_anthropic_key()` resolves the Anthropic
credential in this order:

    BYOK  ->  RELAY  ->  ANTHROPIC_API_KEY  ->  encrypted

That box has no BYOK key (`byok_enabled: false`, `anthropic_configured: false`),
so the **relay is its only path** — and `AGENT_ANTHROPIC_RELAY` was absent from
its `.env`, because the box was upgraded rather than freshly installed. The
chain ran off the end and returned `"none"`.

## The mechanism to use

`EnsureEnvKeyExists(FilePath, Key, Value)` already exists in the `.iss`
(around line 254). It reads the key, appends it only if missing, and logs both
outcomes. Follow the existing `CC_ROUTE_MEMORY` / `CC_INSPECT_ENVIRONMENT`
calls as the pattern.

**There are TWO lists and they must stay in sync.** The `.iss` already carries a
comment saying so; it is easy to update one and miss the other:

| Path | Where | What it does |
|---|---|---|
| Upgrade | `EnsureEnvKeyExists(...)` calls, ~line 1264+ | appends keys missing from an existing `.env` |
| New install | the `ConfigText := #13#10 + ...` block, ~line 1310 | writes keys into a brand-new `.env` |

A key added to only one list produces exactly the bug this document exists to
fix — just for the other install path.

## Keys seeded

| Key | Value | Code default | Why it must be seeded |
|---|---|---|---|
| `AGENT_ANTHROPIC_RELAY` | `true` | `false` | Only Anthropic path for a non-BYOK client. Without it every Agent turn dies. |
| `AGENT_RELAY_URL` | `https://ai-hub-api.azurewebsites.net` | — | Relay endpoint. |
| `NLQ_ENGINE_DEFAULT` | `agentic` | **`legacy`** | Upgraded clients silently run the OLD NL→SQL engine. |
| `DOC_SEARCH_ENGINE_DEFAULT` | `v2` | **`legacy`** | Upgraded clients silently run the OLD document search. |

The last two are the quiet half and matter as much as the relay. Their code
default is `legacy` (`config.py`), so **every client who upgraded rather than
installed fresh has been running the previous-generation NL→SQL and
document-search engines** — same build, different behaviour, no error anywhere.
Unlike the relay failure, this one has no visible symptom at all.

`AGENT_RELAY_URL` was also unpinned from the `-p01` test slot (pinned
2026-08-09) to production in `dist\.env`. A test slot must never ship.

## Deliberately NOT seeded

- **`AUTH_MIDDLEWARE_DRY_RUN`** — owner decision, parked. Worth recording the
  mechanics so nobody trips over them later: the code default in
  `auth_middleware.py:109` is `false`, i.e. **enforcing**. An install lacking
  the key therefore enforces; `dist\.env` setting it `true` is what turns
  enforcement *off*, and only for fresh installs. This means fresh and upgraded
  installs of the same build differ in whether authorization is enforced. Do not
  change without an explicit decision.
- The ~99 inert keys (log paths, SMTP placeholders, LLM prompt text, thread
  counts). Their code defaults are fine and seeding them adds churn.

## How to verify on a box

```bash
findstr /C:"AGENT_ANTHROPIC_RELAY" "C:\Program Files\AIHub\.env"
```

Then confirm the service actually picked it up — this is the authoritative
check, because the key can be present and still not resolve:

```bash
curl http://<host>:5111/health
```

Expect `"anthropic_key_source"` to be `"relay"` (or `"byok"` if the client
entered their own key). `"none"` means the chain still ran off the end.

## Related defect worth fixing separately

When the chain ends at `"none"`, the user sees
`"Claude Code returned an error result: success"`. `/health` already knows
exactly what is wrong. The turn should say "no Anthropic key configured" — on a
client install the current message produces a support ticket instead of a
two-minute settings fix.

## Open question for the owner

`dist\.env` is **gitignored**, so the relay fix and the `-p01` unpin are not in
version control — they live on whichever machine builds the installer. The
`.iss` change *is* committed and covers the upgrade path, but a **fresh** install
reads `dist\.env`. Options: track the file, or derive it from a tracked
template. No action taken.
