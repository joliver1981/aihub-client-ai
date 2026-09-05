"""
Ephemeral lifecycle flag on automations (automations/manager.py, 2026-09-05).

The Agent builds one-off automations (a data fix behind a checkpoint, a
throwaway probe) that used to outlive the conversation. The flag lives in the
WORKING manifest.json — validate_manifest tolerates unknown keys and Azure
tenants have no DDL rights, so no column and no migration:

  * create(lifecycle='ephemeral') records it; the default is 'keep'
  * list_automations() / lifecycle_of() surface it
  * a save whose manifest lacks the key keeps the flag (sticky) — a client
    that does not know about it must not turn a one-off into a keeper
  * promote() clears it: pinning a version means "keep this"

DB access is stubbed via the same _db_* seams as test_automations.py.
"""
from __future__ import annotations

import pytest

from automations.manager import LIFECYCLE_EPHEMERAL, LIFECYCLE_KEEP
from tests_v2.unit.test_automations import StubManager, VALID_MANIFEST

pytestmark = pytest.mark.unit


@pytest.fixture
def mgr(tmp_path):
    return StubManager(str(tmp_path / "automations_data"))


class TestLifecycleFlag:
    def test_default_create_is_keep(self, mgr):
        ok, auto, err = mgr.create_automation("keeper", "", 1)
        assert ok, err
        aid = auto["automation_id"]
        assert mgr.lifecycle_of(aid) == LIFECYCLE_KEEP
        assert "lifecycle" not in (mgr.get_manifest(aid) or {})
        assert mgr.list_automations()[0]["lifecycle"] == LIFECYCLE_KEEP

    def test_ephemeral_create_flags_the_manifest_not_the_db(self, mgr):
        ok, auto, err = mgr.create_automation("oneoff", "", 1, lifecycle=LIFECYCLE_EPHEMERAL)
        assert ok, err
        aid = auto["automation_id"]
        assert mgr.get_manifest(aid)["lifecycle"] == LIFECYCLE_EPHEMERAL
        assert mgr.lifecycle_of(aid) == LIFECYCLE_EPHEMERAL
        assert "lifecycle" not in mgr._rows[aid]            # no column, no migration
        assert [a["lifecycle"] for a in mgr.list_automations()] == [LIFECYCLE_EPHEMERAL]

    def test_unknown_lifecycle_value_is_ignored(self, mgr):
        ok, auto, _ = mgr.create_automation("odd", "", 1, lifecycle="bogus")
        assert ok and mgr.lifecycle_of(auto["automation_id"]) == LIFECYCLE_KEEP

    def test_missing_manifest_reads_as_keep(self, mgr):
        # never swept by accident: an unreadable manifest is a keeper
        assert mgr.lifecycle_of("does-not-exist") == LIFECYCLE_KEEP

    def test_flag_is_sticky_across_saves_without_it(self, mgr):
        _, auto, _ = mgr.create_automation("sticky", "", 1, lifecycle=LIFECYCLE_EPHEMERAL)
        aid = auto["automation_id"]
        ok, v1, errs = mgr.save_version(aid, "print(1)\n", dict(VALID_MANIFEST))  # no key
        assert ok, errs
        assert mgr.get_manifest(aid)["lifecycle"] == LIFECYCLE_EPHEMERAL
        assert mgr.get_manifest(aid, v1)["lifecycle"] == LIFECYCLE_EPHEMERAL
        ok, _, errs = mgr.save_version(aid, "print(2)\n")                          # manifest=None
        assert ok, errs
        assert mgr.lifecycle_of(aid) == LIFECYCLE_EPHEMERAL

    def test_save_with_explicit_keep_is_respected(self, mgr):
        _, auto, _ = mgr.create_automation("explicit", "", 1, lifecycle=LIFECYCLE_EPHEMERAL)
        aid = auto["automation_id"]
        ok, _, errs = mgr.save_version(aid, "print(1)\n", dict(VALID_MANIFEST, lifecycle="keep"))
        assert ok, errs
        assert mgr.lifecycle_of(aid) == LIFECYCLE_KEEP

    def test_promote_clears_the_flag(self, mgr):
        _, auto, _ = mgr.create_automation("promoted", "", 1, lifecycle=LIFECYCLE_EPHEMERAL)
        aid = auto["automation_id"]
        assert mgr.save_version(aid, "print(1)\n", dict(VALID_MANIFEST))[0]
        ok, pinned, err = mgr.promote(aid)
        assert ok and pinned == 1, err
        assert mgr.lifecycle_of(aid) == LIFECYCLE_KEEP
        assert "lifecycle" not in mgr.get_manifest(aid)
        # immutable history: the saved version still records how it was born
        assert mgr.get_manifest(aid, 1).get("lifecycle") == LIFECYCLE_EPHEMERAL
        # and a later save does not resurrect the flag
        assert mgr.save_version(aid, "print(2)\n", dict(VALID_MANIFEST))[0]
        assert mgr.lifecycle_of(aid) == LIFECYCLE_KEEP

    def test_promote_of_a_keeper_leaves_manifest_alone(self, mgr):
        _, auto, _ = mgr.create_automation("plain", "", 1)
        aid = auto["automation_id"]
        assert mgr.save_version(aid, "print(1)\n", dict(VALID_MANIFEST))[0]
        before = mgr.get_manifest(aid)
        assert mgr.promote(aid)[0]
        assert mgr.get_manifest(aid) == before
