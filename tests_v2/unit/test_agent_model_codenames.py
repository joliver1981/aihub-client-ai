"""Model code names for The Agent (james 2026-10-10) — AGENT_MODEL_CODENAMES.

An optional .env mask, "real-id:code-name,..." (comma-separated pairs). With
it set, every place The Agent SHOWS a model id shows the code name instead —
Admins included — and a turn on a mapped model gets one system-prompt line
telling The Agent to go by that name. Display-only: the SDK call,
data/agent/settings.json and the server logs keep the real ids; an Admin who
picks or types a code name stores its real id. Absent or empty = exactly the
prior behavior.

Covers parsing (good, malformed, empty), display_model, the reverse lookup
(resolve_model + the settings write path), mask_model_ids, the /api/me
payload with and without the setting, the settings routes and /health, the
system-prompt line only when the turn's model is mapped, run_turn and replay
masking, the UI wiring and the documented default.

Every test pins agent_config.MODEL_CODENAMES (and the install models)
itself, so nothing depends on this box's .env. No live services and no LLM
(brain.query is faked). Runs standalone
(aihub-agent python tests_v2/unit/test_agent_model_codenames.py) or under
pytest; self-skips without claude_agent_sdk. Force-add to git (gitignore
hides test*.py).
"""
import asyncio
import contextlib
import json
import os
import shutil
import sys
import tempfile
import warnings
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

warnings.filterwarnings("ignore", category=DeprecationWarning)
try:
    import agent_config as C                   # noqa: E402
    import brain as B                          # noqa: E402
    import chat_history                        # noqa: E402
    import main                                # noqa: E402
    import readthrough                         # noqa: E402
    import skills_mount                        # noqa: E402
    import usage_store                         # noqa: E402
    from claude_agent_sdk import (AssistantMessage, ResultMessage,  # noqa: E402
                                  SystemMessage, TextBlock)
    from fastapi.testclient import TestClient  # noqa: E402
    HAVE_SDK = True
except ImportError as e:                       # main-env pytest sweep: no SDK
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(
            reason=f"needs the aihub-agent env (claude_agent_sdk): {_IMPORT_ERR}")
    except ImportError:
        pass

# the value James decided on (2026-10-10), as .env.template documents it
DEFAULT = ("claude-haiku-5-5:the-agent-basic,claude-sonnet-5-5:the-agent-standard,"
           "claude-opus-5-5:the-agent-pro")
MAP = {"claude-haiku-5-5": "the-agent-basic",
       "claude-sonnet-5-5": "the-agent-standard",
       "claude-opus-5-5": "the-agent-pro"}

ADMIN = {"user_id": 717171, "role": 3, "username": "codename-admin",
         "name": "Codename Admin", "tenant_id": None}
DEV = {"user_id": 717172, "role": 2, "username": "codename-dev",
       "name": "Codename Dev", "tenant_id": None}

# /api/me's keys before code names existed — without the setting, unchanged
ME_KEYS = {"user", "model", "model_default", "model_role1", "model_role1_default",
           "turns_per_day", "app_version", "main_port", "server_timezone"}


def codenames(mapping):
    return mock.patch.object(C, "MODEL_CODENAMES", dict(mapping))


@contextlib.contextmanager
def install(default="claude-haiku-5-5", role1="claude-sonnet-5-5", settings=None):
    """Pin the install models (agent_config, plus main's own import of
    AGENT_MODEL), a temp data/agent/settings.json and a temp prompt folder.
    Yields a reader for the settings file."""
    tmp = tempfile.mkdtemp(prefix="agent-codenames-")
    path = os.path.join(tmp, "settings.json")
    if settings is not None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(settings, fh)

    def stored():
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except OSError:
            return {}

    try:
        with mock.patch.object(C, "AGENT_MODEL", default), \
             mock.patch.object(main, "AGENT_MODEL", default), \
             mock.patch.object(C, "AGENT_MODEL_ROLE1", role1), \
             mock.patch.object(C, "RUNTIME_SETTINGS_PATH", path), \
             mock.patch.object(B, "SYSTEM_PROMPT_PATH",
                               os.path.join(tmp, "system_prompt.md")):
            yield stored
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def as_user(user):
    return mock.patch.object(main, "_verify_request", lambda r: dict(user))


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------- parsing

def test_parse_the_decided_default():
    got = C.parse_model_codenames(DEFAULT)
    assert got == MAP, got
    assert list(got.values()) == ["the-agent-basic", "the-agent-standard",
                                  "the-agent-pro"]          # .env order kept


def test_parse_trims_whitespace_and_splits_on_the_last_colon():
    raw = ("  claude-haiku-5-5 :  the-agent-basic ,, "
           "anthropic.claude-x-v1:0:the-agent-x , ")
    assert C.parse_model_codenames(raw) == {
        "claude-haiku-5-5": "the-agent-basic",
        "anthropic.claude-x-v1:0": "the-agent-x"}     # a colon inside the id is fine


def test_parse_skips_and_logs_malformed_pairs():
    raw = ",".join([
        "claude-haiku-5-5:the-agent-basic",     # good
        "no-colon-here",                        # no colon
        ":the-agent-orphan",                    # empty model id
        "claude-x-1:",                          # empty code name
        "bad model!:the-agent-bad",             # an id the model setting refuses
        "claude-y-1:has space",                 # code name with a space
        "claude-z-1:-leading",                  # code name must start alphanumeric
        "claude-haiku-5-5:the-agent-again",     # model id already mapped
        "claude-q-1:The-Agent-Basic",           # code name already used (any case)
        "claude-w-1:claude-haiku-5-5",          # code name = an earlier model id
        "the-agent-basic:the-agent-w",          # model id = an earlier code name
        "claude-opus-5-5:the-agent-pro",        # good
    ])
    with mock.patch.object(C.logger, "warning") as warn:
        got = C.parse_model_codenames(raw)
    assert got == {"claude-haiku-5-5": "the-agent-basic",
                   "claude-opus-5-5": "the-agent-pro"}, got
    assert warn.call_count == 10, warn.call_args_list
    logged = "\n".join(str(c.args[0]) for c in warn.call_args_list)
    assert logged.count("AGENT_MODEL_CODENAMES: ignoring") == 10, logged
    for bad in ("no-colon-here", "has space", "the-agent-again", "The-Agent-Basic"):
        assert bad in logged, (bad, logged)


def test_parse_empty_or_absent_is_no_mapping_and_logs_nothing():
    with mock.patch.object(C.logger, "warning") as warn, \
         mock.patch.object(C.logger, "error") as err:
        for raw in ("", None, "   ", ",, ,"):
            assert C.parse_model_codenames(raw) == {}, raw
    assert not warn.called and not err.called


def test_parse_never_raises():
    class Unreadable:
        def __str__(self):
            raise RuntimeError("boom")

    with mock.patch.object(C.logger, "error") as err:
        assert C.parse_model_codenames(Unreadable()) == {}
    assert err.called
    with mock.patch.object(C.logger, "warning"):
        for junk in (":::", ",:,:,", "a:b:c:d", "::x", "x::"):
            assert isinstance(C.parse_model_codenames(junk), dict), junk


# --------------------------------------------------- display + reverse lookup

def test_display_model():
    with codenames(MAP):
        assert C.display_model("claude-opus-5-5") == "the-agent-pro"
        assert C.display_model(" claude-opus-5-5 ") == "the-agent-pro"
        assert C.display_model("claude-haiku-4-5") == "claude-haiku-4-5"   # unmapped
        assert C.display_model("") == ""
        assert C.display_model(None) is None
    with codenames({}):
        assert C.display_model("claude-opus-5-5") == "claude-opus-5-5"


def test_resolve_model_is_the_reverse_lookup():
    with codenames(MAP):
        assert C.resolve_model("the-agent-pro") == "claude-opus-5-5"
        assert C.resolve_model("  The-Agent-PRO ") == "claude-opus-5-5"   # any case
        assert C.resolve_model("claude-opus-5-5") == "claude-opus-5-5"    # a real id
        assert C.resolve_model("claude-haiku-4-5") == "claude-haiku-4-5"  # unmapped
        assert C.resolve_model("") == "" and C.resolve_model(None) == ""
    with codenames({}):
        assert C.resolve_model(" the-agent-pro ") == "the-agent-pro"


def test_a_picked_code_name_is_stored_as_its_real_id():
    with codenames(MAP), install() as stored:
        assert C.set_model_override("the-agent-pro") == "claude-opus-5-5"
        assert stored()["model"] == "claude-opus-5-5"
        assert C.set_role1_model_override("THE-AGENT-STANDARD") == "claude-sonnet-5-5"
        assert stored()["role1_model"] == "claude-sonnet-5-5"
        # an unmapped real id an Admin types still works
        assert C.set_model_override("claude-haiku-4-5") == "claude-haiku-4-5"
        assert stored()["model"] == "claude-haiku-4-5"
        # clearing still clears; a malformed id is still refused
        assert C.set_model_override("") == "claude-haiku-5-5"
        assert "model" not in stored()
        try:
            C.set_model_override("bad model!!")
            raise AssertionError("a malformed id must raise")
        except ValueError:
            pass


def test_mask_model_ids_in_free_text():
    with codenames(MAP):
        msg = ("There's an issue with the selected model (claude-opus-5-5). "
               "It may not exist or you may not have access to it.")
        assert C.mask_model_ids(msg) == msg.replace("claude-opus-5-5", "the-agent-pro")
        assert C.mask_model_ids("model: claude-haiku-5-5.") == "model: the-agent-basic."
        for longer in ("claude-opus-5-5-preview", "claude-opus-5-5.1",
                       "us.anthropic.claude-opus-5-5", "xclaude-opus-5-5"):
            assert C.mask_model_ids(longer) == longer, longer
        assert C.mask_model_ids(None) is None and C.mask_model_ids("") == ""
    with codenames({"claude-opus-5": "the-agent-old"}):
        assert (C.mask_model_ids("claude-opus-5 and claude-opus-5-5")
                == "the-agent-old and claude-opus-5-5")
    with codenames({}):
        assert C.mask_model_ids("(claude-opus-5-5)") == "(claude-opus-5-5)"


# ------------------------------------------------------------------ routes

def test_me_payload_without_the_setting_is_unchanged():
    with codenames({}), install(), as_user(DEV):
        d = TestClient(main.app).get("/api/me").json()
    assert set(d) == ME_KEYS, sorted(d)                  # no model_suggestions
    assert d["model"] == d["model_default"] == "claude-haiku-5-5", d
    assert d["model_role1"] == d["model_role1_default"] == "claude-sonnet-5-5", d


def test_me_payload_with_the_setting_shows_only_code_names():
    for user in (DEV, ADMIN):
        with codenames(MAP), install(settings={"model": "claude-opus-5-5"}), \
             as_user(user):
            d = TestClient(main.app).get("/api/me").json()
        assert d["model"] == "the-agent-pro", d                 # the override
        assert d["model_default"] == "the-agent-basic", d
        assert d["model_role1"] == d["model_role1_default"] == "the-agent-standard", d
        assert d["model_suggestions"] == ["the-agent-basic", "the-agent-standard",
                                          "the-agent-pro"], d
        assert set(d) == ME_KEYS | {"model_suggestions"}, sorted(d)
        body = json.dumps(d)
        for real in MAP:
            assert real not in body, (real, body)


def test_me_shows_an_unmapped_model_as_is():
    with codenames(MAP), install(settings={"model": "claude-haiku-4-5"}), \
         as_user(ADMIN):
        d = TestClient(main.app).get("/api/me").json()
    assert d["model"] == "claude-haiku-4-5" and d["model_default"] == "the-agent-basic", d


def test_admin_picker_saves_the_real_id_and_answers_with_the_code_name():
    with codenames(MAP), install() as stored, as_user(ADMIN):
        client = TestClient(main.app)
        r = client.post("/api/settings/model", json={"model": "the-agent-pro"})
        assert r.status_code == 200, r.text
        assert r.json() == {"model": "the-agent-pro", "model_default": "the-agent-basic",
                            "override_active": True}, r.json()
        assert stored()["model"] == "claude-opus-5-5"
        r = client.post("/api/settings/role1-model", json={"model": "the-agent-pro"})
        assert r.json() == {"model_role1": "the-agent-pro",
                            "model_role1_default": "the-agent-standard",
                            "override_active": True}, r.json()
        assert stored()["role1_model"] == "claude-opus-5-5"
        # an unmapped real id typed by an Admin still works
        r = client.post("/api/settings/model", json={"model": "claude-haiku-4-5"})
        assert r.json()["model"] == "claude-haiku-4-5" and stored()["model"] == "claude-haiku-4-5"
        # clearing falls back to the default, shown by its code name
        r = client.post("/api/settings/model", json={"model": ""})
        assert r.json() == {"model": "the-agent-basic", "model_default": "the-agent-basic",
                            "override_active": False}, r.json()
        assert "model" not in stored()
        assert client.post("/api/settings/model",
                           json={"model": "bad model!!"}).status_code == 400
    with codenames(MAP), install(), as_user(DEV):            # still Admin-only
        assert TestClient(main.app).post("/api/settings/model",
                                         json={"model": "the-agent-pro"}).status_code == 403


def test_settings_routes_without_the_setting_are_unchanged():
    with codenames({}), install() as stored, as_user(ADMIN):
        r = TestClient(main.app).post("/api/settings/model",
                                      json={"model": "claude-sonnet-5"})
        assert r.json() == {"model": "claude-sonnet-5", "model_default": "claude-haiku-5-5",
                            "override_active": True}, r.json()
        assert stored()["model"] == "claude-sonnet-5"


def test_health_masks_model_ids_only_when_mapped():
    with codenames(MAP), install():
        h = TestClient(main.app).get("/health").json()
    assert h["model"] == h["model_default"] == "the-agent-basic", h
    assert h["model_role1"] == h["model_role1_default"] == "the-agent-standard", h
    body = json.dumps(h)
    for real in MAP:
        assert real not in body, (real, body)
    with codenames({}), install():
        h = TestClient(main.app).get("/health").json()
    assert h["model"] == "claude-haiku-5-5" and h["model_role1"] == "claude-sonnet-5-5", h


# ----------------------------------------------------- system-prompt line

def test_prompt_line_only_for_a_mapped_model():
    with codenames(MAP):
        line = C.codename_prompt_line("claude-opus-5-5")
        assert "the-agent-pro" in line and "claude-opus-5-5" not in line, line
        assert "\n" not in line and "provider" in line, line
        assert C.codename_prompt_line("claude-haiku-4-5") == ""
        assert C.codename_prompt_line(None) == "" and C.codename_prompt_line("") == ""
    with codenames({}):
        assert C.codename_prompt_line("claude-opus-5-5") == ""


def test_turn_prompt_carries_the_line_only_when_the_turn_model_is_mapped():
    with codenames(MAP), install() as stored:
        dev = B._make_options(None, "full", None, 2, [])        # claude-haiku-5-5
        path = dev.system_prompt["path"]
        assert dev.system_prompt["type"] == "file" and path != B.SYSTEM_PROMPT_PATH
        assert _read(path) == (B.SYSTEM_PROMPT + "\n\n"
                               + C.codename_prompt_line("claude-haiku-5-5") + "\n")
        assert dev.model == "claude-haiku-5-5"        # the SDK still gets the real id
        ru = B._make_options(None, "read", None, 1, [])         # claude-sonnet-5-5
        assert ru.model == "claude-sonnet-5-5"
        assert ru.system_prompt["path"] != path
        assert _read(ru.system_prompt["path"]).endswith(
            C.codename_prompt_line("claude-sonnet-5-5") + "\n")
        # a stale copy is rewritten before the turn, like the shared file
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("stale prompt from an older build")
        assert B._make_options(None, "full", None, 2, []).system_prompt["path"] == path
        assert "the-agent-basic" in _read(path)
        # a turn on an UNMAPPED model reads the shared file, byte-identical
        C.set_model_override("claude-haiku-4-5")
        assert stored()["model"] == "claude-haiku-4-5"
        un = B._make_options(None, "full", None, 2, [])
        assert un.system_prompt == {"type": "file", "path": B.SYSTEM_PROMPT_PATH}
        assert _read(B.SYSTEM_PROMPT_PATH) == B.SYSTEM_PROMPT
    with codenames({}), install():
        off = B._make_options(None, "full", None, 2, [])
        assert off.system_prompt == {"type": "file", "path": B.SYSTEM_PROMPT_PATH}
        assert _read(B.SYSTEM_PROMPT_PATH) == B.SYSTEM_PROMPT
        assert off.model == "claude-haiku-5-5"


# ------------------------------------------------- run_turn + history replay

def _fake_query(fail_with=None):
    def query(prompt, options):
        async def gen():
            yield SystemMessage(subtype="init", data={
                "session_id": "codename-sid", "model": "claude-opus-5-5",
                "tools": [], "mcp_servers": []})
            if fail_with:
                raise RuntimeError(fail_with)
            yield AssistantMessage(content=[TextBlock(
                text="There's an issue with the selected model (claude-opus-5-5).")],
                model="<synthetic>", error="invalid_request")
            yield AssistantMessage(content=[TextBlock(
                text="Set the step's model to claude-opus-5-5.")], model="claude-opus-5-5")
            yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1,
                                is_error=False, num_turns=1, session_id="codename-sid")
        return gen()
    return query


def _turn(fail_with=None):
    with mock.patch.object(B, "query", _fake_query(fail_with)), \
         mock.patch.object(usage_store, "count_turn", return_value=(True, "")), \
         mock.patch.object(readthrough, "user_group_ids", return_value=[]), \
         mock.patch.object(skills_mount, "build_user_workspace", return_value=None), \
         mock.patch.object(skills_mount, "list_skills", return_value=[]):
        async def collect():
            return [ev async for ev in B.run_turn("hi", None, dict(DEV))]
        return asyncio.run(collect())


def test_run_turn_shows_code_names_but_leaves_the_models_own_words():
    with codenames(MAP), install():
        events = _turn()
    init = [e for e in events if e["type"] == "init"][0]
    texts = [e["text"] for e in events if e["type"] == "text"]
    assert init["model"] == "the-agent-pro", init
    assert texts == ["There's an issue with the selected model (the-agent-pro).",
                     "Set the step's model to claude-opus-5-5."], texts
    with codenames({}), install():
        events = _turn()
    assert [e for e in events if e["type"] == "init"][0]["model"] == "claude-opus-5-5"
    assert [e["text"] for e in events if e["type"] == "text"][0].endswith(
        "(claude-opus-5-5).")


def test_run_turn_error_event_shows_the_code_name():
    with codenames(MAP), install():
        events = _turn(fail_with="model claude-opus-5-5 is not available")
    err = [e for e in events if e["type"] == "error"][0]
    assert err["error"] == "model the-agent-pro is not available", err
    with codenames({}), install():
        events = _turn(fail_with="model claude-opus-5-5 is not available")
    assert [e for e in events if e["type"] == "error"][0]["error"] == \
        "model claude-opus-5-5 is not available"


def test_history_replay_masks_only_the_api_error_text():
    tmp = tempfile.mkdtemp(prefix="agent-codenames-replay-")
    path = os.path.join(tmp, "codename-sid.jsonl")
    recs = [
        {"type": "user", "message": {"role": "user", "content": "hello"}},
        {"type": "assistant", "isApiErrorMessage": True, "message": {
            "role": "assistant", "model": "<synthetic>", "content": [{
                "type": "text",
                "text": "There's an issue with the selected model (claude-opus-5-5)."}]}},
        {"type": "user", "message": {"role": "user", "content": "and the step?"}},
        {"type": "assistant", "isApiErrorMessage": False, "message": {
            "role": "assistant", "model": "claude-opus-5-5", "content": [{
                "type": "text", "text": "Set the step's model to claude-opus-5-5."}]}},
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(r) for r in recs) + "\n")
    try:
        with codenames(MAP), \
             mock.patch.object(chat_history, "_find_transcript", lambda sid: path):
            agent = [t["text"] for t in chat_history.replay("codename-sid")
                     if t["role"] == "agent"]
        assert agent == ["There's an issue with the selected model (the-agent-pro).",
                         "Set the step's model to claude-opus-5-5."], agent
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------- UI wiring + documentation

def test_ui_pickers_take_the_server_suggestions_when_sent():
    html = _read(os.path.join(APP_ROOT, "agent_service", "static", "index.html"))
    assert "...(d.model_suggestions || [" in html
    # labels and pickers render whatever /api/me sent — no client-side mapping
    assert "AGENT_MODEL_CODENAMES" not in html and "the-agent-basic" not in html


def test_env_template_documents_the_decided_default():
    lines = _read(os.path.join(APP_ROOT, ".env.template")).splitlines()
    assert "AGENT_MODEL_CODENAMES=" + DEFAULT in lines
    assert C.parse_model_codenames(DEFAULT) == MAP


if __name__ == "__main__":
    if not HAVE_SDK:
        print(f"SKIP-ALL: {_IMPORT_ERR}")
        sys.exit(0)
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in fns:
        try:
            f()
            print(f"PASS  {n}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {n}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {n}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
