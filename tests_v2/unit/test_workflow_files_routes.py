"""Workflow file drops — the Files API (workflow_files_routes.py, 2026-10-10).

Access is decided once, in these routes, for the browser and for The Agent
(API key + the user's signed assertion): Developers on every workflow, anyone
else only where it is shared with them or their group — and then only to
drop, collect, retry, remove a waiting file and run. Under test with a
minimal Flask app: the API key is the test key, the caller identity and the
database lookups are injected.
"""
import io
import json
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

flask = pytest.importorskip("flask")
import role_decorators  # noqa: E402
import workflow_file_drops as wfd  # noqa: E402
import workflow_files_routes as routes  # noqa: E402

WID = 1605
KEY = {"X-API-Key": "TESTKEY"}
WF_DATA = {"nodes": [], "connections": [], "variables": {
    "inputFolder": {"type": "string", "defaultValue": r"C:\old\in"},
    "outputWorkbook": {"type": "string", "defaultValue": r"C:\old\out\CI_Lines.xlsx"}}}


class Forged(Exception):
    pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ROOT", str(tmp_path))
    monkeypatch.delenv("WORKFLOW_FILES_ROOT", raising=False)
    monkeypatch.delenv("WORKFLOW_FILE_DROPS_ENABLED", raising=False)
    monkeypatch.setattr(role_decorators, "_get_api_key_from_request",
                        lambda: flask.request.headers.get("X-API-Key"))
    monkeypatch.setattr(role_decorators, "validate_api_key",
                        lambda k: {"valid": k == "TESTKEY", "permissions": []})
    monkeypatch.setattr(wfd, "_send_my_work", lambda *a, **k: True)

    st = types.SimpleNamespace(who=(None, None), groups=[], saved=[], started=[], active=[],
                               kind="", status={})

    def identity():
        if st.who == "forged":
            raise Forged("bad signature")
        return st.who

    def start_run(wid, initiator, requested_by):
        st.started.append((wid, initiator, requested_by))
        return f"E{len(st.started)}"

    routes.configure(save_workflow=lambda name, data, owner_id=None: st.saved.append((name, data, owner_id)),
                     save_mirror=lambda fn, data: None, caller_identity=identity,
                     invalid_assertion=Forged, start_run=start_run,
                     active_runs=lambda: [{"workflow_id": w} for w in st.active],
                     run_status=lambda eid: st.status.get(eid, {}))
    monkeypatch.setattr(routes, "_group_ids", lambda uid: list(st.groups))
    monkeypatch.setattr(routes, "_user_name", lambda uid: f"user{uid}")
    monkeypatch.setattr(routes, "_load_workflow",
                        lambda wid: ("Acme Lines", dict(json.loads(json.dumps(WF_DATA)), kind=st.kind)))
    monkeypatch.setattr(routes, "_workflow_names", lambda ids: {WID: "Acme Lines"})
    monkeypatch.setattr(routes, "_email_addresses", lambda wid: [])
    monkeypatch.setattr(routes, "_grants_view", lambda g: g)
    monkeypatch.setattr(routes, "_resolve_people",
                        lambda values, kind: ([int(v) for v in values if str(v).isdigit()],
                                              [str(v) for v in values if not str(v).isdigit()]))

    app = flask.Flask(__name__)
    app.secret_key = "t"
    app.register_blueprint(routes.workflow_files_bp)
    st.client = app.test_client()
    return st


def _as(env, uid, role):
    env.who = (uid, role)


def _set_up(env, grants=None, **extra):
    _as(env, 1, 2)
    r = env.client.post(f"/api/workflow-files/{WID}/setup", headers=KEY,
                        json={"variables": {"inbox": "inputFolder", "outbox": "outputWorkbook"}})
    assert r.status_code == 200, r.get_json()
    if grants or extra:
        s = wfd.load_settings(WID)
        s.update(extra)
        if grants:
            s["grants"] = grants
        wfd.save_settings(WID, s)


def _upload(env, *names, query=""):
    data = {"files": [(io.BytesIO(b"%PDF " + n.encode()), n) for n in names]}
    return env.client.post(f"/api/workflow-files/{WID}/upload{query}", headers=KEY, data=data,
                           content_type="multipart/form-data")


# ------------------------------------------------------------------- setup
def test_setup_saves_the_workflow_like_the_designer(env):
    _set_up(env)
    name, data, owner = env.saved[-1]
    assert name == "Acme Lines" and owner == 1
    assert data["variables"]["inputFolder"]["defaultValue"] == wfd.folder_path(WID, "inbox")
    assert data["variables"]["outputWorkbook"]["defaultValue"].endswith(os.path.join("outbox", "CI_Lines.xlsx"))
    s = wfd.load_settings(WID)
    assert s["enabled"] and s["owner"]["user_id"] == 1
    assert s["previous_values"]["inputFolder"] == r"C:\old\in"
    assert all(Path(wfd.folder_path(WID, f)).is_dir() for f in wfd.FOLDERS)


def test_setup_refuses_code_flows_and_bad_mappings_and_regular_users(env):
    env.kind = "code_flow"
    _as(env, 1, 2)
    r = env.client.post(f"/api/workflow-files/{WID}/setup", headers=KEY, json={"variables": {"inbox": "inputFolder"}})
    assert r.status_code == 400 and "code flow" in r.get_json()["message"]
    env.kind = ""
    r = env.client.post(f"/api/workflow-files/{WID}/setup", headers=KEY, json={"variables": {"inbox": "nope"}})
    assert r.status_code == 400 and env.saved == []
    _as(env, 12, 1)
    r = env.client.post(f"/api/workflow-files/{WID}/setup", headers=KEY, json={"variables": {"inbox": "inputFolder"}})
    assert r.status_code == 403


# ------------------------------------------------------------------- access
def test_regular_users_see_only_what_is_shared_with_them(env):
    _set_up(env, grants={"users": [], "groups": [3]})
    _as(env, 12, 1)
    assert env.client.get("/api/workflow-files", headers=KEY).get_json()["workflows"] == []
    for method, path in (("get", ""), ("get", "/runs"), ("post", "/run"),
                         ("get", "/download?folder=outbox&name=x.xlsx")):
        r = getattr(env.client, method)(f"/api/workflow-files/{WID}{path}", headers=KEY, json={})
        assert r.status_code == 404, (path, r.status_code)
    assert _upload(env, "a.pdf").status_code == 404
    env.groups = [3]                                       # now in the group it is shared with
    rows = env.client.get("/api/workflow-files", headers=KEY).get_json()["workflows"]
    assert [w["workflow_id"] for w in rows] == [WID]
    d = env.client.get(f"/api/workflow-files/{WID}", headers=KEY).get_json()
    assert d["set_up"] and not d["can_manage"]
    assert "paths" not in d and "grants" not in d["settings"] and "variables" not in d   # no server internals


def test_regular_users_cannot_manage(env):
    _set_up(env, grants={"users": [12], "groups": []})
    _as(env, 12, 1)
    assert env.client.put(f"/api/workflow-files/{WID}/settings", headers=KEY,
                          json={"run_on_upload": True}).status_code == 403
    assert env.client.put(f"/api/workflow-files/{WID}/access", headers=KEY,
                          json={"add_users": [13]}).status_code == 403
    Path(wfd.folder_path(WID, "outbox"), "r.xlsx").write_bytes(b"r")
    r = env.client.post(f"/api/workflow-files/{WID}/delete", headers=KEY, json={"folder": "outbox", "names": ["r.xlsx"]})
    assert r.status_code == 403
    _upload(env, "wrong.pdf")
    r = env.client.post(f"/api/workflow-files/{WID}/delete", headers=KEY, json={"folder": "inbox", "names": ["wrong.pdf"]})
    assert r.status_code == 200 and r.get_json()["deleted"] == ["wrong.pdf"]


def test_forged_identity_is_refused_and_service_calls_are_trusted(env):
    _set_up(env)
    env.who = "forged"
    assert env.client.get(f"/api/workflow-files/{WID}", headers=KEY).status_code == 403
    env.who = (None, None)                                  # API key, no user: a platform service
    d = env.client.get(f"/api/workflow-files/{WID}", headers=KEY).get_json()
    assert d["can_manage"]
    assert env.client.get(f"/api/workflow-files/{WID}").status_code == 401   # no key, no session


def test_switched_off_answers_json_404(env, monkeypatch):
    _set_up(env)
    monkeypatch.setenv("WORKFLOW_FILE_DROPS_ENABLED", "false")
    r = env.client.get("/api/workflow-files", headers=KEY)
    assert r.status_code == 404 and r.get_json()["disabled"]


# ----------------------------------------------------------- files and runs
def test_upload_download_and_traversal(env):
    _set_up(env, grants={"users": [12], "groups": []})
    _as(env, 12, 1)
    r = _upload(env, "inv 1.pdf", "inv 1.pdf")
    assert r.status_code == 200
    assert [s["name"] for s in r.get_json()["stored"]] == ["inv 1.pdf", "inv 1 (2).pdf"]
    assert "run" not in r.get_json()
    assert wfd.uploads(WID)["inv 1.pdf"]["user_name"] == "user12"
    r = env.client.get(f"/api/workflow-files/{WID}/download?folder=inbox&name=inv%201.pdf", headers=KEY)
    assert r.status_code == 200 and r.data.startswith(b"%PDF")
    r = env.client.get(f"/api/workflow-files/{WID}/download?folder=inbox&name=../../settings.json", headers=KEY)
    assert r.status_code == 400
    r = env.client.get(f"/api/workflow-files/{WID}/download?folder=secrets&name=x", headers=KEY)
    assert r.status_code == 400


def test_raw_upload_like_the_agent(env):
    _set_up(env)
    r = env.client.post(f"/api/workflow-files/{WID}/upload?run=0", headers=dict(KEY, **{"X-File-Name": "r%C3%A9sum%C3%A9.pdf"}),
                        data=b"%PDF raw", content_type="application/octet-stream")
    assert r.status_code == 200 and r.get_json()["stored"][0]["name"] == "résumé.pdf"
    assert Path(wfd.folder_path(WID, "inbox"), "résumé.pdf").read_bytes() == b"%PDF raw"


def test_run_after_upload_and_the_setting(env):
    _set_up(env, grants={"users": [12], "groups": []})
    _as(env, 12, 1)
    r = _upload(env, "a.pdf", query="?run=1")
    assert r.get_json()["run"] == {"execution_id": "E1"}
    wid, initiator, req = env.started[-1]
    assert (wid, initiator) == (WID, "files:user12")
    assert req == {"user_id": 12, "name": "user12", "via": "upload", "notify": True}
    s = wfd.load_settings(WID)
    wfd.save_settings(WID, dict(s, run_on_upload=True))
    _upload(env, "b.pdf")
    assert len(env.started) == 2                           # the setting starts one
    _upload(env, "c.pdf", query="?run=0")
    assert len(env.started) == 2                           # ...unless the caller says not to


def test_a_second_run_waits_for_the_first(env):
    _set_up(env, grants={"users": [12], "groups": []})
    _as(env, 12, 1)
    env.active = [WID]
    r = env.client.post(f"/api/workflow-files/{WID}/run", headers=KEY, json={"notify": False, "via": "the_agent"})
    body = r.get_json()
    assert body["queued"] and env.started == []
    queued = json.loads(Path(wfd._state_dir(WID, "run_next.json")).read_text())
    assert queued["requested_by"]["notify"] is True       # nobody waits on a queued run


def test_run_status_and_late_notice(env):
    _set_up(env, grants={"users": [12, 13], "groups": []})
    _as(env, 12, 1)
    r = env.client.post(f"/api/workflow-files/{WID}/run", headers=KEY, json={"notify": False, "via": "the_agent"})
    eid = r.get_json()["execution_id"]
    assert env.started[-1][2]["notify"] is False
    snap = wfd.run_started(WID, eid, "Acme Lines", "files:user12", env.started[-1][2])
    env.status[eid] = {"status": "success", "workflow_status": "Running"}
    d = env.client.get(f"/api/workflow-files/{WID}/runs/{eid}", headers=KEY).get_json()
    assert d["run_status"] == "Running" and not d["finished"]
    _as(env, 13, 1)                                        # someone else may not ask for my notice
    assert env.client.post(f"/api/workflow-files/{WID}/runs/{eid}/notify", headers=KEY).status_code == 403
    _as(env, 12, 1)
    r = env.client.post(f"/api/workflow-files/{WID}/runs/{eid}/notify", headers=KEY)
    assert r.get_json()["result"] == "queued"
    wfd.run_finished(snap, "Completed")
    env.status[eid] = {"status": "success", "workflow_status": "Completed"}
    d = env.client.get(f"/api/workflow-files/{WID}/runs/{eid}", headers=KEY).get_json()
    assert d["finished"] and d["run"]["status"] == "Completed" and d["run"]["started_by"] == "user12"


def test_retry(env):
    _set_up(env, grants={"users": [12], "groups": []})
    _as(env, 12, 1)
    Path(wfd.folder_path(WID, "failed"), "bad.pdf").write_bytes(b"x")
    r = env.client.post(f"/api/workflow-files/{WID}/retry", headers=KEY, json={"names": ["bad.pdf", "nope.pdf"], "run": True})
    body = r.get_json()
    assert body["moved"] == [{"from": "bad.pdf", "to": "bad.pdf"}] and "nope.pdf: not in Failed" in body["errors"]
    assert body["run"] == {"execution_id": "E1"} and env.started[-1][2]["via"] == "retry"
    assert Path(wfd.folder_path(WID, "inbox"), "bad.pdf").exists()


def test_settings_and_sharing(env):
    _set_up(env)
    r = env.client.put(f"/api/workflow-files/{WID}/settings", headers=KEY, json={
        "run_on_upload": True, "sharepoint": {"integration_id": "5", "drive_id": "D1", "folder_path": "/Reports/CI/"}})
    s = r.get_json()["settings"]
    assert s["run_on_upload"] and s["sharepoint"]["folder_path"] == "Reports/CI"
    assert s["sharepoint"]["conflict_behavior"] == "replace" and s["sharepoint"]["integration_id"] == 5
    r = env.client.put(f"/api/workflow-files/{WID}/settings", headers=KEY,
                       json={"sharepoint": {"integration_id": 5, "drive_id": "D1", "conflict_behavior": "merge"}})
    assert r.status_code == 400
    r = env.client.put(f"/api/workflow-files/{WID}/access", headers=KEY, json={"add_users": [12, 13], "add_groups": [3]})
    assert r.get_json()["shared_with"] == {"users": [12, 13], "groups": [3]}
    r = env.client.put(f"/api/workflow-files/{WID}/access", headers=KEY, json={"remove_users": [13]})
    assert r.get_json()["shared_with"] == {"users": [12], "groups": [3]}
    r = env.client.put(f"/api/workflow-files/{WID}/access", headers=KEY, json={"add_users": ["ghost"]})
    assert r.status_code == 400 and r.get_json()["unknown"] == ["ghost"]
    assert wfd.load_settings(WID)["grants"] == {"users": [12], "groups": [3]}     # nothing changed


def test_turned_off_workflow_is_gone_for_users_but_kept(env):
    _set_up(env, grants={"users": [12], "groups": []})
    _upload(env, "keep.pdf")
    env.client.put(f"/api/workflow-files/{WID}/settings", headers=KEY, json={"enabled": False})
    _as(env, 12, 1)
    assert env.client.get(f"/api/workflow-files/{WID}", headers=KEY).status_code == 404
    assert Path(wfd.folder_path(WID, "inbox"), "keep.pdf").exists()


# ----------------------------------------------------------- the wiring
APP = (ROOT / "app.py").read_text(encoding="utf-8", errors="replace")


def test_app_registers_and_feeds_the_blueprint():
    assert "app.register_blueprint(workflow_files_bp)" in APP
    for hook in ("save_workflow=save_workflow_to_database", "caller_identity=_caller_identity_or_session",
                 "invalid_assertion=_InvalidUserAssertion", "start_run=_workflow_files_start_run"):
        assert hook in APP, hook


def test_generic_run_route_names_the_person_behind_the_run():
    start = APP.index("@app.route('/api/workflow/run'")
    block = APP[start:APP.find("@app.route(", start + 10)]
    assert "requester_for_run(initiator" in block
    assert block.count("requested_by=requested_by or None") == 2      # executor service and local engine


def test_engine_snapshots_and_hands_off():
    src = (ROOT / "workflow_execution.py").read_text(encoding="utf-8")
    thread = src[src.index("def _execute_workflow_thread"):src.index("def _execute_node(")]
    assert "workflow_file_drops.run_started(" in thread
    fin = thread[thread.index("finally:"):]
    assert fin.index("_cleanup_execution_resources") < fin.index("run_finished_async")
    assert "requested_by=requested_by" in (ROOT / "app_executor_service.py").read_text(encoding="utf-8")
