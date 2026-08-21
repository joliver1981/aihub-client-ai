"""Drift-proofing for brain.py's hand-maintained tool lists.

Two lists in agent_service/brain.py must track the registered tool set by
hand, and both have silently drifted before:
- _READ_TOOL_NAMES (the read-only side-thread allowlist) was missing
  list_code_flows (A1) and list_skills (A3) — side-threads silently lacked
  them (found 2026-08-20 during the portal gap analysis).
- MUTATING_TOOLS was missing schedule_view_email (Views v2.1) — the
  mutation-claim guard would flag an HONEST "scheduled the email" reply as
  unverified.

These tests fail the suite whenever a future tool is added without deciding
its list membership. Heuristics are prefix-based with CURATED exclusions —
when a test fails, either add the tool to the list or add it to the relevant
exclusion set WITH A REASON, never loosen the heuristic.

Runs standalone (aihub-agent python test_agent_brain_tool_lists.py) or under
pytest; self-skips in envs without claude_agent_sdk (main-app sweep).
"""
import os
import sys

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

try:
    import brain  # noqa: E402
    from platform_tools import AIHUB_TOOLS          # noqa: E402
    from authoring_tools import AUTHORING_TOOLS     # noqa: E402
    from work_tools import WORK_TOOLS               # noqa: E402
    from views_tools import VIEWS_TOOLS             # noqa: E402
    from integration_tools import INTEGRATION_TOOLS  # noqa: E402
    from file_tools import FILE_TOOLS               # noqa: E402
    from document_tools import DOCUMENT_TOOLS       # noqa: E402
    from portal_tools import PORTAL_TOOLS           # noqa: E402
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
else:
    # Enumerate from the module export lists (NOT brain's env-flag-conditional
    # concat) so the check is independent of AGENT_DOCUMENT_TOOLS /
    # AGENT_PORTAL_TOOLS settings on the box running the suite.
    ALL_TOOLS = (AIHUB_TOOLS + AUTHORING_TOOLS + WORK_TOOLS + VIEWS_TOOLS
                 + INTEGRATION_TOOLS + FILE_TOOLS + DOCUMENT_TOOLS + PORTAL_TOOLS)
    ALL_NAMES = {getattr(t, "name", "") for t in ALL_TOOLS}

# Read-shaped name prefixes. A registered tool matching one of these, not in
# MUTATING_TOOLS and not excluded below, MUST be in _READ_TOOL_NAMES.
_READ_PREFIXES = ("list_", "get_", "describe_", "lookup_", "search_",
                  "query_", "check_")
# Curated exclusions from the read allowlist, each with its reason:
_READ_EXCLUSIONS = {
    # stages a private file copy for the user = a write; side-threads answer
    # questions with evidence, they never produce deliverables
    "check_portal_run",
}

# Mutation-shaped name prefixes. A registered tool matching one of these MUST
# be in MUTATING_TOOLS (the mutation-claim guard's ground truth).
_MUTATING_PREFIXES = ("create_", "save_", "delete_", "schedule_", "promote_",
                      "run_", "dry_run_", "wire_", "add_", "import_",
                      "execute_", "assign_", "store_", "rename_", "decide_",
                      "raise_", "setup_", "draft_")
_MUTATING_EXCLUSIONS = set()  # none today; add with a reason, never loosen


def test_registered_names_are_wellformed():
    assert "" not in ALL_NAMES, "a tool registered without a name"
    assert len(ALL_NAMES) == len(ALL_TOOLS), "duplicate tool names registered"


def test_read_list_entries_are_real_tools():
    ghosts = [n for n in brain._READ_TOOL_NAMES if n not in ALL_NAMES]
    assert not ghosts, f"_READ_TOOL_NAMES entries with no registered tool: {ghosts}"


def test_mutating_list_entries_are_real_tools():
    ghosts = [n for n in brain.MUTATING_TOOLS if n not in ALL_NAMES]
    assert not ghosts, f"MUTATING_TOOLS entries with no registered tool: {ghosts}"


def test_no_tool_is_both_read_and_mutating():
    both = set(brain._READ_TOOL_NAMES) & brain.MUTATING_TOOLS
    assert not both, f"tools in BOTH lists: {both}"


def test_read_allowlist_has_no_drift():
    missing = [n for n in sorted(ALL_NAMES)
               if n.startswith(_READ_PREFIXES)
               and n not in brain.MUTATING_TOOLS
               and n not in _READ_EXCLUSIONS
               and n not in brain._READ_TOOL_NAMES]
    assert not missing, (
        f"read-shaped tools missing from _READ_TOOL_NAMES: {missing} — add "
        "them to the allowlist in brain.py, or to _READ_EXCLUSIONS here with "
        "a reason")


def test_mutating_list_has_no_drift():
    missing = [n for n in sorted(ALL_NAMES)
               if n.startswith(_MUTATING_PREFIXES)
               and n not in _MUTATING_EXCLUSIONS
               and n not in brain.MUTATING_TOOLS]
    assert not missing, (
        f"mutation-shaped tools missing from MUTATING_TOOLS: {missing} — the "
        "mutation-claim guard will false-flag honest replies about them")


def test_known_drift_regressions_pinned():
    # The three concrete drifts found 2026-08-20 stay fixed by name.
    assert "list_code_flows" in brain._READ_TOOL_NAMES
    assert "list_skills" in brain._READ_TOOL_NAMES
    assert "schedule_view_email" in brain.MUTATING_TOOLS


def test_sensitive_fields_map_to_real_tools():
    ghosts = [n for n in brain.SENSITIVE_TOOL_FIELDS if n not in ALL_NAMES]
    assert not ghosts, f"SENSITIVE_TOOL_FIELDS keys with no registered tool: {ghosts}"


if __name__ == "__main__":
    if not HAVE_SDK:
        print(f"SKIP-ALL: {_IMPORT_ERR}")
        sys.exit(0)
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in fns:
        try:
            f()
            print(f"PASS  {n}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {n}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {n}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
