"""
sftp_transfer.py - CC tool core: connect to an SFTP / FTP / FTPS server with
caller-supplied credentials and list, download, or upload files.

Lightweight by design (the chosen scope): credentials are passed as arguments
on each call - there is NO stored-connection / saved-credential plumbing here.
The CC agent supplies host/username/password (typically pasted by the user in
chat) and this module opens a one-shot connection, does the transfer, and closes.

Protocols:
  * "sftp"  - SFTP over SSH via paramiko. paramiko is LAZY-imported because the
              Command Center service env (aihubbuilder) does not ship it by
              default; if it is missing, SFTP returns a clear, actionable error
              and FTP/FTPS still work (ftplib is stdlib).
  * "ftp"   - plain FTP via stdlib ftplib.
  * "ftps"  - FTP over TLS via stdlib ftplib.FTP_TLS.

Every function is SYNCHRONOUS and returns a plain dict (never raises for a
remote/credential failure - it returns {"ok": False, "error": ...}). The async
CC tools call these via asyncio.to_thread. Passwords are never logged or echoed
back in error strings.
"""
import datetime
import ftplib
import logging
import os
import stat as _stat
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_PORTS = {"sftp": 22, "ftp": 21, "ftps": 21}
_VALID_PROTOCOLS = tuple(_DEFAULT_PORTS.keys())
# Connection / transfer socket timeout (seconds). Kept modest so a wrong host
# fails fast instead of hanging the chat turn.
_TIMEOUT = int(os.getenv("SFTP_TIMEOUT", "30") or "30")


def normalize_protocol(protocol: Optional[str]) -> str:
    """Coerce a user/LLM-supplied protocol string to one of sftp/ftp/ftps."""
    p = (protocol or "sftp").strip().lower()
    if p in ("ssh", "sftp"):
        return "sftp"
    if p in ("ftps", "ftp-tls", "ftp_tls", "ftpes"):
        return "ftps"
    if p == "ftp":
        return "ftp"
    return p  # caller validates; unknown values surface a clear error


def _resolve_port(protocol: str, port: Optional[int]) -> int:
    try:
        if port:
            return int(port)
    except (TypeError, ValueError):
        pass
    return _DEFAULT_PORTS.get(protocol, 22)


def _fmt_mtime(epoch: Optional[float]) -> str:
    if not epoch:
        return "-"
    try:
        return datetime.datetime.fromtimestamp(float(epoch)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "-"


def _age_minutes(epoch: Optional[float]) -> str:
    if not epoch:
        return "-"
    try:
        delta = datetime.datetime.now() - datetime.datetime.fromtimestamp(float(epoch))
        return str(int(delta.total_seconds() // 60))
    except (TypeError, ValueError, OSError):
        return "-"


def _build_report(entries: List[Dict[str, Any]], remote_dir: str) -> str:
    """Markdown table of a directory listing (name / size / modified / age)."""
    header = (f"Listing of `{remote_dir}` ({len(entries)} item(s)):\n\n"
              "| # | Name | Type | Size (bytes) | Modified | Age (min) |\n"
              "| - | ---- | ---- | ------------ | -------- | --------- |\n")
    rows = []
    for i, e in enumerate(entries, 1):
        kind = "dir" if e.get("is_dir") else "file"
        rows.append(f"| {i} | {e.get('name', '')} | {kind} | {e.get('size', 0)} | "
                    f"{e.get('modified', '-')} | {e.get('age_minutes', '-')} |")
    return header + "\n".join(rows) + ("\n" if rows else "_(empty directory)_\n")


# ---------------------------------------------------------------------------
# SFTP (paramiko) - lazy import so the module loads even when paramiko is absent
# ---------------------------------------------------------------------------

class _ParamikoUnavailable(RuntimeError):
    pass


def _import_paramiko():
    try:
        import paramiko  # noqa: WPS433 (intentional lazy import)
        return paramiko
    except Exception as e:  # ImportError or a broken install
        raise _ParamikoUnavailable(
            "SFTP support requires the 'paramiko' package, which is not installed "
            "in the Command Center service environment. Ask an administrator to run "
            "`pip install paramiko` in that env, or use protocol='ftp'/'ftps' instead."
        ) from e


def _sftp_connect(paramiko, host: str, port: int, username: str, password: str):
    transport = paramiko.Transport((host, port))
    transport.banner_timeout = _TIMEOUT
    transport.connect(username=username, password=password)
    sftp = paramiko.SFTPClient.from_transport(transport)
    return transport, sftp


def _sftp_list(sftp, remote_dir: str) -> List[Dict[str, Any]]:
    target = remote_dir or "."
    items = sftp.listdir_attr(target)
    entries: List[Dict[str, Any]] = []
    for f in items:
        is_dir = bool(f.st_mode and _stat.S_ISDIR(f.st_mode))
        entries.append({
            "name": f.filename,
            "size": int(f.st_size or 0),
            "is_dir": is_dir,
            "modified": _fmt_mtime(f.st_mtime),
            "age_minutes": _age_minutes(f.st_mtime),
        })
    return entries


# ---------------------------------------------------------------------------
# FTP / FTPS (stdlib ftplib)
# ---------------------------------------------------------------------------

def _ftp_connect(host: str, port: int, username: str, password: str, secure: bool):
    ftp = ftplib.FTP_TLS(timeout=_TIMEOUT) if secure else ftplib.FTP(timeout=_TIMEOUT)
    ftp.connect(host, port, timeout=_TIMEOUT)
    ftp.login(username or "anonymous", password or "")
    if secure:
        # Switch the data channel to TLS too (control channel already protected).
        ftp.prot_p()
    return ftp


def _ftp_list(ftp, remote_dir: str) -> List[Dict[str, Any]]:
    if remote_dir and remote_dir not in (".", "./"):
        ftp.cwd(remote_dir)
    entries: List[Dict[str, Any]] = []
    try:
        for name, facts in ftp.mlsd():
            if name in (".", ".."):
                continue
            is_dir = facts.get("type") in ("dir", "cdir", "pdir")
            modify = facts.get("modify")  # YYYYMMDDHHMMSS (UTC)
            modified = "-"
            if modify and len(modify) >= 12:
                modified = (f"{modify[0:4]}-{modify[4:6]}-{modify[6:8]} "
                            f"{modify[8:10]}:{modify[10:12]}")
            entries.append({
                "name": name,
                "size": int(facts.get("size", 0) or 0),
                "is_dir": is_dir,
                "modified": modified,
                "age_minutes": "-",
            })
    except (ftplib.error_perm, ftplib.error_proto):
        # Server doesn't support MLSD - fall back to NLST + SIZE (best effort).
        for raw in ftp.nlst():
            base = raw.rsplit("/", 1)[-1]
            if base in (".", ".."):
                continue
            size = 0
            try:
                size = ftp.size(raw) or 0
            except Exception:
                size = 0
            entries.append({
                "name": base, "size": size, "is_dir": False,
                "modified": "-", "age_minutes": "-",
            })
    return entries


# ---------------------------------------------------------------------------
# Public API: list_dir / download / upload
# ---------------------------------------------------------------------------

def _validate(host: str, protocol: str) -> Optional[str]:
    if not host or not str(host).strip():
        return "A server host/address is required."
    if protocol not in _VALID_PROTOCOLS:
        return (f"Unsupported protocol '{protocol}'. Use one of: "
                f"{', '.join(_VALID_PROTOCOLS)}.")
    return None


def list_dir(host: str, username: str, password: str, remote_dir: str = ".",
             port: Optional[int] = None, protocol: str = "sftp") -> Dict[str, Any]:
    """List a remote directory. Returns {ok, report(markdown), entries, error}."""
    protocol = normalize_protocol(protocol)
    err = _validate(host, protocol)
    if err:
        return {"ok": False, "error": err}
    p = _resolve_port(protocol, port)
    logger.info(f"[sftp_transfer] list {protocol}://{host}:{p} dir={remote_dir!r}")
    try:
        if protocol == "sftp":
            paramiko = _import_paramiko()
            transport, sftp = _sftp_connect(paramiko, host, p, username, password)
            try:
                entries = _sftp_list(sftp, remote_dir)
            finally:
                sftp.close()
                transport.close()
        else:
            ftp = _ftp_connect(host, p, username, password, secure=(protocol == "ftps"))
            try:
                entries = _ftp_list(ftp, remote_dir)
            finally:
                try:
                    ftp.quit()
                except Exception:
                    ftp.close()
        return {"ok": True, "entries": entries,
                "report": _build_report(entries, remote_dir or ".")}
    except _ParamikoUnavailable as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"Could not list the directory: {e}"}


def download(host: str, username: str, password: str, remote_path: str,
             dest_dir: str, port: Optional[int] = None,
             protocol: str = "sftp") -> Dict[str, Any]:
    """Download one remote file into dest_dir. Returns {ok, local_path, name, error}."""
    protocol = normalize_protocol(protocol)
    err = _validate(host, protocol)
    if err:
        return {"ok": False, "error": err}
    if not remote_path or not str(remote_path).strip():
        return {"ok": False, "error": "A remote file path to download is required."}
    p = _resolve_port(protocol, port)
    name = os.path.basename(remote_path.rstrip("/")) or "download"
    local_path = os.path.join(dest_dir, name)
    logger.info(f"[sftp_transfer] download {protocol}://{host}:{p} path={remote_path!r}")
    try:
        os.makedirs(dest_dir, exist_ok=True)
        if protocol == "sftp":
            paramiko = _import_paramiko()
            transport, sftp = _sftp_connect(paramiko, host, p, username, password)
            try:
                sftp.get(remote_path, local_path)
            finally:
                sftp.close()
                transport.close()
        else:
            ftp = _ftp_connect(host, p, username, password, secure=(protocol == "ftps"))
            try:
                with open(local_path, "wb") as fh:
                    ftp.retrbinary(f"RETR {remote_path}", fh.write)
            finally:
                try:
                    ftp.quit()
                except Exception:
                    ftp.close()
        if not os.path.isfile(local_path) or os.path.getsize(local_path) == 0:
            # Some servers create a 0-byte file on a failed RETR; treat as failure
            # only if truly empty AND we got no exception (rare). Keep the file if
            # it has bytes.
            if not os.path.isfile(local_path):
                return {"ok": False, "error": "The download produced no file."}
        return {"ok": True, "local_path": local_path, "name": name}
    except _ParamikoUnavailable as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        # Clean up a partial/empty file so we never register a bogus artifact.
        try:
            if os.path.isfile(local_path) and os.path.getsize(local_path) == 0:
                os.remove(local_path)
        except Exception:
            pass
        return {"ok": False, "error": f"Could not download '{remote_path}': {e}"}


def upload(host: str, username: str, password: str, local_path: str,
           remote_dir: str = ".", remote_name: Optional[str] = None,
           port: Optional[int] = None, protocol: str = "sftp") -> Dict[str, Any]:
    """Upload a local file to remote_dir. Returns {ok, remote_path, error}."""
    protocol = normalize_protocol(protocol)
    err = _validate(host, protocol)
    if err:
        return {"ok": False, "error": err}
    if not local_path or not os.path.isfile(local_path):
        return {"ok": False, "error": "The local file to upload was not found."}
    p = _resolve_port(protocol, port)
    name = remote_name or os.path.basename(local_path)
    rdir = (remote_dir or ".").rstrip("/")
    remote_path = f"{rdir}/{name}" if rdir not in ("", ".") else name
    logger.info(f"[sftp_transfer] upload {protocol}://{host}:{p} -> {remote_path!r}")
    try:
        if protocol == "sftp":
            paramiko = _import_paramiko()
            transport, sftp = _sftp_connect(paramiko, host, p, username, password)
            try:
                if rdir and rdir not in (".", ""):
                    try:
                        sftp.chdir(rdir)
                    except IOError:
                        return {"ok": False,
                                "error": f"Remote directory '{rdir}' does not exist."}
                    sftp.put(local_path, name)
                else:
                    sftp.put(local_path, name)
            finally:
                sftp.close()
                transport.close()
        else:
            ftp = _ftp_connect(host, p, username, password, secure=(protocol == "ftps"))
            try:
                if rdir and rdir not in (".", ""):
                    ftp.cwd(rdir)
                with open(local_path, "rb") as fh:
                    ftp.storbinary(f"STOR {name}", fh)
            finally:
                try:
                    ftp.quit()
                except Exception:
                    ftp.close()
        return {"ok": True, "remote_path": remote_path, "name": name}
    except _ParamikoUnavailable as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"Could not upload '{name}': {e}"}
