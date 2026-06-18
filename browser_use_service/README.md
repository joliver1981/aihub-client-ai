# Browser Use Service

An isolated AI Hub microservice that **simulates a user logging into a web portal and
downloading files** (RPA-style), driven by [`browser-use`](https://github.com/browser-use/browser-use).
Created with the `aihub-new-service` skill (Strategy B: isolated conda env).

- **Port:** `5101` by default (`HOST_PORT + 100`), overridable via `BROWSER_USE_PORT`.
- **Env:** dedicated conda env **`aihub-browseruse`** (Python 3.11) — NOT the main `aihub2.1` env.
- **Why isolated:** `browser-use` pins `openai==2.16.0` and pulls `starlette>=1.x`, which would
  break `langchain-openai` (needs `openai>=2.20`) and `fastapi` (`<0.47`) in the main app.
- **Service name (NSSM):** `AIHubBrowserUse`.

## Files
| File | Purpose |
|---|---|
| `browser_use_config.py` | Port resolver (override-aware), feature flags, paths, secret access |
| `main.py` | FastAPI app: `GET /health`, `POST /portal/fetch` |
| `portal_runner.py` | Drives `browser-use`; harvests downloads by directory-diff |
| `requirements.txt` | Service deps (installed into `aihub-browseruse`) |
| `INSTALL.md` | Exact installer (`.iss`) + `CommonUtils.py` + `.env` wiring |

## Provision the env (once per machine)
```powershell
conda create -n aihub-browseruse python=3.11 -y
conda run -n aihub-browseruse python -m pip install -r browser_use_service\requirements.txt
```
> browser-use 0.12.x drives Chrome over **CDP (`cdp-use`)** — there is **no** `playwright install`.
> It uses a local Chrome/Chromium; if none is auto-detected, set `executable_path`/`channel` on the
> `BrowserSession` in `portal_runner.py`, or install Google Chrome on the host.

## Run (dev)
```powershell
conda run -n aihub-browseruse python browser_use_service\main.py
# health:
Invoke-RestMethod http://127.0.0.1:5101/health
```

## API
`POST /portal/fetch` — header `X-AIHub-Internal: <API_KEY>` (unless `BROWSER_USE_AUTH_ENFORCE=false`)
```jsonc
{
  "task": "download the latest invoice PDFs",
  "start_url": "https://portal.example.com/login",
  "portal_name": "example",
  "username_secret": "PORTAL_EXAMPLE_USERNAME",  // KEY in local_secrets, not the value
  "password_secret": "PORTAL_EXAMPLE_PASSWORD",
  "totp_secret": "PORTAL_EXAMPLE_TOTP",          // optional (authenticator-app 2FA)
  "session_id": "abc123"
}
```
Returns `{ status, error, elapsed_seconds, files:[paths], file_count, final_result }`.
The main app then registers `files` into `ArtifactManager` (scoped to the user/session) so they
surface as download chips in chat.

### Store portal credentials (encrypted, never in .env)
```python
from local_secrets import set_local_secret
set_local_secret("PORTAL_EXAMPLE_USERNAME", "alice@example.com", category="credentials")
set_local_secret("PORTAL_EXAMPLE_PASSWORD", "••••••••", category="credentials")
set_local_secret("PORTAL_EXAMPLE_TOTP", "JBSWY3DPEHPK3PXP", category="credentials")  # TOTP shared secret
```

## Agent integration (Command Center)
The CC agent has a `fetch_from_portal(portal_name, start_url, task)` tool — the `@lc_tool` is in
`command_center_service/graph/nodes.py`; its core is `command_center/tools/portal_fetch.py`. It
POSTs to this service, then registers any downloaded files as CC artifacts so they render as
**download chips** in chat (no frontend change — same path as the code interpreter). It's gated by
`BROWSER_USE_ENABLED` + `BROWSER_USE_ALLOW_ALL_USERS` (default Developer+ only). Credentials never
reach the LLM: the tool derives the secret KEY NAMES from `portal_name`
(`PORTAL_<NAME>_USERNAME/_PASSWORD/_TOTP`) and the service resolves them from the encrypted store.
**Restart the CC service after deploying** (it runs from source under `aihubbuilder`).

Verified end-to-end against `tests/test_portal.py` (a local login+download portal): the agent tool
drives the service, which logs in and downloads, and the file comes back as a registered artifact.

## Config (env vars)
| Var | Default | Meaning |
|---|---|---|
| `BROWSER_USE_PORT` | `HOST_PORT+100` (5101) | Override the port in production |
| `BROWSER_USE_ENABLED` | `true` | Master on/off |
| `BROWSER_USE_ALLOW_ALL_USERS` | `false` | If false, gate to Developer+ in the calling app |
| `BROWSER_USE_LLM_MODEL` | `gpt-4o` | Model that drives the agent |
| `BROWSER_USE_HEADLESS` | `true` | Headless Chromium |
| `BROWSER_USE_TIMEOUT` | `300` | Per-fetch timeout (s) |
| `BROWSER_USE_MAX_STEPS` | `50` | Max agent steps |
| `BROWSER_USE_DOWNLOAD_DIR` | `<root>\data\browser_use_downloads` | Where downloads land |
| `BROWSER_USE_AUTH_ENFORCE` | `true` | Require the internal token on `/portal/fetch` |

## Caveats (RPA reality)
- **MFA:** TOTP is automatable (store the shared secret); **SMS/push generally is not**.
- **ToS:** confirm you are authorized to automate login to the target portal.
- **Maintenance:** AI-driven nav is resilient to UI changes but non-deterministic and costs
  LLM tokens; add retries + alerting on repeated failures.
