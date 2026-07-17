"""
AIHUB-0038 — the builder_distiller can no longer rewrite the honest
persisted-steps block into "✅ created and verified".

Live failure (test pack 09, Scenario F): the AIHUB-0034 chain delivered the
honest block to the CC (`builder_response_raw` was honest), but the
`builder_distiller` mini-LLM in the REAL `build()` node recomposed the final
reply and reinstated the dropped SFTP step as built. The 0034 regression
stopped at the delegator — it never drove build().

These tests drive the REAL `build()` node (command_center_service/graph/nodes.py)
with an ADVERSARIAL distiller mock that always confabulates, and assert the
final user-visible message:
  - dropped capability → distiller is BYPASSED entirely; reply is the
    authoritative persisted-steps block verbatim (disclosure survives),
  - read-back without a drop → the deterministic saved-steps footer is pinned
    after whatever the distiller wrote, and the distiller prompt carries the
    authoritative-steps rule,
  - no read-back at all → behavior unchanged (no footer, distilled text used).
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_CC = str(_ROOT / "command_center_service")


def _import_cc_nodes():
    """Import the COMMAND CENTER graph.nodes (builder_service/graph shadows it)."""
    saved_path = list(sys.path)
    saved_graph = {k: v for k, v in sys.modules.items() if k == "graph" or k.startswith("graph.")}
    try:
        for k in list(saved_graph):
            del sys.modules[k]
        sys.path.insert(0, _CC)
        import graph.nodes as cc_nodes  # noqa: PLC0415
        assert "command_center_service" in cc_nodes.__file__.replace("\\", "/"), \
            f"resolved the wrong graph package: {cc_nodes.__file__}"
        return cc_nodes
    finally:
        sys.path[:] = saved_path


try:
    nodes = _import_cc_nodes()
    from langchain_core.messages import HumanMessage
except Exception as e:  # pragma: no cover
    pytest.skip(f"CC graph.nodes not importable here: {e}", allow_module_level=True)


def _build_reply_mod():
    path = os.path.join(str(_ROOT), "command_center", "orchestration", "build_reply.py")
    spec = importlib.util.spec_from_file_location("_cc_build_reply_0038", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


F1_PROMPT = ("Use the workflow builder to create a workflow named truth-test: query AIRDB "
             "for employee counts per store, save them to a CSV file, and SFTP-upload the "
             "CSV to /outgoing using my AUTODEMO_SFTP secret.")

WF_SAVED_DROPPED = {"workflow_id": 1254, "status": "success",
                    "node_types": ["Database", "Set Variable", "File"]}
PLAN_TEXT = "Query AIRDB. Save CSV. SFTP upload the CSV to /outgoing."

CONFAB = ("✅ Workflow created and verified: 'truth-test'\n"
          "- Query AIRDB\n- Save to CSV\n- Upload the CSV to /outgoing via your "
          "AUTODEMO_SFTP secret\nEverything is ready to use!")


class _AdversarialDistillerLLM:
    """Always rewrites into the historical confabulation — the output pin must win."""

    def __init__(self, record):
        self._record = record

    async def ainvoke(self, msgs):
        self._record.append(msgs)

        class _R:
            content = CONFAB
        return _R()


def _state():
    return {
        "messages": [HumanMessage(content=F1_PROMPT)],
        "session_id": "t-0038",
        "user_context": {"user_id": 1, "role": 3, "tenant_id": 1, "username": "admin"},
        "active_delegation": {},
    }


def _patch_common(monkeypatch, delegator_result, distiller_calls):
    import command_center.orchestration.delegator as delegator_mod

    async def _fake_delegate(**kwargs):
        return dict(delegator_result)

    monkeypatch.setattr(delegator_mod, "delegate_to_builder", _fake_delegate)

    import cc_config

    def _fake_get_step_llm(step_name):
        if step_name == "builder_distiller":
            return _AdversarialDistillerLLM(distiller_calls)

        class _NoLLM:  # affirmative detector etc. — must not be reached
            async def ainvoke(self, msgs):
                raise AssertionError(f"unexpected LLM call for step {step_name}")
        return _NoLLM()

    monkeypatch.setattr(cc_config, "get_step_llm", _fake_get_step_llm)


async def test_dropped_capability_bypasses_distiller_and_keeps_disclosure(monkeypatch):
    br = _build_reply_mod()
    honest_block = br.persisted_steps_block(1254, "success",
                                            WF_SAVED_DROPPED["node_types"], PLAN_TEXT)
    assert "NOT in this workflow" in honest_block  # sanity: fixture really discloses

    distiller_calls = []
    _patch_common(monkeypatch, {
        "text": honest_block,                      # delegator REPLACE already fired
        "status": "completed",
        "plan": {"status": "completed", "steps": []},
        "builder_session_id": "b-0038",
        "workflow_saved": WF_SAVED_DROPPED,
        "dropped_capability": "SFTP/FTP file transfer",
    }, distiller_calls)

    out = await nodes.build(_state())
    reply = out["messages"][0].content

    # the disclosure SURVIVES to the final user-visible text
    assert "NOT in this workflow" in reply
    assert "Code Flow" in reply
    for t in WF_SAVED_DROPPED["node_types"]:
        assert t in reply
    # the confabulation cannot appear — the distiller was never even called
    assert "created and verified" not in reply
    assert "Everything is ready" not in reply
    assert distiller_calls == []
    # and the builder_log's user-visible entry preserves it too
    log = out["active_delegation"]["builder_log"]
    visible = [e for e in log if e["role"] == "cc_user_visible"][-1]["content"]
    assert "NOT in this workflow" in visible


async def test_readback_without_drop_pins_authoritative_footer(monkeypatch):
    wf_saved = {"workflow_id": 1300, "status": "success",
                "node_types": ["Database", "Excel Export"]}
    distiller_calls = []
    _patch_common(monkeypatch, {
        "text": "built the export workflow",
        "status": "completed",
        "plan": {"status": "completed", "steps": []},
        "builder_session_id": "b-0038",
        "workflow_saved": wf_saved,
        "dropped_capability": None,
    }, distiller_calls)

    out = await nodes.build(_state())
    reply = out["messages"][0].content

    # adversarial distiller DID run and wrote its confabulation...
    assert len(distiller_calls) == 1
    # ...but the deterministic read-back footer is pinned after it
    assert "authoritative read-back" in reply
    assert "exactly these 2 step(s)" in reply
    assert "Database → Excel Export" in reply
    assert "Any step not listed here is NOT in the workflow." in reply
    # and the distiller prompt itself carried the authoritative-steps rule
    prompt_text = str(distiller_calls[0][-1].content)
    assert "AUTHORITATIVE SAVED WORKFLOW" in prompt_text
    assert "Database, Excel Export" in prompt_text


async def test_no_readback_behaves_as_before(monkeypatch):
    distiller_calls = []
    _patch_common(monkeypatch, {
        "text": "some builder narration",
        "status": "completed",
        "plan": {"status": "completed", "steps": []},
        "builder_session_id": "b-0038",
        "workflow_saved": None,
        "dropped_capability": None,
    }, distiller_calls)

    out = await nodes.build(_state())
    reply = out["messages"][0].content

    # unchanged legacy behavior: distilled text is the reply, no footer
    assert reply.startswith("✅ Workflow created and verified")
    assert "authoritative read-back" not in reply
    assert len(distiller_calls) == 1
    prompt_text = str(distiller_calls[0][-1].content)
    assert "AUTHORITATIVE SAVED WORKFLOW" not in prompt_text
