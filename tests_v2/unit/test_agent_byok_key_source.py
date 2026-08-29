"""The Agent BYOK precedence — ensure_anthropic_key() + anthropic_key_source.

A client who flips BYOK on and saves their OWN Anthropic key (Settings ->
API Keys -> USER_ANTHROPIC_API_KEY in the encrypted local store) must have
The Agent use THAT key against api.anthropic.com directly, outranking the
cloud relay AND any pinned/encrypted env key. Only an EMPTY key slot falls
through; the function itself must never raise (it runs on every turn).

The regression that matters most here: ensure_anthropic_key() runs per turn,
and relay mode writes ANTHROPIC_BASE_URL into the process env — a mid-process
BYOK flip must CLEAR it, or the client's sk-ant key gets posted to our relay
and 401s on the tenant-LicenseKey lookup.

Runs standalone (python test_agent_byok_key_source.py) or under pytest.
"""
import json
import os
import sys
import tempfile
import types

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

import agent_config  # noqa: E402

_ENV_KEYS = (
    "AGENT_ANTHROPIC_RELAY", "AGENT_RELAY_URL", "AI_HUB_API_URL", "API_KEY",
    "ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY_ENCRYPTED", "ANTHROPIC_BASE_URL",
    "AIHUB_DATA_DIR",
)
_MISSING = object()


class _Sandbox:
    """Blank-slate env + stubbed local_secrets/encrypt around one scenario."""

    def __init__(self, byok_enabled=None, byok_key=None, relay=False,
                 secrets_import_fails=False, byok_config_raw=None):
        self.byok_enabled = byok_enabled
        self.byok_key = byok_key
        self.relay = relay
        self.secrets_import_fails = secrets_import_fails
        self.byok_config_raw = byok_config_raw

    def __enter__(self):
        self._env = {k: os.environ.get(k, _MISSING) for k in _ENV_KEYS}
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        self._source = agent_config._anthropic_key_source
        agent_config._anthropic_key_source = "none"

        self._tmp = tempfile.TemporaryDirectory()
        os.environ["AIHUB_DATA_DIR"] = self._tmp.name
        cfg_path = os.path.join(self._tmp.name, "byok_config.json")
        if self.byok_config_raw is not None:
            with open(cfg_path, "w", encoding="utf-8") as fh:
                fh.write(self.byok_config_raw)
        elif self.byok_enabled is not None:
            with open(cfg_path, "w", encoding="utf-8") as fh:
                json.dump({"byok_enabled": self.byok_enabled}, fh)
        # byok_enabled=None and no raw -> file deliberately missing

        if self.relay:
            os.environ["AGENT_ANTHROPIC_RELAY"] = "true"
            os.environ["AGENT_RELAY_URL"] = "https://relay.example.test"
            os.environ["API_KEY"] = "tenant-license-key-123"

        self._mods = {name: sys.modules.get(name, _MISSING)
                      for name in ("local_secrets", "encrypt")}
        if self.secrets_import_fails:
            # attribute missing -> `from local_secrets import get_local_secret`
            # raises ImportError, exactly like a broken/absent module
            sys.modules["local_secrets"] = types.ModuleType("local_secrets")
        else:
            stub = types.ModuleType("local_secrets")
            key = self.byok_key
            stub.get_local_secret = (
                lambda name, default=None:
                key if name == "USER_ANTHROPIC_API_KEY" else default)
            sys.modules["local_secrets"] = stub
        enc = types.ModuleType("encrypt")
        enc.ENCRYPTION_KEY = "unit-test-key"
        enc.decrypt_value = lambda value, _key: f"decrypted::{value}"
        sys.modules["encrypt"] = enc
        return self

    def __exit__(self, *exc):
        for k, v in self._env.items():
            if v is _MISSING:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for name, mod in self._mods.items():
            if mod is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        agent_config._anthropic_key_source = self._source
        self._tmp.cleanup()
        return False

    # in-scenario mutators for the flip tests -------------------------------
    def flip_byok(self, enabled, key=None):
        with open(os.path.join(self._tmp.name, "byok_config.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"byok_enabled": enabled}, fh)
        if key is not None:
            sys.modules["local_secrets"].get_local_secret = (
                lambda name, default=None:
                key if name == "USER_ANTHROPIC_API_KEY" else default)


def test_byok_on_key_present_wins_direct():
    with _Sandbox(byok_enabled=True, byok_key="sk-ant-client-own-key"):
        assert agent_config.ensure_anthropic_key() is True
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-client-own-key"
        assert "ANTHROPIC_BASE_URL" not in os.environ
        assert agent_config.anthropic_key_source() == "byok"


def test_byok_beats_relay_and_clears_base_url():
    with _Sandbox(byok_enabled=True, byok_key="sk-ant-client-own-key",
                  relay=True):
        os.environ["ANTHROPIC_BASE_URL"] = "https://relay.example.test/api/agent-llm"
        assert agent_config.ensure_anthropic_key() is True
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-client-own-key"
        assert "ANTHROPIC_BASE_URL" not in os.environ
        assert agent_config.anthropic_key_source() == "byok"


def test_byok_on_but_empty_slot_falls_to_relay():
    with _Sandbox(byok_enabled=True, byok_key=None, relay=True):
        assert agent_config.ensure_anthropic_key() is True
        assert os.environ["ANTHROPIC_API_KEY"] == "tenant-license-key-123"
        assert (os.environ["ANTHROPIC_BASE_URL"]
                == "https://relay.example.test/api/agent-llm")
        assert agent_config.anthropic_key_source() == "relay"


def test_byok_off_relay_on_unchanged():
    with _Sandbox(byok_enabled=False, byok_key="sk-ant-should-not-be-read",
                  relay=True):
        assert agent_config.ensure_anthropic_key() is True
        assert os.environ["ANTHROPIC_API_KEY"] == "tenant-license-key-123"
        assert agent_config.anthropic_key_source() == "relay"


def test_byok_off_relay_off_encrypted_path_unchanged():
    with _Sandbox(byok_enabled=False):
        os.environ["ANTHROPIC_API_KEY_ENCRYPTED"] = "gAAAA-ciphertext"
        assert agent_config.ensure_anthropic_key() is True
        assert os.environ["ANTHROPIC_API_KEY"] == "decrypted::gAAAA-ciphertext"
        assert agent_config.anthropic_key_source() == "encrypted"


def test_plain_env_key_reports_env_source():
    with _Sandbox(byok_enabled=False):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-pinned-by-ops"
        assert agent_config.ensure_anthropic_key() is True
        assert agent_config.anthropic_key_source() == "env"


def test_missing_and_malformed_byok_config_treated_as_off():
    with _Sandbox(byok_enabled=None, relay=True):  # file missing entirely
        assert agent_config.ensure_anthropic_key() is True
        assert agent_config.anthropic_key_source() == "relay"
    with _Sandbox(byok_config_raw="{not json!!", relay=True):
        assert agent_config.ensure_anthropic_key() is True
        assert agent_config.anthropic_key_source() == "relay"


def test_local_secrets_import_failure_falls_through():
    with _Sandbox(byok_enabled=True, secrets_import_fails=True, relay=True):
        assert agent_config.ensure_anthropic_key() is True
        assert agent_config.anthropic_key_source() == "relay"


def test_no_source_at_all_returns_false_source_none():
    with _Sandbox(byok_enabled=True, byok_key=None):
        assert agent_config.ensure_anthropic_key() is False
        assert agent_config.anthropic_key_source() == "none"


def test_mid_process_flip_relay_to_byok_clears_stale_base_url():
    """Trap #1's regression: turn 1 on relay, BYOK flipped on, turn 2 must
    switch source AND drop the relay base URL from the process env."""
    with _Sandbox(byok_enabled=False, relay=True) as sb:
        assert agent_config.ensure_anthropic_key() is True
        assert agent_config.anthropic_key_source() == "relay"
        assert "ANTHROPIC_BASE_URL" in os.environ
        sb.flip_byok(True, key="sk-ant-client-own-key")
        assert agent_config.ensure_anthropic_key() is True
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-client-own-key"
        assert "ANTHROPIC_BASE_URL" not in os.environ
        assert agent_config.anthropic_key_source() == "byok"


def test_flip_byok_back_off_resumes_relay():
    with _Sandbox(byok_enabled=True, byok_key="sk-ant-client-own-key",
                  relay=True) as sb:
        assert agent_config.ensure_anthropic_key() is True
        assert agent_config.anthropic_key_source() == "byok"
        sb.flip_byok(False)
        assert agent_config.ensure_anthropic_key() is True
        assert agent_config.anthropic_key_source() == "relay"
        assert (os.environ["ANTHROPIC_BASE_URL"]
                == "https://relay.example.test/api/agent-llm")
        assert os.environ["ANTHROPIC_API_KEY"] == "tenant-license-key-123"


def test_summary_exposes_key_source():
    with _Sandbox(byok_enabled=True, byok_key="sk-ant-client-own-key"):
        agent_config.ensure_anthropic_key()
        s = agent_config.summary()
        assert s["anthropic_key_source"] == "byok"
        assert "sk-ant" not in json.dumps(s)  # never the value itself


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"[PASS] {name}")
            except AssertionError as e:
                fails += 1
                print(f"[FAIL] {name}: {e}")
    print(f"{'ALL PASS' if not fails else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
