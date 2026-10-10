"""Two chat history bugs (2026-10-10).

1. A replayed message that had attachments showed the model-facing note in
   front of the user's words ("[Attached files from the user — server paths
   for YOUR tools ...]" with the server paths). Replay now returns the user's
   own words plus the file names, and the page shows them like a live message.
2. After a page reload the tab kept its conversation id but showed the welcome
   screen, so the next message silently continued the old conversation and two
   chats became one. The page now shows the conversation it is continuing.

Runs under pytest or standalone (python <this file>) in the aihub-agent env.
"""
import json
import os
import sys
import tempfile

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

import chat_history as ch  # noqa: E402

BLOCK = ("[Attached files from the user — server paths for YOUR tools. To answer about their "
         "contents OPEN THEM FIRST: read_file for documents. Never show the paths back to the user:\n"
         "  Sept_2026.xlsx (56.6 KB) -> C:\\app\\data\\agent\\users\\13\\uploads\\3487__Sept_2026.xlsx\n"
         "  report (2).pdf (1.2 MB) -> C:\\app\\data\\agent\\users\\13\\uploads\\9f00__report (2).pdf\n"
         "]")
WORDS = "Here is an example of the file I export from Azure.\nI also have history back to Jan 2026."


def test_the_note_is_split_from_the_users_words():
    text, names = ch.split_attachments_block(BLOCK + "\n\n" + WORDS)
    assert text == WORDS
    assert names == ["Sept_2026.xlsx", "report (2).pdf"]


def test_text_without_the_note_is_untouched():
    for t in (WORDS, "[Attached files from the user — but no closing line", "] not a note", ""):
        assert ch.split_attachments_block(t) == (t, [])


def test_the_marker_matches_what_file_tools_writes():
    src = open(os.path.join(APP_ROOT, "agent_service", "file_tools.py"), encoding="utf-8").read()
    assert '"[Attached files from the user' in src and ch.ATTACHMENTS_OPEN == "[Attached files from the user"


def test_replay_shows_words_and_file_names():
    sid = "11111111-2222-3333-4444-555555555555"
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "projects", "workspace")
        os.makedirs(d)
        prompt = "[Context: now 2026-10-10 17:20 EDT]\n\n" + BLOCK + "\n\n" + WORDS
        with open(os.path.join(d, sid + ".jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "user", "message": {"content": prompt}}) + "\n")
            fh.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "I read the September export."}]}}) + "\n")
        old = ch.CLAUDE_CONFIG_DIR
        ch.CLAUDE_CONFIG_DIR = tmp
        try:
            turns = ch.replay(sid)
        finally:
            ch.CLAUDE_CONFIG_DIR = old
    assert turns[0] == {"role": "user", "text": WORDS, "attachments": ["Sept_2026.xlsx", "report (2).pdf"]}
    assert "server paths" not in json.dumps(turns) and "uploads" not in json.dumps(turns)
    assert turns[1]["role"] == "agent"


HTML = open(os.path.join(APP_ROOT, "agent_service", "static", "index.html"), encoding="utf-8").read()


def test_page_shows_attachment_names_on_replayed_messages():
    i = HTML.index("function renderTurns")
    block = HTML[i:HTML.index("async function openHistory", i)]
    assert 'const files = t.attachments || [];' in block
    assert 'addUser(t.text + (files.length ? "\\n📎 " + files.join(", ") : ""))' in block


def test_reload_shows_the_conversation_it_continues():
    i = HTML.index("async function boot()")
    boot = HTML[i:HTML.index("boot();", i)]
    assert "resumed = await openHistory({ session_id: sessionId });" in boot
    # the welcome screen only when nothing was resumed; a conversation that cannot be
    # loaded is forgotten so the next message starts a new one
    assert "if (!resumed && !document.getElementById(\"hello\")" in boot
    assert 'sessionStorage.removeItem("agent_session")' in boot
    oh = HTML[HTML.index("async function openHistory"):]
    oh = oh[:oh.index("\n  }\n")]
    assert "if (!r.ok) return false;" in oh and "return true;" in oh


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
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
