"""
test_driver_model_resolution.py - the driver-LLM resolution chain in browser_use_config.

Pins the contract that lets the admin UI (Settings → API Keys → Model Overrides, key
'browser_use_model' in data/model_overrides.json) change the model WITHOUT a service restart,
and the precedence between that override, the dedicated .env pin, the platform primary, and
the built-in default. No browser, no LLM call.

Run (browser-use env):  python -m unittest browser_use_service/tests/test_driver_model_resolution.py
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import browser_use_config as cfg  # noqa: E402


class DriverModelResolutionTests(unittest.TestCase):
    ENV_KEYS = ("BROWSER_USE_LLM_MODEL", "AZURE_OPENAI_DEPLOYMENT_NAME")

    def setUp(self):
        self._saved_env = {k: os.environ.get(k) for k in self.ENV_KEYS}
        for k in self.ENV_KEYS:
            os.environ.pop(k, None)
        self._saved_path = cfg.MODEL_OVERRIDES_PATH
        self._tmp = tempfile.TemporaryDirectory()
        cfg.MODEL_OVERRIDES_PATH = os.path.join(self._tmp.name, "model_overrides.json")
        # Neutralise _build_config's AZURE_OPENAI_DEPLOYMENT_NAME (present on a built box) so
        # the "nothing set" tier is reachable deterministically.
        self._saved_eob = cfg._env_or_build
        cfg._env_or_build = lambda name, default="": os.getenv(name) or default

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cfg.MODEL_OVERRIDES_PATH = self._saved_path
        cfg._env_or_build = self._saved_eob
        self._tmp.cleanup()

    def _write(self, payload):
        with open(cfg.MODEL_OVERRIDES_PATH, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    # --- the chain, top to bottom ---------------------------------------------------------

    def test_builtin_default_when_nothing_set(self):
        self.assertEqual(cfg.resolve_llm_model(), cfg.DEFAULT_LLM_MODEL)
        self.assertEqual(cfg.describe_llm_model_source(), "built-in default")

    def test_azure_deployment_env_beats_builtin(self):
        os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"] = "gpt-azure-env"
        self.assertEqual(cfg.resolve_llm_model(), "gpt-azure-env")

    def test_openai_primary_override_beats_azure_env(self):
        os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"] = "gpt-azure-env"
        self._write({"openai_primary": "gpt-ui-primary"})
        self.assertEqual(cfg.resolve_llm_model(), "gpt-ui-primary")
        self.assertIn("openai_primary", cfg.describe_llm_model_source())

    def test_dot_env_pin_beats_openai_primary_override(self):
        os.environ["BROWSER_USE_LLM_MODEL"] = "gpt-dot-env-pin"
        self._write({"openai_primary": "gpt-ui-primary"})
        self.assertEqual(cfg.resolve_llm_model(), "gpt-dot-env-pin")
        self.assertIn("BROWSER_USE_LLM_MODEL", cfg.describe_llm_model_source())

    def test_browser_use_override_beats_everything(self):
        os.environ["BROWSER_USE_LLM_MODEL"] = "gpt-dot-env-pin"
        os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"] = "gpt-azure-env"
        self._write({"browser_use_model": "claude-ui-pick", "openai_primary": "gpt-ui-primary"})
        self.assertEqual(cfg.resolve_llm_model(), "claude-ui-pick")
        self.assertIn("browser_use_model", cfg.describe_llm_model_source())

    # --- live re-read + robustness ---------------------------------------------------------

    def test_override_change_applies_without_reimport(self):
        """The whole point: the file is re-read on every call, so a UI save lands on the
        next run with no restart."""
        os.environ["BROWSER_USE_LLM_MODEL"] = "gpt-dot-env-pin"
        self.assertEqual(cfg.resolve_llm_model(), "gpt-dot-env-pin")
        self._write({"browser_use_model": "gpt-ui-pick"})
        self.assertEqual(cfg.resolve_llm_model(), "gpt-ui-pick")
        self._write({"browser_use_model": ""})          # cleared in the UI
        self.assertEqual(cfg.resolve_llm_model(), "gpt-dot-env-pin")

    def test_blank_and_whitespace_override_is_no_override(self):
        os.environ["BROWSER_USE_LLM_MODEL"] = "gpt-dot-env-pin"
        self._write({"browser_use_model": "   "})
        self.assertEqual(cfg.resolve_llm_model(), "gpt-dot-env-pin")

    def test_override_value_is_stripped(self):
        self._write({"browser_use_model": "  gpt-ui-pick  "})
        self.assertEqual(cfg.resolve_llm_model(), "gpt-ui-pick")

    def test_garbled_or_missing_file_is_fail_soft(self):
        os.environ["BROWSER_USE_LLM_MODEL"] = "gpt-dot-env-pin"
        # missing
        self.assertEqual(cfg.resolve_llm_model(), "gpt-dot-env-pin")
        # not JSON
        with open(cfg.MODEL_OVERRIDES_PATH, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(cfg.resolve_llm_model(), "gpt-dot-env-pin")
        # JSON but not an object
        self._write(["gpt-x"])
        self.assertEqual(cfg.resolve_llm_model(), "gpt-dot-env-pin")

    def test_startup_snapshot_is_a_string_and_matches_live_when_unchanged(self):
        self.assertIsInstance(cfg.LLM_MODEL, str)
        self.assertTrue(cfg.LLM_MODEL)

    def test_ensure_llm_api_key_defaults_to_live_model(self):
        """ensure_llm_api_key() with no model must pick the provider of the LIVE model, so a
        UI switch to claude-* resolves the Anthropic key and not OpenAI's."""
        seen = {}
        orig = cfg._provider_for_model

        def spy(m):
            seen["model"] = m
            return orig(m)

        cfg._provider_for_model = spy
        saved_key = os.environ.get("ANTHROPIC_API_KEY")
        try:
            self._write({"browser_use_model": "claude-ui-pick"})
            # A plaintext key short-circuits ensure_llm_api_key before any store lookup.
            os.environ["ANTHROPIC_API_KEY"] = saved_key or "dummy-not-used"
            cfg.ensure_llm_api_key()
        finally:
            cfg._provider_for_model = orig
            if saved_key is None:
                os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                os.environ["ANTHROPIC_API_KEY"] = saved_key
        self.assertEqual(seen.get("model"), "claude-ui-pick")


if __name__ == "__main__":
    unittest.main()
