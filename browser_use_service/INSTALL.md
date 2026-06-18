# Installer / service wiring — Browser Use service (`AIHubBrowserUse`)

Generated via the `aihub-new-service` skill. This is a **Strategy B** service (runs from source
under the dedicated conda env `aihub-browseruse`), so unlike the frozen services it needs its env
**provisioned on the target host**. Read `.claude/skills/aihub-new-service/references/inno-nssm.md`
→ "Strategy B on the target" first.

## Wiring status
**Already applied (dev repo):**
- `browser_use_service/*` — service code.
- `CommonUtils.py` — `get_browser_use_api_base_url()` (override-aware).
- `.env` — `BROWSER_USE_*` keys.
- Isolated env `aihub-browseruse` provisioned on this dev box.

**To apply in `AIHub_Setup_Script_v4_OneDir_Dev.iss` (4 edits below).** Left as a documented patch,
not auto-applied: the `.iss` is production-critical and must be compiled + test-installed on a clean box.

---

## 0. Target prerequisite — provision the env (Strategy B)
The installer copies source, not the interpreter. Provision `aihub-browseruse` on the target before
the service starts:
```
conda create -n aihub-browseruse python=3.11 -y
conda run -n aihub-browseruse python -m pip install -r "{app}\browser_use_service\requirements.txt"
```
Pick one delivery path: (a) a post-install `[Run]` step that calls a bootstrap `.bat`; (b) ship an
embedded Python folder under `{app}\browser_use_env\` and point NSSM there; (c) a documented manual
step. Then set `BROWSER_USE_PYTHON` in `{app}\.env` to that `python.exe`.

**Browser:** browser-use 0.12.9 drives Chrome over CDP (via `cdp-use`) — it does **not** use Playwright.
Ensure Chrome/Chromium is present on the target (confirm exact requirement via
`introspect_browser_use.py` / the runner-refine step).

## 1. `[Files]` — ship the source (add after line 62)
```
Source: "C:\src\aihub-client-ai-dev\browser_use_service\*"; DestDir: "{app}\browser_use_service"; Flags: ignoreversion recursesubdirs createallsubdirs
```
(There is no `dist\browser_use_service` — Strategy B ships source.)

## 2. `StopAndRemoveServices()` — clean upgrades (lines 316-352)
Grow both arrays from `[0..12]` to `[0..13]` and add the 14th entry:
```pascal
  Services: array[0..13] of String;
  Executables: array[0..13] of String;
  ...
  Services[13] := 'AIHubBrowserUse';
  Executables[13] := 'python.exe';   // see CAUTION
```
**Also bump every `for I := 0 to 12` loop in this procedure to `0 to 13`** — the `.iss` has several
hardcoded loop counters that iterate the service list (not just the two array declarations); an
un-bumped loop silently skips `AIHubBrowserUse` during upgrade cleanup.

**CAUTION** — the cleanup loop runs `taskkill /F /IM <Executables[i]>`. For a Strategy B service the
image name is `python.exe`, so taskkilling it would kill **unrelated** python processes. Safer:
- rely on the NSSM `stop`/`remove` calls (issued here and in `[UninstallRun]`) and **skip the taskkill
  for index 13** — e.g. guard the loop to ignore `python.exe`; **or**
- run the service behind a uniquely-named launcher exe and use that name here.

## 3. `InstallServices()` — add "Service 14" (after the Command Center block, line 968)
Strategy B shape — NSSM `Application` = the env python, argument = `main.py`, plus `AppDirectory`:
```pascal
  // =========================================================================
  // Service 14: Browser Use (Strategy B - conda python runs main.py)
  // =========================================================================
  ShellExec('', ExpandConstant('{app}\nssm.exe'),
    'install AIHubBrowserUse "' + BrowserUsePython + '" "' + ExpandConstant('{app}\browser_use_service\main.py') + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubBrowserUse AppDirectory "' + ExpandConstant('{app}\browser_use_service') + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  if not UseSystemAccount then
    Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubBrowserUse ObjectName ' + LocalDomain + '\' + LocalUser + ' ' + LocalPwd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  // Pass env so prod can override the port without a rebuild (Step 1 of the skill):
  Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubBrowserUse AppEnvironmentExtra HOST_PORT=5001 APP_ROOT=' + ExpandConstant('{app}'), '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{app}\nssm.exe'), 'set AIHubBrowserUse Description "AI Hub Browser Use (portal RPA) service"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  ConfigureServiceRecovery('AIHubBrowserUse');
  Exec(ExpandConstant('{app}\nssm.exe'), 'start AIHubBrowserUse', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Log('AIHubBrowserUse service started');
```
Define `BrowserUsePython` in `[Code]` — e.g. read it from `{app}\.env` (`BROWSER_USE_PYTHON`) or a
const pointing at the provisioned env's `python.exe`.

## 4. `[UninstallRun]` — stop/remove on uninstall (add after line 1259)
```
Filename: "{app}\nssm.exe"; Parameters: "stop AIHubBrowserUse"; Flags: runhidden waituntilterminated
Filename: "{app}\nssm.exe"; Parameters: "remove AIHubBrowserUse confirm"; Flags: runhidden
```

## 5. Verify (post-install, on the box)
```
Get-Service AIHubBrowserUse
Get-NetTCPConnection -LocalPort 5101 -State Listen
Invoke-RestMethod http://127.0.0.1:5101/health
```

> The port is `HOST_PORT + 100` (5101) by default. To move it in production, set `BROWSER_USE_PORT`
> in `{app}\.env` (or via the NSSM `AppEnvironmentExtra` line) and restart the service — the
> `CommonUtils` client helper reads the same var, so callers follow automatically.
