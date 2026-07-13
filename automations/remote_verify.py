"""
Remote-output verification for automation runs (P1).

After a script exits 0 claiming it uploaded a file, the runner independently
checks the remote side — the same read-back philosophy as the CC silent-success
remediation: a claim is verified by observation, never by the absence of an
exception.

Manifest shape for a remote output:

    {"kind": "sftp_upload",              # or "ftp_upload" (+ "tls": true for FTPS)
     "secret": "ACME_SFTP",              # local-secrets entry with the credentials
     "remote_dir": "/outgoing",
     "name": "payroll_{period}.csv",     # filename template, inputs substituted
     "verify": {"remote_listing": true, "min_size": 1}}

Secret value formats (the local secrets store holds a single string):
    URL form:   sftp://user:pass@host:2222        (also ftp:// / ftps://)
    JSON form:  {"host": "...", "port": 2222, "username": "...", "password": "..."}

Return contract of check_remote_output: (ok, note)
    ok=True   file observed remotely (and min_size satisfied)
    ok=False  we connected and the file is NOT there / too small -> run FAILS
    ok=None   could not check (bad secret, connect/auth error)    -> UNVERIFIED
"""

import json
import logging
import posixpath
from typing import Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 10


def parse_transfer_secret(value: str) -> Optional[Dict]:
    """Parse a secret value into {host, port, username, password} or None."""
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    if value.startswith("{"):
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return None
        host = data.get("host")
        if not host:
            return None
        return {
            "host": host,
            "port": int(data["port"]) if data.get("port") else None,
            "username": data.get("username") or data.get("user"),
            "password": data.get("password") or data.get("pass"),
        }
    if "://" in value:
        parsed = urlparse(value)
        if not parsed.hostname:
            return None
        return {
            "host": parsed.hostname,
            "port": parsed.port,
            "username": unquote(parsed.username) if parsed.username else None,
            "password": unquote(parsed.password) if parsed.password else None,
        }
    return None


def _check_sftp(creds: Dict, remote_path: str, min_size: int) -> Tuple[Optional[bool], str]:
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            creds["host"], port=creds.get("port") or 22,
            username=creds.get("username"), password=creds.get("password"),
            timeout=CONNECT_TIMEOUT_SECONDS,
            allow_agent=False, look_for_keys=False,
        )
        sftp = client.open_sftp()
        try:
            attrs = sftp.stat(remote_path)
        except FileNotFoundError:
            return False, f"{remote_path} not found on remote"
        size = attrs.st_size or 0
        if size < min_size:
            return False, f"{remote_path} exists but is {size} bytes (< min_size {min_size})"
        return True, f"{remote_path} verified on remote ({size} bytes)"
    except Exception as e:
        return None, f"could not verify over SFTP: {e}"
    finally:
        try:
            client.close()
        except Exception:
            pass


def _check_ftp(creds: Dict, remote_path: str, min_size: int, tls: bool) -> Tuple[Optional[bool], str]:
    import ftplib
    ftp = ftplib.FTP_TLS() if tls else ftplib.FTP()
    try:
        ftp.connect(creds["host"], creds.get("port") or 21, timeout=CONNECT_TIMEOUT_SECONDS)
        ftp.login(creds.get("username") or "", creds.get("password") or "")
        if tls:
            ftp.prot_p()
        ftp.voidcmd("TYPE I")  # SIZE requires binary mode on most servers
        try:
            size = ftp.size(remote_path)
        except ftplib.error_perm:
            size = None
        if size is None:
            # SIZE unsupported / refused: fall back to a directory listing
            dirname = posixpath.dirname(remote_path) or "/"
            basename = posixpath.basename(remote_path)
            try:
                names = [posixpath.basename(n) for n in ftp.nlst(dirname)]
            except ftplib.error_perm as e:
                return None, f"could not verify over FTP (listing refused): {e}"
            if basename not in names:
                return False, f"{remote_path} not found on remote"
            if min_size > 0:
                return None, f"{remote_path} listed on remote but size unavailable (min_size not checked)"
            return True, f"{remote_path} listed on remote"
        if size < min_size:
            return False, f"{remote_path} exists but is {size} bytes (< min_size {min_size})"
        return True, f"{remote_path} verified on remote ({size} bytes)"
    except Exception as e:
        return None, f"could not verify over {'FTPS' if tls else 'FTP'}: {e}"
    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass


def check_remote_output(kind: str, secret_value: Optional[str], remote_dir: str,
                        filename: str, verify: Dict) -> Tuple[Optional[bool], str]:
    """Independently observe a claimed remote upload. See module docstring for
    the (ok, note) contract."""
    if not filename:
        return None, "no 'name' declared for the remote output — nothing to check"
    creds = parse_transfer_secret(secret_value or "")
    if not creds:
        return None, "secret missing or not in URL/JSON transfer format — cannot verify"
    remote_path = posixpath.join(remote_dir or "/", filename)
    min_size = int(verify.get("min_size", 1))
    if kind == "sftp_upload":
        return _check_sftp(creds, remote_path, min_size)
    if kind == "ftp_upload":
        return _check_ftp(creds, remote_path, min_size, tls=bool(verify.get("tls")))
    return None, f"no verifier for kind '{kind}'"
