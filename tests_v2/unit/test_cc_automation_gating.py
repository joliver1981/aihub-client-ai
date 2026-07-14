"""
Regression tests for AIHUB-0028 findings F1 + F2 (CC automation tool gating
and build-guard routing).

F1: a message about an AUTOMATION must never take the deterministic
    build-guard route to the Builder Agent (which doesn't know the asset type
    and role-plays the work) — automation operations stay with the converse
    automation tools.
F2: a non-Developer must be refused automations access at the CC layer, not
    just server-side — enforced at tool BIND time via _automations_allowed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_CC = str(Path(__file__).resolve().parents[2] / "command_center_service")


def _import_cc_nodes():
    """Import the COMMAND CENTER graph.nodes. Both builder_service and
    command_center_service ship a `graph` package and the shared conftest puts
    builder_service first on sys.path — so import with CC forced to the front
    and the module cache purged, then restore everything so other tests keep
    resolving the builder's package."""
    saved_path = list(sys.path)
    saved_mods = {k: v for k, v in sys.modules.items()
                  if k == "graph" or k.startswith("graph.")}
    try:
        for k in list(saved_mods):
            del sys.modules[k]
        sys.path.insert(0, _CC)
        import graph.nodes as cc_nodes  # noqa: PLC0415
        assert "command_center_service" in cc_nodes.__file__.replace("\\", "/"), \
            f"resolved the wrong graph package: {cc_nodes.__file__}"
        return cc_nodes
    finally:
        sys.path[:] = saved_path
        for k in [k for k in sys.modules if k == "graph" or k.startswith("graph.")]:
            del sys.modules[k]
        sys.modules.update(saved_mods)


try:
    nodes = _import_cc_nodes()
except Exception as e:  # pragma: no cover - env-dependent
    pytest.skip(f"CC graph.nodes not importable here: {e}", allow_module_level=True)


class TestBuildGuardAutomationExclusion:
    """F1 — automations never route to the Builder Agent."""

    def test_automation_requests_stay_with_converse(self):
        for text in [
            "create an automation that reads the vendor PDFs and uploads a CSV",
            "save this code to the payroll automation",
            "update the automation to add a checkpoint before the upload",
            "build me an automation for the nightly invoice export workflow",
            "delete the old invoice automation",
        ]:
            assert nodes._is_explicit_build_request(text) is False, text

    def test_genuine_builds_still_route_to_builder(self):
        for text in [
            "create a data agent for AIRDB",
            "create a connection to ERPDB",
            "build a workflow that queries sales and exports to excel",
            "register an mcp server for the docs api",
        ]:
            assert nodes._is_explicit_build_request(text) is True, text

    def test_question_guard_unaffected(self):
        assert nodes._is_explicit_build_request("how do I create a workflow?") is False


class TestAutomationsRoleGate:
    """F2 — _automations_allowed drives both the bind-time tool gate and the
    prompt branch; it must be strict for role<2 including string roles."""

    def _state(self, role):
        return {"user_context": {"user_id": 9, "role": role}}

    def test_viewer_refused(self, monkeypatch):
        monkeypatch.setattr(nodes, "_AUTOMATIONS_ALLOW_ALL", False)
        assert nodes._automations_allowed(self._state(1)) is False
        assert nodes._automations_allowed(self._state("1")) is False
        assert nodes._automations_allowed(self._state(0)) is False
        assert nodes._automations_allowed(self._state(None)) is False
        assert nodes._automations_allowed({}) is False

    def test_developer_and_admin_allowed(self, monkeypatch):
        monkeypatch.setattr(nodes, "_AUTOMATIONS_ALLOW_ALL", False)
        assert nodes._automations_allowed(self._state(2)) is True
        assert nodes._automations_allowed(self._state("2")) is True
        assert nodes._automations_allowed(self._state(3)) is True

    def test_allow_all_flag_opens_gate(self, monkeypatch):
        monkeypatch.setattr(nodes, "_AUTOMATIONS_ALLOW_ALL", True)
        assert nodes._automations_allowed(self._state(1)) is True

    def test_bind_time_gate_present_in_source(self):
        """The tools must be gated at BIND time (not only inside each tool):
        the append block must be conditioned on _automations_allowed(state)."""
        src = Path(nodes.__file__).read_text(encoding="utf-8")
        assert "_AUTOMATIONS_TOOLS_ENABLED and _automations_allowed(state):" in src

    def test_non_developer_gets_explicit_refusal_prompt(self):
        """The system prompt must tell the LLM to refuse (not substitute
        workflow data) when the user lacks the role."""
        src = Path(nodes.__file__).read_text(encoding="utf-8")
        assert "AUTOMATIONS — NOT AVAILABLE TO THIS USER" in src
