# Local SFTP / FTP / FTPS test server

A self-contained, localhost-only file-transfer server so the **AI Hub Command Center
SFTP/FTP tools** (and any client) have a real endpoint to list, download, and upload against.
One launcher serves all three protocols:

| Protocol | Port | Client lib it satisfies |
|----------|-----:|-------------------------|
| **SFTP** (SSH) | `2222` | `paramiko` (what the CC SFTP tool uses) |
| **FTP** (plain) | `2121` | `ftplib.FTP` |
| **FTPS** (explicit, AUTH TLS) | `2121` | `ftplib.FTP_TLS` |

> FTP and FTPS share port `2121`: the listener accepts plain FTP **and** explicit FTPS
> (`tls_control_required=False`), so a client picks the mode.

## Credentials & layout

- **User / pass:** `testuser` / `testpass` — a **throwaway localhost fixture**, not a secret
  (it unlocks nothing but the ephemeral dir below). Override via env vars (see `config.py`):
  `SFTP_TEST_USER`, `SFTP_TEST_PASS`, `SFTP_TEST_HOST`, `SFTP_TEST_SFTP_PORT`, `SFTP_TEST_FTP_PORT`.
- **Served root** (chrooted): `runtime/server_root/`
  - `incoming/` — `report.csv`, `notes.txt`, `data.bin` (known fixtures for download tests)
  - `outgoing/` — writable (upload target)
- Generated, git-ignored runtime material: SSH host key (`runtime/ssh_host_key`), self-signed
  TLS cert (`runtime/ftps_cert.pem`).

## Environment

Use the dedicated **`testftp`** conda env (Python 3.12):

```powershell
$py = "C:\Users\james\miniconda3\envs\testftp\python.exe"
& $py -m pip install -r requirements.txt   # asyncssh, pyftpdlib, pyOpenSSL, paramiko, cryptography
```

`pyOpenSSL` is **required** for FTPS — pyftpdlib only exposes `TLS_FTPHandler` when pyOpenSSL is
installed, and `ftp_server.py` imports it at module load, so without pyOpenSSL the FTP/FTPS server
fails to start with an `ImportError` (it does not silently fall back to plain FTP).

## Run it

```powershell
$py = "C:\Users\james\miniconda3\envs\testftp\python.exe"
cd C:\src\aihub-client-ai-dev\test_human\_sftp_test_server

& $py make_fixtures.py    # create the served tree, sample files, host key, TLS cert (idempotent)
& $py run_all.py          # start SFTP + FTP + FTPS; leave running; Ctrl+C to stop
```

Leave `run_all.py` running in one terminal while you drive the CC SFTP/FTP tools (or any client)
against `127.0.0.1`.

To run a single protocol: `& $py sftp_server.py` or `& $py ftp_server.py`.

## Verify it works (self-test)

`selftest.py` starts all three servers in-process and round-trips a probe file over each protocol
using **paramiko** (SFTP) and **ftplib.FTP / FTP_TLS** (FTP/FTPS) — the same client libraries the
CC `sftp_transfer` tool is specified to use — then exits `0` only if all pass:

```powershell
& $py selftest.py
```

Last verified 2026-06-28 (testftp env):

```
SFTP  PASS  listed 2 entries; round-trip 267B ok; read report.csv 98B
FTP   PASS  listed 2 entries; round-trip 266B ok
FTPS  PASS  listed 2 entries; round-trip 267B ok
ALL PASS
```

## Files

| File | Purpose |
|------|---------|
| `config.py` | host / ports / creds / paths (all env-overridable) |
| `make_fixtures.py` | create served tree + sample files + SSH host key + TLS cert (idempotent) |
| `sftp_server.py` | asyncssh SFTP server (chrooted, password auth) |
| `ftp_server.py` | pyftpdlib FTP + explicit-FTPS server |
| `run_all.py` | start all three and block (the one a tester leaves running) |
| `selftest.py` | start + round-trip list/upload/download over all three (paramiko/ftplib); exit 0 on success |
| `verify_cc_client.py` | start + drive the **real** `command_center/tools/sftp_transfer.py` (list/download/upload × sftp/ftp/ftps) — proves the actual CC tool code works against the server |
| `CC_AGENT_TEST_PLAN.md` | **human test plan** — step-by-step prompts to drive the CC **agent/UI** SFTP tools against this server (list/download/upload, negative/honesty cases, pass criteria) |
| `requirements.txt` | the server/client stack for the `testftp` env |

## Manual connection examples

```powershell
$py = "C:\Users\james\miniconda3\envs\testftp\python.exe"

# SFTP (paramiko)
& $py -c "import paramiko; t=paramiko.Transport(('127.0.0.1',2222)); t.connect(username='testuser',password='testpass'); s=paramiko.SFTPClient.from_transport(t); print(s.listdir('/incoming')); t.close()"

# FTP (ftplib)
& $py -c "import ftplib; f=ftplib.FTP(); f.connect('127.0.0.1',2121); f.login('testuser','testpass'); print(f.nlst('incoming')); f.quit()"
```

## How the CC client must connect (two non-defaults)

A client built for real servers will not reach this fixture without two adjustments — note these
when wiring up the CC SFTP/FTP tool's test:

1. **Explicit ports.** The fixture uses non-standard ports (SFTP `2222`, FTP/FTPS `2121`). The CC
   tool's protocol defaults are the standard 22/21/21, so every call must pass an explicit port
   (or set `SFTP_TEST_SFTP_PORT=22` / `SFTP_TEST_FTP_PORT=21` if binding to privileged ports is
   allowed).
2. **FTPS connects to the self-signed cert as-is — but it isn't verified.** The CC helper builds
   `ftplib.FTP_TLS()` with no SSL context; on current Python that default context does **not**
   verify the server cert (`verify_mode=CERT_NONE`, `check_hostname=False`), so FTPS works against
   this fixture with no setup. ⚠️ It also means the CC FTPS path has **no protection against a MITM
   / forged server cert** — a real finding for the feature, not the fixture. (`selftest.py` uses an
   explicit unverified context for the same reason; `verify_cc_client.py` confirms the real helper
   connects out of the box.)

## Notes

- **Localhost only.** Binds `127.0.0.1` by default; a non-loopback `SFTP_TEST_HOST` triggers a loud
  startup warning (throwaway creds + cleartext-capable FTP should not be exposed off-box).
- The served directory is **chrooted** — SFTP clients see `server_root/` as `/`.
- `outgoing/` is **not reset** between runs (uploads accumulate); stale `_probe_*` files are swept
  on each `make_fixtures.ensure()`. For a clean baseline: `& $py make_fixtures.py --reset`.
- See task **AIHUB-0011** in the ai-colab board and
  `projects/aihub-client-ai-dev/RESOURCES.md` for how this server is used in CC testing.
