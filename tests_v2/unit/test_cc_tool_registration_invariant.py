"""
Static guard for the CC converse-node dual-registration trap (AIHUB-0028):
every tool BOUND to the LLM must also be executable via `tool_map`, or a call
falls through to "Unknown tool: <name>" and silently does nothing.

This parses command_center_service/graph/nodes.py at the source level (no heavy
import of the CC service env) and asserts: {names in the `tools = [...]` literal}
∪ {names in every `tools.append(x)`} ⊆ {keys of `tool_map = {...}`}.

It fails loudly if someone adds a new tool (e.g. a code-flow tool) to the bind
site but forgets the tool_map — exactly the bug that broke create_automation.
"""
from __future__ import annotations

import os
import re

import pytest

pytestmark = pytest.mark.unit

_NODES = os.path.join(os.path.dirname(__file__), "..", "..",
                      "command_center_service", "graph", "nodes.py")


def _read():
    with open(os.path.abspath(_NODES), "r", encoding="utf-8") as f:
        return f.read()


def _tool_map_keys(src: str) -> set:
    start = src.index("tool_map = {")
    # walk braces to find the matching close
    depth = 0
    i = src.index("{", start)
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                block = src[i:j + 1]
                break
    return set(re.findall(r'"(\w+)"\s*:', block))


def _bound_names(src: str) -> set:
    names = set(re.findall(r"tools\.append\((\w+)\)", src))
    m = re.search(r"^\s*tools = \[([^\]]*)\]", src, re.MULTILINE)
    if m:
        names |= {n.strip() for n in m.group(1).split(",") if n.strip()}
    return names


def test_every_bound_tool_is_in_tool_map():
    src = _read()
    bound = _bound_names(src)
    mapped = _tool_map_keys(src)
    missing = sorted(bound - mapped)
    assert not missing, (
        "these tools are bound to the LLM but missing from tool_map "
        f"(they would return 'Unknown tool'): {missing}")


def test_code_flow_tools_are_registered_both_places():
    """Belt-and-suspenders for the new authoring tools specifically."""
    src = _read()
    bound = _bound_names(src)
    mapped = _tool_map_keys(src)
    cf_tools = {"list_code_flows", "create_code_flow", "add_code_step", "wire_steps",
                "update_step_code", "get_code_flow", "dry_run_code_flow",
                "run_code_flow", "schedule_code_flow"}
    assert cf_tools <= bound, f"code-flow tools not bound: {sorted(cf_tools - bound)}"
    assert cf_tools <= mapped, f"code-flow tools not in tool_map: {sorted(cf_tools - mapped)}"
