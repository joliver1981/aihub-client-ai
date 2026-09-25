"""My Work group routing for items The Agent raises itself (james 2026-09-24).

Items in agent_service/workitem_store.py could be addressed to ONE user or to
nobody (the shared pool); the addressed_group column existed since A2 but was
never written or read. Now an item can be routed to a platform GROUP, with the
same rule workflow and automation approvals already follow:

  * addressed to a USER  -> that user only
  * addressed to a GROUP -> members of that group only, ANY role; any member
    can claim it, and a claim hides it from the rest of the group
  * addressed to nobody  -> the shared pool, Developer+ only (F-7, unchanged)
  * role never widens a user- or group-addressed item: an admin who is not in
    the group does not see it

Pinned here:
  * the store: list_items(group_ids=...) and visible_to() agree on that matrix;
    a group item never leaks into the Developer+ pool; omitted group_ids fails
    closed; create_item refuses user+group and non-numeric group ids;
  * the raise_work_item tool (SDK env): resolves a group by exact name or id,
    refuses both-set / unknown / unreadable without creating anything;
  * the routes (SDK env): /api/work/list passes the caller's groups and labels
    group items; claim / release / respond / thread act only on items the
    caller can see (404 otherwise — an id alone never reaches someone else's).

Store tests use a throwaway SQLite file; nothing touches data/agent/mywork.db.
Run:
  C:\\Users\\james\\miniconda3\\envs\\aihub-agent\\python.exe -m unittest tests_v2/unit/test_workitem_group_routing.py -v
  C:\\Users\\james\\miniconda3\\envs\\aihub2.1\\python.exe -m pytest tests_v2/unit/test_workitem_group_routing.py -v   (store half only)
"""
import asyncio
import os
import sys
import tempfile
import unittest
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for _p in (APP_ROOT, os.path.join(APP_ROOT, "agent_service")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import workitem_store as W  # noqa: E402

try:
    import claude_agent_sdk as _sdk             # noqa: E402
    # Sibling test files install a bare stub under this name so readthrough
    # imports in the main env. A stub has no __file__; only the real SDK can
    # run the tool/route tests below.
    if not getattr(_sdk, "__file__", None):
        raise ImportError("claude_agent_sdk is a test stub, not the real SDK")
    import work_tools as WK                     # noqa: E402
    from platform_tools import CURRENT_USER     # noqa: E402
    HAVE_SDK = True
    _IMPORT_ERR = None
except ImportError as e:                        # main-env sweep: no claude_agent_sdk
    HAVE_SDK = False
    _IMPORT_ERR = e

# Seats mirror docs/openclaw-tester-setup-regular-users.md
ALEX, CASEY = 101, 102       # role 1
ERIN = 201                   # role 2
ADMIN = 1                    # role 3
AP_TEAM, HR_TEAM = 7, 9      # platform Groups.id
MEMBERS = {ALEX: [AP_TEAM], CASEY: [HR_TEAM], ERIN: [], ADMIN: []}
ROLE = {ALEX: 1, CASEY: 1, ERIN: 2, ADMIN: 3}
GROUPS = [{"id": AP_TEAM, "name": "AP Team"}, {"id": HR_TEAM, "name": "HR"}]


def _titles(items):
    return sorted(i["title"] for i in items)


def _seen(uid):
    return _titles(W.list_items(uid, role=ROLE[uid], group_ids=MEMBERS[uid]))


class _StoreCase(unittest.TestCase):
    """Fresh store per test: one AP-group item, one pool item, one item
    addressed to Alex."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._patch = mock.patch.object(
            W, "DB_PATH", os.path.join(self._tmp.name, "mywork.db"))
        self._patch.start()
        W.init()
        self.group_item = W.create_item(
            "review", "Review vendor 4411 bank change", addressed_group=AP_TEAM,
            summary="new routing number 021000021", from_kind="agent_session")
        self.pool_item = W.create_item(
            "acknowledge", "Shared pool FYI", from_kind="agent_session")
        self.alex_item = W.create_item(
            "acknowledge", "FYI for Alex", addressed_user=ALEX,
            from_kind="agent_session")

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()


class StoreVisibility(_StoreCase):

    def test_group_item_reaches_members_of_any_role_only(self):
        self.assertIn(self.group_item["title"], _seen(ALEX))        # role-1 member
        for uid in (CASEY, ERIN, ADMIN):                             # not members
            self.assertNotIn(self.group_item["title"], _seen(uid), uid)

    def test_group_item_never_rides_the_developer_pool(self):
        # addressed_user is NULL on a group item too — the pool clause must
        # also require "no group", or every Developer+ would see every group
        self.assertEqual(_seen(ERIN), ["Shared pool FYI"])
        self.assertEqual(_seen(ADMIN), ["Shared pool FYI"])

    def test_pool_and_personal_rules_unchanged(self):
        self.assertEqual(_seen(ALEX), sorted(["FYI for Alex", self.group_item["title"]]))
        self.assertEqual(_seen(CASEY), [])
        self.assertNotIn("FYI for Alex", _seen(ADMIN))

    def test_omitted_group_ids_fail_closed(self):
        self.assertEqual(_titles(W.list_items(ALEX, role=1)), ["FYI for Alex"])
        self.assertEqual(_titles(W.list_items(ALEX, role=1, group_ids=None)), ["FYI for Alex"])
        self.assertEqual(_titles(W.list_items(ALEX, role=1, group_ids=[])), ["FYI for Alex"])

    def test_group_ids_may_be_ints_or_text_and_junk_is_ignored(self):
        for gids in ([AP_TEAM], [str(AP_TEAM)], ["x", None, AP_TEAM]):
            self.assertIn(self.group_item["title"],
                          _titles(W.list_items(CASEY, role=1, group_ids=gids)), gids)

    def test_claim_hides_it_from_the_rest_of_the_group(self):
        other_member = 103
        W.claim(self.group_item["work_item_id"], ALEX)
        self.assertIn(self.group_item["title"],
                      _titles(W.list_items(ALEX, role=1, group_ids=[AP_TEAM])))
        self.assertNotIn(self.group_item["title"],
                         _titles(W.list_items(other_member, role=1, group_ids=[AP_TEAM])))
        W.release(self.group_item["work_item_id"], ALEX)
        self.assertIn(self.group_item["title"],
                      _titles(W.list_items(other_member, role=1, group_ids=[AP_TEAM])))

    def test_claimant_who_leaves_the_group_keeps_what_they_hold(self):
        # review finding 2026-09-24: otherwise the claim hides it from the
        # group, membership hides it from the claimant, and nobody can act
        gid = self.group_item["work_item_id"]
        W.claim(gid, ALEX)
        left = W.list_items(ALEX, role=1, group_ids=[])          # no longer a member
        self.assertIn(self.group_item["title"], _titles(left))
        self.assertTrue(W.visible_to(W.get_item(gid), ALEX, role=1, group_ids=[]))
        _, err = W.release(gid, ALEX)
        self.assertIsNone(err)
        # released: gone from the ex-member, back with the group
        self.assertNotIn(self.group_item["title"], _titles(W.list_items(ALEX, role=1, group_ids=[])))
        self.assertFalse(W.visible_to(W.get_item(gid), ALEX, role=1, group_ids=[]))
        self.assertIn(self.group_item["title"],
                      _titles(W.list_items(103, role=1, group_ids=[AP_TEAM])))

    def test_demoted_pool_claimant_keeps_what_they_hold(self):
        pid = self.pool_item["work_item_id"]
        W.claim(pid, ERIN)
        self.assertIn("Shared pool FYI", _titles(W.list_items(ERIN, role=1)))
        self.assertTrue(W.visible_to(W.get_item(pid), ERIN, role=1))
        self.assertFalse(W.visible_to(W.get_item(pid), ALEX, role=1))

    def test_legacy_empty_string_group_is_the_pool(self):
        with W._LOCK, W._connect() as c:
            c.execute("UPDATE work_items SET addressed_group='' WHERE work_item_id=?",
                      (self.pool_item["work_item_id"],))
        self.assertIn("Shared pool FYI", _seen(ERIN))
        self.assertNotIn("Shared pool FYI", _seen(ALEX))

    def test_visible_to_matches_list_items(self):
        items = [self.group_item, self.pool_item, self.alex_item]
        for uid in (ALEX, CASEY, ERIN, ADMIN):
            listed = set(_seen(uid))
            for it in items:
                fresh = W.get_item(it["work_item_id"])
                self.assertEqual(
                    W.visible_to(fresh, uid, role=ROLE[uid], group_ids=MEMBERS[uid]),
                    it["title"] in listed, (uid, it["title"]))

    def test_visible_to_fails_closed_on_bad_input(self):
        self.assertFalse(W.visible_to(None, ALEX, role=3, group_ids=[AP_TEAM]))
        self.assertFalse(W.visible_to(self.group_item, "not-a-number", role=3))
        self.assertFalse(W.visible_to(self.pool_item, ERIN, role="junk"))
        self.assertFalse(W.visible_to(self.pool_item, ERIN, role=None))


class StoreWrites(_StoreCase):

    def test_group_is_stored_as_the_group_id_text(self):
        self.assertEqual(W.get_item(self.group_item["work_item_id"])["addressed_group"],
                         str(AP_TEAM))
        self.assertIsNone(W.get_item(self.group_item["work_item_id"])["addressed_user"])

    def test_user_and_group_together_is_refused(self):
        with self.assertRaises(ValueError):
            W.create_item("review", "both", addressed_user=ALEX, addressed_group=AP_TEAM)

    def test_non_numeric_group_is_refused(self):
        with self.assertRaises(ValueError):
            W.create_item("review", "by name", addressed_group="AP Team")

    def test_empty_group_means_the_pool(self):
        it = W.create_item("review", "empty group", addressed_group="")
        self.assertIsNone(W.get_item(it["work_item_id"])["addressed_group"])


# ---------------------------------------------------------------------------
# The raise_work_item / list_my_work tools — aihub-agent env only
# ---------------------------------------------------------------------------

def _count():
    with W._connect() as c:
        return c.execute("SELECT COUNT(*) FROM work_items").fetchone()[0]


@unittest.skipUnless(HAVE_SDK, f"needs the aihub-agent env (claude_agent_sdk): {_IMPORT_ERR}")
class RaiseWorkItemRoutesToAGroup(_StoreCase):

    def _raise(self, args, groups=GROUPS):
        import readthrough
        tok = CURRENT_USER.set({"user_id": ERIN, "role": 2, "username": "dev_erin",
                                "name": "Erin"})

        def _all():
            if isinstance(groups, Exception):
                raise groups
            return list(groups)
        try:
            with mock.patch.object(readthrough, "all_groups", _all):
                res = asyncio.run(WK.raise_work_item.handler(
                    dict({"verb": "review", "title": "T", "summary": "S"}, **args)))
        finally:
            CURRENT_USER.reset(tok)
        return res.get("is_error", False), res["content"][0]["text"]

    def test_by_exact_name_case_insensitive(self):
        before = _count()
        err, text = self._raise({"addressed_group": "ap team"})
        self.assertFalse(err, text)
        self.assertIn("'AP Team' group", text)
        self.assertEqual(_count(), before + 1)
        newest = [i for i in W.list_items(ALEX, role=1, group_ids=[AP_TEAM]) if i["title"] == "T"]
        self.assertEqual(len(newest), 1)
        self.assertEqual(newest[0]["addressed_group"], str(AP_TEAM))

    def test_by_numeric_id(self):
        err, text = self._raise({"addressed_group": str(HR_TEAM)})
        self.assertFalse(err, text)
        self.assertIn("'HR' group", text)

    def test_unknown_group_creates_nothing_and_lists_the_groups(self):
        before = _count()
        err, text = self._raise({"addressed_group": "Payroll"})
        self.assertTrue(err)
        self.assertIn("no group 'Payroll'", text)
        self.assertIn("AP Team (id 7)", text)
        self.assertEqual(_count(), before)

    def test_ambiguous_name_asks_for_the_id(self):
        before = _count()
        err, text = self._raise({"addressed_group": "HR"},
                                groups=GROUPS + [{"id": 12, "name": "hr"}])
        self.assertTrue(err)
        self.assertIn("pass its id", text)
        self.assertEqual(_count(), before)

    def test_user_and_group_together_creates_nothing(self):
        before = _count()
        err, text = self._raise({"addressed_group": "AP Team", "addressed_user_id": ALEX})
        self.assertTrue(err)
        self.assertIn("not both", text)
        self.assertEqual(_count(), before)

    def test_unreadable_group_list_creates_nothing(self):
        before = _count()
        err, text = self._raise({"addressed_group": "AP Team"},
                                groups=RuntimeError("main app down"))
        self.assertTrue(err)
        self.assertIn("could not be read", text)
        self.assertEqual(_count(), before)

    def test_no_group_is_still_the_pool(self):
        err, text = self._raise({})
        self.assertFalse(err, text)
        self.assertIn("shared queue", text)

    def test_tool_schema_offers_addressed_group(self):
        self.assertIn("addressed_group", WK.raise_work_item.input_schema["properties"])

    def test_list_my_work_shows_group_items_to_members(self):
        import readthrough
        tok = CURRENT_USER.set({"user_id": ALEX, "role": 1, "username": "ru_alex",
                                "name": "Alex"})
        try:
            with mock.patch.object(readthrough, "user_group_ids", lambda uid: [AP_TEAM]), \
                 mock.patch.object(readthrough, "all_groups", lambda: list(GROUPS)):
                text = asyncio.run(WK.list_my_work.handler({}))["content"][0]["text"]
        finally:
            CURRENT_USER.reset(tok)
        self.assertIn("Review vendor 4411 bank change", text)
        self.assertIn("group AP Team · unclaimed", text)
        self.assertNotIn("Shared pool FYI", text)


# ---------------------------------------------------------------------------
# The /api/work routes — aihub-agent env only
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAVE_SDK, f"needs the aihub-agent env (claude_agent_sdk): {_IMPORT_ERR}")
class RoutesActOnlyOnVisibleItems(_StoreCase):

    def setUp(self):
        super().setUp()
        import main
        import readthrough
        from fastapi.testclient import TestClient

        async def _no_email(user):
            return []
        self.seat = {}
        self._patches = [
            mock.patch.object(main, "_verify_request", lambda _r: dict(self.seat)),
            mock.patch.object(readthrough, "user_group_ids",
                              lambda uid: list(MEMBERS.get(int(uid), []))),
            mock.patch.object(readthrough, "all_groups", lambda: list(GROUPS)),
            mock.patch.object(readthrough, "workflow_pending", lambda uid, *, role: []),
            mock.patch.object(readthrough, "automation_pending", lambda uid, g, *, role: []),
            mock.patch.object(readthrough, "email_pending", _no_email),
            # a thread POST would run a model turn — never here
            mock.patch.object(main, "run_turn", self._no_turn),
        ]
        for p in self._patches:
            p.start()
        self.client = TestClient(main.app)

    async def _no_turn(self, *a, **k):
        raise AssertionError("a model turn started for a request that should be refused")
        yield  # pragma: no cover

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        super().tearDown()

    def _as(self, uid):
        self.seat.clear()
        self.seat.update({"user_id": uid, "role": ROLE[uid], "username": f"u{uid}",
                          "name": f"U{uid}", "tenant_id": 1})

    def test_list_labels_group_items_for_members(self):
        self._as(ALEX)
        items = self.client.get("/api/work/list").json()["items"]
        grp = [i for i in items if i["id"] == self.group_item["work_item_id"]]
        self.assertEqual(len(grp), 1)
        self.assertEqual(grp[0]["addressed_group"], str(AP_TEAM))
        self.assertEqual(grp[0]["group_name"], "AP Team")
        for uid in (CASEY, ERIN, ADMIN):
            self._as(uid)
            ids = [i["id"] for i in self.client.get("/api/work/list").json()["items"]]
            self.assertNotIn(self.group_item["work_item_id"], ids, uid)

    def test_non_members_cannot_claim_respond_or_read_the_thread(self):
        gid = self.group_item["work_item_id"]
        W.append_thread(gid, "human", "PRIVATE-THREAD-TEXT", actor="ru_alex")
        for uid in (CASEY, ERIN, ADMIN):
            self._as(uid)
            self.assertEqual(self.client.post("/api/work/claim", json={"id": gid}).status_code, 404)
            self.assertEqual(self.client.post("/api/work/respond", json={
                "id": gid, "response": {"decision": "acknowledged"}}).status_code, 404)
            self.assertEqual(self.client.post("/api/work/thread", json={
                "source": "agent", "id": gid, "question": "what is it?"}).status_code, 404)
            r = self.client.get("/api/work/thread", params={"source": "agent", "id": gid})
            self.assertEqual(r.json(), {"thread": []})
        self.assertEqual(W.get_item(gid)["status"], "open")

    def test_a_member_can_claim_release_and_respond(self):
        gid = self.group_item["work_item_id"]
        self._as(ALEX)
        r = self.client.post("/api/work/claim", json={"id": gid})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["item"]["status"], "claimed")
        self.assertEqual(self.client.post("/api/work/release", json={"id": gid}).status_code, 200)
        r = self.client.post("/api/work/respond", json={
            "id": gid, "response": {"decision": "approved"}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(W.get_item(gid)["status"], "closed")

    def test_personal_items_are_not_answerable_by_others_even_admins(self):
        aid = self.alex_item["work_item_id"]
        for uid in (CASEY, ERIN, ADMIN):
            self._as(uid)
            self.assertEqual(self.client.post("/api/work/respond", json={
                "id": aid, "response": {"decision": "acknowledged"}}).status_code, 404)
        self._as(ALEX)
        self.assertEqual(self.client.post("/api/work/respond", json={
            "id": aid, "response": {"decision": "acknowledged"}}).status_code, 200)

    def test_pool_stays_developer_plus(self):
        pid = self.pool_item["work_item_id"]
        self._as(ALEX)
        self.assertEqual(self.client.post("/api/work/claim", json={"id": pid}).status_code, 404)
        self._as(ERIN)
        self.assertEqual(self.client.post("/api/work/claim", json={"id": pid}).status_code, 200)

    def test_unknown_id_is_the_same_404(self):
        self._as(ADMIN)
        self.assertEqual(self.client.post("/api/work/claim",
                                          json={"id": "no-such-id"}).status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
