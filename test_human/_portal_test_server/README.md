# Meridian Supply Co. — local 2FA vendor-portal fixture

A self-contained, **localhost-only** web portal used by the WOW-demo Portal Workflow beat and
any Portal Workflow / browser-automation test that needs a real login + **TOTP 2FA** + gated
file downloads. Looks like a real B2B supplier portal on screen.

| Thing | Value |
|---|---|
| URL | `http://127.0.0.1:3000` (`PORTAL_TEST_PORT` to override) |
| Login | `tc_purchasing` / `Demo2026!` (`PORTAL_TEST_USER` / `PORTAL_TEST_PASS`) |
| TOTP seed | `JBSWY3DPEHPK3PXP` — base32, RFC 6238, 30 s, 6 digits (`PORTAL_TEST_TOTP_SECRET`) |
| Flow | `/login` → `/verify` (2FA) → `/documents` (downloads, auth-gated) |
| Stage prop | `/authenticator` — shows the live rotating code like a phone authenticator app |
| Run | `Start_Portal_Server.bat` (aihub2.1 python; generates `files/` then serves) |
| Verify | `python selftest.py` with the server up — 7 checks, expects ALL PASS |

Local throwaway demo credentials, committed on purpose — they gate only generated fixture
files. The TOTP implementation is stdlib (`hmac`/`struct`), so the server has **no deps
beyond Flask**; `make_fixtures.py` uses PyMuPDF for the PDF invoices when available.

## How the demo wiring uses it

- Registered in AI Hub as portal **“Meridian Vendor Portal”** (owner: admin/13) via
  `command_center.tools.portal_registry.save_portal` — credentials live in the encrypted
  local secrets store under `PORTAL_U13_MERIDIAN_VENDOR_PORTAL_USERNAME/_PASSWORD/_TOTP`;
  only key names appear in `data/portal_registry.json`.
- Saved Portal Workflow **“Vendor Invoice Download - 2FA”** (goto → login → `verify_code` →
  goto /documents → click Download). With the TOTP seed on the portal, `verify_code` is fully
  **unattended**: the runner generates the live code with pyotp at entry time, types it via
  CDP, and *proves* the page moved past the 2FA gate before continuing.
- Downloaded files land in `data/browser_use_downloads/<run_id>/`.

## Anchors (if you re-record or extend the workflow)

`#username`, `#password`, `#signin` (Sign in) on `/login`; `#otp-code` +
`#verify-btn` (Verify code) on `/verify`; `a.dl` Download links on `/documents`
(first row = newest invoice, tagged “Latest”).
