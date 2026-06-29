"""
FTP + explicit-FTPS server backed by pyftpdlib, serving SERVER_ROOT for the
configured test user. One listener on FTP_PORT accepts BOTH plain FTP and
explicit FTPS (AUTH TLS), so ftplib.FTP and ftplib.FTP_TLS both work against it.

Run standalone (blocks):
    python ftp_server.py
Or import start_in_thread()/run_blocking() from run_all.py / selftest.py.
"""
import threading

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import TLS_FTPHandler
from pyftpdlib.servers import FTPServer

from config import HOST, FTP_PORT, USER, PASSWORD, SERVER_ROOT, TLS_CERT, PASV_LOW, PASV_HIGH


def build_server():
    authorizer = DummyAuthorizer()
    # Full perms: list/cd, retrieve, store, append, delete, rename, mkdir, rmdir, chmod, chmtime.
    # Deliberately broad so the CC tool's upload/delete/rename paths can be exercised too.
    authorizer.add_user(USER, PASSWORD, str(SERVER_ROOT), perm="elradfmwMT")

    # Subclass per build instead of mutating the imported TLS_FTPHandler CLASS: pyftpdlib
    # stores config (and caches the SSL context) on the handler class, so mutating the shared
    # class would leak/clobber state between servers in one process. A fresh subclass isolates it.
    class _Handler(TLS_FTPHandler):
        pass

    _Handler.certfile = str(TLS_CERT)
    _Handler.ssl_context = None            # force the context to build from THIS cert
    _Handler.tls_control_required = False  # allow plain FTP too (FTPS is opt-in per client)
    _Handler.tls_data_required = False
    _Handler.authorizer = authorizer
    _Handler.masquerade_address = HOST
    _Handler.passive_ports = range(PASV_LOW, PASV_HIGH + 1)

    return FTPServer((HOST, FTP_PORT), _Handler)


def run_blocking():
    import config
    config.warn_if_nonlocal()
    srv = build_server()
    print(f"[ftp]   listening on {HOST}:{FTP_PORT}  (plain FTP + explicit FTPS, user '{USER}')", flush=True)
    srv.serve_forever()


def start_in_thread():
    """Start the FTP/FTPS server in a daemon thread. Returns (server, thread)."""
    srv = build_server()
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="ftp-server")
    t.start()
    return srv, t


if __name__ == "__main__":
    import make_fixtures

    make_fixtures.ensure()
    run_blocking()
