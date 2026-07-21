"""
test_portal_workflows.py - unit tests for the saved-workflow store (command_center/tools).

Pure stdlib store (no DB). Uses a temp APP_ROOT so it writes to a throwaway JSON.
Run:  python -m unittest tests/unit/test_portal_workflows.py
"""
import os
import sys
import tempfile
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO, "command_center", "tools"))

import portal_workflows as pw  # noqa: E402


GOOD_STEPS = [
    {"type": "goto", "url": "http://x/login"},
    {"type": "login", "username_anchor": {"css": "#u"}},
    {"type": "click", "anchor": {"text": "Invoices", "role": "link"}},
    {"type": "fill", "anchor": {"css": "#year"}, "value": "2026"},
    {"type": "agent", "prompt": "download the latest invoice", "max_steps": 8},
    {"type": "verify", "downloaded": True},
]


class WorkflowStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._prev = os.environ.get("APP_ROOT")
        os.environ["APP_ROOT"] = self._tmp

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("APP_ROOT", None)
        else:
            os.environ["APP_ROOT"] = self._prev

    def test_validate_good(self):
        self.assertEqual(pw.validate_steps(GOOD_STEPS), [])

    def test_validate_catches_problems(self):
        bad = [
            {"type": "goto"},                       # missing url
            {"type": "click"},                      # missing anchor
            {"type": "fill", "anchor": {"css": "#x"}},   # no value/secret
            {"type": "agent"},                      # missing prompt
            {"type": "frobnicate"},                 # unknown type
        ]
        problems = pw.validate_steps(bad)
        self.assertEqual(len(problems), 5)
        self.assertTrue(any("goto" in p for p in problems))
        self.assertTrue(any("unknown type" in p for p in problems))

    def test_validate_empty(self):
        self.assertTrue(pw.validate_steps([]))

    def test_save_and_get_roundtrip(self):
        saved = pw.save_workflow(7, "Acme - latest invoice", GOOD_STEPS,
                                 portal_slug="acme_vendor", start_url="http://x/login",
                                 goal="log in and download the latest invoice")
        self.assertEqual(saved["slug"], "acme_latest_invoice")
        self.assertEqual(saved["step_count"], 6)
        got = pw.get_workflow(7, "Acme - latest invoice")
        self.assertIsNotNone(got)
        self.assertEqual(got["portal_slug"], "acme_vendor")
        self.assertEqual(len(got["steps"]), 6)
        self.assertEqual(got["success_count"], 0)

    def test_save_invalid_raises(self):
        with self.assertRaises(ValueError):
            pw.save_workflow(7, "bad", [{"type": "goto"}])

    def test_update_preserves_created_and_counts(self):
        pw.save_workflow(7, "wf", GOOD_STEPS)
        pw.record_run(7, "wf", "ok")
        first = pw.get_workflow(7, "wf")
        pw.save_workflow(7, "wf", GOOD_STEPS[:2], goal="new goal")
        second = pw.get_workflow(7, "wf")
        self.assertEqual(second["created_at"], first["created_at"])
        self.assertEqual(second["success_count"], 1)  # preserved across edit
        self.assertEqual(second["goal"], "new goal")
        self.assertEqual(len(second["steps"]), 2)

    def test_record_run_increments_on_success_only(self):
        pw.save_workflow(7, "wf", GOOD_STEPS)
        pw.record_run(7, "wf", "ok")
        pw.record_run(7, "wf", "error")
        got = pw.get_workflow(7, "wf")
        self.assertEqual(got["success_count"], 1)
        self.assertEqual(got["last_run_status"], "error")

    def test_list_and_delete(self):
        pw.save_workflow(7, "one", GOOD_STEPS)
        pw.save_workflow(7, "two", GOOD_STEPS)
        names = {w["slug"] for w in pw.list_workflows(7)}
        self.assertEqual(names, {"one", "two"})
        self.assertTrue(pw.delete_workflow(7, "one"))
        self.assertFalse(pw.delete_workflow(7, "one"))
        self.assertEqual({w["slug"] for w in pw.list_workflows(7)}, {"two"})

    def test_per_user_isolation(self):
        pw.save_workflow(7, "mine", GOOD_STEPS)
        self.assertIsNone(pw.get_workflow(99, "mine"))
        self.assertEqual(pw.list_workflows(99), [])

    # --- AIHUB-0067: URL format validation at save (bug #3) ---
    def test_valid_url_helper(self):
        self.assertTrue(pw.valid_url("http://x/y"))
        self.assertTrue(pw.valid_url("https://a.b.com/p?q=1"))
        self.assertFalse(pw.valid_url("abc"))            # the reported case
        self.assertFalse(pw.valid_url("ftp://h/f"))      # not http(s)
        self.assertFalse(pw.valid_url("/relative/path")) # no scheme/host
        self.assertFalse(pw.valid_url(""))
        self.assertFalse(pw.valid_url(None))

    def test_validate_rejects_invalid_goto_url(self):
        probs = pw.validate_steps([{"type": "goto", "url": "abc"}])
        self.assertTrue(any("invalid url" in p for p in probs))

    def test_validate_accepts_valid_goto_url(self):
        self.assertEqual(
            pw.validate_steps([{"type": "goto", "url": "https://portal.example.com/login"}]), [])

    def test_save_rejects_invalid_start_url(self):
        with self.assertRaises(ValueError):
            pw.save_workflow(7, "wf", GOOD_STEPS, start_url="abc")

    def test_save_accepts_valid_or_empty_start_url(self):
        pw.save_workflow(7, "wf1", GOOD_STEPS, start_url="https://ok.example.com")
        pw.save_workflow(7, "wf2", GOOD_STEPS, start_url=None)   # start_url is optional
        self.assertIsNotNone(pw.get_workflow(7, "wf1"))

    # --- AIHUB-0067: numeric-field validation at save (bug #4) ---
    def test_validate_rejects_negative_wait(self):
        probs = pw.validate_steps([{"type": "wait", "timeout": -1}])
        self.assertTrue(any("wait" in p and "0 or greater" in p for p in probs))

    def test_validate_accepts_zero_and_positive_and_missing_wait(self):
        self.assertEqual(pw.validate_steps([{"type": "wait", "timeout": 0}]), [])
        self.assertEqual(pw.validate_steps([{"type": "wait", "timeout": 5}]), [])
        self.assertEqual(pw.validate_steps([{"type": "wait"}]), [])   # defaults, no timeout given

    def test_validate_rejects_non_numeric_wait(self):
        probs = pw.validate_steps([{"type": "wait", "timeout": "soon"}])
        self.assertTrue(any("wait" in p and "number" in p for p in probs))

    def test_validate_rejects_negative_human_and_verify_code_timeout(self):
        self.assertTrue(any("0 or greater" in p for p in
                            pw.validate_steps([{"type": "human", "reason": "2fa", "timeout": -5}])))
        self.assertTrue(any("0 or greater" in p for p in
                            pw.validate_steps([{"type": "verify_code", "timeout": -5}])))

    def test_validate_rejects_negative_agent_max_steps(self):
        probs = pw.validate_steps([{"type": "agent", "prompt": "go", "max_steps": -3}])
        self.assertTrue(any("max_steps" in p for p in probs))

    # --- AIHUB-0066: duplicate-name detection primitive (exact, not loose contains-match) ---
    def test_workflow_exists_exact_and_per_user(self):
        pw.save_workflow(7, "Acme Invoices", GOOD_STEPS)
        self.assertTrue(pw.workflow_exists(7, "Acme Invoices"))
        self.assertTrue(pw.workflow_exists(7, "acme   invoices"))   # slug-canonical match
        self.assertFalse(pw.workflow_exists(7, "Acme"))             # exact slug, NOT loose contains
        self.assertFalse(pw.workflow_exists(99, "Acme Invoices"))   # per-user isolation
        self.assertFalse(pw.workflow_exists(7, ""))


if __name__ == "__main__":
    unittest.main()
