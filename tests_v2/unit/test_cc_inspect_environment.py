"""
CC read-only environment inspection (CC_INSPECT_ENVIRONMENT) — change 3 of the
Tier C remediation (after the stall guard 839de15 and the builder data
dictionary c60407f).

The defect: "What tables are in the ERPDB connection?" routed router→none →
classifier→query → gather_data picked an unrelated data agent (AIRDB5_Wizard)
→ CC returned a DIFFERENT database's tables under a grounded-looking
[Source: ...] footer, with zero discovery tools called.

The fix adds an inspect_environment capability to the mini-LLM router, a
matching second-net rule in the main classifier, converse-side guidance +
discovery-tool binding, three new read-only tools, and a connection-linkage
enrichment for the agent picker (CC_PICKER_CONNECTIONS). Everything hangs off
two default-on kill switches; OFF must be behavior-identical to the
pre-change code.

Layers covered here:
1. Router parser — inspect_environment → intent "chat" when enabled; refused
   (normalized to none) when the kill switch is off, so a stale/hallucinated
   label can never activate the disabled path.
2. Router prompt + strip helper — flag ON prompt teaches the capability; the
   pure strip helper removes every trace (guards the literal-drift hazard of
   the duplicated replace() strings).
3. Classifier second net + converse guidance + binding — source-level pins
   that each addition sits inside its flag gate (same style as
   test_cc_tool_registration_invariant).
4. Landscape summary connection linkage (CC_PICKER_CONNECTIONS) — data agents
   show "queries connection: X" when on, byte-identical formatting when off.
"""
from __future__ import annotations

import json
import re
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_CC = str(_ROOT / "command_center_service")
_NODES_SRC = _ROOT / "command_center_service" / "graph" / "nodes.py"


def _import_cc(module: str):
    saved_path = list(sys.path)
    saved_graph = {k: v for k, v in sys.modules.items() if k == "graph" or k.startswith("graph.")}
    try:
        for k in list(saved_graph):
            del sys.modules[k]
        sys.path.insert(0, _CC)
        mod = __import__(module, fromlist=["_"])
        assert "command_center_service" in mod.__file__.replace("\\", "/")
        return mod
    finally:
        sys.path[:] = saved_path


try:
    nodes = _import_cc("graph.nodes")
    _NODES_OK = True
except Exception:  # pragma: no cover — langchain absent in this env
    _NODES_OK = False

sys.path.insert(0, str(_ROOT))
from command_center.orchestration import landscape_scanner as scanner  # noqa: E402


def _src() -> str:
    return _NODES_SRC.read_text(encoding="utf-8")


def _fake_resp(payload) -> types.SimpleNamespace:
    return types.SimpleNamespace(content=payload if isinstance(payload, str) else json.dumps(payload))


# ─── 1. Router parser ────────────────────────────────────────────────────

@pytest.mark.skipif(not _NODES_OK, reason="graph.nodes not importable in this env")
class TestParserMapping:
    def test_inspect_environment_maps_to_chat_when_enabled(self, monkeypatch):
        monkeypatch.setattr(nodes, "_INSPECT_ENV_ENABLED", True)
        out = nodes._parse_capability_router_response(
            _fake_resp({"capability": "inspect_environment", "confidence": 0.92}))
        assert out["capability"] == "inspect_environment"
        assert out["intent"] == "chat"
        assert out["confidence"] == pytest.approx(0.92)

    def test_kill_switch_refuses_the_label(self, monkeypatch):
        """Flag off → the label normalizes to none even if the mini-LLM (or a
        stale admin prompt override) still emits it. Fail-closed."""
        monkeypatch.setattr(nodes, "_INSPECT_ENV_ENABLED", False)
        out = nodes._parse_capability_router_response(
            _fake_resp({"capability": "inspect_environment", "confidence": 0.99}))
        assert out["capability"] == "none"
        assert out["intent"] is None

    def test_existing_capabilities_unchanged(self, monkeypatch):
        """Regression pin: the pre-change labels behave exactly as before in
        BOTH flag states."""
        for flag in (True, False):
            monkeypatch.setattr(nodes, "_INSPECT_ENV_ENABLED", flag)
            assert nodes._parse_capability_router_response(
                _fake_resp({"capability": "build", "confidence": 0.9}))["intent"] == "build"
            assert nodes._parse_capability_router_response(
                _fake_resp({"capability": "document_search", "confidence": 0.9}))["intent"] == "chat"
            assert nodes._parse_capability_router_response(
                _fake_resp({"capability": "none", "confidence": 0.9}))["intent"] is None
            assert nodes._parse_capability_router_response(
                _fake_resp("not json at all"))["capability"] == "none"


# ─── 2. Router prompt + strip helper ─────────────────────────────────────

@pytest.mark.skipif(not _NODES_OK, reason="graph.nodes not importable in this env")
class TestRouterPromptAndStrip:
    def _formatted(self) -> str:
        return nodes._CAPABILITY_ROUTER_PROMPT.format(
            tool_names="(no custom tools)", user_text="what tables are in ERPDB",
            recent_conversation="")

    def test_flag_on_prompt_teaches_the_capability(self):
        body = self._formatted()
        assert "- inspect_environment: READ-ONLY questions" in body
        assert "CONTAINER vs CONTENTS" in body

    def test_strip_removes_every_trace(self):
        """The kill-switch prompt must be indistinguishable from the pre-change
        prompt: no capability line, no rules, no dangling mention. This is the
        drift guard for the duplicated literals — if someone edits the prompt
        text without updating _strip_inspect_capability, this fails."""
        stripped = nodes._strip_inspect_capability(self._formatted())
        assert "inspect_environment" not in stripped
        assert "CONTAINER vs CONTENTS" not in stripped
        assert "READ-ONLY environment question" not in stripped
        # and it must not have eaten neighbouring capabilities/rules
        assert "- build: creating, configuring" in stripped
        assert "- none: does NOT cleanly match" in stripped
        assert 'Database/data-agent queries (sales, revenue, orders, headcount, inventory metrics)' in stripped

    def test_strip_is_noop_on_pre_change_prompt(self):
        stripped = nodes._strip_inspect_capability(self._formatted())
        assert nodes._strip_inspect_capability(stripped) == stripped


# ─── 3. Source-level flag-gate pins ──────────────────────────────────────

class TestFlagGatesInSource:
    """Everything behavioral must hang off the kill switch — an unconditional
    edit would survive CC_INSPECT_ENVIRONMENT=false and break the revert
    guarantee. Source-level checks, same style as the tool-registration
    invariant test."""

    def test_classifier_second_net_is_flag_gated(self):
        src = _src()
        i = src.index("ADDITIONAL DISTINCTION — schema questions")
        gate = src.rindex("if _INSPECT_ENV_ENABLED:", 0, i)
        # no other statement at the same-or-outer indent between gate and text
        between = src[gate:i]
        assert between.count("\n    if ") <= 1, "second net must sit directly under its flag gate"

    def test_converse_guidance_is_flag_gated(self):
        src = _src()
        i = src.index("## ENVIRONMENT INSPECTION (read-only)")
        gate = src.rindex("if _INSPECT_ENV_ENABLED:", 0, i)
        assert gate != -1 and (i - gate) < 600, "converse guidance must sit under the flag gate"

    def test_binding_block_is_flag_and_role_gated(self):
        src = _src()
        i = src.index("tools.append(list_platform_agents)")
        gate = src.rindex(
            "if _INSPECT_ENV_ENABLED and (_automations_allowed(state) or _workflow_tools_allowed(state)):",
            0, i)
        assert (i - gate) < 900, "new tool binding must sit under flag+role gate"
        block = src[gate:i]
        assert "if list_data_connections not in tools:" in block
        assert "if get_connection_schema not in tools:" in block
        assert "if probe_connection_query not in tools:" in block

    def test_new_tools_are_dual_registered(self):
        """AIHUB-0028 discipline: bound ⊆ tool_map (the standing invariant test
        checks the whole set; this pins the three new names explicitly)."""
        src = _src()
        for name in ("list_platform_agents", "list_secret_names", "list_mcp_servers"):
            assert f'"{name}": {name},' in src, f"{name} missing from tool_map"
            assert f"async def {name}(" in src, f"{name} tool def missing"

    def test_picker_enrichment_is_flag_gated(self):
        src = _src()
        assert '_PICKER_CONNECTIONS_ENABLED and a.get("connection_names")' in src
        i = src.index("NONE: no data agent is configured for connection")
        gate = src.rindex("if _PICKER_CONNECTIONS_ENABLED:", 0, i)
        assert (i - gate) < 600, "picker NONE rule must sit under CC_PICKER_CONNECTIONS"

    def test_router_strip_is_wired_to_the_flag(self):
        src = _src()
        assert "if not _INSPECT_ENV_ENABLED:\n        prompt_body = _strip_inspect_capability(prompt_body)" in src


# ─── 4. Landscape summary connection linkage ─────────────────────────────

class TestLandscapeConnectionLinkage:
    _LANDSCAPE = {
        "agents": [],
        "data_agents": [
            {"agent_id": 260, "agent_name": "AIRDB5_Wizard", "description": "Retail data",
             "enabled": True, "is_data_agent": True, "connection_names": "AIRDB"},
            {"agent_id": 301, "agent_name": "ERP Finance", "description": "ERP finance data",
             "enabled": True, "is_data_agent": True, "connection_names": "ERPDB"},
            {"agent_id": 302, "agent_name": "Legacy", "description": "old",
             "enabled": True, "is_data_agent": True},  # pre-change payload: no key
        ],
        "connections": [], "mcp_servers": [], "all_agents": [],
    }

    def test_connections_shown_when_enabled(self, monkeypatch):
        monkeypatch.setattr(scanner, "_AGENT_CONNECTIONS_ENABLED", True)
        out = scanner.format_landscape_summary(self._LANDSCAPE)
        assert "(queries connection: AIRDB)" in out
        assert "(queries connection: ERPDB)" in out
        # an agent without the field renders exactly as before — no crash, no suffix
        assert "**Legacy** — old" in out

    def test_kill_switch_restores_previous_format(self, monkeypatch):
        monkeypatch.setattr(scanner, "_AGENT_CONNECTIONS_ENABLED", False)
        out = scanner.format_landscape_summary(self._LANDSCAPE)
        assert "queries connection" not in out
        assert "**AIRDB5_Wizard** — Retail data" in out

    def test_scan_merge_defaults_missing_field_to_empty(self):
        """Older platform builds don't return connection_names — the scanner
        must record '' (unknown), never crash or invent."""
        src = (Path(scanner.__file__)).read_text(encoding="utf-8")
        assert '"connection_names": a.get("connection_names") or ""' in src
