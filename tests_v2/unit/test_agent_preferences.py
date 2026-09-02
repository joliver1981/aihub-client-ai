"""Unit pack for per-user standing preferences (agent_service/preferences.py +
the remember/forget tools + the envelope block) and for send_email's recipient
name resolution and View embedding (2026-09-02).

Preferences write to the real per-user skills tree under data/agent/users/
for a throwaway user id and clean up after themselves. Everything else is
monkeypatched. Runs standalone or under pytest; self-skips without the SDK.
"""
import asyncio
import os
import sys
import types
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import preferences as PR                   # noqa: E402
    import work_tools as WK                    # noqa: E402
    import skills_mount                        # noqa: E402
    from platform_tools import CURRENT_USER    # noqa: E402
    HAVE_SDK = True
except ImportError as e:
    HAVE_SDK = False
    _IMPORT_ERR = e

if not HAVE_SDK:
    try:
        import pytest
        pytestmark = pytest.mark.skip(
            reason=f"needs the aihub-agent env (claude_agent_sdk): {_IMPORT_ERR}")
    except ImportError:
        pass

UID = 998877          # throwaway user for the on-disk store


def _run(coro):
    return asyncio.run(coro)


def _txt(res):
    return res["content"][0]["text"]


def _cleanup():
    skills_mount.delete_skill("user", PR.SKILL_NAME, user_id=UID)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def test_parse_items_skips_frontmatter_and_prose():
    text = "---\nname: my-preferences\ndescription: d\n---\n\n# My preferences\n\nprose\n- one\n*  two  spaced \n"
    assert PR.parse_items(text) == ["one", "two spaced"]
    assert PR.parse_items("") == []


def test_remember_dedupes_caps_and_persists_as_a_user_skill():
    _cleanup()
    try:
        items, added, err = PR.remember(UID, "  Always answer in Eastern time ")
        assert added and err is None and items == ["Always answer in Eastern time"]
        items, added, err = PR.remember(UID, "always answer in eastern time")
        assert not added and err is None and len(items) == 1        # case-insensitive dedupe
        items, added, err = PR.remember(UID, "x" * 301)
        assert not added and "under 300" in err
        items, added, err = PR.remember(UID, "")
        assert not added and "empty" in err
        # it is a real user-scope skill (Skills rail visibility) …
        names = [s["name"] for s in skills_mount.list_skills(UID) if s["scope"] == "user"]
        assert PR.SKILL_NAME in names
        # … and survives a fresh read
        assert PR.get(UID) == ["Always answer in Eastern time"]
    finally:
        _cleanup()


def test_forget_exact_unique_fragment_ambiguous_and_clear_all():
    _cleanup()
    try:
        for p in ("Call me Jim", "Default charts to bars", "Default tables to 20 rows"):
            PR.remember(UID, p)
        items, removed, err = PR.forget(UID, "jim")
        assert err is None and removed == ["Call me Jim"] and len(items) == 2
        items, removed, err = PR.forget(UID, "default")
        assert err and "More than one" in err and len(items) == 2   # ambiguous: nothing removed
        items, removed, err = PR.forget(UID, "nothing like this")
        assert err and "No saved preference" in err
        items, removed, err = PR.forget(UID, "", clear_all=True)
        assert items == [] and len(removed) == 2 and err is None
        assert PR.get(UID) == []
        names = [s["name"] for s in skills_mount.list_skills(UID) if s["scope"] == "user"]
        assert PR.SKILL_NAME not in names                            # empty list = skill removed
    finally:
        _cleanup()


def test_envelope_block_is_empty_without_prefs_and_capped_with_many():
    _cleanup()
    try:
        assert PR.envelope_block(UID) == ""
        assert PR.envelope_block(0) == ""
        PR.remember(UID, "Call me Jim")
        block = PR.envelope_block(UID)
        assert block.startswith("\n[Standing preferences") and "- Call me Jim" in block and block.endswith("]")
        for i in range(30):
            PR.remember(UID, f"Preference number {i} " + "detail " * 20)
        block = PR.envelope_block(UID)
        assert len(block) < PR.MAX_BLOCK_CHARS + 200 and "capped" in block
    finally:
        _cleanup()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def test_remember_and_forget_tools_round_trip():
    _cleanup()
    tok = CURRENT_USER.set({"user_id": UID, "role": 1, "username": "pref"})
    try:
        res = _run(WK.remember_preference.handler({"preference": "Call me Jim"}))
        assert not res.get("is_error") and "Saved" in _txt(res) and "- Call me Jim" in _txt(res)
        res = _run(WK.remember_preference.handler({"preference": "call me jim"}))
        assert "Already saved" in _txt(res)
        res = _run(WK.forget_preference.handler({"clear_all": True}))
        assert "CONFIRMATION REQUIRED" in _txt(res) and PR.get(UID) == ["Call me Jim"]
        res = _run(WK.forget_preference.handler({"preference": "jim"}))
        assert "Forgot: Call me Jim" in _txt(res) and PR.get(UID) == []
        res = _run(WK.forget_preference.handler({"preference": "jim"}))
        assert res.get("is_error")
    finally:
        CURRENT_USER.reset(tok)
        _cleanup()


# ---------------------------------------------------------------------------
# send_email: names + views
# ---------------------------------------------------------------------------

DIRECTORY = [{"id": 1, "name": "John Smith", "username": "jsmith", "email": "john@x.co"},
             {"id": 2, "name": "John Smithers", "username": "jsmithers", "email": "smithers@x.co"},
             {"id": 3, "name": "Ann Lee", "username": "alee", "email": "ann@x.co"},
             {"id": 4, "name": "No Mail", "username": "nomail", "email": ""}]


def test_resolve_recipients_rules():
    emails, resolved, err = WK.resolve_recipients(["ann@x.co", "Ann Lee", "jsmith", "Ann Le"], DIRECTORY)
    assert err is None
    assert emails == ["ann@x.co", "john@x.co"]                     # ann deduped (name + typo); jsmith by username
    assert resolved["Ann Le"]["name"] == "Ann Lee"
    # 'john smit' is a substring of BOTH Smith and Smithers -> ambiguous, fail closed …
    _e, _r, err = WK.resolve_recipients(["john smit"], DIRECTORY)
    assert err and "2 users" in err
    # … but unique once Smithers is gone (the typo case James described)
    emails, resolved, err = WK.resolve_recipients(["john smit"], [u for u in DIRECTORY if u["id"] != 2])
    assert err is None and emails == ["john@x.co"] and resolved["john smit"]["name"] == "John Smith"
    _e, _r, err = WK.resolve_recipients(["Smith"], DIRECTORY)
    assert err and "2 users" in err and "John Smith <john@x.co>" in err
    _e, _r, err = WK.resolve_recipients(["Nobody Here"], DIRECTORY)
    assert err and "no user in the directory" in err
    _e, _r, err = WK.resolve_recipients(["No Mail"], DIRECTORY)
    assert err and "no email on file" in err
    emails, resolved, err = WK.resolve_recipients(["Lee Ann"], DIRECTORY)      # all-words match
    assert err is None and emails == ["ann@x.co"]


def _patch_send(sends, items, addr, view_err=None):
    fake_store = types.SimpleNamespace(get_address=lambda uid: addr)

    async def fake_send(to, subject, body, from_address, from_name, html_body=None, attachments=None):
        sends.append({"to": to, "body": body, "html": html_body})
        return {"success": True}

    async def fake_directory():
        return DIRECTORY

    async def fake_render_view(name, scope, gid, principal):
        if view_err:
            return "", "", view_err, ""
        return "<table>VIEWHTML</table>", "VIEWTEXT 42", None, "2 of 2 tiles refreshed live"

    def create_item(kind, title, summary="", payload=None, **kw):
        items.append({"kind": kind, "summary": summary, "payload": payload})
        return {"work_item_id": "wi-9"}

    fake_render = types.SimpleNamespace(html_enabled=lambda: True,
                                        render_email_with_view=lambda b, v, title="": f"<p>{b}</p>{v}")
    return [mock.patch.dict(sys.modules, {"email_store": fake_store,
                                          "email_client": types.SimpleNamespace(send_reply=fake_send),
                                          "email_render": fake_render}),
            mock.patch.object(WK, "_user_directory", fake_directory),
            mock.patch.object(WK, "render_view_for_email", fake_render_view),
            mock.patch.object(WK.workitem_store, "create_item", create_item)]


def test_send_email_resolves_a_name_and_embeds_a_view_for_developers():
    tok = CURRENT_USER.set({"user_id": 7, "role": 2, "username": "dev"})
    sends, items = [], []
    ps = _patch_send(sends, items, None)
    for p in ps:
        p.start()
    try:
        res = _run(WK.send_email.handler({"to": ["jsmith"], "subject": "Sales", "body": "See below.",
                                          "view_name": "My View 123"}))
        assert not res.get("is_error"), _txt(res)
        out = _txt(res)
        assert "SENT" in out and "'jsmith' -> John Smith <john@x.co>" in out
        assert "embedded as a dashboard: 2 of 2 tiles refreshed live" in out
        assert "do not describe them" in out
        assert sends[0]["to"] == ["john@x.co"]
        assert "VIEWTEXT 42" in sends[0]["body"] and "VIEWHTML" in sends[0]["html"]
        # ambiguity: nothing sent, candidates relayed
        res = _run(WK.send_email.handler({"to": ["Smith"], "subject": "S", "body": "b"}))
        assert res.get("is_error") and "2 users" in _txt(res) and len(sends) == 1
    finally:
        for p in ps:
            p.stop()
        CURRENT_USER.reset(tok)


def test_send_email_view_error_stops_everything_and_approval_carries_the_view():
    tok = CURRENT_USER.set({"user_id": 7, "role": 2, "username": "dev"})
    sends, items = [], []
    ps = _patch_send(sends, items, None, view_err="No saved View named 'Nope' is visible to this user")
    for p in ps:
        p.start()
    try:
        res = _run(WK.send_email.handler({"to": ["ann@x.co"], "subject": "S", "body": "b",
                                          "view_name": "Nope"}))
        assert res.get("is_error") and "No saved View" in _txt(res) and not sends
    finally:
        for p in ps:
            p.stop()
        CURRENT_USER.reset(tok)
    tok = CURRENT_USER.set({"user_id": 7, "role": 1, "username": "reg"})
    sends, items = [], []
    ps = _patch_send(sends, items, {"is_active": 1, "outbound_enabled": 1,
                                    "email_address": "reg-agent.1@x.io", "prefix": "reg"})
    for p in ps:
        p.start()
    try:
        res = _run(WK.send_email.handler({"to": ["Ann Lee"], "subject": "S", "body": "b",
                                          "view_name": "My View 123"}))
        assert not res.get("is_error"), _txt(res)
        assert not sends and items and items[0]["payload"]["view"]["name"] == "My View 123"
        assert items[0]["payload"]["to"] == ["ann@x.co"]
        assert "refreshed and embedded when they approve" in _txt(res)
        assert "Embedded View: My View 123" in items[0]["summary"]
    finally:
        for p in ps:
            p.stop()
        CURRENT_USER.reset(tok)


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
