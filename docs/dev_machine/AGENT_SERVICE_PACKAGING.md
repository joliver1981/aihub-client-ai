# Packaging The Agent (agent_service) into the installer

The Agent is a **Strategy B** service (runs `main.py` from source under its own
isolated conda env), exactly like Browser Use. The v5 installer
(`AIHub_Setup_Script_v5_OneDir_Dev.iss`) already carries the service block; the
only build-time work is staging two `dist\` trees.

## What the installer expects

| Staged tree | Installed to | Produced by |
|---|---|---|
| `dist\agent_service\*` | `{app}\agent_service` | `scripts\build_agent_service.ps1` (source minus `__pycache__`, `data\*.db`, dev `.bat`) |
| `dist\agent_env\*` | `{app}\agent_env` | `conda-pack` of the `aihub-agent` env (relocatable; `python.exe` at the root) |

No new loose modules are needed: The Agent imports `shared_auth`, `encrypt`,
`secure_config`, `local_secrets` from `APP_ROOT`, and the installer already
ships those loose to `{app}` for Browser Use.

## Build steps

```powershell
# once: pip install conda-pack   (in any base/conda shell)
powershell -ExecutionPolicy Bypass -File scripts\build_agent_service.ps1
```

This stages both trees. Use `-SkipEnv` to restage only the source when the env
is unchanged (fast iteration). Run it as part of the same build that produces
the other `dist\*` trees, before `ISCC AIHub_Setup_Script_v5_OneDir_Dev.iss`.

## How it runs on the target (NSSM)

Mirrors Browser Use exactly (installer Service 15):

- App image = `{app}\agent_env\python.exe`
- `AppDirectory` = `{app}\agent_service`
- `AppParameters` = `main.py`  (RELATIVE — NSSM strips quotes on spaced
  absolute paths; this is the proven-robust form)
- `AppEnvironmentExtra` = `HOST_PORT=5001` + `"APP_ROOT={app}"` (each KEY=VALUE
  its own quoted argv element, or a spaced install path poisons `APP_ROOT`)
- Service name `AIHubTheAgent`; recovery configured; auto-started.

Port resolves to `HOST_PORT+110 = 5111` (override with `AGENT_SERVICE_PORT`).

## Runtime data & config on the target

- `{app}\.env` is the SHARED root env (already installed). The Agent's flags
  live there: `THE_AGENT_ENABLED`, `THE_AGENT_MODE`, `AGENT_MODEL`,
  `AGENT_SESSION_JOBS_ENABLED`, `AGENT_EMAIL_ENABLED`, and (optional)
  `AGENT_ANTHROPIC_RELAY` / `AGENT_RELAY_URL`. Ship the desired defaults in the
  packaged `dist\.env` template.
- `data\agent\` (SQLite: mywork.db + skills workspace) is created by the
  service on first boot under `APP_ROOT` — never staged, never shipped.

## Verify after install

```powershell
Get-Service AIHubTheAgent
Get-NetTCPConnection -LocalPort 5111 -State Listen
Invoke-RestMethod http://127.0.0.1:5111/health
```

## Not verifiable on the dev box

The `.iss` **compiles clean** (`ISCC` exit 0) with the staged trees, and the
staging script is proven. The full install (conda-pack relocation actually
running on a clean target, service start under the service account) can only be
verified by running the built installer on a target machine — do that before
the first client ship.
