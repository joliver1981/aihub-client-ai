"""Workflow file drops — the folders, settings and run bookkeeping
(workflow_file_drops.py, 2026-10-10).

People drop files into a workflow's managed inbox from a browser, the
workflow runs on the server, and what it produced comes back to them. Under
test: safe names and folder containment, uploads that appear whole and never
overwrite, the variable setup (an output workbook keeps its name), access,
what a run took and produced, who hears about it, the late-notice handshake
with The Agent, the queued next run, email attachments and the SharePoint copy.
"""
import io
import json
import os
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import workflow_file_drops as wfd  # noqa: E402

WID = 1605


@pytest.fixture
def box(tmp_path, monkeypatch):
    app_root = tmp_path / "app"
    app_root.mkdir()
    monkeypatch.setenv("APP_ROOT", str(app_root))
    monkeypatch.delenv("WORKFLOW_FILES_ROOT", raising=False)
    monkeypatch.delenv("WORKFLOW_FILE_DROPS_ENABLED", raising=False)
    sent = []
    monkeypatch.setattr(wfd, "_send_my_work",
                        lambda uid, title, summary, files, payload, priority:
                        sent.append({"uid": str(uid), "title": title, "summary": summary,
                                     "files": files, "priority": priority}) or True)
    monkeypatch.setattr(wfd, "_email_user_ids", lambda emails: {})
    return types.SimpleNamespace(root=app_root, sent=sent)


def _set_up(owner=1, **extra):
    s = wfd.default_settings({"user_id": owner, "name": "owner"})
    s.update(extra)
    wfd.ensure_folders(WID)
    return wfd.save_settings(WID, s)


def _drop(name, data=b"x", user=12, uname="ru_drew"):
    return wfd.store_bytes(WID, name, data, {"user_id": user, "name": uname})


# ------------------------------------------------------------ names, folders
def test_folders_live_under_app_root_and_state_elsewhere(box):
    paths = wfd.ensure_folders(WID)
    assert set(paths) == {"inbox", "processed", "failed", "outbox"}
    for p in paths.values():
        assert Path(p).is_dir() and Path(p).is_relative_to(box.root / "data" / "workflow_files" / str(WID))
    assert not Path(wfd.state_root()).is_relative_to(Path(wfd.files_root()))


def test_files_root_can_be_moved(box, tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_FILES_ROOT", str(tmp_path / "share"))
    assert Path(wfd.folder_path(WID, "inbox")) == tmp_path / "share" / str(WID) / "inbox"


def test_safe_name():
    assert wfd.safe_name(r"C:\Users\x\invoice 7.pdf") == "invoice 7.pdf"
    assert wfd.safe_name("../../a:b?.pdf") == "a_b_.pdf"
    assert wfd.safe_name("CON.txt") == "_CON.txt"
    assert wfd.safe_name("résumé.docx") == "résumé.docx"
    long = wfd.safe_name("x" * 400 + ".xlsx")
    assert long.endswith(".xlsx") and len(long) <= 180
    for bad in ("", "  ", "..", "/"):
        with pytest.raises(ValueError):
            wfd.safe_name(bad)


def test_resolve_in_folder_stays_inside(box):
    wfd.ensure_folders(WID)
    out = Path(wfd.folder_path(WID, "outbox"))
    assert Path(wfd.resolve_in_folder(WID, "outbox", "sub/report.xlsx")) == (out / "sub" / "report.xlsx").resolve()
    for bad in ("../inbox/a.pdf", "..", "", "C:/Windows/win.ini", "sub/../../x"):
        with pytest.raises(ValueError):
            wfd.resolve_in_folder(WID, "outbox", bad)
    with pytest.raises(ValueError):
        wfd.folder_path(WID, "secrets")


# ------------------------------------------------------------------- uploads
def test_upload_lands_whole_and_never_overwrites(box):
    _set_up()
    a = _drop("scan.pdf", b"one")
    b = _drop("scan.pdf", b"two")
    c = _drop("scan.pdf", b"three")
    assert [a["name"], b["name"], c["name"]] == ["scan.pdf", "scan (2).pdf", "scan (3).pdf"]
    inbox = Path(wfd.folder_path(WID, "inbox"))
    assert (inbox / "scan.pdf").read_bytes() == b"one" and (inbox / "scan (3).pdf").read_bytes() == b"three"
    assert os.listdir(wfd._incoming_dir(WID)) == []          # nothing half-written left behind
    rec = wfd.uploads(WID)["scan (2).pdf"]
    assert rec["user_id"] == 12 and rec["user_name"] == "ru_drew" and rec["size"] == 3


def test_streamed_upload_in_chunks(box):
    _set_up()
    data = os.urandom(3 * 1024 * 1024 + 5)
    out = wfd.store_stream(WID, "big.bin", io.BytesIO(data), {"user_id": 1}, chunk=1024 * 1024)
    assert out["size"] == len(data)
    assert Path(wfd.folder_path(WID, "inbox"), "big.bin").read_bytes() == data


def test_delete_and_retry(box):
    _set_up()
    _drop("a.pdf")
    wfd.delete_file(WID, "inbox", "a.pdf")
    assert "a.pdf" not in wfd.uploads(WID)
    with pytest.raises(FileNotFoundError):
        wfd.delete_file(WID, "inbox", "a.pdf")
    failed = Path(wfd.folder_path(WID, "failed"))
    (failed / "b.pdf").write_bytes(b"b")
    _drop("b.pdf")                                              # one of that name is waiting
    assert wfd.retry_file(WID, "b.pdf", {"user_id": 5, "name": "dev"}) == "b (2).pdf"
    assert not (failed / "b.pdf").exists()
    assert wfd.uploads(WID)["b (2).pdf"]["via"] == "retry"


def test_listing_counts_and_newest_first(box):
    _set_up()
    _drop("old.pdf")
    p = Path(wfd.folder_path(WID, "inbox"), "old.pdf")
    os.utime(p, (time.time() - 100, time.time() - 100))
    _drop("new.pdf")
    out = Path(wfd.folder_path(WID, "outbox"))
    (out / "sub").mkdir()
    (out / "sub" / "r.xlsx").write_bytes(b"r")
    rows = wfd.list_files(WID, "inbox")
    assert [r["name"] for r in rows] == ["new.pdf", "old.pdf"] and rows[0]["uploaded_by"] == "ru_drew"
    assert wfd.list_files(WID, "outbox")[0]["name"] == "sub/r.xlsx"
    assert wfd.counts(WID) == {"inbox": 2, "processed": 0, "failed": 0, "outbox": 1}


# -------------------------------------------------------------- variable setup
WF = {"nodes": [], "connections": [], "variables": {
    "inputFolder": {"type": "string", "defaultValue": r"C:\AIHub\Acme\input"},
    "processedFolder": {"type": "string", "defaultValue": r"C:\AIHub\Acme\processed"},
    "failedFolder": {"type": "string", "value": r"C:\AIHub\Acme\failed"},
    "outputWorkbook": {"type": "string", "defaultValue": r"C:\AIHub\Acme\output\CI_Lines.xlsx"},
    "apiUrl": {"type": "string", "defaultValue": "https://example.test/api"},
}}


def test_suggest_mapping():
    assert wfd.suggest_mapping(WF) == {"inbox": "inputFolder", "processed": "processedFolder",
                                       "failed": "failedFolder", "outbox": "outputWorkbook"}
    assert wfd.suggest_mapping({"variables": {"apiUrl": {"defaultValue": "x"}}}) == \
        {"inbox": "", "processed": "", "failed": "", "outbox": ""}


def test_plan_variables_points_at_the_managed_folders(box):
    data, changes, errors = wfd.plan_variables(WID, WF, wfd.suggest_mapping(WF))
    assert errors == []
    v = data["variables"]
    assert v["inputFolder"]["defaultValue"] == wfd.folder_path(WID, "inbox")
    assert v["failedFolder"]["defaultValue"] == wfd.folder_path(WID, "failed")
    assert "value" not in v["failedFolder"]                      # the old builder key is replaced
    # an output WORKBOOK keeps its file name inside Results
    assert v["outputWorkbook"]["defaultValue"] == os.path.join(wfd.folder_path(WID, "outbox"), "CI_Lines.xlsx")
    assert v["apiUrl"]["defaultValue"] == "https://example.test/api"
    assert changes["inputFolder"] == (r"C:\AIHub\Acme\input", wfd.folder_path(WID, "inbox"))
    assert WF["variables"]["inputFolder"]["defaultValue"] == r"C:\AIHub\Acme\input"   # input untouched


def test_plan_variables_errors(box):
    _d, _c, errors = wfd.plan_variables(WID, WF, {"inbox": "nope"})
    assert "no variable named 'nope'" in errors[0]
    _d, _c, errors = wfd.plan_variables(WID, WF, {"inbox": "inputFolder", "outbox": "inputFolder"})
    assert "both" in errors[0]
    _d, _c, errors = wfd.plan_variables(WID, WF, {})
    assert errors


def test_variable_roles_notice_a_variable_pointed_elsewhere(box):
    data, _c, _e = wfd.plan_variables(WID, WF, {"inbox": "inputFolder", "outbox": "outputWorkbook"})
    s = dict(wfd.default_settings({"user_id": 1}), workflow_id=WID,
             variables={"inbox": "inputFolder", "outbox": "outputWorkbook", "failed": "gone"})
    roles = wfd.variable_roles(s, data)
    assert roles["inbox"]["managed"] and roles["outbox"]["managed"]
    assert roles["failed"]["missing"]
    data["variables"]["inputFolder"]["defaultValue"] = wfd.folder_path(WID, "inbox") + "2"   # inbox2 is NOT inbox
    assert not wfd.variable_roles(s, data)["inbox"]["managed"]


# --------------------------------------------------------------------- access
def test_access_rules(box):
    s = _set_up(grants={"users": [12], "groups": [3]})
    assert wfd.can_manage({"role": 2}) and wfd.can_manage({"service": True})
    assert not wfd.can_manage({"role": 1})
    assert wfd.can_use({"user_id": 12, "role": 1}, s)
    assert wfd.can_use({"user_id": 40, "role": 1}, s, group_ids=[7, 3])
    assert not wfd.can_use({"user_id": 40, "role": 1}, s, group_ids=[7])
    assert wfd.can_use({"user_id": 40, "role": 2}, s)
    off = dict(s, enabled=False)
    assert not wfd.can_use({"user_id": 12, "role": 1}, off)
    assert not wfd.can_use({"user_id": 12, "role": 1}, None)


def test_switch_off(box, monkeypatch):
    _set_up()
    monkeypatch.setenv("WORKFLOW_FILE_DROPS_ENABLED", "false")
    assert not wfd.enabled() and not wfd.is_set_up(WID)
    assert wfd.run_started(WID, "E1") is None


# ------------------------------------------------------------------------ runs
def _run(eid="E1", requested_by=None, before=None, after=None, status="Completed"):
    """Simulate a run: `before` happens, the run starts, `after` happens."""
    if before:
        before()
    snap = wfd.run_started(WID, eid, "Acme Lines", "files:dev", requested_by)
    assert snap is not None
    assert wfd.load_run(WID, eid)["status"] == "Running"
    if after:
        after()
    return wfd.run_finished(snap, status)


def _process(name, ok=True, out="CI_Lines.xlsx"):
    """What the Acme workflow does with one file."""
    src = Path(wfd.folder_path(WID, "inbox"), name)
    dest = Path(wfd.folder_path(WID, "processed" if ok else "failed"), name)
    os.replace(src, dest)
    if ok and out:
        with open(Path(wfd.folder_path(WID, "outbox"), out), "ab") as fh:
            fh.write(b"row\n")


def test_not_set_up_runs_are_left_alone(box):
    assert wfd.run_started(WID, "E1") is None
    assert wfd.run_finished(None, "Completed") is None


def test_run_record_what_it_took_and_made(box):
    _set_up()
    out = Path(wfd.folder_path(WID, "outbox"), "CI_Lines.xlsx")

    def before():
        _drop("a.pdf", user=12, uname="ru_drew")
        _drop("b.pdf", user=13, uname="ru_kim")
        _drop("c.txt", user=13, uname="ru_kim")
        out.write_bytes(b"header\n")

    def after():
        _process("a.pdf")
        _process("b.pdf", ok=False)

    rec = _run(before=before, after=after)
    assert [c["name"] for c in rec["consumed"]] == ["a.pdf", "b.pdf"]
    assert rec["consumed"][0]["uploaded_by"] == "ru_drew"
    assert rec["processed"] == ["a.pdf"] and rec["failed"] == ["b.pdf"]
    assert rec["outputs"] == [{"name": "CI_Lines.xlsx", "size": out.stat().st_size, "new": False}]
    assert rec["waiting_now"] == 1 and rec["finished_at"] and rec["waiting_at_start"] == 3
    assert set(wfd.uploads(WID)) == {"c.txt"}                    # taken files are forgotten
    assert wfd.load_run(WID, "E1")["status"] == "Completed"
    # both people who dropped files hear about it, with the results attached
    assert {m["uid"] for m in box.sent} == {"12", "13"}
    msg = next(m for m in box.sent if m["uid"] == "12")
    assert msg["title"] == "Acme Lines: results ready" and msg["priority"] == 1
    assert msg["files"] == [str(Path(wfd.folder_path(WID, "outbox"), "CI_Lines.xlsx").resolve())]
    assert "Failed (set aside): b.pdf" in msg["summary"]


def test_a_file_dropped_during_the_run_and_taken_counts(box):
    _set_up()

    def after():
        _drop("late.pdf", user=12)
        _process("late.pdf")

    rec = _run(after=after)
    assert [c["name"] for c in rec["consumed"]] == ["late.pdf"]


def test_empty_scheduled_runs_are_not_news(box):
    _set_up()
    rec = _run()
    assert rec["consumed"] == [] and rec["outputs"] == [] and box.sent == []


def test_unattended_run_tells_the_owner(box):
    _set_up(owner=1)
    Path(wfd.folder_path(WID, "inbox"), "copied-in.pdf").write_bytes(b"x")   # put there by a share
    rec = _run(after=lambda: _process("copied-in.pdf"))
    assert rec["notified"] == ["1"] and box.sent[0]["uid"] == "1"


def test_owner_can_opt_out(box):
    _set_up(owner=1, notify_owner=False)
    Path(wfd.folder_path(WID, "inbox"), "x.pdf").write_bytes(b"x")
    _run(after=lambda: _process("x.pdf"))
    assert box.sent == []


def test_a_failed_run_is_news(box):
    _set_up(owner=1)
    rec = _run(status="Failed")
    assert box.sent and box.sent[0]["title"] == "Acme Lines: run failed" and box.sent[0]["priority"] == 1
    assert rec["status"] == "Failed"


def test_the_agent_requester_is_quiet_then_hears_late(box):
    _set_up(owner=1)
    req = {"user_id": 12, "name": "ru_drew", "via": "the_agent", "notify": False}

    # The Agent waited and delivered in chat: no My Work notice, not even as uploader.
    rec = _run(eid="E1", requested_by=req, before=lambda: _drop("a.pdf", user=12),
               after=lambda: _process("a.pdf"))
    assert box.sent == [] and rec["notified"] == []

    # The Agent stopped waiting before the end: the notice comes when it ends.
    snap = wfd.run_started(WID, "E2", "Acme Lines", "files", req)
    _drop("b.pdf", user=12)
    assert wfd.notify_when_done(WID, "E2") == "queued"
    _process("b.pdf")
    rec = wfd.run_finished(snap, "Completed")
    assert [m["uid"] for m in box.sent] == ["12"] and rec["notified"] == ["12"]
    assert not os.path.exists(wfd._flag_path(WID, "E2"))

    # It had already ended when The Agent asked: sent right away, once.
    box.sent.clear()
    _drop("c.pdf", user=12)
    rec3 = _run(eid="E3", requested_by=req, after=lambda: _process("c.pdf"))
    assert box.sent == []
    assert wfd.notify_when_done(WID, "E3") == "sent"
    assert len(box.sent) == 1 and not os.path.exists(wfd._flag_path(WID, "E3"))


def test_a_person_who_started_it_hears_by_default(box):
    _set_up()
    _drop("a.pdf", user=13, uname="ru_kim")
    _run(requested_by={"user_id": 12, "name": "dev", "via": "files"}, after=lambda: _process("a.pdf"))
    whys = {m["uid"]: m["summary"].split(".")[0] for m in box.sent}
    assert whys == {"12": "The run you started finished with status Completed",
                    "13": "A run that processed your files finished with status Completed"}


def test_run_asked_for_meanwhile_starts_next(box, monkeypatch):
    _set_up()
    started = []
    monkeypatch.setattr(wfd, "_start_next_run", lambda wid, req: started.append(req) or "E9")
    snap = wfd.run_started(WID, "E1", "Acme Lines")
    _drop("a.pdf")
    wfd.request_run_when_free(WID, {"user_id": 12, "via": "upload", "notify": True})
    rec = wfd.run_finished(snap, "Completed")                     # a.pdf arrived after the listing
    assert started == [{"user_id": 12, "via": "upload", "notify": True}] and rec["started_next"] == "E9"
    assert not os.path.exists(wfd._state_dir(WID, "run_next.json"))


def test_no_next_run_when_nothing_waits(box, monkeypatch):
    _set_up()
    started = []
    monkeypatch.setattr(wfd, "_start_next_run", lambda wid, req: started.append(req))
    wfd.request_run_when_free(WID, {"user_id": 12})
    _run()
    assert started == []


def test_email_senders_who_are_users_hear_about_their_files(box, monkeypatch):
    _set_up(owner=1)
    monkeypatch.setattr(wfd, "_email_user_ids", lambda emails: {"kim@example.test": 13})
    wfd.store_bytes(WID, "inv.pdf", b"x", {"email": "Kim@Example.test", "via": "email"})
    rec = _run(requested_by={"email": "Kim@Example.test", "via": "email"}, after=lambda: _process("inv.pdf"))
    assert rec["consumed"][0]["user_id"] == 13 and [m["uid"] for m in box.sent] == ["13"]


def test_recent_runs_newest_first(box):
    _set_up()
    _run(eid="E1")
    time.sleep(0.02)
    _run(eid="E2")
    assert [r["execution_id"] for r in wfd.recent_runs(WID)] == ["E2", "E1"]
    assert len(wfd.recent_runs(WID, limit=1)) == 1


def test_bookkeeping_failure_never_raises(box, monkeypatch):
    _set_up()
    snap = wfd.run_started(WID, "E1")
    monkeypatch.setattr(wfd, "summarize_run", lambda *a: 1 / 0)
    assert wfd.run_finished(snap, "Completed") is None


# ------------------------------------------------------------ email, SharePoint
def test_email_attachments_land_in_the_inbox(box, monkeypatch):
    _set_up()
    fake = types.SimpleNamespace(fetch_attachments_for_event=lambda eid: [
        {"filename": "inv 1.pdf", "content": b"%PDF"}, {"filename": "empty.pdf", "content": None},
        {"filename": "../evil.pdf", "content": b"x"}])
    monkeypatch.setitem(sys.modules, "agent_email_attachments", fake)
    email = {"event_id": 82, "attachment_count": 3, "sender_email": "kim@example.test", "sender_name": "Kim"}
    assert wfd.save_email_attachments(WID, email) == ["inv 1.pdf", "evil.pdf"]
    assert wfd.uploads(WID)["inv 1.pdf"]["email"] == "kim@example.test"
    s = wfd.load_settings(WID)
    wfd.save_settings(WID, dict(s, email_to_inbox=False))
    assert wfd.save_email_attachments(WID, email) == []


def test_email_attachments_need_a_set_up_workflow(box):
    assert wfd.save_email_attachments(WID, {"event_id": 1, "attachment_count": 1}) == []


def test_sharepoint_copy(box, monkeypatch):
    calls = []
    fake = types.SimpleNamespace(execute_integration_operation=lambda iid, op, params, **ctx:
                                 calls.append((iid, op, params, ctx)) or {"success": params["filename"] != "bad.xlsx",
                                                                          "error": "denied"})
    monkeypatch.setitem(sys.modules, "integration_manager", fake)
    _set_up(sharepoint={"integration_id": 5, "drive_id": "D1", "folder_path": "Reports/CI",
                        "conflict_behavior": "replace"})
    out = Path(wfd.folder_path(WID, "outbox"))

    def after():
        (out / "CI_Lines.xlsx").write_bytes(b"x")
        (out / "2026").mkdir()
        (out / "2026" / "bad.xlsx").write_bytes(b"x")

    rec = _run(after=after)
    by_name = {c[2]["filename"]: c for c in calls}
    assert by_name["CI_Lines.xlsx"][2]["folder_path"] == "Reports/CI"
    assert by_name["bad.xlsx"][2]["folder_path"] == "Reports/CI/2026"
    assert by_name["CI_Lines.xlsx"][1] == "upload_file" and by_name["CI_Lines.xlsx"][0] == 5
    assert by_name["CI_Lines.xlsx"][3] == {"workflow_execution_id": "E1"}
    assert {s["name"]: s["ok"] for s in rec["sharepoint"]} == {"CI_Lines.xlsx": True, "2026/bad.xlsx": False}
    title, summary = wfd.notice_text(rec, "owner")
    assert "Copied to SharePoint: CI_Lines.xlsx" in summary and "2026/bad.xlsx failed: denied" in summary


def test_folders_on_another_drive_still_hand_results_over(box, tmp_path, monkeypatch):
    """WORKFLOW_FILES_ROOT on another drive or a share (D:\AIHub\...) works end to end:
    The Agent hands over only files under APP_ROOT, so the notice gets a temporary
    copy there, removed once it is sent."""
    monkeypatch.setenv("WORKFLOW_FILES_ROOT", str(tmp_path / "other_drive" / "workflow_files"))
    seen = []

    def send(uid, title, summary, files, payload, priority):
        seen.append([(f, Path(f).is_file(), Path(f).is_relative_to(box.root)) for f in files])
        return True
    monkeypatch.setattr(wfd, "_send_my_work", send)
    _set_up(owner=1)
    assert not Path(wfd.folder_path(WID, "inbox")).is_relative_to(box.root)
    _drop("a.pdf", user=12)
    rec = _run(after=lambda: _process("a.pdf"))
    assert rec["notified"] == ["12"]
    [(path, existed, under_root)] = seen[0]
    assert existed and under_root and Path(path).name == "CI_Lines.xlsx"
    assert not Path(path).exists()                                  # temporary copy removed
    assert Path(wfd.folder_path(WID, "outbox"), "CI_Lines.xlsx").exists()   # the result itself stays


def test_a_run_that_took_nothing_is_flagged_in_the_report(box):
    """The final record keeps what was waiting at the start, so the run report can
    say 'files were waiting but the run took none' (a Folder Selector reading the
    wrong folder or pattern)."""
    from workflow_run_report import build_run_report
    _set_up()
    _drop("scan.pdf")
    rec = _run()                                     # nothing taken: the workflow read elsewhere
    assert rec["waiting_at_start"] == 1 and rec["consumed"] == []
    report = build_run_report({"execution_id": "E1", "workflow_name": "Acme Lines", "status": "Completed"},
                              [], [], files=wfd.load_run(WID, "E1"))
    assert any("were waiting in the workflow's inbox but the run took none" in f for f in report["flags"])
    assert box.sent == []                            # still not news for My Work
