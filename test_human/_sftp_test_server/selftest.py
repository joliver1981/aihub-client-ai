"""
Self-test: start all three servers in-process, then exercise the operations the
Command Center sftp_transfer tool is specified to perform (per
tests/unit/test_sftp_transfer.py) against each protocol:

  SFTP  (paramiko)        : listdir, chdir + bad-dir validation, put + get round-trip, read a fixture
  FTP   (ftplib.FTP)      : MLSD (primary) + NLST (fallback) listing, download a fixture, upload round-trip
  FTPS  (ftplib.FTP_TLS)  : same as FTP over explicit AUTH TLS (self-signed cert -> unverified context)

Exits 0 only if all three protocols pass. Probe files are cleaned up even on failure.

    python selftest.py
"""
import io
import os
import socket
import ssl
import sys
import tempfile
import time

import make_fixtures
import sftp_server
import ftp_server
from config import HOST, SFTP_PORT, FTP_PORT, USER, PASSWORD

_FIXTURES = {"report.csv", "notes.txt", "data.bin"}


def _wait_port(host, port, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def check_sftp():
    import paramiko

    transport = paramiko.Transport((HOST, SFTP_PORT))
    transport.connect(username=USER, password=PASSWORD)
    sftp = paramiko.SFTPClient.from_transport(transport)
    probe = "/outgoing/_probe_sftp.bin"
    tmp_up = tmp_dn = None
    try:
        listing = sftp.listdir("/")
        assert "incoming" in listing and "outgoing" in listing, f"unexpected listing: {listing}"

        # remote-dir validation the client uses: chdir to a missing dir must fail.
        try:
            sftp.chdir("/no_such_dir")
            raise AssertionError("chdir to a missing dir should have failed")
        except (IOError, OSError):
            pass
        sftp.chdir("/outgoing")

        # put/get round-trip — the exact methods the client uses (not just open()).
        payload = b"probe-sftp-" + bytes(range(256))
        fd, tmp_up = tempfile.mkstemp()
        os.close(fd)
        with open(tmp_up, "wb") as f:
            f.write(payload)
        sftp.put(tmp_up, probe)
        fd, tmp_dn = tempfile.mkstemp()
        os.close(fd)
        sftp.get(probe, tmp_dn)
        with open(tmp_dn, "rb") as f:
            got = f.read()
        assert got == payload, "SFTP put/get round-trip mismatch"

        # download a known fixture
        with sftp.open("/incoming/report.csv", "rb") as f:
            report = f.read()
        assert b"region,units,revenue" in report, "report.csv content unexpected"

        return f"listdir+chdir-validate ok; put/get {len(payload)}B ok; read report.csv {len(report)}B"
    finally:
        try:
            sftp.remove(probe)
        except Exception:
            pass
        for p in (tmp_up, tmp_dn):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass
        sftp.close()
        transport.close()


def _check_ftp_like(ftp, label):
    probe = "outgoing/_probe_%s.bin" % label.lower()
    try:
        # MLSD = the client's PRIMARY listing path; assert the known fixtures appear.
        names = {name for name, _facts in ftp.mlsd("incoming")}
        assert _FIXTURES <= names, f"{label} mlsd(incoming) missing fixtures: {sorted(names)}"
        ftp.nlst("incoming")  # NLST = the client's fallback path — exercise it too

        # download a known fixture
        rep = io.BytesIO()
        ftp.retrbinary("RETR incoming/report.csv", rep.write)
        assert b"region,units,revenue" in rep.getvalue(), f"{label} report.csv content unexpected"

        # upload round-trip
        payload = ("probe-%s-" % label.lower()).encode() + bytes(range(256))
        ftp.storbinary("STOR " + probe, io.BytesIO(payload))
        back = io.BytesIO()
        ftp.retrbinary("RETR " + probe, back.write)
        assert back.getvalue() == payload, f"{label} round-trip mismatch"

        return f"mlsd+nlst incoming ok; read report.csv {rep.getvalue().__len__()}B; round-trip {len(payload)}B ok"
    finally:
        try:
            ftp.delete(probe)
        except Exception:
            pass
        try:
            ftp.quit()
        except Exception:
            ftp.close()


def check_ftp():
    import ftplib

    ftp = ftplib.FTP()
    ftp.connect(HOST, FTP_PORT, timeout=10)
    ftp.login(USER, PASSWORD)
    return _check_ftp_like(ftp, "FTP")


def check_ftps():
    import ftplib

    ctx = ssl._create_unverified_context()  # server uses a self-signed cert
    ftps = ftplib.FTP_TLS(context=ctx)
    ftps.connect(HOST, FTP_PORT, timeout=10)
    ftps.auth()        # AUTH TLS — secure the control channel
    ftps.login(USER, PASSWORD)
    ftps.prot_p()      # secure the data channel
    return _check_ftp_like(ftps, "FTPS")


def main():
    make_fixtures.ensure()
    sftp_server.start_in_thread()
    ftp_server.start_in_thread()

    if not _wait_port(HOST, SFTP_PORT):
        print(f"FAIL: SFTP port {SFTP_PORT} never opened")
        return 1
    if not _wait_port(HOST, FTP_PORT):
        print(f"FAIL: FTP port {FTP_PORT} never opened")
        return 1

    results = []
    ok = True
    for name, fn in (("SFTP", check_sftp), ("FTP", check_ftp), ("FTPS", check_ftps)):
        try:
            detail = fn()
            results.append((name, "PASS", detail))
        except Exception as exc:  # noqa: BLE001 - report any failure per protocol
            ok = False
            results.append((name, "FAIL", f"{type(exc).__name__}: {exc}"))

    print("\n" + "=" * 64)
    print("SELF-TEST RESULTS (mirrors the CC client's list/download/upload paths)")
    print("=" * 64)
    for name, verdict, detail in results:
        print(f"  {name:5} {verdict:4}  {detail}")
    print("=" * 64)
    print("ALL PASS" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
