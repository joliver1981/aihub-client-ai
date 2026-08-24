"""Portal workflow auto-naming + collision-safe slugs (2026-08-23) — unit tests.

The historical bug: every recorded portal workflow saved as "Recorded workflow"
-> slug "recorded_workflow", so each new recording silently OVERWROTE the last
(they all collide on one key). Fix, all in the shared store
(command_center/tools/portal_workflows.py):

  * is_generic_name / derive_name_from_goal — deterministic helpers.
  * save_workflow: a generic/blank name is re-derived from the goal, and a
    recording's slug is made collision-safe (a re-record of the SAME task
    updates in place; a different task gets a _2/_3 suffix). Intentional saves
    (auto_record=False + a real name) keep the exact prior overwrite semantics.

Pure/deterministic, no LLM and no services. Runs standalone
(python test_portal_workflow_naming.py) or under pytest.
"""
import os
import sys
import tempfile

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)

import importlib  # noqa: E402


def _fresh_store():
    """Point the store at a throwaway APP_ROOT/data dir and reload it."""
    d = tempfile.mkdtemp(prefix="pwf-naming-")
    os.makedirs(os.path.join(d, "data"), exist_ok=True)
    os.environ["APP_ROOT"] = d
    from command_center.tools import portal_workflows as pwf
    importlib.reload(pwf)
    return pwf


_LOGIN = [{"type": "goto", "url": "https://acme.example/login"},
          {"type": "login", "username_anchor": {"css": "#u"},
           "password_anchor": {"css": "#p"}, "submit_anchor": {"css": "#go"}}]


# ---------------------------------------------------------------- helpers
def test_is_generic_name():
    pwf = _fresh_store()
    for g in ["Recorded workflow", "recorded workflow", "RECORDED WORKFLOW",
              "Recorded portal run", "", "   ", "<<<"]:
        assert pwf.is_generic_name(g) is True, g
    for real in ["Master Price List Download", "Vendor Invoice 2FA", "acme upload"]:
        assert pwf.is_generic_name(real) is False, real


def test_derive_name_from_goal():
    pwf = _fresh_store()
    cases = {
        "Download the master price list": "Download the master price list",
        "go to the portal and download the latest invoice":
            "The portal and download the latest invoice",   # filler stripped, first letter capitalized
        "please log in and grab the statement": "Grab the statement",
        "  Log In To  the   vendor site and  export orders ": "The vendor site and export orders",
        "": "",
        "!!!": "",
    }
    for goal, expect in cases.items():
        got = pwf.derive_name_from_goal(goal)
        assert got == expect, f"{goal!r} -> {got!r} (want {expect!r})"
    # never blank for a real goal, always sluggable
    n = pwf.derive_name_from_goal("download the Q4 numbers")
    assert n and pwf.slug(n)


# ---------------------------------------------------------------- save_workflow
def test_generic_recording_is_named_from_goal():
    pwf = _fresh_store()
    saved = pwf.save_workflow(1, "Recorded workflow", _LOGIN, None,
                              "https://acme.example/login", "download the master price list",
                              auto_record=True)
    assert saved["name"] == "Download the master price list"
    assert saved["slug"] == "download_the_master_price_list"
    # generic name with NO goal still avoids the clobber (keeps a reachable slug)
    s2 = pwf.save_workflow(1, "Recorded workflow", _LOGIN, None, None, None, auto_record=True)
    assert s2["slug"] == "recorded_workflow"


def test_two_different_recordings_do_not_overwrite():
    pwf = _fresh_store()
    a = pwf.save_workflow(1, "Recorded workflow", _LOGIN, None,
                          "https://acme.example/login", "download the invoice", auto_record=True)
    b = pwf.save_workflow(1, "Recorded workflow", _LOGIN, None,
                          "https://acme.example/login", "download the statement", auto_record=True)
    assert a["slug"] != b["slug"]
    names = {w["slug"]: w["name"] for w in pwf.list_workflows(1)}
    assert len(names) == 2 and set(names.values()) == {"Download the invoice", "Download the statement"}


def test_same_task_rerecord_updates_in_place():
    pwf = _fresh_store()
    a = pwf.save_workflow(1, "Recorded workflow", _LOGIN, None,
                          "https://acme.example/login", "download the invoice", auto_record=True)
    # same start_url + goal, more steps -> same slug, updated definition (no dup)
    more = _LOGIN + [{"type": "click", "anchor": {"text": "Invoices"}}]
    b = pwf.save_workflow(1, "Recorded workflow", more, None,
                          "https://acme.example/login", "download the invoice", auto_record=True)
    assert a["slug"] == b["slug"]
    wfs = pwf.list_workflows(1)
    assert len(wfs) == 1 and wfs[0]["step_count"] == len(more)


def test_same_task_rerecord_with_different_name_updates_in_place():
    pwf = _fresh_store()
    a = pwf.save_workflow(1, "Download Master Price List", _LOGIN, None,
                          "https://acme.example/login", "download the master price list",
                          auto_record=True)
    # SAME task, a DIFFERENT (LLM-varied) name the second time -> reuse the
    # existing entry and KEEP its established name (no near-duplicate, no flip).
    b = pwf.save_workflow(1, "Master Price List Download", _LOGIN + [{"type": "click", "anchor": {"text": "x"}}],
                          None, "https://acme.example/login", "download the master price list",
                          auto_record=True)
    assert a["slug"] == b["slug"] == "download_master_price_list"
    assert b["name"] == "Download Master Price List"        # first name kept
    wfs = pwf.list_workflows(1)
    assert len(wfs) == 1 and wfs[0]["step_count"] == 3      # updated in place


def test_llm_style_name_kept_and_uniquified_on_collision():
    pwf = _fresh_store()
    # caller (the agent) passes a crisp non-generic name + auto_record
    a = pwf.save_workflow(1, "Master Price List", _LOGIN, None,
                          "https://acme.example/login", "download the master price list",
                          auto_record=True)
    assert a["slug"] == "master_price_list"
    # a DIFFERENT task the model happened to name the same -> suffixed, not clobbered
    b = pwf.save_workflow(1, "Master Price List", _LOGIN, None,
                          "https://other.example/login", "download the wholesale sheet",
                          auto_record=True)
    assert b["slug"] == "master_price_list_2"
    assert len(pwf.list_workflows(1)) == 2


def test_intentional_save_still_overwrites_in_place():
    pwf = _fresh_store()
    # builder-UI style: a real name, auto_record defaults False -> classic update-in-place
    pwf.save_workflow(1, "My Portal Flow", _LOGIN, None, "https://acme.example/login", "v1")
    more = _LOGIN + [{"type": "click", "anchor": {"text": "Go"}}]
    pwf.save_workflow(1, "My Portal Flow", more, None, "https://acme.example/login", "v2")
    wfs = pwf.list_workflows(1)
    assert len(wfs) == 1 and wfs[0]["slug"] == "my_portal_flow"
    assert wfs[0]["step_count"] == len(more) and wfs[0]["goal"] == "v2"


def test_recordings_are_isolated_per_user():
    pwf = _fresh_store()
    pwf.save_workflow(1, "Recorded workflow", _LOGIN, None, "https://a.example/login", "task", auto_record=True)
    pwf.save_workflow(2, "Recorded workflow", _LOGIN, None, "https://a.example/login", "task", auto_record=True)
    assert len(pwf.list_workflows(1)) == 1 and len(pwf.list_workflows(2)) == 1


def test_all_special_char_name_still_rejected():
    pwf = _fresh_store()
    try:
        pwf.save_workflow(1, "<<<", _LOGIN, None, None, None)  # no goal -> cannot derive
        raised = False
    except ValueError:
        raised = True
    assert raised


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"FAIL {name}: {e}")
            traceback.print_exc()
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
