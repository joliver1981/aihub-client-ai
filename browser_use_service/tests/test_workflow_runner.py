"""
test_workflow_runner.py - unit tests for the Workflow-mode engine (browser_use_service).

No real Chrome/LLM: BrowserSession, the LLM, the scoped-agent runner and the deterministic
in-page action (_do/_wait_ready) are faked, so these exercise the CONTROL FLOW that matters -
JS building, step dispatch, the heal ladder, the full-agent fallback, and download harvesting.

Run (browser-use env):  python -m unittest browser_use_service/tests/test_workflow_runner.py
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import workflow_runner as wr  # noqa: E402


async def _noop(*a, **k):
    return None


async def _noop_true(*a, **k):
    return True


async def _ok_do(session, anchor, action, value=""):
    return "OK"


async def _nf_do(session, anchor, action, value=""):
    return "NOTFOUND"


def _make_fake_agent(download_dir, fail=False):
    counter = {"n": 0}

    async def fake(session, llm, task, sensitive_data, max_steps, timeout):
        if fail:
            raise RuntimeError("agent boom")
        counter["n"] += 1
        with open(os.path.join(download_dir, f"invoice_{counter['n']}.pdf"), "w") as fh:
            fh.write("x")
        return {"ok": True}

    return fake


class FakeSession:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.navigations = []

    async def start(self):
        return None

    async def navigate_to(self, url, new_tab=False):
        self.navigations.append(url)


class JsBuildTests(unittest.TestCase):
    def test_click_embeds_anchor_and_action(self):
        js = wr._action_js({"text": "Invoices", "role": "link"}, "click")
        self.assertIn('"Invoices"', js)
        self.assertIn('"link"', js)
        self.assertIn("el.click()", js)
        self.assertIn("var ANCHOR =", js)

    def test_fill_embeds_value(self):
        js = wr._action_js({"css": "#u"}, "fill", "secret123")
        self.assertIn('"secret123"', js)
        self.assertIn("setValue(el, VAL)", js)

    def test_exists_returns_token(self):
        js = wr._action_js({"text": "x"}, "exists")
        self.assertIn("'OK'", js)
        self.assertIn("'NOTFOUND'", js)

    def test_value_is_json_escaped(self):
        # a value with quotes must not break out of the JS string literal
        js = wr._action_js({"css": "#u"}, "fill", 'a"b\\c')
        self.assertIn(r'"a\"b\\c"', js)


class StepDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_success_fills_and_submits(self):
        sess = FakeSession()
        calls = []

        async def rec_do(session, anchor, action, value=""):
            calls.append((action, value))
            return "OK"

        with mock.patch.object(wr, "_do", rec_do), mock.patch.object(wr, "_wait_ready", _noop_true):
            status, detail = await wr._step_login(
                sess, {"url": "http://x/login"}, {"username": "u", "password": "p"})
        self.assertEqual(status, "ok")
        self.assertEqual(sess.navigations, ["http://x/login"])
        fills = [v for a, v in calls if a == "fill"]
        self.assertIn("u", fills)
        self.assertIn("p", fills)
        self.assertTrue(any(a == "click" for a, _ in calls))

    async def test_login_missing_creds_fails(self):
        status, _ = await wr._step_login(FakeSession(), {}, {})
        self.assertEqual(status, "failed")

    async def test_login_username_field_missing_fails(self):
        with mock.patch.object(wr, "_do", _nf_do), mock.patch.object(wr, "_wait_ready", _noop_true):
            status, detail = await wr._step_login(
                FakeSession(), {}, {"username": "u", "password": "p"})
        self.assertEqual(status, "failed")
        self.assertIn("username", detail)

    async def test_click_notfound_fails(self):
        with mock.patch.object(wr, "_do", _nf_do), mock.patch.object(wr, "_wait_ready", _noop_true):
            status, _ = await wr._step_click(FakeSession(), {"anchor": {"text": "Nope"}})
        self.assertEqual(status, "failed")

    async def test_fill_resolves_secret(self):
        seen = {}

        async def rec_do(session, anchor, action, value=""):
            seen["value"] = value
            return "OK"

        with mock.patch.object(wr, "_do", rec_do):
            status, _ = await wr._step_fill(
                FakeSession(), {"anchor": {"css": "#u"}, "secret": "username"}, {"username": "joe"})
        self.assertEqual(status, "ok")
        self.assertEqual(seen["value"], "joe")

    async def test_verify_downloaded_detects_new_file(self):
        d = tempfile.mkdtemp()
        before = wr._snapshot(d)
        with open(os.path.join(d, "f.pdf"), "w") as fh:
            fh.write("x")
        status, _ = await wr._step_verify(FakeSession(), {"downloaded": True}, before, d)
        self.assertEqual(status, "ok")

    async def test_verify_downloaded_none_fails(self):
        d = tempfile.mkdtemp()
        before = wr._snapshot(d)
        status, _ = await wr._step_verify(
            FakeSession(), {"downloaded": True, "timeout": 1}, before, d)
        self.assertEqual(status, "failed")

    # --- AIHUB-0067: wait-step timeout floor/ceiling (bug #4 runner defense) ---
    async def test_wait_negative_timeout_floored_to_zero(self):
        seen = {}

        async def fake_sleep(secs):
            seen["secs"] = secs

        with mock.patch.object(wr.asyncio, "sleep", fake_sleep):
            status, detail = await wr._step_wait(FakeSession(), {"type": "wait", "timeout": -1})
        self.assertEqual(status, "ok")
        self.assertEqual(seen["secs"], 0.0)          # negative floored to 0, NOT slept as -1
        self.assertIn("waited 0", detail)            # never "waited -1.0s"

    async def test_wait_ceiling_capped_at_30(self):
        seen = {}

        async def fake_sleep(secs):
            seen["secs"] = secs

        with mock.patch.object(wr.asyncio, "sleep", fake_sleep):
            status, detail = await wr._step_wait(FakeSession(), {"type": "wait", "timeout": 999})
        self.assertEqual(status, "ok")
        self.assertEqual(seen["secs"], 30)           # fixed-sleep ceiling preserved

    async def test_wait_non_numeric_timeout_defaults(self):
        seen = {}

        async def fake_sleep(secs):
            seen["secs"] = secs

        with mock.patch.object(wr.asyncio, "sleep", fake_sleep):
            status, _ = await wr._step_wait(FakeSession(), {"type": "wait", "timeout": "soon"})
        self.assertEqual(status, "ok")
        self.assertEqual(seen["secs"], 10)           # bad value -> default 10, not a crash


class RunWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def _patches(self, download_dir, do=_ok_do, agent=None, heal=None):
        import browser_use
        agent = agent or _make_fake_agent(download_dir)
        ctx = [
            mock.patch.object(browser_use, "BrowserSession", FakeSession),
            mock.patch.object(wr, "_build_llm", lambda m: "LLM"),
            mock.patch.object(wr, "_wait_ready", _noop_true),
            mock.patch.object(wr, "_do", do),
            mock.patch.object(wr, "_close_session", _noop),
            mock.patch.object(wr, "_run_agent", agent),
        ]
        if heal is not None:
            ctx.append(mock.patch.object(wr, "_heal_step", heal))
        return ctx

    async def _run(self, wf, creds, d, **patch_kw):
        patches = self._patches(d, **patch_kw)
        for p in patches:
            p.start()
        try:
            return await wr.run_workflow(wf, creds, d, "claude-x", headless=True, agent_fallback=True)
        finally:
            for p in reversed(patches):
                p.stop()

    async def test_happy_path_downloads_and_harvests(self):
        d = tempfile.mkdtemp()
        wf = {"name": "t", "start_url": "http://x", "goal": "g", "steps": [
            {"type": "goto", "url": "http://x/login"},
            {"type": "login"},
            {"type": "click", "anchor": {"text": "Invoices", "role": "link"}},
            {"type": "agent", "prompt": "download the latest invoice"},
            {"type": "verify", "downloaded": True},
        ]}
        res = await self._run(wf, {"username": "u", "password": "p"}, d)
        self.assertEqual(res["status"], "ok", res)
        self.assertEqual(res["file_count"], 1)
        self.assertTrue(all(s["status"] in ("ok", "healed") for s in res["steps"]))

    async def test_failed_step_triggers_agent_fallback(self):
        d = tempfile.mkdtemp()

        async def heal_fail(session, step, llm, sd, timeout):
            return "failed", "no heal"

        wf = {"name": "t", "goal": "do the whole thing", "steps": [
            {"type": "click", "anchor": {"text": "Nope"}},
        ]}
        res = await self._run(wf, {"username": "u", "password": "p"}, d, do=_nf_do, heal=heal_fail)
        self.assertEqual(res["file_count"], 1)  # fallback agent produced a download
        self.assertIn(res["status"], ("ok", "partial"))
        self.assertTrue(any(s["type"] == "oversight" for s in res["steps"]))

    async def test_agent_oversight_off_disables_recovery(self):
        # Per-workflow setting agent_oversight=False -> a stuck step is NOT handed to the agent.
        d = tempfile.mkdtemp()

        async def heal_fail(session, step, llm, sd, timeout):
            return "failed", "no heal"

        async def agent_must_not_run(session, llm, task, sd, max_steps, timeout):
            raise AssertionError("agent oversight must NOT run when agent_oversight is False")

        wf = {"name": "t", "goal": "do the whole thing", "agent_oversight": False, "steps": [
            {"type": "click", "anchor": {"text": "Nope"}},
        ]}
        res = await self._run(wf, {"username": "u", "password": "p"}, d,
                              do=_nf_do, heal=heal_fail, agent=agent_must_not_run)
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["file_count"], 0)
        self.assertFalse(any(s["type"] == "oversight" for s in res["steps"]))

    async def test_failed_deterministic_step_heals(self):
        d = tempfile.mkdtemp()

        async def heal_ok(session, step, llm, sd, timeout):
            return "healed", "fixed it"

        wf = {"name": "t", "goal": "g", "steps": [
            {"type": "click", "anchor": {"text": "Nope"}},
            {"type": "agent", "prompt": "download"},
        ]}
        res = await self._run(wf, {"username": "u", "password": "p"}, d, do=_nf_do, heal=heal_ok)
        self.assertEqual(res["steps"][0]["status"], "healed")
        self.assertEqual(res["file_count"], 1)

    async def test_no_fallback_when_disabled(self):
        d = tempfile.mkdtemp()

        async def heal_fail(session, step, llm, sd, timeout):
            return "failed", "no heal"

        # agent_fallback handled inside run_workflow; here goal omitted so fallback can't run
        wf = {"name": "t", "steps": [{"type": "click", "anchor": {"text": "Nope"}}]}
        patches = self._patches(d, do=_nf_do, heal=heal_fail)
        for p in patches:
            p.start()
        try:
            res = await wr.run_workflow(wf, {"username": "u", "password": "p"}, d, "claude-x",
                                        headless=True, agent_fallback=True)
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertEqual(res["status"], "error")
        self.assertIsNotNone(res["error"])


class VerifyCodeStepTests(unittest.IsolatedAsyncioTestCase):
    """verify_code: enter via TOTP and CONFIRM the 2FA gate actually cleared; else loop a human
    take-over (re-prompting) until it clears, else fail — never advance blind past a 2FA gate."""

    class _Run:
        run_id = "r"

    class _TOTP:
        def __init__(self, secret): pass
        def now(self): return "654321"

    async def test_totp_clears_gate(self):
        captured = {}

        async def fake_eval(session, js):
            captured["js"] = js
            return '{"found":true,"submitted":true,"mode":"single"}'

        async def cleared(session, timeout=12.0):
            return True

        with mock.patch.object(wr, "_eval", fake_eval), mock.patch("pyotp.TOTP", self._TOTP), \
                mock.patch.object(wr, "_twofa_cleared", cleared):
            status, detail = await wr._step_verify_code(
                FakeSession(), {"type": "verify_code"}, {"totp_secret": "SEED"}, self._Run())
        self.assertEqual(status, "ok")
        self.assertIn("654321", captured["js"])        # live code substituted into the code-entry JS
        self.assertNotIn("__CODE__", captured["js"])

    async def test_totp_entered_but_gate_blocked_then_human_completes(self):
        calls = {"clear": 0, "human": 0}

        async def fake_eval(session, js):
            return '{"found":true,"submitted":true}'

        async def clear(session, timeout=12.0):
            calls["clear"] += 1
            return calls["clear"] > 1                   # auto attempt blocked; human attempt clears

        async def human(run, step):
            calls["human"] += 1
            return "ok", "resumed"

        with mock.patch.object(wr, "_eval", fake_eval), mock.patch("pyotp.TOTP", self._TOTP), \
                mock.patch.object(wr, "_twofa_cleared", clear), mock.patch.object(wr, "_step_human", human):
            status, detail = await wr._step_verify_code(
                FakeSession(), {"type": "verify_code"}, {"totp_secret": "S"}, self._Run())
        self.assertEqual(status, "ok")
        self.assertEqual(calls["human"], 1)            # one human take-over completed it

    async def test_no_totp_human_completes(self):
        called = {}

        async def human(run, step):
            called["h"] = True
            return "ok", "resumed"

        async def clear(session, timeout=12.0):
            return True

        with mock.patch.object(wr, "_step_human", human), mock.patch.object(wr, "_twofa_cleared", clear):
            status, detail = await wr._step_verify_code(
                FakeSession(), {"type": "verify_code", "reason": "2fa"}, {}, self._Run())
        self.assertEqual(status, "ok")
        self.assertTrue(called.get("h"))

    async def test_human_engaged_but_gate_not_cleared_signals_oversight(self):
        # The observed zero-file bug: a take-over handed back WITHOUT submitting 2FA. The operator
        # engaged (status ok) but the gate didn't clear -> signal needs_oversight so the AGENT
        # submits the entered code (one human take-over, no nagging re-prompts).
        calls = {"human": 0}

        async def human(run, step):
            calls["human"] += 1
            return "ok", "resumed"

        async def clear(session, timeout=12.0):
            return False

        with mock.patch.object(wr, "_step_human", human), mock.patch.object(wr, "_twofa_cleared", clear):
            status, detail = await wr._step_verify_code(
                FakeSession(), {"type": "verify_code"}, {}, self._Run())   # oversight defaults ON
        self.assertEqual(status, "needs_oversight")
        self.assertEqual(calls["human"], 1)            # one take-over, then hand to the agent

    async def test_oversight_off_reprompts_human_then_fails(self):
        # OVERSIGHT OFF: no agent — nag the operator to ENTER+SUBMIT the code up to N times, else fail.
        calls = {"human": 0}

        async def human(run, step):
            calls["human"] += 1
            return "ok", "resumed"

        async def clear(session, timeout=12.0):
            return False                               # operator never finishes the gate

        with mock.patch.object(wr, "_step_human", human), mock.patch.object(wr, "_twofa_cleared", clear):
            status, detail = await wr._step_verify_code(
                FakeSession(), {"type": "verify_code"}, {}, self._Run(), oversight=False)
        self.assertEqual(status, "failed")
        self.assertEqual(calls["human"], wr._VERIFY_HUMAN_ATTEMPTS)   # re-prompted each attempt
        self.assertIn("not completed", detail)

    async def test_human_timeout_propagates(self):
        async def human(run, step):
            return "failed", "human step timed out (nobody took over)"

        async def clear(session, timeout=12.0):
            return False

        with mock.patch.object(wr, "_step_human", human), mock.patch.object(wr, "_twofa_cleared", clear):
            status, detail = await wr._step_verify_code(
                FakeSession(), {"type": "verify_code"}, {}, self._Run())
        self.assertEqual(status, "failed")
        self.assertIn("timed out", detail)


if __name__ == "__main__":
    unittest.main()
