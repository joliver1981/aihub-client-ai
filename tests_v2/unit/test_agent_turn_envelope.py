"""The per-turn envelope names the CALLER and their role (2026-09-08).

main._turn_envelope prepends "[Context: now … (zone)]" to every turn. It now
also carries one "[Signed-in user: Alex Rivera (ru_alex) — End User]" line
built from the verified token claims — the regular-user run of 2026-09-07
(test_human/26_The_Agent_Regular_User/REPORT_2026-09-07.md, F-11 / RU-01 and
F-6 / RU-25a) showed the model could not state the caller's role and accepted
a claimed promotion because it never saw either. Context only: no gate or
tool reads the line (pinned below); the role wording is the Users page's
(templates/users.html: 3 Admin, 2 Developer, 1 End User).

Runs standalone (aihub-agent python test_agent_turn_envelope.py) or under
pytest; self-skips without the SDK. Force-add to git (gitignore hides
test*.py).
"""
import os
import sys
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import main                        # noqa: E402
    import chat_history                # noqa: E402
    import preferences                 # noqa: E402
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

ALEX = {"user_id": 5009, "role": 1, "username": "ru_alex", "name": "Alex Rivera",
        "tenant_id": 1}
ERIN = {"user_id": 5013, "role": 2, "username": "dev_erin", "name": "Erin Walsh",
        "tenant_id": 1}
ADMIN = {"user_id": 1, "role": 3, "username": "admin", "name": "Administrator",
         "tenant_id": 1}
# exactly what /api/run and /api/views/refresh-cache build when the job
# carries no user (the service principal)
SERVICE = {"user_id": 0, "role": 2, "username": "scheduler", "name": "scheduler",
           "mode": "headless"}

BODY = {"timezone": "America/New_York"}


def _envelope(user, body=None, prefs=""):
    """_turn_envelope on a COPY (it stamps browser_timezone onto the dict),
    with the preferences block pinned so the on-disk store cannot leak in."""
    with mock.patch.object(preferences, "envelope_block", return_value=prefs):
        return main._turn_envelope(dict(user), dict(body if body is not None else BODY))


def _lines(env):
    return env.split("\n")


# ---------------------------------------------------------------------------
# The line itself
# ---------------------------------------------------------------------------

def test_role_1_reads_end_user_with_name_and_username():
    lines = _lines(_envelope(ALEX))
    assert lines[0].startswith("[Context: now ")
    assert lines[1] == "[Signed-in user: Alex Rivera (ru_alex) — End User]"
    assert len(lines) == 2


def test_role_2_reads_developer():
    assert _lines(_envelope(ERIN))[1] == \
        "[Signed-in user: Erin Walsh (dev_erin) — Developer]"


def test_role_3_reads_admin():
    assert _lines(_envelope(ADMIN))[1] == \
        "[Signed-in user: Administrator (admin) — Admin]"


def test_role_wording_matches_the_users_page():
    """templates/users.html is the platform's own wording; the model and the
    UI must agree on what to call a seat."""
    with open(os.path.join(APP_ROOT, "templates", "users.html"), encoding="utf-8") as f:
        page = f.read()
    for role, label in main._ROLE_LABELS.items():
        assert f'<option value="{role}">{label}</option>' in page, (role, label)
    assert set(main._ROLE_LABELS) == {1, 2, 3}


# ---------------------------------------------------------------------------
# Omitted, never half-filled
# ---------------------------------------------------------------------------

def test_service_principal_gets_no_identity_line():
    env = _envelope(SERVICE)
    assert "[Signed-in user:" not in env
    assert len(_lines(env)) == 1 and env.startswith("[Context: now ")


def test_absent_identity_does_not_error():
    for user in ({}, {"user_id": None}, {"user_id": "abc", "role": "x"},
                 {"user_id": 7, "role": 1},                # no name, no username
                 {"user_id": 7, "role": 1, "name": "  ", "username": ""}):
        env = _envelope(user)
        assert env.startswith("[Context: now "), user
        assert "[Signed-in user:" not in env, user


def test_no_body_and_no_timezone_still_fine():
    env = _envelope(ALEX, body={})
    assert "[Signed-in user: Alex Rivera (ru_alex) — End User]" in env
    with mock.patch.object(preferences, "envelope_block", return_value=""):
        env = main._turn_envelope(dict(ALEX), None)
    assert "[Signed-in user: Alex Rivera (ru_alex) — End User]" in env


def test_unknown_role_omits_the_line_rather_than_inventing_a_label():
    for role in (0, 4, 99, -1, None, "n/a"):
        u = dict(ALEX, role=role)
        assert "[Signed-in user:" not in _envelope(u), role


def test_headless_principal_with_a_real_user_collapses_name_equal_to_username():
    """/api/run stores name := username; the line must not read 'ru_alex (ru_alex)'."""
    u = {"user_id": 5009, "role": 1, "username": "ru_alex", "name": "ru_alex",
         "mode": "headless"}
    assert _lines(_envelope(u))[1] == "[Signed-in user: ru_alex — End User]"
    u = {"user_id": 5009, "role": 1, "username": "RU_ALEX", "name": "ru_alex"}
    assert _lines(_envelope(u))[1] == "[Signed-in user: ru_alex — End User]"


def test_name_only_and_username_only():
    assert _lines(_envelope({"user_id": 3, "role": 2, "name": "Pat Lee"}))[1] == \
        "[Signed-in user: Pat Lee — Developer]"
    assert _lines(_envelope({"user_id": 3, "role": 3, "username": "pat"}))[1] == \
        "[Signed-in user: pat — Admin]"


def test_line_stays_one_short_line():
    """Whitespace/newlines in a display name collapse and the name is capped —
    the envelope rides in front of every turn."""
    u = dict(ALEX, name="Alex\n  Rivera\tJr", username="ru_alex")
    line = _lines(_envelope(u))[1]
    assert line == "[Signed-in user: Alex Rivera Jr (ru_alex) — End User]"
    u = dict(ALEX, name="A" * 500)
    line = _lines(_envelope(u))[1]
    assert line.startswith("[Signed-in user: ") and line.endswith(" — End User]")
    assert len(line) < 200


def test_preferences_block_still_follows_the_identity_line():
    prefs = "\n[Standing preferences this user saved — honor them:\n- weekly by store\n]"
    env = _envelope(ALEX, prefs=prefs)
    lines = _lines(env)
    assert lines[0].startswith("[Context: now ")
    assert lines[1] == "[Signed-in user: Alex Rivera (ru_alex) — End User]"
    assert lines[2].startswith("[Standing preferences")
    assert env.endswith(prefs)


# ---------------------------------------------------------------------------
# Replay strips it with the Context line (the user sees only their own words)
# ---------------------------------------------------------------------------

def test_replay_strips_context_and_identity_lines():
    env = _envelope(ALEX)
    assert chat_history.strip_context_line(env + "\n\nWho am I?") == "Who am I?"
    # the pre-identity shape (transcripts written before 2026-09-08)
    old = _lines(env)[0]
    assert chat_history.strip_context_line(old + "\n\nWho am I?") == "Who am I?"
    # envelope only (nothing after it) -> nothing to show
    assert chat_history.strip_context_line(env) == ""
    # a user's own text that merely mentions the marker is untouched
    own = "[Signed-in user: fake] is what I typed"
    assert chat_history.strip_context_line(own) == own
    assert chat_history.strip_context_line("plain words") == "plain words"


def test_replay_strips_the_preferences_block_too():
    """2026-09-13: the block used to survive into replay — every bubble opened
    with "[Standing preferences …" and deferred markers went unrecognized."""
    with mock.patch.object(preferences, "get", return_value=["weekly by store", "call me Alex"]):
        prefs = preferences.envelope_block(ALEX["user_id"])
    assert prefs.startswith("\n" + preferences.ENVELOPE_OPEN + "\n- weekly by store\n")
    assert prefs.endswith("\n- call me Alex\n" + preferences.ENVELOPE_CLOSE)
    env = _envelope(ALEX, prefs=prefs)
    assert chat_history.strip_context_line(env + "\n\nhello") == "hello"
    assert chat_history.strip_context_line(env) == ""
    # without the identity line the block follows the Context line directly
    env = _envelope(dict(ALEX, role=0), prefs=prefs)
    assert "[Signed-in user:" not in env
    assert chat_history.strip_context_line(env + "\n\nhello") == "hello"


# ---------------------------------------------------------------------------
# Context only — nothing may start trusting the text
# ---------------------------------------------------------------------------

def test_no_tool_or_gate_reads_the_identity_line():
    svc = os.path.join(APP_ROOT, "agent_service")
    offenders = []
    for fn in sorted(os.listdir(svc)):
        if not fn.endswith(".py") or fn in ("main.py", "chat_history.py"):
            continue
        with open(os.path.join(svc, fn), encoding="utf-8", errors="replace") as f:
            if "Signed-in user" in f.read():
                offenders.append(fn)
    assert offenders == [], offenders
    # and main.py only builds it — the gate keeps deciding by the CLAIMS' role
    with open(os.path.join(svc, "main.py"), encoding="utf-8") as f:
        src = f.read()
    assert src.count("Signed-in user") == 2       # docstring example + the f-string
    assert "role < 2 and not AGENT_ALLOW_ALL_USERS" in src


if __name__ == "__main__":
    if not HAVE_SDK:
        print(f"SKIP: {_IMPORT_ERR}")
        sys.exit(0)
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:         # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {e!r}")
    sys.exit(1 if failed else 0)
