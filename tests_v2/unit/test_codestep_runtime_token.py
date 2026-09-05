"""
Code Flow step run token — aihub.send_email() (and its sibling run-token
verbs) from an inline Code Flow step. docs/handoff-codeflow-send-email-403.md

The defect: a Code Flow step runs WITHOUT an AutomationRuns row (the table's
FK to Automations makes one impossible for an ephemeral step), every run-token
endpoint required exactly that row, the SDK swallowed the resulting 403, and
the walk summary hid stdout on success — so "build the workbook and email it
to me" reported ✓ success and delivered nothing, every time.

The fix, in four parts, each pinned here:
  1. the runner registers the step IN ITS TOKEN ({kind: codestep, workdir,
     name, user_id}) and the endpoints prove liveness from the step's
     heartbeat through ONE chokepoint (automations.api._live_run_from_token);
  2. the walk summary surfaces a stdout tail for EVERY step;
  3. the chat-lane doctrine no longer advertises verbs that cannot work there;
  4. aihub.send_email() RAISES on a 4xx / missing token / chat lane, and still
     returns False on an operational failure (5xx, transport, provider).

Real subprocess execution for the end-to-end cases (sys.executable), DB-free.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import threading
import time
import types
import urllib.error

import pytest

from automations.runner import AutomationRunner
import automations.runner as runner_mod

pytestmark = pytest.mark.unit

_SECRET = "codestep-token-test-secret"


class _CfgStub:
    AUTOMATIONS_ENV_CRED_INJECTION = False


@pytest.fixture(autouse=True)
def _stub_cfg(monkeypatch):
    monkeypatch.setattr(runner_mod, "_load_cfg", lambda: _CfgStub)
    monkeypatch.setenv("CC_JWT_SECRET", _SECRET)
    # keep the runner off CommonUtils.get_base_url in the unit env
    monkeypatch.setenv("AUTOMATIONS_RUNTIME_URL", "http://127.0.0.1:9")


def _runner(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_mod, "get_app_path",
                        lambda *parts: os.path.join(str(tmp_path), *parts))
    r = AutomationRunner.__new__(AutomationRunner)
    r.manager = None
    r.tenant_id = "cstoken"
    r.connection_string = "stub"
    r._resolve_python = lambda env_id: sys.executable
    r._resolve_connection = lambda n: None
    r._resolve_secret = lambda n: None
    return r


def _load_sdk():
    path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..",
        "automations", "sdk", "aihub_runtime", "__init__.py"))
    spec = importlib.util.spec_from_file_location("_aihub_runtime_cst", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _codestep_token(workdir, name="email-workbook", user_id=13, connections=(), run_id="run-cs"):
    from shared_auth import sign_automation_run_token
    return sign_automation_run_token(
        f"codestep-{run_id}", run_id, list(connections), [], 300,
        extra={"kind": "codestep", "workdir": str(workdir), "name": name, "user_id": user_id})


def _touch_heartbeat(workdir, age_seconds=0.0):
    hb = os.path.join(str(workdir), "_heartbeat")
    with open(hb, "w", encoding="utf-8") as f:
        f.write(str(time.time()))
    if age_seconds:
        t = time.time() - age_seconds
        os.utime(hb, (t, t))
    return hb


class _FakeNotify(types.ModuleType):
    """Stand-in for notification_client — records the send, reports success."""
    def __init__(self):
        super().__init__("notification_client")
        self.calls = []

    def send_email_notification(self, **kw):
        self.calls.append(kw)
        return {"success": True}


def _api_app(monkeypatch, notify=None, runner=None):
    """Flask app carrying the automations blueprint, wired the way the
    existing runtime endpoint tests wire it (test_automations)."""
    from flask import Flask
    import automations.api as api_mod
    monkeypatch.setattr(api_mod.cfg, "AUTOMATIONS_ENABLED", True, raising=False)
    monkeypatch.setattr(api_mod, "_runner", runner or _NoRowRunner())
    monkeypatch.setattr(api_mod, "_manager", _NoManager())
    if notify is not None:
        monkeypatch.setitem(sys.modules, "notification_client", notify)
    app = Flask(__name__)
    app.register_blueprint(api_mod.automations_bp)
    return app


class _NoRowRunner:
    """A runner with NO run rows (the code-flow reality) and a resolvable
    connection — the automation flavor must still need the row."""
    def __init__(self, rows=None):
        self.rows = rows or {}

    def get_run(self, run_id):
        return self.rows.get(run_id)

    def _resolve_connection(self, name):
        return "Driver={X};Server=t;PWD=resolved;" if name == "ERPDB" else None

    def _resolve_secret(self, name):
        return None


class _NoManager:
    def get_automation(self, aid):
        return None


# ═══════════════════════════ 1. the runner registers the step in its token ═══
class TestRunnerMintsCodestepToken:
    def test_token_carries_kind_workdir_name_user(self, tmp_path, monkeypatch):
        r = _runner(tmp_path, monkeypatch)
        captured = {}

        def _fake_supervise(cmd, workdir, env, timeout, run_id, events):
            captured["env"] = dict(env)
            return 0, "", "", False, False

        monkeypatch.setattr(r, "_supervise", _fake_supervise)
        wd = str(tmp_path / "r1")
        r.run_code_step("print(1)\n", {"timeout_seconds": 30}, "email-workbook",
                        workdir=wd, requested_by=13)
        tok = captured["env"].get("AIHUB_RUN_TOKEN")
        assert tok, "a code step must always get a run token"
        assert captured["env"].get("AIHUB_RUNTIME_URL") == "http://127.0.0.1:9"
        from shared_auth import verify_automation_run_token
        claims, err = verify_automation_run_token(tok)
        assert err is None, err
        assert claims["kind"] == "codestep"
        assert os.path.realpath(claims["workdir"]) == os.path.realpath(wd)
        assert claims["name"] == "email-workbook"
        assert claims["user_id"] == 13
        assert claims["automation_id"].startswith("codestep-")
        # the SDK still auto-approves gates in a step (documented behaviour)
        assert captured["env"].get("AIHUB_CHECKPOINTS_ENABLED") == "0"

    def test_extra_claims_cannot_override_reserved_ones(self):
        from shared_auth import sign_automation_run_token, verify_automation_run_token
        tok = sign_automation_run_token(
            "auto-1", "run-1", ["ERPDB"], [], 300,
            extra={"automation_id": "auto-EVIL", "connections": ["ALL"], "aud": "x",
                   "kind": "codestep"})
        claims, err = verify_automation_run_token(tok)
        assert err is None
        assert claims["automation_id"] == "auto-1" and claims["connections"] == ["ERPDB"]
        assert claims["kind"] == "codestep"


# ═══════════════════════════ 1b. the API chokepoint: heartbeat liveness ══════
class TestLiveRunChokepoint:
    def test_fresh_heartbeat_is_a_live_codestep_run(self, tmp_path, monkeypatch):
        import automations.api as api_mod
        wd = tmp_path / "step"
        wd.mkdir()
        _touch_heartbeat(wd)
        app = _api_app(monkeypatch)
        with app.app_context():
            run, claims, fail = api_mod._live_run_from_token(_codestep_token(wd))
        assert fail is None
        assert run["codestep"] is True and run["status"] == "running"
        assert run["name"] == "email-workbook" and run["requested_by"] == 13
        assert api_mod._run_workdir(run) == os.path.join(os.path.realpath(str(wd)))
        assert api_mod._run_display_name(run) == "email-workbook"

    @pytest.mark.parametrize("case", ["stale", "absent", "no_workdir"])
    def test_dead_step_is_refused(self, tmp_path, monkeypatch, case):
        import automations.api as api_mod
        wd = tmp_path / "step"
        wd.mkdir()
        if case == "stale":
            _touch_heartbeat(wd, age_seconds=api_mod._CODESTEP_LIVE_SECONDS + 60)
        elif case == "no_workdir":
            wd = tmp_path / "gone"
        app = _api_app(monkeypatch)
        with app.app_context():
            run, claims, fail = api_mod._live_run_from_token(_codestep_token(wd))
        assert run is None and fail is not None
        resp, status = fail
        assert status == 403
        assert "does not match a live run" in resp.get_json()["error"]

    def test_codestep_refused_where_a_supervised_run_is_required(self, tmp_path, monkeypatch):
        import automations.api as api_mod
        wd = tmp_path / "step"
        wd.mkdir()
        _touch_heartbeat(wd)
        app = _api_app(monkeypatch)
        with app.app_context():
            run, claims, fail = api_mod._live_run_from_token(_codestep_token(wd),
                                                             allow_codestep=False)
        assert run is None
        resp, status = fail
        assert status == 403
        assert "Code Flow step" in resp.get_json()["error"]

    def test_automation_flavor_unchanged(self, tmp_path, monkeypatch):
        """A promoted run still needs its live DB row — the code-step path
        must not have loosened the original contract."""
        import automations.api as api_mod
        from shared_auth import sign_automation_run_token
        wd = tmp_path / "auto"
        wd.mkdir()
        live = {"run-a": {"run_id": "run-a", "automation_id": "auto-a", "status": "running",
                          "log_path": str(wd / "run.log"), "requested_by": 7}}
        app = _api_app(monkeypatch, runner=_NoRowRunner(live))
        tok = sign_automation_run_token("auto-a", "run-a", [], [], 300)
        with app.app_context():
            run, _c, fail = api_mod._live_run_from_token(tok)
            assert fail is None and run["run_id"] == "run-a" and not run.get("codestep")
            # no row → refused, even with a fresh heartbeat lying around
            _touch_heartbeat(wd)
            tok2 = sign_automation_run_token("auto-b", "run-b", [], [], 300)
            run2, _c, fail2 = api_mod._live_run_from_token(tok2)
        assert run2 is None and fail2[1] == 403
        # and a bad signature is still a bad signature
        with app.app_context():
            _r, _c, fail3 = api_mod._live_run_from_token("not.a.token")
        assert fail3[1] == 403 and "invalid run token" in fail3[0].get_json()["error"]


# ═══════════════════════════ 1c. the endpoints, both flavors ═════════════════
class TestRuntimeEndpointsAcceptCodestep:
    def test_notify_email_sends_for_a_live_code_step(self, tmp_path, monkeypatch):
        """THE regression: this call 403'd for every code-flow step."""
        wd = tmp_path / "step"
        wd.mkdir()
        (wd / "past_due_invoice_aging.xlsx").write_bytes(b"PK\x03\x04 workbook")
        _touch_heartbeat(wd)
        notify = _FakeNotify()
        client = _api_app(monkeypatch, notify=notify).test_client()
        r = client.post("/automations/api/runtime/notify_email", json={
            "token": _codestep_token(wd), "to": "james@example.com",
            "subject": "Past-due aging", "body": "attached",
            "files": ["past_due_invoice_aging.xlsx"]})
        body = r.get_json()
        assert r.status_code == 200, body
        assert body["sent"] is True and body["attachments"] == 1
        assert len(notify.calls) == 1
        call = notify.calls[0]
        assert call["to"] == ["james@example.com"]
        assert call["agent_name"] == "code-flow-step:email-workbook"
        assert call["attachments"][0]["filename"] == "past_due_invoice_aging.xlsx"

    def test_notify_email_refuses_a_finished_code_step(self, tmp_path, monkeypatch):
        import automations.api as api_mod
        wd = tmp_path / "step"
        wd.mkdir()
        _touch_heartbeat(wd, age_seconds=api_mod._CODESTEP_LIVE_SECONDS + 60)
        notify = _FakeNotify()
        client = _api_app(monkeypatch, notify=notify).test_client()
        r = client.post("/automations/api/runtime/notify_email", json={
            "token": _codestep_token(wd), "to": "james@example.com", "subject": "x"})
        assert r.status_code == 403 and notify.calls == []

    def test_ai_passes_the_gate_for_a_code_step(self, tmp_path, monkeypatch):
        import automations.api as api_mod
        wd = tmp_path / "step"
        wd.mkdir()
        _touch_heartbeat(wd)
        monkeypatch.setattr(api_mod.cfg, "ANTHROPIC_API_KEY", "k-test", raising=False)

        class FakeResp:
            status_code = 200
            def json(self):
                return {"content": [{"type": "text", "text": "two sentences"}]}

        import requests as _requests
        monkeypatch.setattr(_requests, "post", lambda *a, **k: FakeResp())
        client = _api_app(monkeypatch).test_client()
        r = client.post("/automations/api/runtime/ai",
                        json={"token": _codestep_token(wd), "prompt": "Summarize."})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["text"] == "two sentences"

    def test_review_items_status_passes_the_gate(self, tmp_path, monkeypatch):
        wd = tmp_path / "step"
        wd.mkdir()
        _touch_heartbeat(wd)
        client = _api_app(monkeypatch).test_client()
        # past the token gate → the body validation answers (400), not a 403
        r = client.post("/automations/api/runtime/review_items_status",
                        json={"token": _codestep_token(wd), "request_ids": []})
        assert r.status_code == 400 and "request_ids" in r.get_json()["error"]

    def test_resolve_accepts_a_code_step_token(self, tmp_path, monkeypatch):
        import automations.api as api_mod
        wd = tmp_path / "step"
        wd.mkdir()
        _touch_heartbeat(wd)
        client = _api_app(monkeypatch).test_client()
        tok = _codestep_token(wd, connections=["ERPDB"])
        r = client.post("/automations/api/runtime/resolve",
                        json={"token": tok, "kind": "connection", "name": "ERPDB"})
        assert r.status_code == 200 and "PWD=resolved" in r.get_json()["value"]
        # allowlist still enforced
        r = client.post("/automations/api/runtime/resolve",
                        json={"token": tok, "kind": "connection", "name": "OTHER"})
        assert r.status_code == 403
        # and a dead step cannot resolve anything
        _touch_heartbeat(wd, age_seconds=api_mod._CODESTEP_LIVE_SECONDS + 60)
        r = client.post("/automations/api/runtime/resolve",
                        json={"token": tok, "kind": "connection", "name": "ERPDB"})
        assert r.status_code == 403

    @pytest.mark.parametrize("path", ["/automations/api/runtime/checkpoint",
                                      "/automations/api/runtime/review_item"])
    def test_human_gates_refuse_a_code_step_with_a_reason(self, tmp_path, monkeypatch, path):
        wd = tmp_path / "step"
        wd.mkdir()
        _touch_heartbeat(wd)
        client = _api_app(monkeypatch).test_client()
        r = client.post(path, json={"token": _codestep_token(wd), "message": "gate?"})
        assert r.status_code == 403
        assert "Code Flow step" in r.get_json()["error"]


# ═══════════════════════════ 4. SDK send_email error taxonomy ════════════════
def _http_error(code, error_text):
    return urllib.error.HTTPError("http://x/notify_email", code, "msg", {},
                                  io.BytesIO(json.dumps({"error": error_text}).encode()))


class TestSdkSendEmailSemantics:
    def _sdk(self, monkeypatch, token="hdr.eyJhdWQiOiAiYXV0b21hdGlvbi1ydW4ifQ.sig"):
        sdk = _load_sdk()
        monkeypatch.setenv("AIHUB_RUN_TOKEN", token)
        monkeypatch.setenv("AIHUB_RUNTIME_URL", "http://127.0.0.1:9")
        return sdk

    def test_4xx_raises_instead_of_returning_false(self, monkeypatch):
        """The exact live symptom: HTTP 403 used to log-and-return False."""
        sdk = self._sdk(monkeypatch)
        monkeypatch.setattr(sdk, "_runtime_post",
                            lambda *a, **k: (_ for _ in ()).throw(
                                _http_error(403, "run token does not match a live run")))
        with pytest.raises(sdk.AutomationRuntimeError) as ei:
            sdk.send_email("a@b.co", "s", "b")
        msg = str(ei.value)
        assert "HTTP 403" in msg and "does not match a live run" in msg
        assert "not a mail outage" in msg

    def test_400_raises_too(self, monkeypatch):
        sdk = self._sdk(monkeypatch)
        monkeypatch.setattr(sdk, "_runtime_post",
                            lambda *a, **k: (_ for _ in ()).throw(
                                _http_error(400, "attachment 'x' does not exist")))
        with pytest.raises(sdk.AutomationRuntimeError) as ei:
            sdk.send_email("a@b.co", "s", "b", files=["x"])
        assert "HTTP 400" in str(ei.value) and "does not exist" in str(ei.value)

    def test_5xx_and_transport_errors_are_reported_not_fatal(self, monkeypatch, capsys):
        sdk = self._sdk(monkeypatch)
        monkeypatch.setattr(sdk, "_runtime_post",
                            lambda *a, **k: (_ for _ in ()).throw(_http_error(502, "smtp down")))
        assert sdk.send_email("a@b.co", "s", "b") is False
        monkeypatch.setattr(sdk, "_runtime_post",
                            lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("refused")))
        assert sdk.send_email("a@b.co", "s", "b") is False
        out = capsys.readouterr().out
        assert "HTTP 502" in out and "smtp down" in out and "refused" in out

    def test_platform_reported_delivery_failure_is_false(self, monkeypatch):
        sdk = self._sdk(monkeypatch)
        monkeypatch.setattr(sdk, "_runtime_post",
                            lambda *a, **k: {"sent": False, "error": "provider rejected"})
        assert sdk.send_email("a@b.co", "s", "b") is False
        monkeypatch.setattr(sdk, "_runtime_post", lambda *a, **k: {"sent": True})
        assert sdk.send_email("a@b.co; c@d.co", "s", "b") is True

    def test_no_token_raises(self, monkeypatch):
        sdk = _load_sdk()
        monkeypatch.delenv("AIHUB_RUN_TOKEN", raising=False)
        with pytest.raises(sdk.AutomationRuntimeError) as ei:
            sdk.send_email("a@b.co", "s", "b")
        assert "AIHUB_RUN_TOKEN" in str(ei.value)

    def test_no_recipients_is_still_a_soft_false(self, monkeypatch):
        sdk = self._sdk(monkeypatch)
        assert sdk.send_email("", "s", "b") is False

    def test_chat_lane_token_gets_a_plain_answer(self, monkeypatch):
        """A run_python token (audience code-interpreter-run) can never satisfy
        the endpoint; say so at the call instead of 'HTTP 403 wrong audience'."""
        from shared_auth import sign_code_run_token
        tok = sign_code_run_token("general-agent", "run-chat", [], [], 300, user_id=1)
        sdk = self._sdk(monkeypatch, token=tok)
        called = {"n": 0}
        monkeypatch.setattr(sdk, "_runtime_post",
                            lambda *a, **k: called.__setitem__("n", called["n"] + 1))
        with pytest.raises(sdk.AutomationRuntimeError) as ei:
            sdk.send_email("a@b.co", "s", "b")
        assert "chat run_python" in str(ei.value) and called["n"] == 0
        with pytest.raises(sdk.AutomationRuntimeError) as ei:
            sdk.llm("hello")
        assert "chat run_python" in str(ei.value)
        monkeypatch.delenv("AIHUB_CHECKPOINTS_ENABLED", raising=False)
        with pytest.raises(sdk.AutomationRuntimeError) as ei:
            sdk.checkpoint("gate")
        assert "chat run_python" in str(ei.value)
        assert sdk.review_item("x") is None and called["n"] == 0

    def test_help_tells_the_truth_per_context(self, monkeypatch, capsys):
        from shared_auth import sign_code_run_token
        sdk = self._sdk(monkeypatch, token=sign_code_run_token("cc", "r", [], [], 300))
        sdk.help()
        assert "CHAT run_python" in capsys.readouterr().out
        sdk = self._sdk(monkeypatch)
        monkeypatch.setenv("AIHUB_CHECKPOINTS_ENABLED", "0")
        sdk.help()
        out = capsys.readouterr().out
        assert "CODE FLOW STEP" in out and "send_email / llm / ai_extract / query work" in out


# ═══════════════════════════ 2. walk summaries show stdout on success ════════
_WALK = {"status": "success", "steps": [
    {"status": "success", "name": "email-workbook", "exit_code": 0, "output_files": [],
     "stdout_tail": "[aihub] Staged workbook\n[aihub] email could not be sent (continuing): "
                    "HTTP Error 403: FORBIDDEN\n[aihub] Email send result: False\n"},
]}


class TestWalkSummariesSurfaceStdout:
    def test_cc_summarize_walk(self):
        path = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "..",
            "command_center_service", "graph", "codeflow_tools.py"))
        spec = importlib.util.spec_from_file_location("_cc_codeflow_tools_cst", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out = mod.summarize_walk(_WALK)
        assert "✓ step 1" in out
        assert "stdout:" in out and "Email send result: False" in out
        assert "HTTP Error 403" in out
        # empty stdout adds no line
        assert "stdout:" not in mod.summarize_walk(
            {"status": "success", "steps": [{"status": "success", "name": "a", "exit_code": 0}]})

    def test_agent_summarize_walk(self):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        sys.path.insert(0, os.path.join(root, "agent_service"))
        try:
            import authoring_tools as A  # needs claude_agent_sdk (The Agent's env)
        except ImportError as e:
            pytest.skip(f"needs the aihub-agent env: {e}")
        finally:
            sys.path.pop(0)
        out = A._summarize_walk(_WALK)
        assert "✓ step 1" in out and "stdout:" in out and "Email send result: False" in out


# ═══════════════════════════ 3. doctrine honesty per surface ═════════════════
class TestDoctrine:
    def test_chat_lane_clause_no_longer_advertises_platform_run_verbs(self):
        from code_exec.doctrine import SDK_CLAUSE, RUN_PYTHON_DOCTRINE_GA
        assert "NOT available from run_python" in SDK_CLAUSE
        for verb in ("send_email", "checkpoint", "llm"):
            assert verb in SDK_CLAUSE  # named, as things that RAISE here
        assert "aihub.query" in SDK_CLAUSE
        assert "NOT available from run_python" in RUN_PYTHON_DOCTRINE_GA


# ═══════════════════════════ END-TO-END: real subprocess ═════════════════════
def _serve(app):
    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", 0, app)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


_EMAIL_STEP = (
    "import aihub_runtime as aihub\n"
    "with open('past_due_invoice_aging.xlsx', 'wb') as f:\n"
    "    f.write(b'PK workbook')\n"
    "ok = aihub.send_email('james@example.com', 'Past-due aging', 'attached',\n"
    "                      files=['past_due_invoice_aging.xlsx'])\n"
    "print('email_sent=%s' % ok)\n"
)


class TestEndToEnd:
    def test_step_emails_through_the_real_endpoint(self, tmp_path, monkeypatch):
        """runner → subprocess → SDK → HTTP → Flask notify_email → sent.
        The token the runner mints must satisfy the endpoint's liveness
        check while the step is running."""
        notify = _FakeNotify()
        app = _api_app(monkeypatch, notify=notify)
        srv, url = _serve(app)
        try:
            monkeypatch.setenv("AUTOMATIONS_RUNTIME_URL", url)
            r = _runner(tmp_path, monkeypatch)
            res = r.run_code_step(_EMAIL_STEP, {"timeout_seconds": 60}, "email-workbook",
                                  workdir=str(tmp_path / "e2e"), requested_by=13)
        finally:
            srv.shutdown()
        assert res["status"] == "success", res
        assert "email_sent=True" in res["stdout_tail"], res["stdout_tail"]
        assert len(notify.calls) == 1
        assert notify.calls[0]["agent_name"] == "code-flow-step:email-workbook"
        assert notify.calls[0]["attachments"][0]["filename"] == "past_due_invoice_aging.xlsx"

    def test_platform_rejection_fails_the_step_instead_of_passing(self, tmp_path, monkeypatch):
        """The observed run, replayed: the platform answers 403. Before the
        fix: exit 0, '✓ success', workbook never sent. Now the step FAILS
        and the stderr carries the reason."""
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class Deny(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = json.dumps({"error": "run token does not match a live run"}).encode()
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):  # quiet
                pass

        httpd = HTTPServer(("127.0.0.1", 0), Deny)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            monkeypatch.setenv("AUTOMATIONS_RUNTIME_URL", f"http://127.0.0.1:{httpd.server_port}")
            r = _runner(tmp_path, monkeypatch)
            res = r.run_code_step(_EMAIL_STEP, {"timeout_seconds": 60}, "email-workbook",
                                  workdir=str(tmp_path / "deny"))
        finally:
            httpd.shutdown()
        assert res["status"] == "failed", res
        assert res["exit_code"] not in (0, None)
        assert "email rejected by the platform (HTTP 403" in res["stderr_tail"], res["stderr_tail"]
        assert "email_sent=" not in res["stdout_tail"]
