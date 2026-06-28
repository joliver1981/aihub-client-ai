"""
Unit tests for the Command Center SFTP/FTP transfer helper.
=========================================================
Exercises command_center/tools/sftp_transfer.py (list_dir / download / upload)
for all three protocols (sftp / ftp / ftps) WITHOUT a real server: paramiko is
injected via a fake (monkeypatching _import_paramiko) and ftplib.FTP / FTP_TLS
are replaced with fakes. Env-independent — does not require paramiko installed.

Run:
    python -m pytest tests/unit/test_sftp_transfer.py -v
"""

import os
import stat as _stat
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, r"C:/src/aihub-client-ai-dev")

from command_center.tools import sftp_transfer as st  # noqa: E402


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------

_FILE_MODE = 0o100644
_DIR_MODE = 0o040755


class _FakeAttr:
    def __init__(self, filename, size, mtime, mode):
        self.filename = filename
        self.st_size = size
        self.st_mtime = mtime
        self.st_mode = mode


class _FakeSFTPClient:
    def __init__(self, store):
        self.store = store

    @classmethod
    def from_transport(cls, transport):
        return transport._client

    def listdir_attr(self, target="."):
        self.store["listed_dir"] = target
        return self.store["entries"]

    def get(self, remote, local):
        data = self.store["files"].get(remote)
        if data is None:
            raise IOError(f"No such file: {remote}")
        with open(local, "wb") as fh:
            fh.write(data)

    def put(self, local, remote):
        with open(local, "rb") as fh:
            self.store["uploaded"][remote] = fh.read()

    def chdir(self, d):
        if d not in self.store["dirs"]:
            raise IOError(f"No such dir: {d}")
        self.store["cwd"] = d

    def close(self):
        self.store["sftp_closed"] = True


def _make_fake_paramiko(store):
    mod = types.SimpleNamespace()

    class _Transport:
        def __init__(self, addr):
            store["addr"] = addr
            self.banner_timeout = None
            self._client = _FakeSFTPClient(store)

        def connect(self, username=None, password=None):
            store["auth"] = (username, password)

        def close(self):
            store["transport_closed"] = True

    mod.Transport = _Transport
    mod.SFTPClient = _FakeSFTPClient
    return mod


class _FakeFTP:
    """Stand-in for ftplib.FTP. Subclassed for FTP_TLS."""
    secure = False

    def __init__(self, timeout=None):
        self.timeout = timeout
        self.store = _FakeFTP.shared
        self.prot_called = False

    def connect(self, host, port, timeout=None):
        self.store["addr"] = (host, port)

    def login(self, user, passwd):
        self.store["auth"] = (user, passwd)

    def prot_p(self):
        self.prot_called = True
        self.store["prot_p"] = True

    def cwd(self, d):
        self.store["cwd"] = d

    def mlsd(self):
        if self.store.get("mlsd_unsupported"):
            import ftplib
            raise ftplib.error_perm("500 MLSD not understood")
        return iter(self.store["mlsd"])

    def nlst(self):
        return list(self.store["nlst"])

    def size(self, name):
        return self.store["sizes"].get(name, 0)

    def retrbinary(self, cmd, callback):
        name = cmd.split(" ", 1)[1]
        data = self.store["files"].get(name)
        if data is None:
            import ftplib
            raise ftplib.error_perm(f"550 {name}: No such file")
        callback(data)

    def storbinary(self, cmd, fh):
        name = cmd.split(" ", 1)[1]
        self.store["uploaded"][name] = fh.read()

    def quit(self):
        self.store["quit"] = True

    def close(self):
        self.store["closed"] = True


class _FakeFTPTLS(_FakeFTP):
    secure = True


def _install_fake_ftp(monkeypatch, store):
    _FakeFTP.shared = store
    monkeypatch.setattr(st.ftplib, "FTP", _FakeFTP)
    monkeypatch.setattr(st.ftplib, "FTP_TLS", _FakeFTPTLS)


def _install_fake_paramiko(monkeypatch, store):
    monkeypatch.setattr(st, "_import_paramiko", lambda: _make_fake_paramiko(store))


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------

def test_normalize_protocol():
    assert st.normalize_protocol(None) == "sftp"
    assert st.normalize_protocol("SFTP") == "sftp"
    assert st.normalize_protocol("ssh") == "sftp"
    assert st.normalize_protocol("ftp") == "ftp"
    assert st.normalize_protocol("FTPS") == "ftps"
    assert st.normalize_protocol("ftp-tls") == "ftps"


def test_resolve_port_defaults():
    assert st._resolve_port("sftp", None) == 22
    assert st._resolve_port("ftp", None) == 21
    assert st._resolve_port("ftps", 0) == 21
    assert st._resolve_port("sftp", 2222) == 2222


def test_build_report_has_columns_and_rows():
    entries = [{"name": "a.csv", "size": 10, "is_dir": False, "modified": "2026-01-01 00:00", "age_minutes": "5"}]
    rep = st._build_report(entries, "/out")
    assert "/out" in rep
    assert "a.csv" in rep and "file" in rep
    assert "Size (bytes)" in rep


def test_validate_missing_host():
    r = st.list_dir("", "u", "p")
    assert r["ok"] is False and "host" in r["error"].lower()


def test_validate_bad_protocol():
    r = st.list_dir("h", "u", "p", protocol="carrier-pigeon")
    assert r["ok"] is False and "protocol" in r["error"].lower()


# --------------------------------------------------------------------------
# SFTP (fake paramiko)
# --------------------------------------------------------------------------

def test_sftp_list(monkeypatch):
    store = {"entries": [
        _FakeAttr("report.csv", 1234, 1700000000, _FILE_MODE),
        _FakeAttr("archive", 0, 1700000000, _DIR_MODE),
    ]}
    _install_fake_paramiko(monkeypatch, store)
    r = st.list_dir("sftp.example.com", "user", "pw", "/out", protocol="sftp")
    assert r["ok"] is True
    names = [e["name"] for e in r["entries"]]
    assert "report.csv" in names and "archive" in names
    arch = next(e for e in r["entries"] if e["name"] == "archive")
    assert arch["is_dir"] is True
    assert store["auth"] == ("user", "pw")
    assert store["addr"] == ("sftp.example.com", 22)
    assert "report.csv" in r["report"]


def test_sftp_download(monkeypatch, tmp_path):
    store = {"files": {"/out/data.csv": b"col1,col2\n1,2\n"}}
    _install_fake_paramiko(monkeypatch, store)
    r = st.download("h", "u", "p", "/out/data.csv", str(tmp_path), protocol="sftp")
    assert r["ok"] is True
    assert r["name"] == "data.csv"
    assert Path(r["local_path"]).read_bytes() == b"col1,col2\n1,2\n"


def test_sftp_download_missing_file(monkeypatch, tmp_path):
    store = {"files": {}}
    _install_fake_paramiko(monkeypatch, store)
    r = st.download("h", "u", "p", "/out/nope.csv", str(tmp_path), protocol="sftp")
    assert r["ok"] is False and "download" in r["error"].lower()


def test_sftp_upload(monkeypatch, tmp_path):
    src = tmp_path / "send.txt"
    src.write_bytes(b"payload")
    store = {"uploaded": {}, "dirs": {"/incoming"}}
    _install_fake_paramiko(monkeypatch, store)
    r = st.upload("h", "u", "p", str(src), "/incoming", protocol="sftp")
    assert r["ok"] is True
    assert r["remote_path"] == "/incoming/send.txt"
    assert store["uploaded"]["send.txt"] == b"payload"


def test_sftp_upload_bad_remote_dir(monkeypatch, tmp_path):
    src = tmp_path / "send.txt"
    src.write_bytes(b"x")
    store = {"uploaded": {}, "dirs": set()}  # /incoming not present -> chdir raises
    _install_fake_paramiko(monkeypatch, store)
    r = st.upload("h", "u", "p", str(src), "/incoming", protocol="sftp")
    assert r["ok"] is False and "does not exist" in r["error"].lower()


def test_paramiko_unavailable_message(monkeypatch, tmp_path):
    # A real local file so upload() gets past input validation and actually
    # reaches the (missing) paramiko import.
    src = tmp_path / "x"
    src.write_bytes(b"x")

    def _boom():
        raise st._ParamikoUnavailable(
            "SFTP support requires the 'paramiko' package, which is not installed ...")
    monkeypatch.setattr(st, "_import_paramiko", _boom)
    for call in (
        lambda: st.list_dir("h", "u", "p", protocol="sftp"),
        lambda: st.download("h", "u", "p", "/f", str(tmp_path), protocol="sftp"),
        lambda: st.upload("h", "u", "p", str(src), protocol="sftp"),
    ):
        r = call()
        assert r["ok"] is False
        assert "paramiko" in r["error"].lower()


# --------------------------------------------------------------------------
# FTP / FTPS (fake ftplib)
# --------------------------------------------------------------------------

def test_ftp_list_mlsd(monkeypatch):
    store = {"mlsd": [
        ("data.csv", {"type": "file", "size": "99", "modify": "20260101120000"}),
        ("sub", {"type": "dir", "size": "0"}),
        (".", {"type": "cdir"}),
    ]}
    _install_fake_ftp(monkeypatch, store)
    r = st.list_dir("ftp.example.com", "u", "p", "/pub", protocol="ftp")
    assert r["ok"] is True
    names = [e["name"] for e in r["entries"]]
    assert "data.csv" in names and "sub" in names
    assert "." not in names  # cdir filtered
    dc = next(e for e in r["entries"] if e["name"] == "data.csv")
    assert dc["size"] == 99
    assert dc["modified"].startswith("2026-01-01")


def test_ftp_list_nlst_fallback(monkeypatch):
    store = {"mlsd_unsupported": True, "nlst": ["a.txt", "b.txt"], "sizes": {"a.txt": 5, "b.txt": 7}}
    _install_fake_ftp(monkeypatch, store)
    r = st.list_dir("h", "u", "p", ".", protocol="ftp")
    assert r["ok"] is True
    assert {e["name"] for e in r["entries"]} == {"a.txt", "b.txt"}


def test_ftp_download(monkeypatch, tmp_path):
    store = {"files": {"data.bin": b"\x00\x01\x02"}}
    _install_fake_ftp(monkeypatch, store)
    r = st.download("h", "u", "p", "data.bin", str(tmp_path), protocol="ftp")
    assert r["ok"] is True
    assert Path(r["local_path"]).read_bytes() == b"\x00\x01\x02"


def test_ftp_upload(monkeypatch, tmp_path):
    src = tmp_path / "up.txt"
    src.write_bytes(b"hello ftp")
    store = {"uploaded": {}}
    _install_fake_ftp(monkeypatch, store)
    r = st.upload("h", "u", "p", str(src), "/dropbox", protocol="ftp")
    assert r["ok"] is True
    assert store["cwd"] == "/dropbox"
    assert store["uploaded"]["up.txt"] == b"hello ftp"


def test_ftps_switches_to_tls(monkeypatch):
    store = {"mlsd": [("f.csv", {"type": "file", "size": "1"})]}
    _install_fake_ftp(monkeypatch, store)
    r = st.list_dir("h", "u", "p", ".", protocol="ftps")
    assert r["ok"] is True
    assert store.get("prot_p") is True  # data channel encryption was requested


def test_download_requires_remote_path(tmp_path):
    r = st.download("h", "u", "p", "", str(tmp_path), protocol="ftp")
    assert r["ok"] is False and "remote file path" in r["error"].lower()


def test_upload_missing_local_file():
    r = st.upload("h", "u", "p", r"C:/nope/missing_file_12345.txt", protocol="ftp")
    assert r["ok"] is False and "not found" in r["error"].lower()
