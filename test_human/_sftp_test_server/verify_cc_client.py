"""
Live verification of the REAL Command Center SFTP transfer code against this
local test server. Unlike selftest.py (which uses paramiko/ftplib directly), this
imports the actual shipping helper command_center/tools/sftp_transfer.py and
drives its list_dir / download / upload over sftp + ftp + ftps — i.e. it proves
the code the CC `sftp_list_files`/`sftp_download`/`sftp_upload` tools call works
against a real server.

Run in the testftp env (needs paramiko, which the helper lazy-imports for SFTP):
    python verify_cc_client.py

Exit 0 only if every (protocol x operation) the helper supports succeeds.
FTPS note: the helper builds ftplib.FTP_TLS() with the DEFAULT (verifying) SSL
context, which is correct for production but rejects this fixture's self-signed
cert. So FTPS is tested twice: once as-is (expected to fail cert verification)
and once with SSL_CERT_FILE pointed at the fixture cert (expected to pass) — to
show the real path works when the cert is trusted.
"""
import importlib.util
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

import make_fixtures
import sftp_server
import ftp_server
from config import HOST, SFTP_PORT, FTP_PORT, USER, PASSWORD, SERVER_ROOT, TLS_CERT

# Import the real CC helper by file path (avoids importing the whole command_center package).
_REPO = Path(__file__).resolve().parents[2]
_CC_HELPER = _REPO / "command_center" / "tools" / "sftp_transfer.py"
_spec = importlib.util.spec_from_file_location("cc_sftp_transfer", _CC_HELPER)
st = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(st)


def _wait_port(host, port, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _exercise(proto, port):
    """Run list_dir + download + upload via the REAL helper. Returns (ok, [lines])."""
    lines, ok = [], True

    # list_dir on incoming/
    r = st.list_dir(HOST, USER, PASSWORD, remote_dir="incoming", port=port, protocol=proto)
    if r.get("ok") and any(e["name"] == "report.csv" for e in r.get("entries", [])):
        lines.append(f"list_dir(incoming) -> {len(r['entries'])} entries incl. report.csv")
    else:
        ok = False
        lines.append(f"list_dir FAILED: {r.get('error') or r}")

    # download incoming/report.csv into a temp dir
    dest = tempfile.mkdtemp()
    r = st.download(HOST, USER, PASSWORD, "incoming/report.csv", dest, port=port, protocol=proto)
    if r.get("ok") and os.path.isfile(r.get("local_path", "")):
        data = Path(r["local_path"]).read_bytes()
        if b"region,units,revenue" in data:
            lines.append(f"download(report.csv) -> {len(data)}B, content ok")
        else:
            ok = False
            lines.append("download FAILED: content mismatch")
    else:
        ok = False
        lines.append(f"download FAILED: {r.get('error') or r}")

    # upload a probe into outgoing/
    fd, tmp = tempfile.mkstemp()
    os.write(fd, b"cc-client-probe-" + proto.encode())
    os.close(fd)
    probe_name = f"_cc_probe_{proto}.txt"
    r = st.upload(HOST, USER, PASSWORD, tmp, remote_dir="outgoing",
                  remote_name=probe_name, port=port, protocol=proto)
    if r.get("ok") and (SERVER_ROOT / "outgoing" / probe_name).is_file():
        lines.append(f"upload({probe_name}) -> landed in outgoing/")
        try:
            (SERVER_ROOT / "outgoing" / probe_name).unlink()
        except OSError:
            pass
    else:
        ok = False
        lines.append(f"upload FAILED: {r.get('error') or r}")

    try:
        os.unlink(tmp)
    except OSError:
        pass
    return ok, lines


def main():
    make_fixtures.ensure()
    sftp_server.start_in_thread()
    ftp_server.start_in_thread()
    if not (_wait_port(HOST, SFTP_PORT) and _wait_port(HOST, FTP_PORT)):
        print("FAIL: servers did not open their ports")
        return 1

    print("=" * 68)
    print("LIVE verification of the REAL CC helper (command_center/tools/sftp_transfer.py)")
    print(f"  module: {_CC_HELPER}")
    print("=" * 68)

    overall = True

    # SFTP + FTP — straightforward
    for proto, port in (("sftp", SFTP_PORT), ("ftp", FTP_PORT)):
        ok, lines = _exercise(proto, port)
        overall = overall and ok
        print(f"\n[{proto.upper()}]  {'PASS' if ok else 'FAIL'}")
        for ln in lines:
            print(f"    {ln}")

    # FTPS — default verifying context rejects the self-signed fixture cert (expected),
    # then trust the fixture cert via SSL_CERT_FILE and confirm the real path works.
    print("\n[FTPS] (default verifying context — expected to reject self-signed fixture cert)")
    ok_default, lines = _exercise("ftps", FTP_PORT)
    for ln in lines:
        print(f"    {ln}")
    if ok_default:
        print("    note: succeeded as-is (the runtime trusts this cert)")
    else:
        print("    -> expected: helper uses ftplib.FTP_TLS() default context (correct for prod)")

    print("\n[FTPS] (SSL_CERT_FILE = fixture cert — real path works when the cert is trusted)")
    os.environ["SSL_CERT_FILE"] = str(TLS_CERT)
    ok_trusted, lines = _exercise("ftps", FTP_PORT)
    for ln in lines:
        print(f"    {ln}")
    os.environ.pop("SSL_CERT_FILE", None)
    # FTPS counts as verified if it works when the cert is trusted.
    overall = overall and ok_trusted
    print(f"\n[FTPS]  {'PASS (with trusted cert)' if ok_trusted else 'FAIL'}")

    print("\n" + "=" * 68)
    print("REAL CC HELPER: ALL PASS" if overall else "REAL CC HELPER: SOME CHECKS FAILED")
    print("=" * 68)
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
