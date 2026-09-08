"""My Work visibility — the Developer+ rule on the shared pool (RU pack finding
F-7, 2026-09-07).

workitem_store's docstring always said: an item addressed to a user is visible
to that user only; an item with NO user address is a shared "anyone" item
visible to all Developer+ users until claimed. The role half was never in code —
it held implicitly while The Agent itself was Developer+ only, and
AGENT_ALLOW_ALL_USERS=true removed that guarantee: a role-1 user saw an admin's
pending tenant-skill promotion, SKILL.md and all.

The guard now lives INSIDE workitem_store.list_items (role is a REQUIRED
keyword argument), the chokepoint both callers converge on:
  * GET /api/work/list (agent_service/main.py)   — the UI list AND the badge
  * the list_my_work tool (agent_service/work_tools.py)

Pinned here:
  role < 2   -> ONLY items addressed to that user; no shared-pool items at all,
                claimed or not.
  role >= 2  -> addressed-to-them items + the unaddressed pool; claimed pool
                items hidden from everyone but the claimant (unchanged).
  include_closed, readthrough-shadow hiding, ordering: unchanged.
  a caller that forgets `role` gets a TypeError, not a silent widening.
  a missing/zero role fails CLOSED (addressed-only).
  the tool and the route pass the caller's real role through (SDK-env only;
  those two tests self-skip in the main-app pytest sweep).

Store tests run against a throwaway SQLite file; nothing touches
data/agent/mywork.db. Run:
  C:\\Users\\james\\miniconda3\\envs\\aihub2.1\\python.exe -m pytest tests_v2/unit/test_workitem_store_role_visibility.py -v
  (aihub-agent env for the tool/route tests too)
"""
import asyncio
import os
import sys
import tempfile
import unittest
from unittest import mock

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))

import workitem_store as W  # noqa: E402

try:
    import work_tools as WK                     # noqa: E402
    from platform_tools import CURRENT_USER     # noqa: E402
    HAVE_SDK = True
except ImportError as e:                        # main-env sweep: no claude_agent_sdk
    HAVE_SDK = False
    _IMPORT_ERR = e

# Seats mirror docs/openclaw-tester-setup-regular-users.md
ALEX, CASEY = 101, 102       # role 1, regular users
ERIN = 201                   # role 2, developer
ADMIN = 1                    # role 3


def _titles(items):
    return sorted(i["title"] for i in items)


class _StoreCase(unittest.TestCase):
    """Fresh throwaway store per test, seeded with one shared-pool item and one
    addressed item per regular user."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._patch = mock.patch.object(
            W, "DB_PATH", os.path.join(self._tmp.name, "mywork.db"))
        self._patch.start()
        W.init()
        self.shared = W.create_item(
            "approve_deny", "Promote skill 'collections-triage' to tenant",
            summary="--- SKILL.md with internal table names ---",
            payload={"kind": "skill_promotion", "name": "collections-triage"},
            from_kind="agent_session", created_by="admin")
        self.alex_item = W.create_item(
            "edit_and_return", "Send: Lease check", addressed_user=ALEX,
            payload={"kind": "agent_email_reply", "from_user": ALEX},
            from_kind="agent_session", created_by="ru_alex")
        self.casey_item = W.create_item(
            "acknowledge", "FYI for Casey", addressed_user=CASEY,
            from_kind="agent_session", created_by="agent")

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()


class RegularUserSeesOnlyAddressed(_StoreCase):

    def test_role1_sees_own_addressed_item_and_nothing_else(self):
        self.assertEqual(_titles(W.list_items(ALEX, role=1)), ["Send: Lease check"])
        self.assertEqual(_titles(W.list_items(CASEY, role=1)), ["FYI for Casey"])

    def test_role1_never_sees_the_shared_pool(self):
        for uid in (ALEX, CASEY):
            titles = _titles(W.list_items(uid, role=1))
            self.assertNotIn(self.shared["title"], titles)
        # ...whether the pool item is open, claimed by a developer, or released.
        W.claim(self.shared["work_item_id"], ERIN)
        self.assertNotIn(self.shared["title"], _titles(W.list_items(ALEX, role=1)))
        W.release(self.shared["work_item_id"], ERIN)
        self.assertNotIn(self.shared["title"], _titles(W.list_items(ALEX, role=1)))

    def test_addressed_items_stay_private_between_regular_users(self):
        self.assertNotIn("Send: Lease check", _titles(W.list_items(CASEY, role=1)))
        self.assertNotIn("FYI for Casey", _titles(W.list_items(ALEX, role=1)))

    def test_role1_with_no_open_items_gets_an_empty_queue(self):
        self.assertEqual(W.list_items(999, role=1), [])

    def test_missing_or_zero_role_fails_closed(self):
        for role in (0, None):
            self.assertEqual(_titles(W.list_items(ALEX, role=role)),
                             ["Send: Lease check"])
            self.assertEqual(W.list_items(999, role=role), [])


class DeveloperPlusUnchanged(_StoreCase):

    def test_developer_and_admin_see_the_pool(self):
        self.assertEqual(_titles(W.list_items(ERIN, role=2)), [self.shared["title"]])
        self.assertEqual(_titles(W.list_items(ADMIN, role=3)), [self.shared["title"]])

    def test_developer_plus_sees_pool_plus_own_addressed_items(self):
        own = W.create_item("review", "Erin's own review", addressed_user=ERIN,
                            from_kind="agent_session")
        self.assertEqual(_titles(W.list_items(ERIN, role=2)),
                         sorted([self.shared["title"], own["title"]]))
        # ...but never another user's addressed items.
        self.assertNotIn("Send: Lease check", _titles(W.list_items(ERIN, role=2)))
        self.assertNotIn("Send: Lease check", _titles(W.list_items(ADMIN, role=3)))

    def test_claim_release_semantics_unchanged_for_developer_plus(self):
        item_id = self.shared["work_item_id"]
        claimed, err = W.claim(item_id, ERIN)
        self.assertIsNone(err)
        self.assertEqual(claimed["status"], "claimed")
        # claimant still sees it; other Developer+ users do not while claimed
        self.assertIn(self.shared["title"], _titles(W.list_items(ERIN, role=2)))
        self.assertNotIn(self.shared["title"], _titles(W.list_items(ADMIN, role=3)))
        released, err = W.release(item_id, ERIN)
        self.assertIsNone(err)
        self.assertEqual(released["status"], "open")
        self.assertIn(self.shared["title"], _titles(W.list_items(ADMIN, role=3)))

    def test_include_closed_and_readthrough_shadow_rows_unchanged(self):
        W.respond(self.shared["work_item_id"], ADMIN, {"decision": "rejected"})
        W.shadow_item("email", "42", "Send: shadow anchor")   # threads only
        self.assertEqual(W.list_items(ERIN, role=2), [])
        closed = W.list_items(ERIN, role=2, include_closed=True)
        self.assertEqual(_titles(closed), [self.shared["title"]])
        self.assertEqual(closed[0]["status"], "closed")
        # a regular user with include_closed still sees only their own items
        self.assertEqual(_titles(W.list_items(ALEX, role=1, include_closed=True)),
                         ["Send: Lease check"])
        for uid, role in ((ERIN, 2), (ADMIN, 3), (ALEX, 1)):
            self.assertNotIn("Send: shadow anchor",
                             _titles(W.list_items(uid, role=role, include_closed=True)))

    def test_ordering_unchanged(self):
        W.create_item("acknowledge", "urgent pool item", priority=9,
                      from_kind="agent_session")
        titles = [i["title"] for i in W.list_items(ERIN, role=2)]
        self.assertEqual(titles[0], "urgent pool item")


class RoleIsRequiredAtTheChokepoint(_StoreCase):

    def test_forgetting_role_is_a_loud_error_not_a_silent_widening(self):
        with self.assertRaises(TypeError):
            W.list_items(ALEX)                     # noqa — the drift we are guarding
        with self.assertRaises(TypeError):
            W.list_items(ALEX, 1)                  # keyword-only on purpose


@unittest.skipUnless(HAVE_SDK, "needs the aihub-agent env (claude_agent_sdk)")
class CallersPassTheRealRole(_StoreCase):
    """The two callers hand list_items the caller's role from the verified
    principal — the tool via CURRENT_USER, the route via _verify_request."""

    def _tool(self, uid, role):
        tok = CURRENT_USER.set({"user_id": uid, "role": role,
                                "username": f"u{uid}", "name": f"User {uid}"})
        try:
            res = asyncio.run(WK.list_my_work.handler({}))
        finally:
            CURRENT_USER.reset(tok)
        return res["content"][0]["text"]

    def test_list_my_work_tool_role1_vs_role2(self):
        casey = self._tool(CASEY, 1)
        self.assertIn("FYI for Casey", casey)
        self.assertNotIn("collections-triage", casey)
        self.assertNotIn("Lease check", casey)
        erin = self._tool(ERIN, 2)
        self.assertIn("collections-triage", erin)
        self.assertNotIn("FYI for Casey", erin)
        nobody = self._tool(999, 1)
        self.assertIn("queue is empty", nobody)

    def test_work_list_route_and_badge_total_agree_with_role(self):
        import main
        import readthrough
        from fastapi.testclient import TestClient

        async def _no_email():
            return []

        seat = {}

        def _verify(_request):
            return dict(seat)

        with mock.patch.object(main, "_verify_request", _verify), \
             mock.patch.object(readthrough, "user_group_ids", lambda uid: []), \
             mock.patch.object(readthrough, "workflow_pending", lambda uid: []), \
             mock.patch.object(readthrough, "automation_pending", lambda uid, g: []), \
             mock.patch.object(readthrough, "email_pending", _no_email):
            client = TestClient(main.app)
            seat.update({"user_id": CASEY, "role": 1, "username": "ru_casey",
                         "name": "Casey", "tenant_id": 1})
            d = client.get("/api/work/list").json()
            self.assertEqual([i["title"] for i in d["items"]], ["FYI for Casey"])
            self.assertEqual(d["total"], 1)         # the badge reads this number
            seat.update({"user_id": ADMIN, "role": 3, "username": "admin"})
            d = client.get("/api/work/list").json()
            self.assertEqual([i["title"] for i in d["items"]], [self.shared["title"]])
            self.assertEqual(d["total"], 1)
            seat.update({"user_id": ALEX, "role": 1, "username": "ru_alex"})
            d = client.get("/api/work/list").json()
            self.assertEqual([i["title"] for i in d["items"]], ["Send: Lease check"])
            self.assertEqual(d["total"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
