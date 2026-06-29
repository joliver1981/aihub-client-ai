"""
Central config for the local SFTP / FTP / FTPS test server.
========================================================
All values are overridable via environment variables so a tester can shift
ports/creds without editing code. Defaults bind to localhost only.

These credentials are NOT secrets: they grant access to nothing but an
ephemeral, localhost-only directory created under ./runtime/. They exist so the
AI Hub Command Center SFTP/FTP tools (and any client) have a real server to talk
to. Do not reuse them for anything real.
"""
import ipaddress
import os
import socket
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME = HERE / "runtime"
SERVER_ROOT = RUNTIME / "server_root"      # the directory exposed to clients (chroot)
HOST_KEY = RUNTIME / "ssh_host_key"        # generated SSH host key (SFTP)
TLS_CERT = RUNTIME / "ftps_cert.pem"       # generated self-signed cert+key (FTPS)

HOST = os.environ.get("SFTP_TEST_HOST", "127.0.0.1")
SFTP_PORT = int(os.environ.get("SFTP_TEST_SFTP_PORT", "2222"))
FTP_PORT = int(os.environ.get("SFTP_TEST_FTP_PORT", "2121"))   # serves plain FTP AND explicit FTPS
PASV_LOW = int(os.environ.get("SFTP_TEST_PASV_LOW", "60000"))
PASV_HIGH = int(os.environ.get("SFTP_TEST_PASV_HIGH", "60099"))  # 100 ports — absorb TIME_WAIT churn

USER = os.environ.get("SFTP_TEST_USER", "testuser")
PASSWORD = os.environ.get("SFTP_TEST_PASS", "testpass")


def is_loopback_host() -> bool:
    """True if HOST resolves to a loopback address (127.0.0.1 / ::1 / localhost)."""
    try:
        return ipaddress.ip_address(socket.gethostbyname(HOST)).is_loopback
    except Exception:
        return HOST in ("localhost", "127.0.0.1", "::1")


def warn_if_nonlocal() -> None:
    """Loud guardrail: this fixture has throwaway creds, full FTP perms, and accepts
    cleartext FTP. Binding off-loopback exposes all of that to the network."""
    if is_loopback_host():
        return
    bang = "!" * 64
    print(bang)
    print(f"WARNING: binding to NON-LOOPBACK host {HOST!r}.")
    print("This is a throwaway TEST fixture (well-known creds, full FTP perms,")
    print("cleartext-capable FTP). It is now reachable OFF this machine.")
    print("Do not do this on an untrusted network.")
    print(bang, flush=True)


def password_display() -> str:
    """Don't echo a tester's custom password to the console/logs."""
    return PASSWORD if PASSWORD == "testpass" else "<from SFTP_TEST_PASS env>"
