"""
test_internal_token.py - the Browser Use service's internal-token ladder (2026-09-08).

Guards the 2026-09-05 outage: a chat-pasted key saved to the encrypted store as API_KEY was
preferred over the platform key, so every X-AIHub-Internal call 401'd for three days while
/health said ok. The token must resolve registry > .env/env ONLY (never the store), the
startup log must name the source, /health must expose the resolution, and the gate must
accept exactly the platform key.

Run (browser-use env):
  C:\\Users\\james\\miniconda3\\envs\\aihub-browseruse\\python.exe -m unittest browser_use_service/tests/test_internal_token.py
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO)

PLATFORM_KEY = "platform-key-0000-1111-2222-333333333333"
PASTED_KEY = "pasted14chars!"

# Pin the platform key BEFORE any service module imports (main.py reads it at import).
os.environ["API_KEY"] = PLATFORM_KEY
os.environ.setdefault("CC_JWT_SECRET", "internal-token-test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy-not-used")
os.environ["BROWSER_USE_AUTH_ENFORCE"] = "true"

import browser_use_config as config  # noqa: E402
import local_secrets  # noqa: E402


class ResolveInternalToken(unittest.TestCase):
    def test_store_entry_is_ignored_even_when_present(self):
        """The exact 2026-09-05 poisoning: store has a DIFFERENT API_KEY. Must lose."""
        with mock.patch.object(local_secrets, "get_local_secret",
                               lambda name, default="": PASTED_KEY if name == "API_KEY" else default):
            token, source = config.resolve_internal_token()
            self.assertEqual(token, PLATFORM_KEY)
            self.assertIn(source, ("env", "registry"))
            self.assertEqual(config.internal_token_store_shadow(token), "differs")

    def test_get_secret_still_prefers_the_store_for_portal_credentials(self):
        """The store-first ladder is RIGHT for portal creds — only identity moved off it."""
        with mock.patch.object(local_secrets, "get_local_secret",
                               lambda name, default="": "from-store" if name == "PORTAL_U13_X_PASSWORD" else default):
            self.assertEqual(config.get_secret("PORTAL_U13_X_PASSWORD"), "from-store")

    def test_shadow_reports_matches_and_none(self):
        with mock.patch.object(local_secrets, "get_local_secret",
                               lambda name, default="": PLATFORM_KEY if name == "API_KEY" else default):
            self.assertEqual(config.internal_token_store_shadow(PLATFORM_KEY), "matches")
        with mock.patch.object(local_secrets, "get_local_secret", lambda name, default="": default):
            self.assertIsNone(config.internal_token_store_shadow(PLATFORM_KEY))

    def test_no_key_anywhere_resolves_none(self):
        with mock.patch.dict(os.environ, {"API_KEY": ""}):
            self.assertEqual(config.resolve_internal_token(), (None, "none"))


try:
    from fastapi.testclient import TestClient
    import main as svc
    _HAVE_CLIENT = True
except Exception as _e:  # pragma: no cover
    _HAVE_CLIENT = False
    _IMPORT_ERR = _e


@unittest.skipUnless(_HAVE_CLIENT, "fastapi TestClient / main import unavailable")
class ServiceGateAndHealth(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(svc.app)

    def test_token_is_the_platform_key_and_source_is_named(self):
        self.assertEqual(svc.INTERNAL_TOKEN, PLATFORM_KEY)
        self.assertIn(svc.INTERNAL_TOKEN_SOURCE, ("env", "registry"))

    def test_health_reports_internal_auth_without_leaking_the_value(self):
        body = self.client.get("/health").json()
        ia = body["internal_auth"]
        self.assertEqual(ia["enforced"], True)
        self.assertEqual(ia["token_resolved"], True)
        self.assertIn(ia["source"], ("env", "registry"))
        self.assertIn("store_shadow", ia)
        self.assertNotIn(PLATFORM_KEY, str(body))
        self.assertNotIn(PLATFORM_KEY[:12], str(body))

    def test_gate_accepts_platform_key_and_rejects_the_pasted_one(self):
        self.assertEqual(self.client.get("/runs", headers={"X-AIHub-Internal": PLATFORM_KEY}).status_code, 200)
        self.assertEqual(self.client.get("/runs", headers={"X-AIHub-Internal": PASTED_KEY}).status_code, 401)
        self.assertEqual(self.client.get("/runs").status_code, 401)


if __name__ == "__main__":
    unittest.main()
