"""
aihub_runtime chat-lane refusals name a REAL alternative per verb (2026-09-05).

One shared sentence used to send every blocked verb to "the chat's own tools
(e.g. its email tool)". That is true for send_email only: no chat tool runs an
LLM inside a script, and a chat has no supervised run to pause. These tests
pin the per-verb text so the refusal the model reads stays honest, and that an
automation-flavor token is never blocked.
"""
from __future__ import annotations

import os
import sys

import pytest

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "automations", "sdk"))

import aihub_runtime as sdk  # noqa: E402
import shared_auth  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture
def chat_token(monkeypatch):
    monkeypatch.setenv("CC_JWT_SECRET", "test-secret-chat-lane")
    tok = shared_auth.sign_code_run_token("the-agent", "run1", connections=["ERPDB"], user_id=7)
    monkeypatch.setenv("AIHUB_RUN_TOKEN", tok)
    return tok


@pytest.fixture
def automation_token(monkeypatch):
    monkeypatch.setenv("CC_JWT_SECRET", "test-secret-chat-lane")
    tok = shared_auth.sign_automation_run_token("run1", "auto1", connections=["ERPDB"], secrets=[])
    monkeypatch.setenv("AIHUB_RUN_TOKEN", tok)
    return tok


class TestChatLaneMessages:
    def test_every_blocked_verb_has_its_own_alternative(self, chat_token):
        for verb in ("send_email", "checkpoint", "review_item", "llm/ai_extract"):
            msg = sdk._chat_lane_block(verb)
            assert msg and msg.startswith(f"aihub.{verb}() is not available from a chat run_python execution"), msg
            assert verb in sdk._CHAT_LANE_ALTERNATIVES

    def test_llm_points_at_automations_not_a_phantom_tool(self, chat_token):
        msg = sdk._chat_lane_block("llm/ai_extract")
        assert "Automation" in msg and "reasoning yourself" in msg
        assert "No chat tool runs an LLM" in msg
        assert "email tool" not in msg

    def test_send_email_points_at_the_chat_email_tool(self, chat_token):
        msg = sdk._chat_lane_block("send_email")
        # the shared first sentence names "a saved Automation" for every verb;
        # the ALTERNATIVE for email must be the chat's tool, not "build an Automation"
        assert "email tool" in msg and "build an" not in msg

    def test_checkpoint_points_at_the_conversation(self, chat_token):
        msg = sdk._chat_lane_block("checkpoint")
        assert "ask the user directly" in msg

    def test_review_item_points_at_the_reply(self, chat_token):
        msg = sdk._chat_lane_block("review_item")
        assert "Report the exceptions in your reply" in msg

    def test_unknown_verb_still_gets_a_generic_but_honest_line(self, chat_token):
        msg = sdk._chat_lane_block("something_new")
        assert msg and "Use the chat's own tools for this instead." in msg
        assert "email tool" not in msg

    def test_automation_flavor_is_never_blocked(self, automation_token):
        for verb in ("send_email", "checkpoint", "review_item", "llm/ai_extract"):
            assert sdk._chat_lane_block(verb) is None

    def test_no_token_is_not_a_chat_block(self, monkeypatch):
        monkeypatch.delenv("AIHUB_RUN_TOKEN", raising=False)
        assert sdk._chat_lane_block("llm/ai_extract") is None


class TestBlockedCallsRaiseTheHonestText:
    def test_llm_raises_with_the_automation_pointer(self, chat_token):
        with pytest.raises(sdk.AutomationRuntimeError) as ei:
            sdk.llm("summarize this")
        assert "ephemeral) Automation" in str(ei.value) and "email tool" not in str(ei.value)

    def test_ai_extract_raises_the_same_way(self, chat_token):
        with pytest.raises(sdk.AutomationRuntimeError) as ei:
            sdk.ai_extract("extract", schema={"x": "string"})
        assert "No chat tool runs an LLM" in str(ei.value)

    def test_checkpoint_raises_with_the_conversation_pointer(self, chat_token):
        with pytest.raises(sdk.AutomationRuntimeError) as ei:
            sdk.checkpoint("ok to write?")
        assert "ask the user directly" in str(ei.value)
