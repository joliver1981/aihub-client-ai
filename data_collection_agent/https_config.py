"""
HTTPS configuration for the standalone runner.

Stores the deployment's TLS posture: are we plain HTTP, behind a reverse
proxy, using a self-signed cert, or using a customer-supplied cert? The
admin UI at `/data-collection/admin/https` writes this file; `run_dca.py`
reads it on startup to decide how to bind.

Per-schema HTTPS isn't a real thing — TLS terminates at the host:port
layer, before the request reaches a route. What IS meaningful per-schema
is an advisory `requires_secure_context` flag (see schema_validator.py)
that surfaces a warning banner when voice features won't work.
"""

import datetime
import json
import logging
import os
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


# Storage locations
APP_ROOT = os.getenv('APP_ROOT', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(APP_ROOT, 'data')
HTTPS_CONFIG_FILE = os.path.join(DATA_DIR, '_https_config.json')

# Where self-signed certs land
DEFAULT_CERT_DIR = os.path.join(DATA_DIR, 'dca_certs')
DEFAULT_CERT_NAME = 'dca-self-signed'

# Allowed modes
MODE_NONE = 'none'
MODE_REVERSE_PROXY = 'reverse_proxy'
MODE_SELF_SIGNED = 'self_signed'
MODE_CUSTOM_CERT = 'custom_cert'
ALLOWED_MODES = (MODE_NONE, MODE_REVERSE_PROXY, MODE_SELF_SIGNED, MODE_CUSTOM_CERT)

_file_lock = Lock()


# ----------------------------------------------------------------------
# Config CRUD
# ----------------------------------------------------------------------

def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def default_config() -> Dict[str, Any]:
    return {
        'mode': MODE_NONE,
        'cert_path': '',
        'key_path': '',
        'hostname': 'localhost',
        # Bookkeeping for the UI — when did we last generate a self-signed cert
        'last_generated_at': None,
        'last_generated_for': None,
    }


def load_config() -> Dict[str, Any]:
    """Load HTTPS config; return defaults if not configured."""
    _ensure_data_dir()
    if not os.path.exists(HTTPS_CONFIG_FILE):
        return default_config()
    try:
        with open(HTTPS_CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f) or {}
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid HTTPS config: {e}; falling back to defaults")
        return default_config()
    except Exception as e:
        logger.warning(f"Could not read HTTPS config: {e}; falling back to defaults")
        return default_config()

    cfg = default_config()
    cfg.update({k: v for k, v in data.items() if k in cfg})
    if cfg['mode'] not in ALLOWED_MODES:
        logger.warning(f"Unknown HTTPS mode {cfg['mode']!r}; using 'none'")
        cfg['mode'] = MODE_NONE
    return cfg


def save_config(cfg: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Persist HTTPS config. Returns (ok, error_message)."""
    if not isinstance(cfg, dict):
        return False, "config must be an object"
    if cfg.get('mode') not in ALLOWED_MODES:
        return False, f"invalid mode {cfg.get('mode')!r} (allowed: {ALLOWED_MODES})"

    # Validate cert paths if mode requires them
    if cfg['mode'] in (MODE_SELF_SIGNED, MODE_CUSTOM_CERT):
        cert_path = (cfg.get('cert_path') or '').strip()
        key_path = (cfg.get('key_path') or '').strip()
        if not cert_path or not key_path:
            return False, f"mode={cfg['mode']!r} requires cert_path and key_path"
        if not os.path.exists(cert_path):
            return False, f"cert_path does not exist: {cert_path}"
        if not os.path.exists(key_path):
            return False, f"key_path does not exist: {key_path}"

    # Merge with defaults so we always write a complete object
    full = default_config()
    full.update({k: cfg.get(k, full.get(k)) for k in full})

    try:
        _ensure_data_dir()
        with _file_lock:
            with open(HTTPS_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(full, f, indent=2, default=str)
        return True, None
    except Exception as e:
        return False, str(e)


# ----------------------------------------------------------------------
# Self-signed cert generation
# ----------------------------------------------------------------------

def generate_self_signed(
    hostname: str = 'localhost',
    out_dir: Optional[str] = None,
    name: str = DEFAULT_CERT_NAME,
    days_valid: int = 825,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Generate a self-signed TLS cert + RSA private key. Writes two files:
      - <out_dir>/<name>.crt  (PEM-encoded certificate)
      - <out_dir>/<name>.key  (PEM-encoded RSA private key, unencrypted)

    Includes Subject Alternative Names for `localhost` + `127.0.0.1` + the
    given hostname (if different) so the cert works for both local browser
    access and same-host clients.

    Returns (ok, details_dict).
    """
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import ipaddress
    except ImportError as e:
        return False, {'error': f"cryptography lib not available: {e}"}

    out_dir = out_dir or DEFAULT_CERT_DIR
    os.makedirs(out_dir, exist_ok=True)
    cert_path = os.path.join(out_dir, f'{name}.crt')
    key_path = os.path.join(out_dir, f'{name}.key')

    # Generate a 2048-bit RSA key
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Subject + issuer (self-signed → same)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, hostname or 'localhost'),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Data Collection Agent'),
    ])

    # SAN: localhost + 127.0.0.1 + provided hostname (de-duped)
    san_dns = {'localhost'}
    san_ip = {ipaddress.ip_address('127.0.0.1'), ipaddress.ip_address('::1')}
    if hostname and hostname.lower() not in san_dns:
        try:
            # If hostname is an IP, add to san_ip; otherwise to san_dns
            ip = ipaddress.ip_address(hostname)
            san_ip.add(ip)
        except ValueError:
            san_dns.add(hostname)

    san_entries = [x509.DNSName(d) for d in sorted(san_dns)] + \
                  [x509.IPAddress(ip) for ip in san_ip]

    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))  # avoid clock skew
        .not_valid_after(now + datetime.timedelta(days=days_valid))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
    )
    cert = builder.sign(private_key=key, algorithm=hashes.SHA256())
    # Use not_valid_after_utc when available (newer cryptography), fall back
    not_valid_after = getattr(cert, 'not_valid_after_utc', None) or cert.not_valid_after

    # Write key (mode 0600 where supported)
    with open(key_path, 'wb') as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    try:
        os.chmod(key_path, 0o600)
    except (PermissionError, NotImplementedError):
        pass  # Windows is fine; key is in user-private data dir

    with open(cert_path, 'wb') as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    return True, {
        'cert_path': cert_path,
        'key_path': key_path,
        'hostname': hostname,
        'expires_at': not_valid_after.isoformat(),
        'san_dns': sorted(san_dns),
        'san_ip': [str(ip) for ip in san_ip],
        'serial': str(cert.serial_number),
    }


# ----------------------------------------------------------------------
# Cert inspection — for the admin UI
# ----------------------------------------------------------------------

def cert_info(cert_path: str) -> Dict[str, Any]:
    """Read a cert file and return human-friendly metadata."""
    if not cert_path or not os.path.exists(cert_path):
        return {'error': f"file not found: {cert_path}"}
    try:
        from cryptography import x509
    except ImportError as e:
        return {'error': f"cryptography lib not available: {e}"}

    try:
        with open(cert_path, 'rb') as f:
            cert = x509.load_pem_x509_certificate(f.read())
    except Exception as e:
        return {'error': f"could not parse cert: {e}"}

    san_dns: list = []
    san_ip: list = []
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        # get_values_for_type returns plain values (str / IPAddress) in
        # cryptography >=37, not wrapper objects with .value
        san_dns = [str(v) for v in san.get_values_for_type(x509.DNSName)]
        san_ip = [str(v) for v in san.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        pass

    # Prefer the timezone-aware accessors when available
    not_before = getattr(cert, 'not_valid_before_utc', None) or cert.not_valid_before
    not_after = getattr(cert, 'not_valid_after_utc', None) or cert.not_valid_after
    # Use a comparable "now" — match aware-ness of not_after
    if not_after.tzinfo is not None:
        now = datetime.datetime.now(datetime.timezone.utc)
    else:
        now = datetime.datetime.utcnow()
    days_remaining = (not_after - now).days

    return {
        'subject':       cert.subject.rfc4514_string(),
        'issuer':        cert.issuer.rfc4514_string(),
        'self_signed':   cert.subject == cert.issuer,
        'not_valid_before': not_before.isoformat(),
        'not_valid_after':  not_after.isoformat(),
        'days_remaining':   days_remaining,
        'expired':       days_remaining < 0,
        'san_dns':       san_dns,
        'san_ip':        san_ip,
        'serial':        str(cert.serial_number),
    }


# ----------------------------------------------------------------------
# Effective deployment helper — used by run_dca.py at startup
# ----------------------------------------------------------------------

def effective_runtime_settings() -> Dict[str, Any]:
    """
    Translate the persisted config into concrete startup choices for
    `run_dca.py`. Returns a dict like:
        {
          'mode': '...',
          'enable_proxy_fix': bool,
          'ssl_context': (cert_path, key_path) | None,
          'protocol': 'http' | 'https',  # for the printed banner
          'warnings': [str, ...],
        }
    """
    cfg = load_config()
    mode = cfg.get('mode', MODE_NONE)
    out: Dict[str, Any] = {
        'mode': mode,
        'enable_proxy_fix': False,
        'ssl_context': None,
        'protocol': 'http',
        'warnings': [],
    }

    if mode == MODE_REVERSE_PROXY:
        out['enable_proxy_fix'] = True
        # Protocol from the user's perspective is HTTPS (the proxy terminates),
        # but the local Flask binds plain HTTP. We display a hint.
        out['protocol'] = 'http'   # local; URL printed reflects that
        out['warnings'].append(
            "Mode 'reverse_proxy': Flask binds plain HTTP locally. Make sure "
            "your reverse proxy terminates TLS and forwards X-Forwarded-Proto."
        )

    elif mode in (MODE_SELF_SIGNED, MODE_CUSTOM_CERT):
        cert_path = (cfg.get('cert_path') or '').strip()
        key_path = (cfg.get('key_path') or '').strip()
        if cert_path and key_path and os.path.exists(cert_path) and os.path.exists(key_path):
            out['ssl_context'] = (cert_path, key_path)
            out['protocol'] = 'https'
            if mode == MODE_SELF_SIGNED:
                out['warnings'].append(
                    "Self-signed cert in use. Browsers will show a security "
                    "warning unless the cert is trusted in the OS / browser."
                )
        else:
            out['warnings'].append(
                f"Mode {mode!r} configured but cert/key files missing — falling "
                "back to plain HTTP. Re-generate or re-upload the cert."
            )

    return out
