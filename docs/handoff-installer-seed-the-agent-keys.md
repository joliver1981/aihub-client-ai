# Handoff — Installer must seed the `THE_AGENT_*` keys on upgrade

**Status:** OPEN — diagnosed, not fixed
**Filed:** 2026-09-02
**Component:** `AIHub_Setup_Script_v5_OneDir_Dev.iss` (v2.0 installer)
**Severity:** High for the v2.0 release — the headline feature is unreachable for every
upgrading customer without hand-editing a file.

---

## 1. The problem

The Agent is the headline feature of v2.0. It ships **off** by default (deliberate — james,
2026-08-25: client installs go legacy-first). An administrator turns it on by setting three keys
in `{app}\.env`:

```
THE_AGENT_ENABLED=true
THE_AGENT_MODE=true
```

Those keys exist in the template `dist\.env` (lines 281, 284, 287), which the installer ships:

```
Source: "C:\src\aihub-client-ai-dev\dist\.env"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
```

`AIHub_Setup_Script_v5_OneDir_Dev.iss:84`

The `onlyifdoesntexist` flag is correct and must stay — it is what stops an upgrade from
destroying a customer's configuration. But its consequence is that **the template only ever
reaches fresh installs.** On an upgrade the customer keeps their existing 1.8.1-era `.env`, which
has never heard of these keys, and nothing adds them.

**Result:** an administrator upgrading 1.8.1 → 2.0 opens `.env`, finds no `THE_AGENT_*` keys, and
has no in-product way to discover that the setting exists or what to name it. The code defaults
are `false` (`os.getenv('THE_AGENT_ENABLED', 'false')`), so The Agent stays invisible and the
upgrade silently delivers none of the release's headline feature.

### This exact bug has been fixed once before

Commit `fc042d6` (2026-08-04) hit the identical root cause for three Command Center keys and
records the diagnosis verbatim: *"The dist .env template only reaches FRESH installs; an upgrade
keeps the customer's existing .env, so a new setting never appears there on its own."*

That commit established the pattern this fix should follow. **Reuse it; do not invent a new
mechanism.**

---

## 2. The fix

There is already a helper for exactly this, and it is idempotent:

```pascal
function EnsureEnvKeyExists(const FilePath, Key, Value: String): Boolean;
```

`AIHub_Setup_Script_v5_OneDir_Dev.iss:254` — appends `Key=Value` only when the key is absent,
always preserves an existing value, logs both branches, returns `False` only on a write failure.

### Change required — upgrade path only

Add three `EnsureEnvKeyExists` calls to the existing upgrade block, immediately after the
`CC_PICKER_CONNECTIONS` call that currently ends it (**around line 1268**, just before the
closing `end` / `else` for the new-install branch). Match the surrounding style — a comment
block explaining *why*, then the guarded call with a warning `MsgBox` on failure:

```pascal
      // --- THE_AGENT_* (v2.0) ---
      // The Agent ships OFF on client installs (legacy-first, james 2026-08-25).
      // The dist .env template carries these keys, but it only reaches FRESH
      // installs (onlyifdoesntexist), so an upgraded .env would never contain
      // them and an admin would have no way to discover the setting exists.
      // Seed them as false: behaviour is unchanged, but the switch is now
      // present, commented in-file, and flippable without a deploy.
      if not EnsureEnvKeyExists(EnvConfigFile, 'THE_AGENT_ENABLED', 'false') then
      begin
        MsgBox('Warning: Failed to write THE_AGENT_ENABLED to .env.' + #13#10 +
               'You may need to add it manually',
               mbError, MB_OK);
      end;

      if not EnsureEnvKeyExists(EnvConfigFile, 'THE_AGENT_MODE', 'false') then
      begin
        MsgBox('Warning: Failed to write THE_AGENT_MODE to .env.' + #13#10 +
               'You may need to add it manually',
               mbError, MB_OK);
      end;

      if not EnsureEnvKeyExists(EnvConfigFile, 'THE_AGENT_NAV_LENS', 'false') then
      begin
        MsgBox('Warning: Failed to write THE_AGENT_NAV_LENS to .env.' + #13#10 +
               'You may need to add it manually',
               mbError, MB_OK);
      end;
```

### ⚠️ Do NOT add these to the `ConfigText` block

This is where this fix **differs from the `fc042d6` precedent** — read this before copying that
commit wholesale.

`fc042d6` added its three keys to *both* the upgrade path and the fresh-install `ConfigText`
block (line ~1310). For `THE_AGENT_*` that would be wrong, because `dist\.env` **already
contains all three keys** (unlike `APP_ROOT` or `HOST_PORT`, which are computed at install time).

`ConfigText` is appended to the freshly-copied `dist\.env` (`SaveStringToFile(..., True)`), so
adding them there would write each key **twice** on every fresh install.

**Pre-existing wart, out of scope but worth knowing:** `CC_ROUTE_MEMORY`,
`CC_INSPECT_ENVIRONMENT`, `CC_PICKER_CONNECTIONS` and `EMAIL_PROVIDER` are *already* duplicated
this way on fresh installs — they sit in both `dist\.env` and `ConfigText`. It is currently
harmless (identical values, and python-dotenv's last-write-wins means the `ConfigText` copy
takes effect), but do not add a fourth instance of the mistake. Cleaning up the existing
duplicates is a separate, optional task — mention it, don't bundle it.

### Minor caveat in the helper

`EnsureEnvKeyExists` treats a key whose value is **empty** (`KEY=`) as missing, because
`ReadEnvFileFromPath` returns `''` for both cases. A customer who blanked a value would get a
duplicate appended. Not a concern for these three keys (seeded as `false`, never blank), but
don't "fix" it by changing the helper — every existing caller depends on its current behavior.

---

## 3. Verification

1. **Compile.** `ISCC` (v6) must parse the script and the `[Code]` Pascal must compile. This is
   the check `fc042d6` used and is the minimum bar.
2. **Upgrade path (the actual bug).** Install 1.8.1, confirm `{app}\.env` has no `THE_AGENT_*`
   keys, run the v2.0 installer over it, then confirm all three keys are present and set to
   `false`. Check the install log for the three `Env key missing, appending:` lines.
3. **Idempotency.** Run the v2.0 installer a second time over the now-upgraded install. The log
   must show `Env key already present:` for all three, and `.env` must contain exactly one copy
   of each.
4. **Value preservation — the important one.** Hand-set `THE_AGENT_ENABLED=true`, re-run the
   installer, and confirm it stays `true`. An upgrade must never switch off a feature the
   customer deliberately turned on.
5. **Fresh install.** Confirm `.env` contains exactly one copy of each of the three keys (this is
   what regresses if someone also adds them to `ConfigText`).
6. **Functional.** With the seeded `false` values, the upgraded install must behave exactly as it
   did before — no nav entry, no redirect. Then flip `THE_AGENT_ENABLED=true`, restart services,
   and confirm the nav entry appears and `/the-agent` redirects.

---

## 4. Related

- `docs/RELEASE_NOTES_v2.0.md` — the upgrade notes currently document the manual `.env` edit as a
  workaround. **When this ships, simplify that bullet** back to "enabled by an administrator" and
  drop the hand-editing instructions.
- `fc042d6` — the precedent commit (CC keys, same root cause, same helper).
- Keys are consumed in `app.py:1624-1625` (Agent Mode redirect), `app.py:1971` (`/the-agent`
  gate), and `app.py:14103-14105` / `14155-14157` (template flags).
