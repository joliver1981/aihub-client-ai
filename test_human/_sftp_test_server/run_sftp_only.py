"""Start only the SFTP test server (FTP/FTPS needs pyOpenSSL which is broken)."""
import time
import make_fixtures
import sftp_server
from config import HOST, SFTP_PORT, FTP_PORT, USER, SERVER_ROOT

make_fixtures.ensure()
sftp_server.start_in_thread()

bar = "=" * 64
print(bar)
print("AI Hub local SFTP test server is UP (FTP/FTPS skipped)")
print(bar)
print(f"  host          : {HOST}")
print(f"  SFTP          : port {SFTP_PORT}   (sftp)")
print(f"  user / pass   : {USER} / testpass")
print(f"  served root   : {SERVER_ROOT}")
print(bar)
print("Press Ctrl+C to stop.", flush=True)

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass
