"""
An inbound e-mail session is handed the mail whole (2026-11-25).

It used to get the first 15,000 characters of the body, the first 10,000 of
each attachment (20,000 without the reading tools), the first ten attachments
and the first 2,000 characters of the user's own standing instructions. What
came after was not there, and of an eleventh attachment not even the name: an
answer built on such a mail could be wrong with nothing saying why.

Now the body, every attachment's text and the instructions are whole. The only
bound is a quarter of the context window of the model the session runs on;
what does not fit is NAMED with its size and the tool call that reads it.

Run with The Agent's python:  python tests_v2/unit/test_agent_inbound_mail_whole.py
"""
import asyncio
import logging
import os
import sys
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

import agent_config   # noqa: E402
import email_client   # noqa: E402
import email_poller as P  # noqa: E402
import email_store    # noqa: E402
import workitem_store  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _text(n, tag):
    """n characters of numbered lines that end in a line only this text has."""
    line = f"{tag} line %06d of the text\n"
    out, i = [], 0
    while sum(len(x) for x in out) < n:
        i += 1
        out.append(line % i)
    body = "".join(out)[:n - len(f"END-OF-{tag}")]
    return body + f"END-OF-{tag}"


EV = {"event_id": 4711, "sender_email": "buyer@acme.example", "recipient_email": "pat.agent@hub.example",
      "subject": "Order questions", "message_key": "mk-1", "has_attachments": True}
OWNER = {"user_id": 7, "role": 2, "username": "pat", "reply_instructions": ""}


class _Mail:
    """Stands in for the cloud feed and the main app's text extraction."""

    def __init__(self, body, attachments=None, failing=()):
        self.body = body
        self.attachments = dict(attachments or {})        # attachment id -> (file name, text)
        self.failing = set(failing)
        self.extract_calls = []                           # (attachment id, max_chars)

    async def full_body(self, _key):
        return self.body

    async def attachments_for(self, _event_id):
        return [{"attachment_id": aid, "filename": name} for aid, (name, _t) in self.attachments.items()]

    async def extract(self, attachment_id, max_chars=20000):
        self.extract_calls.append((attachment_id, max_chars))
        if attachment_id in self.failing:
            return {"success": False, "error": "unsupported file type"}
        text = self.attachments[attachment_id][1]
        return {"success": True, "text": text[:max_chars], "truncated": len(text) > max_chars,
                "original_length": len(text)}


def _prompt(mail, owner=None, tools=True, budget=None, each=0, max_atts=0, ev=None):
    patches = [
        mock.patch.object(email_client, "full_body", mail.full_body),
        mock.patch.object(email_client, "attachments_for", mail.attachments_for),
        mock.patch.object(email_client, "extract_attachment_text", mail.extract),
        mock.patch.object(P, "email_tools_enabled", lambda: tools),
        mock.patch.object(P, "PROMPT_CHARS", budget or 0),
        mock.patch.object(P, "ATTACH_CHARS_EACH", each),
        mock.patch.object(P, "MAX_ATTACHMENTS", max_atts),
    ]
    for p in patches:
        p.start()
    try:
        return _run(P.build_prompt(dict(ev or EV), dict(owner or OWNER)))
    finally:
        for p in reversed(patches):
            p.stop()


# ----------------------------------------------------------------- whole
def test_a_long_body_is_handed_over_whole():
    body = _text(60_000, "BODY")                              # four times the old 15,000
    prompt = _prompt(_Mail(body))
    assert body in prompt and "END-OF-BODY" in prompt
    assert "NOT ALL HERE" not in prompt and "NOT INCLUDED" not in prompt


def test_a_long_attachment_is_handed_over_whole():
    big = _text(300_000, "MANUAL")                            # thirty times the old 10,000
    mail = _Mail("See attached.", {31: ("routing guide.pdf", big)})
    prompt = _prompt(mail)
    assert "--- routing guide.pdf (attachment_id 31) ---\n" + big in prompt
    assert "NOT ALL OF" not in prompt
    assert mail.extract_calls[0][1] >= 300_000                # asked for as much as there is room for


def test_every_attachment_is_handed_over_not_the_first_ten():
    atts = {100 + i: (f"file {i:02d}.pdf", _text(3_000, f"F{i:02d}")) for i in range(1, 15)}
    mail = _Mail("Fourteen files attached.", atts)
    prompt = _prompt(mail)
    assert "Attachments (14), with their extracted text:" in prompt
    for i in range(1, 15):
        assert f"--- file {i:02d}.pdf (attachment_id {100 + i}) ---" in prompt
        assert f"END-OF-F{i:02d}" in prompt
    assert len(mail.extract_calls) == 14


def test_the_users_standing_instructions_are_whole():
    rules = _text(9_000, "RULES")                             # they were cut at 2,000
    prompt = _prompt(_Mail("hi"), owner=dict(OWNER, reply_instructions=rules))
    assert rules in prompt


def test_the_ids_the_reading_tools_take_are_still_given():
    prompt = _prompt(_Mail("hi", {5: ("a.txt", "abc")}))
    assert "Email event_id: 4711" in prompt
    assert "read_attachment(event_id=4711, attachment_id=...)" in prompt


# ----------------------------------------------------------------- the room is the model's context window
def test_the_room_is_a_quarter_of_the_context_window_of_the_owners_model():
    with mock.patch.object(P, "PROMPT_CHARS", 0), \
            mock.patch.object(agent_config, "get_effective_model", lambda role=None: f"model-for-{role}"), \
            mock.patch.object(agent_config, "context_window_tokens",
                              lambda m: 1_000_000 if m == "model-for-3" else 200_000):
        assert P.mail_budget_chars(3) == 650_000              # 1,000,000 tokens / 4 x 2.6
        assert P.mail_budget_chars(1) == 130_000              # 200,000 tokens
    with mock.patch.object(P, "PROMPT_CHARS", 0), \
            mock.patch.object(agent_config, "context_window_tokens", mock.Mock(side_effect=RuntimeError("x"))):
        assert P.mail_budget_chars(2) == 130_000              # unknown: the runtime's default window
    with mock.patch.object(P, "PROMPT_CHARS", 90_000):
        assert P.mail_budget_chars(3) == 90_000               # the setting, when someone sets it


def test_what_does_not_fit_is_named_with_its_size_and_how_to_read_it():
    a = _text(25_000, "FIRST")
    b = _text(40_000, "SECOND")
    c = _text(8_000, "THIRD")
    mail = _Mail(_text(20_000, "BODY"), {1: ("first.pdf", a), 2: ("second.pdf", b), 3: ("third.pdf", c)})
    prompt = _prompt(mail, budget=50_000)
    assert "END-OF-BODY" in prompt and a in prompt            # these fit: whole
    # the second has 5,000 characters of room left: its beginning, and a note that says so
    assert b[:5_000] in prompt and b[:5_001] not in prompt and "END-OF-SECOND" not in prompt
    assert ("[NOT ALL OF second.pdf IS HERE: its text is 40,000 characters long and the first 5,000 "
            "are above; read_attachment(event_id=4711, attachment_id=2) returns it.") in prompt
    # the third has no room: named, with the call that reads it, and not fetched for nothing
    assert "--- third.pdf (attachment_id 3) ---\n[NOT INCLUDED:" in prompt
    assert "read_attachment(event_id=4711, attachment_id=3) returns it" in prompt
    assert "END-OF-THIRD" not in prompt
    assert [aid for aid, _n in mail.extract_calls] == [1, 2]
    assert mail.extract_calls[1][1] == 5_000


def test_a_body_longer_than_the_room_says_so_and_names_every_attachment():
    body = _text(80_000, "BODY")
    mail = _Mail(body, {1: ("one.pdf", "text one"), 2: ("two.pdf", "text two")})
    prompt = _prompt(mail, budget=50_000)
    assert body[:50_000] in prompt and "END-OF-BODY" not in prompt
    assert ("[THE BODY IS NOT ALL HERE: it is 80,000 characters long and the first 50,000 are above. "
            "read_email(event_id=4711) returns the whole body") in prompt
    for aid, name in ((1, "one.pdf"), (2, "two.pdf")):
        assert f"--- {name} (attachment_id {aid}) ---\n[NOT INCLUDED:" in prompt
    assert mail.extract_calls == []


def test_without_the_reading_tools_the_session_is_told_it_has_not_read_everything():
    mail = _Mail(_text(80_000, "BODY"), {1: ("one.pdf", "text one")})
    prompt = _prompt(mail, budget=50_000, tools=False)
    assert "read_email(" not in prompt and "read_attachment(" not in prompt
    assert "do not treat the mail as fully read" in prompt
    assert "it could not be handed to this session; say so in what you report" in prompt


def test_the_optional_settings_still_bound_and_what_they_leave_out_is_named():
    atts = {i: (f"f{i}.txt", _text(30_000, f"A{i}")) for i in (1, 2, 3)}
    mail = _Mail("hi", atts)
    prompt = _prompt(mail, each=10_000, max_atts=2)
    assert atts[1][1][:10_000] in prompt and "END-OF-A1" not in prompt
    assert "[NOT ALL OF f1.txt IS HERE: its text is 30,000 characters long and the first 10,000" in prompt
    assert "--- f3.txt (attachment_id 3) ---\n[NOT INCLUDED: the setting AGENT_EMAIL_MAX_ATTACHMENTS" in prompt
    assert [aid for aid, _n in mail.extract_calls] == [1, 2]


def test_an_attachment_whose_text_cannot_be_read_says_so_and_the_others_still_come():
    mail = _Mail("hi", {1: ("scan.xyz", ""), 2: ("ok.txt", "the second file")}, failing={1})
    prompt = _prompt(mail)
    assert "--- scan.xyz (attachment_id 1) --- (extraction failed: unsupported file type)" in prompt
    assert "--- ok.txt (attachment_id 2) ---\nthe second file" in prompt


def test_the_settings_default_to_not_set():
    with mock.patch.dict(os.environ, {}, clear=False):
        for name in ("AGENT_EMAIL_PROMPT_CHARS", "AGENT_EMAIL_ATTACH_CHARS", "AGENT_EMAIL_MAX_ATTACHMENTS"):
            os.environ.pop(name, None)
            assert P._setting(name) == 0
        os.environ["AGENT_EMAIL_ATTACH_CHARS"] = "20000"
        assert P._setting("AGENT_EMAIL_ATTACH_CHARS") == 20000
        os.environ["AGENT_EMAIL_ATTACH_CHARS"] = "lots"
        assert P._setting("AGENT_EMAIL_ATTACH_CHARS") == 0


# ----------------------------------------------------------------- what the user sees in My Work
def test_the_note_in_my_work_carries_everything_the_session_said():
    said = _text(7_000, "SUMMARY")                            # it was cut at 2,000

    async def fake_turn(prompt, session, user_ctx, tool_scope="full"):
        yield {"type": "text", "text": said}
        yield {"type": "result", "ok": True, "session_id": "s-1"}

    created = {}
    mail = _Mail("please check")
    with mock.patch.object(email_client, "full_body", mail.full_body), \
            mock.patch.object(email_client, "attachments_for", mail.attachments_for), \
            mock.patch.object(email_store, "already_processed", lambda *a: False), \
            mock.patch.object(email_store, "processed_today", lambda *a: 0), \
            mock.patch.object(email_store, "last_processed_at", lambda *a: None), \
            mock.patch.object(email_store, "record", lambda *a, **k: None), \
            mock.patch.object(workitem_store, "create_item",
                              lambda verb, title, **kw: created.update(kw, title=title)):
        outcome = _run(P.process_event(dict(EV, has_attachments=None), dict(OWNER), set(),
                                       run_turn_fn=fake_turn))
    assert outcome == "processed"
    assert said in created["summary"] and created["summary"].endswith("END-OF-SUMMARY")


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS", name)
        except Exception as e:            # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {str(e)[:300]}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
