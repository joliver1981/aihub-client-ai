"""REAL-USER: attach a file to a general agent on /chat — paperclip AND drag & drop.

Both gestures end in the same upload (addFilesToQueue → POST
/add/agent_knowledge → the file listed under "Agent Files"), but each one is
wired on its own:

  - Paperclip: #attach-btn opens the hidden #contextFileInput picker, and the
    picked file only uploads if that input's 'change' listener is bound. From
    350869d (2026-05-29) until 2026-11-05 it wasn't — the listener sat behind
    an early return for the removed #documentUploadZone markup — so the picker
    opened, then nothing happened, with no error anywhere.
  - Drag & drop: document-level listeners show #dropOverlay and queue
    e.dataTransfer.files on drop.

Each test makes its own JOURNEY_TEST_ general agent, hands the page a tiny .txt
through the real UI (Playwright's file chooser / a DataTransfer carrying a
File), and checks that the server accepted it and the file shows up under
Agent Files. Teardown deletes the uploads, then the agent. No LLM call: this
guards the wiring, not answer quality (test_real_user_agent_knowledge_qa.py
covers that).
"""
from __future__ import annotations

import uuid

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, expect

from .conftest import ARTIFACT_PREFIX, MAIN_BASE


# Processing a one-line .txt takes ~10 s; the headroom is for a busy Doc API.
UPLOAD_TIMEOUT_MS = 120_000
# The gesture must turn into an upload request almost at once. Failing fast
# here is what tells "nothing sent the file" apart from "the upload was slow".
REQUEST_TIMEOUT_MS = 10_000

# One drag event carrying a DataTransfer; returns whether the page cancelled
# it (a real browser only allows the drop when dragover is cancelled).
_FIRE_DRAG_JS = """([selector, type, dataTransfer]) => {
    const ev = new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer });
    document.querySelector(selector).dispatchEvent(ev);
    return ev.defaultPrevented;
}"""

_MAKE_DATA_TRANSFER_JS = """([name, text]) => {
    const dt = new DataTransfer();
    dt.items.add(new File([text], name, { type: 'text/plain' }));
    return dt;
}"""


def _probe_name(kind: str) -> str:
    return f"{ARTIFACT_PREFIX}{kind}_{uuid.uuid4().hex[:6]}.txt"


def _probe_text(name: str) -> str:
    return f"Journey test attachment {name}. Safe to delete.\n"


def _is_upload_request(req) -> bool:
    return req.method == "POST" and req.url.endswith("/add/agent_knowledge")


def _is_upload_response(resp) -> bool:
    return _is_upload_request(resp.request)


@pytest.fixture
def attach_agent(http_session, cleanup_artifacts):
    """A throwaway general agent. Teardown deletes its uploads, then the agent."""
    s = http_session
    name = f"{ARTIFACT_PREFIX}ATTACH_{uuid.uuid4().hex[:8].upper()}"
    r = s.post(
        f"{MAIN_BASE}/add/agent",
        json={
            "agent_id": 0,
            "agent_description": name,
            "agent_objective": "Journey-test agent for /chat file attachments. Safe to delete.",
            "agent_enabled": True,
            "core_tool_names": [],
            "tool_names": [],
        },
        timeout=30,
    )
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or body.get("status") != "success":
        pytest.fail(f"Could not create the test agent: HTTP {r.status_code} {r.text[:200]}")
    agent_id = int(body["message"])  # /add/agent answers {"status": "success", "message": <new id>}
    cleanup_artifacts["agents"].append(agent_id)

    yield {"id": agent_id, "name": name}

    # Uploads first, so no knowledge rows outlive their agent.
    try:
        docs = s.get(f"{MAIN_BASE}/get/agent_knowledge_user/{agent_id}", timeout=15).json() or []
        for doc in docs:
            s.post(f"{MAIN_BASE}/delete/agent_knowledge/{int(doc['knowledge_id'])}", timeout=30)
    except Exception as e:
        print(f"[attach cleanup] could not delete the uploads on agent {agent_id}: {e}")
    try:
        s.post(f"{MAIN_BASE}/delete/agent", json={"agent_id": agent_id}, timeout=15)
    except Exception as e:
        print(f"[attach cleanup] could not delete agent {agent_id}: {e}")


def _open_chat(page, agent: dict) -> None:
    """Open /chat with the test agent preselected and wait for the paperclip."""
    page.goto(f"{MAIN_BASE}/chat?agent={agent['id']}", timeout=20000)
    assert "/login" not in page.url, "Auth lost on /chat"
    # populateForm() falls back to the FIRST agent when ?agent= doesn't match;
    # pin it so an upload can only ever land on the throwaway agent.
    expect(page.locator("#agent_id")).to_have_value(str(agent["id"]), timeout=15000)
    expect(page.locator("#attach-btn")).to_be_visible()


def _upload_through_ui(page, gesture, how: str) -> dict:
    """Run the gesture that hands the page a file; return the server's JSON answer."""
    with page.expect_response(_is_upload_response, timeout=UPLOAD_TIMEOUT_MS) as resp_info:
        try:
            with page.expect_request(_is_upload_request, timeout=REQUEST_TIMEOUT_MS):
                gesture()
        except PlaywrightTimeoutError:
            pytest.fail(
                f"{how}: the page got the file but sent no POST /add/agent_knowledge "
                f"within {REQUEST_TIMEOUT_MS // 1000}s; nothing is listening for it"
            )
    resp = resp_info.value
    assert resp.ok, f"{how}: /add/agent_knowledge answered HTTP {resp.status}: {resp.text()[:200]}"
    body = resp.json()
    assert body.get("status") == "success", f"{how}: the upload was refused: {body}"
    return body


def _expect_listed(page, name: str) -> None:
    """The file shows up under Agent Files (reloaded from /get/agent_knowledge_user)."""
    # Match on the title attribute: the visible label is cut at 38 characters.
    expect(page.locator(f'#uploadResults small[title="{name}"]')).to_be_visible(timeout=15000)


def _fire_drag(page, selector: str, event_type: str, data_transfer) -> bool:
    return page.evaluate(_FIRE_DRAG_JS, [selector, event_type, data_transfer])


@pytest.mark.slow
@pytest.mark.journey
def test_paperclip_uploads_file_to_agent(authed_page, attach_agent):
    page = authed_page
    _open_chat(page, attach_agent)
    name = _probe_name("clip")

    try:
        with page.expect_file_chooser(timeout=REQUEST_TIMEOUT_MS) as chooser_info:
            page.locator("#attach-btn").click()
    except PlaywrightTimeoutError:
        pytest.fail("Clicking the paperclip did not open a file picker")
    chooser = chooser_info.value

    _upload_through_ui(
        page,
        lambda: chooser.set_files(
            files=[{"name": name, "mimeType": "text/plain", "buffer": _probe_text(name).encode()}]
        ),
        "Paperclip",
    )
    _expect_listed(page, name)


@pytest.mark.slow
@pytest.mark.journey
def test_drag_and_drop_uploads_file_to_agent(authed_page, attach_agent):
    page = authed_page
    _open_chat(page, attach_agent)
    name = _probe_name("drop")
    data_transfer = page.evaluate_handle(_MAKE_DATA_TRANSFER_JS, [name, _probe_text(name)])
    overlay = page.locator("#dropOverlay")

    _fire_drag(page, "#chat-window", "dragenter", data_transfer)
    expect(overlay).to_contain_class("visible")
    assert _fire_drag(page, "#dropOverlay", "dragover", data_transfer), (
        "dragover is not cancelled, so a real browser would refuse the drop "
        "(and open the file in the tab instead)"
    )

    _upload_through_ui(
        page, lambda: _fire_drag(page, "#dropOverlay", "drop", data_transfer), "Drag & drop"
    )
    expect(overlay).not_to_contain_class("visible")
    _expect_listed(page, name)
