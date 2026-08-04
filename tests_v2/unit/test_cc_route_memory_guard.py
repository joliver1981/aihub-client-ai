"""
Route memory must yield to explicit agent-id references.

Pack 16 b4 live failure (run 20260804_024717): "ask agent 999999 how many
stores are there" normalized to "store count", matched a confident learned
route (agent 281, usage crossed the >=2 @ >=70% threshold as earlier checks in
the same run logged successes), and CC answered from agent 281 with a
"(learned route)" header instead of admitting agent 999999 does not exist.
The shortcut fired BEFORE the fail-closed id resolver could run.

The guard (_route_memory_yields_to_id_ref) is deterministic and landscape-free:
_AGENT_ID_REF_RE extracts id tokens (format extraction only — "agent(s)" must
cue the number, glued digits never match), and any referenced id that is not
exactly the remembered route's agent sends the turn through normal
classification. It can only ever fall back to the full path, never guess.

Also pinned here: the recall_all_memories "learned routes" stats block is
shown only while USE_ROUTE_MEMORY is on — with CC_ROUTE_MEMORY=false the
memory recall must not advertise a switched-off capability.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_CC = str(_ROOT / "command_center_service")


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


def _agent_route(agent_id="281"):
    return {"agent_id": agent_id, "agent_name": "Retail Demo", "intent": "query",
            "is_cc_tool": False, "success_rate": 1.0, "usage_count": 9,
            "normalized_query": "store count"}


def _cc_tool_route():
    return {"agent_id": "cc:search_documents", "intent": "chat",
            "is_cc_tool": True, "success_rate": 1.0, "usage_count": 5,
            "normalized_query": "find contracts"}


@pytest.mark.skipif(not _NODES_OK, reason="graph.nodes not importable in this env")
class TestRouteMemoryYieldsToIdRef:
    def test_no_id_reference_keeps_shortcut(self):
        assert nodes._route_memory_yields_to_id_ref(
            "how many stores are there", _agent_route()) is False

    def test_same_agent_id_keeps_shortcut(self):
        """Naming the remembered agent is consistent — shortcut allowed."""
        assert nodes._route_memory_yields_to_id_ref(
            "ask agent 281 how many stores there are", _agent_route("281")) is False

    def test_unknown_id_yields(self):
        """The b4 defect verbatim: unknown id must fall through fail-closed."""
        assert nodes._route_memory_yields_to_id_ref(
            "ask agent 999999 how many stores are there", _agent_route("281")) is True

    def test_different_known_id_yields(self):
        assert nodes._route_memory_yields_to_id_ref(
            "ask agent 283 how many stores there are", _agent_route("281")) is True

    def test_multiple_cued_ids_yield(self):
        """Several ids (even including the remembered one) → the full
        classifier owns multi-agent turns (pack 16 b3 shape). Each id needs
        its own 'agent' cue — the _AGENT_ID_REF_RE contract (a5 'bothcued')."""
        assert nodes._route_memory_yields_to_id_ref(
            "compare agent 281 and agent 283 on store counts", _agent_route("281")) is True

    def test_uncued_second_id_follows_regex_contract(self):
        """Known limitation inherited from _AGENT_ID_REF_RE (deliberate,
        fail-closed format extraction): in 'agents 281 and 283' the second
        number has no 'agent' cue of its own, so only 281 extracts and the
        shortcut stays allowed. Same behavior as the standing id resolver —
        pinned here so a future regex change is a conscious one."""
        assert nodes._route_memory_yields_to_id_ref(
            "compare agents 281 and 283 on store counts", _agent_route("281")) is False

    def test_cc_tool_route_yields_to_any_id(self):
        assert nodes._route_memory_yields_to_id_ref(
            "ask agent 47 to find the lease contracts", _cc_tool_route()) is True

    def test_glued_digits_are_not_id_references(self):
        """The _AGENT_ID_REF_RE contract: 'AIRDB2' must never read as agent 2,
        and an uncued number is not an id reference."""
        assert nodes._route_memory_yields_to_id_ref(
            "how many stores are in AIRDB2", _agent_route("281")) is False
        assert nodes._route_memory_yields_to_id_ref(
            "show the top 5 products", _agent_route("281")) is False

    def test_int_vs_str_agent_id(self):
        """route_match agent_id may arrive as int — comparison must not care."""
        assert nodes._route_memory_yields_to_id_ref(
            "ask agent 281 for store count", _agent_route(281)) is False
        assert nodes._route_memory_yields_to_id_ref(
            "ask agent 999999 for store count", _agent_route(281)) is True


class TestGuardWiredInSource:
    """The helper must actually gate the shortcut, and the stats display must
    honor USE_ROUTE_MEMORY (source-level pins, same style as the other
    invariant tests)."""

    def _src(self) -> str:
        p = _ROOT / "command_center_service" / "graph" / "nodes.py"
        return p.read_text(encoding="utf-8")

    def test_shortcut_checks_the_guard(self):
        src = self._src()
        i = src.index("Route memory hit:")
        gate = src.rindex("_route_memory_yields_to_id_ref(user_text, route_match)", 0, i)
        assert (i - gate) < 900, "guard must run before the shortcut fires"

    def test_stats_display_gated_by_flag(self):
        src = self._src()
        i = src.index("**Learned routes:**")
        gate = src.rindex("USE_ROUTE_MEMORY", 0, i)
        assert (i - gate) < 900, "recall_all_memories stats must be behind USE_ROUTE_MEMORY"
