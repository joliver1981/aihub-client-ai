"""Addresses users reach AI Hub's services at behind an HTTPS reverse proxy.

Each browser-facing service (the main app, the Builder, the Command Center)
normally hands the browser links of the form http://<host>:<service port>.
Published through a TLS-terminating reverse proxy those links cannot be
followed: the service ports speak plain HTTP and are usually not reachable
from outside at all. The settings read here name the address the proxy
publishes each service at, for example:

    USE_REVERSE_PROXY=true
    APP_PUBLIC_BASE_URL=https://aihub.example.com
    BUILDER_PUBLIC_BASE_URL=https://builder.example.com
    CC_PUBLIC_BASE_URL=https://aihub.example.com:8444

Rules, the same for every setting:
  * Only honoured while USE_REVERSE_PROXY is on (read the way app.py reads
    it). A direct-HTTP install never takes this path, whatever else is set.
  * The value must start with http:// or https://. Anything else is ignored
    with a logged warning and is never handed to a browser.
  * Unset or ignored means "" and the caller keeps its existing behaviour.

Deliberately dependency-free (os + logging only) so the Builder and Command
Center services can import it as well as the main app.
"""

import logging
import os

logger = logging.getLogger(__name__)

_TRUE_VALUES = ('true', '1', 'yes')


def behind_proxy() -> bool:
    """True when the install is switched to its reverse-proxy posture."""
    return os.getenv('USE_REVERSE_PROXY', 'false').strip().lower() in _TRUE_VALUES


def published_address(setting: str) -> str:
    """The published address stored in the environment setting `setting`,
    without a trailing slash, or "" when it must not be used (proxy posture
    off, setting absent, or not an http(s) address)."""
    if not behind_proxy():
        return ''
    raw = os.getenv(setting, '').strip().rstrip('/')
    if not raw:
        return ''
    if not raw.lower().startswith(('http://', 'https://')):
        logger.warning('%s ignored: it must start with http:// or https://', setting)
        return ''
    return raw
