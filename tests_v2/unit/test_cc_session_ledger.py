"""
CC_SESSION_LEDGER (docs/cc-session-ledger-design.md) — deterministic hidden
state for the LLM, + the AIHUB-0058 A/B layers that ship with it (pause pin,
self-resolving decide tool). Origin: james's design decision after the
expense-audit tests — the visible reply was the only durable memory, so ids
the LLM paraphrased away were unrecoverable on the next turn.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_CC = str(_ROOT / "command_center_service")


def _sl():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_sl_test", str(Path(_CC) / "graph" / "session_ledger.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestLedgerModule:
    def test_record_caps_per_kind_newest_first(self):
        sl = _sl()
        led = {}
        for i in range(5):
            led = sl.record(led, "automation_version", {"automation_id": f"a{i}",
                                                        "version": i})
        assert len(led["automation_version"]) == 3
        assert led["automation_version"][0]["automation_id"] == "a4"  # newest first
        assert led["automation_version"][-1]["automation_id"] == "a2"  # oldest evicted

    def test_truncation_and_cleaning(self):
        sl = _sl()
        led = sl.record({}, "paused_run", {"question": "q" * 500, "run_id": "r1",
                                           "skipme": None, "dry_run": True})
        e = led["paused_run"][0]
        assert len(e["question"]) == 200
        assert "skipme" not in e
        assert e["dry_run"] is True and "ts" in e

    def test_unknown_kind_ignored(self):
        sl = _sl()
        assert "bogus" not in sl.record({}, "bogus", {"x": 1})

    def test_clear_paused_run_by_run_id(self):
        sl = _sl()
        led = sl.record({}, "paused_run", {"run_id": "r1", "automation_id": "a1"})
        led = sl.record(led, "paused_run", {"run_id": "r2", "automation_id": "a1"})
        led = sl.clear_paused_run(led, run_id="r1")
        assert [e["run_id"] for e in led["paused_run"]] == ["r2"]

    def test_render_includes_facts_and_past_facts_header(self):
        sl = _sl()
        led = sl.record({}, "paused_run", {
            "run_id": "r9", "checkpoint_id": "c9", "automation_name": "expense-audit",
            "question": "Upload 11 rows?", "dry_run": True})
        led = sl.record(led, "automation_version", {"name": "expense-audit", "version": 7})
        led = sl.record(led, "workflow_row", {"workflow_id": "1297",
                                              "name": "daily-store-headcount",
                                              "readback_head": "🧾 Read-back…"})
        block = sl.render(led)
        assert "SESSION STATE (deterministic" in block
        assert "PAST facts" in block and "not" in block  # data-not-instructions header
        assert "r9" in block and "c9" in block and "Upload 11 rows?" in block
        assert "v7" in block and "1297" in block

    def test_empty_ledger_renders_nothing(self):
        sl = _sl()
        assert sl.render({}) == ""
        assert sl.render(None) == ""
        assert sl.render({"paused_run": []}) == ""


class TestLedgerWiring:
    def _nodes_src(self):
        return (Path(_CC) / "graph" / "nodes.py").read_text(encoding="utf-8")

    def test_flag_and_injection_gated(self):
        src = self._nodes_src()
        assert '_SESSION_LEDGER_ENABLED = os.getenv("CC_SESSION_LEDGER", "true")' in src
        inj = src[src.find("inject the"):src.find("llm_messages = [SystemMessage")]
        assert "_SESSION_LEDGER_ENABLED and _ledger" in inj
        assert "_sl.render(_ledger)" in inj

    def test_channel_declared_and_persisted(self):
        gsrc = (Path(_CC) / "graph" / "__init__.py").read_text(encoding="utf-8")
        assert "session_ledger: Optional[Dict[str, Any]]" in gsrc
        csrc = (Path(_CC) / "routes" / "chat.py").read_text(encoding="utf-8")
        assert 'session_state.get("session_ledger")' in csrc
        assert '_ledger = final_state.get("session_ledger") or session_state.get("session_ledger")' in csrc

    def test_recorded_at_both_tool_rounds_and_returned_when_dirty(self):
        src = self._nodes_src()
        assert src.count("_ledger_note(tool_name, tool_args, result)") == 1   # round 1
        assert src.count("_ledger_note(tool_name, tool_args, rn)") == 1       # round N
        assert src.count('result["session_ledger"] = _ledger') >= 3           # all return sites

    def test_ledger_note_covers_the_v1_kinds(self):
        src = self._nodes_src()
        body = src[src.find("def _ledger_note"):src.find("# Execute the tool — ALL tools")]
        assert '"paused_run"' in body and '"automation_version"' in body
        assert '"workflow_row"' in body
        assert "clear_paused_run" in body                  # decided runs don't linger
        assert "ledger note skipped" in body               # bookkeeping never breaks a turn


class TestPausePinAndSelfResolvingDecide:
    def _nodes_src(self):
        return (Path(_CC) / "graph" / "nodes.py").read_text(encoding="utf-8")

    def test_pause_pin_appended_deterministically(self):
        src = self._nodes_src()
        start = src.find("AIHUB-0058 A: pause pin")
        blk = src[start:src.find("# P5-1:", start)]
        assert "⏸️ _Paused run: run_id" in blk
        assert "reply approve or abort" in blk
        assert "_paused_pin[0] not in _cur" in blk         # idempotent

    def test_pin_captured_even_with_ledger_off(self):
        src = self._nodes_src()
        body = src[src.find("def _ledger_note"):src.find("# Execute the tool — ALL tools")]
        pin_set = body.find("_paused_pin = (m.group(1), m.group(2))")
        gate = body.find("if _SESSION_LEDGER_ENABLED", pin_set)
        assert 0 < pin_set < gate                          # pin before the flag gate

    def test_decide_tool_resolves_ids_and_reads_back(self):
        src = self._nodes_src()
        body = src[src.find("async def decide_automation_checkpoint"):]
        body = body[:body.find("@lc_tool", 10)]
        assert 'run_id: str = ""' in body                  # ids optional now
        assert "newest WAITING run" in body
        assert '"list"' in body and '"runs"' in body and '"run_events"' in body
        assert "No paused (waiting) run found" in body     # honest empty case
        assert "_resolved_question" in body                # reads back what it decided
        assert "which automation is this?" in body         # honest ambiguity ask


class TestCrossChatMemoryMasterSwitch:
    """james 2026-07-20: the legacy cross-chat memory injected STALE volatile
    state ('still draft-only') into fresh chats as system-prompt fact — the
    agent repeated it as current truth. Master flag CC_CROSS_CHAT_MEMORY,
    DEFAULT OFF: no injection, no insight writing; nothing deleted (the
    stored memories + Manage Memory UI stay for the future memory system).
    Within-conversation continuity remains the session ledger's job."""

    def _chat_src(self):
        return (Path(_CC) / "routes" / "chat.py").read_text(encoding="utf-8")

    def test_flag_exists_and_defaults_off(self):
        cfg = (Path(_CC) / "cc_config.py").read_text(encoding="utf-8")
        assert 'CROSS_CHAT_MEMORY = os.getenv("CC_CROSS_CHAT_MEMORY", "false")' in cfg
        assert "DISABLED by default" in cfg

    def test_injection_fully_gated(self):
        src = self._chat_src()
        blk = src[src.find("Cross-chat memory injection (LEGACY"):
                  src.find('graph_input["user_memory"] = user_memory_context')]
        assert "if CROSS_CHAT_MEMORY:" in blk
        # both legacy layers live INSIDE the gate
        assert blk.find("if CROSS_CHAT_MEMORY:") < blk.find("get_preferences")
        assert blk.find("if CROSS_CHAT_MEMORY:") < blk.find("get_insights_for_context")
        # flag off ⇒ the empty default string reaches the graph unconditionally
        assert 'user_memory_context = ""' in blk

    def test_insight_writer_master_gated(self):
        src = self._chat_src()
        assert "if USE_SESSION_INSIGHTS and CROSS_CHAT_MEMORY:" in src

    def test_nothing_deleted_routes_and_store_survive(self):
        # the memory routes file must still exist untouched (view/manage only)
        assert (Path(_CC) / "routes" / "memory.py").exists()
