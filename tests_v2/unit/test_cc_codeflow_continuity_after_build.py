"""
AIHUB-0043 — code-flow continuity survives a COMPLETED builder delegation.

Live failure (test pack 09, Scenario B ×2 + D self-correct): after any builder
delegation in the session (e.g. scheduling in A-6), `active_delegation`
(agent_type='builder', build_status='completed') stayed in state — and both
code-flow routing guards exempted ANY builder delegation, so they went dark for
the rest of the session. Every terse follow-up then fell through to the
capability router → intent='build' → the visual Builder (which destroyed flow
1256, see AIHUB-0039). Clean sessions routed correctly — the regression was
exactly the stale-delegation state.

These tests drive the REAL `classify_intent` with the tester's session shape
(completed builder delegation present) and assert the guards fire again; the
in-flight exemption is covered via _builder_in_flight directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_CC = str(_ROOT / "command_center_service")


def _import_cc_nodes():
    saved_path = list(sys.path)
    saved_graph = {k: v for k, v in sys.modules.items() if k == "graph" or k.startswith("graph.")}
    try:
        for k in list(saved_graph):
            del sys.modules[k]
        sys.path.insert(0, _CC)
        import graph.nodes as cc_nodes  # noqa: PLC0415
        assert "command_center_service" in cc_nodes.__file__.replace("\\", "/")
        return cc_nodes
    finally:
        sys.path[:] = saved_path


try:
    nodes = _import_cc_nodes()
    from langchain_core.messages import HumanMessage
except Exception as e:  # pragma: no cover
    pytest.skip(f"CC graph.nodes not importable here: {e}", allow_module_level=True)


COMPLETED_BUILDER = {
    "agent_id": "builder", "agent_name": "Builder Agent", "agent_type": "builder",
    "build_status": "completed", "builder_session_id": "b-1", "builder_log": [],
}
IN_FLIGHT_BUILDER = {**COMPLETED_BUILDER, "build_status": "in_progress"}


def _state(text, active=None, code_flow_context=None):
    s = {
        "messages": [HumanMessage(content=text)],
        "session_id": "t-0043",
        "user_context": {"user_id": 1, "role": 3, "tenant_id": 1, "username": "admin"},
    }
    if active is not None:
        s["active_delegation"] = active
    if code_flow_context is not None:
        s["code_flow_context"] = code_flow_context
    return s


class TestBuilderInFlight:
    def test_completed_build_is_not_in_flight(self):
        assert nodes._builder_in_flight(COMPLETED_BUILDER) is False

    @pytest.mark.parametrize("status", ["partial", "failed"])
    def test_terminal_builds_are_not_in_flight(self, status):
        assert nodes._builder_in_flight({**COMPLETED_BUILDER, "build_status": status}) is False

    def test_in_progress_build_is_in_flight(self):
        assert nodes._builder_in_flight(IN_FLIGHT_BUILDER) is True

    def test_missing_status_defaults_to_in_flight(self):
        # fail-safe: an untagged builder delegation is treated as live
        a = {k: v for k, v in COMPLETED_BUILDER.items() if k != "build_status"}
        assert nodes._builder_in_flight(a) is True

    def test_non_builder_delegations_never_in_flight(self):
        assert nodes._builder_in_flight({"agent_type": "data", "build_status": "in_progress"}) is False
        assert nodes._builder_in_flight(None) is False


class TestGuardsFireAfterCompletedBuild:
    """The tester's exact live repro shape: completed builder delegation in state."""

    async def test_code_process_request_reclaims_routing(self):
        # Scenario C-as-scripted wording — previously went to the Builder and
        # destroyed flow 1256.
        out = await nodes.classify_intent(_state(
            "Add a step to store-headcount (before the upload) that opens the expense "
            "PDF with pdfplumber, extracts the TOTAL, and logs it. Dry-run the flow.",
            active=COMPLETED_BUILDER,
        ))
        assert out["intent"] == "chat"
        assert out.get("active_delegation") is None   # stale delegation cleared

    async def test_terse_followup_with_context_reclaims_routing(self):
        # Scenario B-1 wording.
        out = await nodes.classify_intent(_state(
            "add a step that logs a one-line summary and dry-run it again",
            active=COMPLETED_BUILDER,
            code_flow_context={"name": "store-headcount"},
        ))
        assert out["intent"] == "chat"
        assert out.get("active_delegation") is None

    async def test_clean_session_still_routes_to_chat(self):
        # control: no delegation at all (worked before, must keep working)
        out = await nodes.classify_intent(_state(
            "add a step that logs a one-line summary and dry-run it again",
            code_flow_context={"name": "store-headcount"},
        ))
        assert out["intent"] == "chat"
