"""The Agent runs its models with their full context window (agent_service/agent_config.py, brain.py).

The current models have a 1,000,000-token context window at the API. The runtime the SDK
drives is older than they are: a model id it does not know is run with 200,000 tokens, and
at that size it summarises the older part of a conversation without saying so. Its switch
for the full window is the suffix "[1m]" on the model id.

What must hold:
- a turn hands the runtime the suffixed id for a model that has the larger window, and the
  plain id for the older 200,000-token models and for ids that are not Claude ids;
- the suffix is never shown and never stored: display names, code names and masked error
  texts are unchanged;
- AGENT_CONTEXT_1M=false keeps the runtime's default, and an API refusal switches it off
  for the process instead of failing every later message.

Needs the agent env (claude_agent_sdk); self-skips elsewhere. Standalone, with that env's
python: tests_v2/unit/test_agent_context_window.py
"""
import logging
import os
import sys
import unittest

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import agent_config as ac                       # noqa: E402
    import brain                                    # noqa: E402
    HAVE_SDK = True
except Exception as e:                              # pragma: no cover
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(reason=f"needs the aihub-agent env: {_IMPORT_ERR}")
    except ImportError:
        pass

LARGE = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5",
         "claude-fable-5-2", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6",
         "claude-haiku-6", "claude-something-new-7-1"]          # a model released later gets it too
OLDER = ["claude-haiku-4-5", "claude-haiku-4-5-20251001", "claude-opus-4-5", "claude-opus-4-5-20251101",
         "claude-opus-4-1-20250805", "claude-sonnet-4-5", "claude-sonnet-4-5-20250929",
         "claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-3-haiku-20240307",
         "claude-3-7-sonnet-20250219"]
NOT_CLAUDE = ["gpt-5.6-terra", "some-other-model", ""]


@unittest.skipUnless(HAVE_SDK, "agent_config not importable in this env")
class _Case(unittest.TestCase):
    def setUp(self):
        self._saved = (ac.AGENT_CONTEXT_1M, ac._context_1m_refused, dict(ac.MODEL_CODENAMES))
        ac.AGENT_CONTEXT_1M, ac._context_1m_refused = True, ""

    def tearDown(self):
        ac.AGENT_CONTEXT_1M, ac._context_1m_refused, names = self._saved
        ac.MODEL_CODENAMES.clear()
        ac.MODEL_CODENAMES.update(names)


class TestWhichModelsGetTheFullWindow(_Case):

    def test_the_current_models_are_run_with_the_switch(self):
        for m in LARGE:
            self.assertEqual(ac.runtime_model_id(m), m + "[1m]", m)
            self.assertEqual(ac.context_window_tokens(m), 1_000_000, m)

    def test_the_older_200000_token_models_are_left_as_they_are(self):
        for m in OLDER:
            self.assertEqual(ac.runtime_model_id(m), m, m)
            self.assertEqual(ac.context_window_tokens(m), 200_000, m)

    def test_an_id_that_is_not_a_claude_id_is_left_alone(self):
        for m in NOT_CLAUDE:
            self.assertEqual(ac.runtime_model_id(m), m, m)
            self.assertEqual(ac.context_window_tokens(m), 200_000, m)

    def test_the_suffix_is_never_doubled(self):
        self.assertEqual(ac.runtime_model_id("claude-haiku-5-5[1m]"), "claude-haiku-5-5[1m]")
        self.assertEqual(ac.runtime_model_id("  claude-haiku-5-5  "), "claude-haiku-5-5[1m]")
        self.assertEqual(ac.context_window_tokens("claude-haiku-5-5[1m]"), 1_000_000)

    def test_switched_off_everything_runs_as_before(self):
        ac.AGENT_CONTEXT_1M = False
        for m in LARGE + OLDER + NOT_CLAUDE:
            self.assertEqual(ac.runtime_model_id(m), m, m)
            self.assertEqual(ac.context_window_tokens(m), 200_000, m)

    def test_the_environment_switch(self):
        src = open(os.path.join(APP_ROOT, "agent_service", "agent_config.py"), encoding="utf-8").read()
        line = next(ln for ln in src.splitlines() if ln.startswith("AGENT_CONTEXT_1M ="))
        expr = line.split("=", 1)[1].strip()
        for value, want in ((None, True), ("true", True), ("TRUE", True), ("1", True), ("false", False),
                            ("False", False), ("0", False), ("off", False), ("no", False), (" false ", False)):
            env = {} if value is None else {"AGENT_CONTEXT_1M": value}

            class _Os:
                @staticmethod
                def getenv(key, default=None, _e=env):
                    return _e.get(key, default)
            self.assertIs(eval(expr, {"os": _Os}), want, value)


class TestTheSuffixIsNeverShownOrStored(_Case):

    def setUp(self):
        super().setUp()
        ac.MODEL_CODENAMES.clear()
        ac.MODEL_CODENAMES.update({"claude-haiku-5-5": "the-agent-basic", "claude-opus-5": "the-agent-pro"})

    def test_a_code_named_model_still_shows_its_code_name(self):
        self.assertEqual(ac.display_model("claude-haiku-5-5[1m]"), "the-agent-basic")
        self.assertEqual(ac.display_model(ac.runtime_model_id("claude-haiku-5-5")), "the-agent-basic")
        self.assertEqual(ac.display_model("claude-haiku-5-5"), "the-agent-basic")

    def test_an_unmapped_model_shows_its_plain_id(self):
        self.assertEqual(ac.display_model("claude-sonnet-5-5[1m]"), "claude-sonnet-5-5")
        ac.MODEL_CODENAMES.clear()                                     # code names off
        self.assertEqual(ac.display_model("claude-haiku-5-5[1m]"), "claude-haiku-5-5")
        self.assertIsNone(ac.display_model(None))

    def test_an_error_text_naming_the_runtime_id_is_masked_whole(self):
        text = "API Error: model claude-haiku-5-5[1m] is overloaded. Retry claude-haiku-5-5. Not claude-opus-5-5[1m]."
        self.assertEqual(ac.mask_model_ids(text),
                         "API Error: model the-agent-basic is overloaded. Retry the-agent-basic. "
                         "Not claude-opus-5-5[1m].")              # a longer id is not a mapped one

    def test_plain_model_id(self):
        self.assertEqual(ac.plain_model_id("claude-haiku-5-5[1m]"), "claude-haiku-5-5")
        self.assertEqual(ac.plain_model_id("claude-haiku-5-5[1M] "), "claude-haiku-5-5")
        self.assertEqual(ac.plain_model_id("claude-haiku-5-5"), "claude-haiku-5-5")
        self.assertIsNone(ac.plain_model_id(None))

    def test_a_model_id_an_admin_may_store_cannot_carry_the_suffix(self):
        self.assertIsNone(ac._MODEL_RE.match("claude-haiku-5-5[1m]"))
        self.assertIsNotNone(ac._MODEL_RE.match("claude-haiku-5-5"))

    def test_the_turn_hands_the_runtime_the_suffixed_id_and_reads_the_prompt_of_the_plain_one(self):
        saved = ac.get_effective_model
        seen = {}
        real_prompt_file = brain.prompt_file_for_model
        brain.prompt_file_for_model = lambda model: seen.setdefault("prompt_for", model) or "prompt.md"
        ac.get_effective_model = lambda role=None: "claude-haiku-5-5"
        try:
            opts = brain._make_options(None, "full", None, 2, ["one-skill"])
            self.assertEqual(opts.model, "claude-haiku-5-5[1m]")
            self.assertEqual(seen["prompt_for"], "claude-haiku-5-5")
            ac.get_effective_model = lambda role=None: "claude-haiku-4-5"
            self.assertEqual(brain._make_options(None, "full", None, 2, ["one-skill"]).model, "claude-haiku-4-5")
            ac.AGENT_CONTEXT_1M = False
            ac.get_effective_model = lambda role=None: "claude-haiku-5-5"
            self.assertEqual(brain._make_options(None, "full", None, 2, ["one-skill"]).model, "claude-haiku-5-5")
        finally:
            ac.get_effective_model = saved
            brain.prompt_file_for_model = real_prompt_file

    def test_the_health_summary_says_which_window_a_turn_has(self):
        saved = ac.get_effective_model
        ac.get_effective_model = lambda role=None: "claude-haiku-4-5" if role == 1 else "claude-haiku-5-5"
        try:
            s = ac.summary()
            self.assertEqual((s["context_window_tokens"], s["context_window_tokens_role1"]), (1_000_000, 200_000))
            self.assertEqual(s["model"], "claude-haiku-5-5")           # the id itself stays plain
        finally:
            ac.get_effective_model = saved


class TestARefusalDoesNotKillTheService(_Case):

    def setUp(self):
        super().setUp()
        # the refusal is logged as an error: keep these made-up ones out of the service's own log
        self._logger = ac.logger
        ac.logger = logging.getLogger("test_agent_context_window.silent")
        ac.logger.addHandler(logging.NullHandler())
        ac.logger.propagate = False

    def tearDown(self):
        ac.logger = self._logger
        super().tearDown()

    def test_the_apis_refusal_switches_the_larger_window_off_for_the_process(self):
        self.assertEqual(ac.runtime_model_id("claude-haiku-5-5"), "claude-haiku-5-5[1m]")
        refused = ac.note_context_1m_refused(
            "API Error: 400 The long context beta is not yet available for this subscription.")
        self.assertTrue(refused)
        self.assertEqual(ac.runtime_model_id("claude-haiku-5-5"), "claude-haiku-5-5")
        self.assertEqual(ac.context_window_tokens("claude-haiku-5-5"), 200_000)
        # said once, not on every later error
        self.assertFalse(ac.note_context_1m_refused("The long context beta is not yet available"))

    def test_other_errors_change_nothing(self):
        for text in ("API Error: 529 Overloaded", "rate limit exceeded", "prompt is too long: 1200000 tokens",
                     "Connection reset", "", None):
            self.assertFalse(ac.note_context_1m_refused(text), text)
        self.assertEqual(ac.runtime_model_id("claude-haiku-5-5"), "claude-haiku-5-5[1m]")

    def test_the_other_wordings_of_the_refusal_are_recognised(self):
        for text in ("Extra usage is required for long context requests.",
                     "Unexpected value(s) `context-1m-2025-08-07` for the `anthropic-beta` header",
                     "1M context is not enabled for this organization"):
            ac._context_1m_refused = ""
            self.assertTrue(ac.note_context_1m_refused(text), text)

    def test_with_the_switch_off_there_is_nothing_to_refuse(self):
        ac.AGENT_CONTEXT_1M = False
        self.assertFalse(ac.note_context_1m_refused("The long context beta is not yet available"))
        self.assertEqual(ac._context_1m_refused, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
