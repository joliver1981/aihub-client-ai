"""
Create the test server's on-disk fixtures: the served directory tree, a few
known sample files, an SSH host key (SFTP), and a self-signed TLS cert (FTPS).

Idempotent:
- the directory tree + sample files are (re)written every run (deterministic);
- the host key and TLS cert are generated only if missing (so the SFTP host key
  stays stable across restarts).

Run directly to (re)create fixtures and print a summary + checksums:
    python make_fixtures.py
"""
import datetime
import hashlib
import ipaddress
from pathlib import Path

from config import RUNTIME, SERVER_ROOT, HOST_KEY, TLS_CERT, HOST

# Deterministic sample files placed in <root>/incoming for download tests.
_SAMPLES = {
    "report.csv": (
        "region,units,revenue\n"
        "West,1200,75026.13\n"
        "East,980,61240.50\n"
        "North,1100,68110.00\n"
        "South,1300,79980.25\n"
    ).encode("utf-8"),
    "notes.txt": b"AI Hub SFTP/FTP/FTPS test server. This file is a fixture for download tests.\n",
    "data.bin": bytes(range(256)) * 4,  # 1024 bytes, fixed content for checksum tests
}


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _make_tree() -> None:
    (SERVER_ROOT / "incoming").mkdir(parents=True, exist_ok=True)
    (SERVER_ROOT / "outgoing").mkdir(parents=True, exist_ok=True)
    for name, content in _SAMPLES.items():
        (SERVER_ROOT / "incoming" / name).write_bytes(content)


def _restrict(path) -> None:
    """Best-effort owner-only perms on a private key file (no-op effect on Windows)."""
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _make_host_key() -> None:
    if HOST_KEY.exists():
        return
    import asyncssh
    # ed25519: widely supported and avoids the SHA-1-era 'ssh-rsa' algorithm-name concern.
    key = asyncssh.generate_private_key("ssh-ed25519")
    HOST_KEY.write_bytes(key.export_private_key())
    _restrict(HOST_KEY)


def _make_tls_cert() -> None:
    if TLS_CERT.exists():
        return
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ) + cert.public_bytes(serialization.Encoding.PEM)
    TLS_CERT.write_bytes(pem)
    _restrict(TLS_CERT)


def _clear_probes() -> None:
    """Remove any leftover _probe_* files (e.g. from an interrupted self-test)."""
    out = SERVER_ROOT / "outgoing"
    if out.exists():
        for p in out.glob("_probe_*"):
            try:
                p.unlink()
            except OSError:
                pass


def reset() -> None:
    """Wipe the served tree (and its keys/cert) for a clean baseline."""
    import shutil

    if RUNTIME.exists():
        shutil.rmtree(RUNTIME, ignore_errors=True)


def ensure() -> None:
    """Create everything needed to run the servers (idempotent).

    Note: incoming/ sample files are rewritten each run; outgoing/ is NOT reset
    (uploads accumulate) — only leftover _probe_* files are swept. Use reset() or
    `python make_fixtures.py --reset` for a clean baseline.
    """
    RUNTIME.mkdir(parents=True, exist_ok=True)
    _make_tree()
    _clear_probes()
    _make_host_key()
    _make_tls_cert()


def summary() -> str:
    lines = [
        f"server root : {SERVER_ROOT}",
        f"host key    : {HOST_KEY}  ({'present' if HOST_KEY.exists() else 'MISSING'})",
        f"tls cert    : {TLS_CERT}  ({'present' if TLS_CERT.exists() else 'MISSING'})",
        "sample files (in incoming/):",
    ]
    for name, content in _SAMPLES.items():
        lines.append(f"  - {name:12} {len(content):>5} bytes  sha256={_sha256(content)}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if "--reset" in sys.argv:
        reset()
        print("Reset: wiped runtime/.")
    ensure()
    print("Fixtures ready.\n")
    print(summary())
