"""
SFTP server (SSH) backed by asyncssh, serving SERVER_ROOT (chrooted) with
password auth for the configured test user.

Run standalone (blocks):
    python sftp_server.py
Or import start_in_thread()/run_blocking() from run_all.py / selftest.py.
"""
import asyncio
import threading

import asyncssh

from config import HOST, SFTP_PORT, USER, PASSWORD, SERVER_ROOT, HOST_KEY


class _SSHServer(asyncssh.SSHServer):
    def connection_made(self, conn):
        self._conn = conn

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return username == USER and password == PASSWORD


def _sftp_factory(chan):
    # chroot confines every client to SERVER_ROOT; '/' maps to SERVER_ROOT.
    return asyncssh.SFTPServer(chan, chroot=str(SERVER_ROOT))


async def _create():
    return await asyncssh.create_server(
        _SSHServer,
        HOST,
        SFTP_PORT,
        server_host_keys=[str(HOST_KEY)],
        sftp_factory=_sftp_factory,
        reuse_address=True,  # avoid a lingering TIME_WAIT socket blocking a quick restart
    )


def run_blocking():
    import config

    config.warn_if_nonlocal()

    async def main():
        await _create()
        print(f"[sftp]  listening on {HOST}:{SFTP_PORT}  (user '{USER}')", flush=True)
        await asyncio.Event().wait()

    asyncio.run(main())


def start_in_thread():
    """Start the SFTP server in a daemon thread with its own event loop.

    Returns the thread. Raises if the server failed to bind within the timeout.
    """
    ready = threading.Event()
    holder = {}

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def boot():
            try:
                holder["srv"] = await _create()
            except Exception as exc:  # surface bind/auth errors to the caller
                holder["err"] = exc
            finally:
                ready.set()
            await asyncio.Event().wait()

        loop.run_until_complete(boot())

    t = threading.Thread(target=_run, daemon=True, name="sftp-server")
    t.start()
    if not ready.wait(20):
        raise TimeoutError("SFTP server did not start within 20s")
    if "err" in holder:
        raise holder["err"]
    return t


if __name__ == "__main__":
    import make_fixtures

    make_fixtures.ensure()
    run_blocking()
