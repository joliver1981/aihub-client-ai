"""Portal-workflow store: names that slug to '' (e.g. a workflow named '<').

Scenario 1 of the 2026-08-06 Portal Workflow bug report: an all-special-character
name slugs to the empty string and was saved under the key '' — it showed in the
Saved Workflows list but the dropdown value collided with the placeholder (Load/
Delete silently no-op), GET/DELETE couldn't address it by URL, and the empty key
contains-matched every OTHER run-by-name lookup. Fixes under test: save_workflow
rejects empty-slug names, _load() heals legacy '' keys to 'unnamed', and the
loose match skips empty keys.
"""
import json

import pytest

from command_center.tools import portal_workflows as store

UID = 13
STEPS = [{"type": "goto", "url": "https://example.com/login"}]


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    """Point the store at a tmp APP_ROOT so tests never touch data/portal_workflows.json."""
    monkeypatch.setenv("APP_ROOT", str(tmp_path))
    return tmp_path / "data" / "portal_workflows.json"


def _write_legacy(path, workflows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"users": {str(UID): {"workflows": workflows}}}), encoding="utf-8")


def _raw_workflows(path):
    return json.loads(path.read_text(encoding="utf-8"))["users"][str(UID)]["workflows"]


def _entry(name):
    return {"name": name, "steps": STEPS}


def test_slug_of_all_special_name_is_empty():
    assert store.slug("<") == ""
    assert store.slug("<<>>!?*") == ""


def test_save_rejects_name_without_alphanumerics(tmp_store):
    with pytest.raises(ValueError, match="letter or number"):
        store.save_workflow(UID, "<", STEPS)
    assert not tmp_store.exists()  # nothing persisted
    assert store.list_workflows(UID) == []


def test_save_still_accepts_names_with_special_characters_mixed_in(tmp_store):
    saved = store.save_workflow(UID, "Invoices <2026>", STEPS)
    assert saved["slug"] == "invoices_2026"
    wf = store.get_workflow(UID, "Invoices <2026>")
    assert wf and wf["name"] == "Invoices <2026>"


def test_legacy_empty_slug_entry_is_healed_listable_loadable_deletable(tmp_store):
    _write_legacy(tmp_store, {"": _entry("<")})
    wfs = store.list_workflows(UID)
    assert [w["slug"] for w in wfs] == ["unnamed"]
    assert wfs[0]["name"] == "<"  # display name preserved
    wf = store.get_workflow(UID, "unnamed")
    assert wf and wf["name"] == "<"
    assert store.delete_workflow(UID, "unnamed") is True
    assert store.list_workflows(UID) == []


def test_heal_persists_on_next_write(tmp_store):
    _write_legacy(tmp_store, {"": _entry("<")})
    store.save_workflow(UID, "Other", STEPS)
    raw = _raw_workflows(tmp_store)
    assert "" not in raw
    assert "unnamed" in raw and "other" in raw


def test_heal_suffixes_on_collision_with_existing_unnamed(tmp_store):
    _write_legacy(tmp_store, {"unnamed": _entry("unnamed"), "": _entry("<")})
    slugs = sorted(w["slug"] for w in store.list_workflows(UID))
    assert slugs == ["unnamed", "unnamed_2"]
    assert store.get_workflow(UID, "unnamed_2")["name"] == "<"


def test_empty_key_no_longer_hijacks_loose_match_for_other_names(tmp_store):
    # Pre-fix: '' iterated first and '' in <anything> is True, so ANY non-exact
    # run-by-name lookup (CC agent, scheduler) resolved to the '<' workflow.
    _write_legacy(tmp_store, {"": _entry("<"), "vendor_invoices": _entry("Vendor Invoices")})
    wf = store.get_workflow(UID, "vendor invoice")
    assert wf and wf["slug"] == "vendor_invoices"
