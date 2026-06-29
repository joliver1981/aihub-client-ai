"""
Start the SFTP + FTP + FTPS test servers together and block until Ctrl+C.
Leave this running in one terminal while you drive the Command Center SFTP/FTP
tools (or any client) against it.

    python run_all.py
"""
import time

import config
import make_fixtures
import sftp_server
import ftp_server
from config import HOST, SFTP_PORT, FTP_PORT, USER, SERVER_ROOT


def main():
    make_fixtures.ensure()
    config.warn_if_nonlocal()
    sftp_server.start_in_thread()
    ftp_server.start_in_thread()

    bar = "=" * 64
    print(bar)
    print("AI Hub local SFTP / FTP / FTPS test server is UP")
    print(bar)
    print(f"  host          : {HOST}")
    print(f"  SFTP          : port {SFTP_PORT}   (sftp)")
    print(f"  FTP  (plain)  : port {FTP_PORT}   (ftp)")
    print(f"  FTPS (AUTH TLS, explicit) : port {FTP_PORT}   (ftps)")
    print(f"  user / pass   : {USER} / {config.password_display()}   (throwaway localhost fixture)")
    print(f"  served root   : {SERVER_ROOT}")
    print("  layout        : /incoming (report.csv, notes.txt, data.bin), /outgoing (writable)")
    print(bar)
    print("Press Ctrl+C to stop.", flush=True)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping test server.")


if __name__ == "__main__":
    main()
