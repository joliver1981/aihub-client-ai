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
    import claude_agent_sdk as _sdk             # noqa: E402
    # Sibling test files install a bare stub under this name so readthrough
    # imports in the main env (test_agent_readthrough_http.py,
    # test_readthrough_pending_role_floor.py). A stub has no __file__; only
    # the real SDK can run the tool/route tests below.
    if not getattr(_sdk, "__file__", None):
        raise ImportError("claude_agent_sdk is a test stub, not the real SDK")
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

        async def _no_email(user):        # email_pending runs AS the viewer (F-7 part 2)
            return []

        seat = {}

        def _verify(_request):
            return dict(seat)

        with mock.patch.object(main, "_verify_request", _verify), \
             mock.patch.object(readthrough, "user_group_ids", lambda uid: []), \
             mock.patch.object(readthrough, "workflow_pending", lambda uid, *, role: []), \
             mock.patch.object(readthrough, "automation_pending", lambda uid, g, *, role: []), \
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


@unittest.skipUnless(HAVE_SDK, "needs the aihub-agent env (claude_agent_sdk)")
class EmailReadthroughRunsAsTheViewer(_StoreCase):
    """F-7 part 2 (2026-09-08, customer-data leak): the agent-email source of
    My Work used to be read with the service key and NO user, so every viewer
    got every pending approval on the install, bodies included. Both My Work
    routes now hand readthrough the VERIFIED principal (user_id + role), which
    mints the X-AIHub-User assertion the platform scopes and attributes by.
    The platform side and the assertion itself are pinned in
    test_agent_email_approvals_identity.py (pytest, main-app env)."""

    PROBE = {"approval_id": 5001, "agent_id": 1037, "status": "pending",
             "subject": "PROBE lease renewal terms", "to_addresses": ["probe@example.com"],
             "draft_body": "PROBE-BODY confidential rent figure 12345", "final_body": None}

    def test_work_list_hands_email_pending_the_verified_principal(self):
        import main
        import readthrough
        from fastapi.testclient import TestClient
        seen = []

        async def _email(user):
            seen.append(dict(user))
            if int(user.get("role") or 0) >= 3:
                return [dict(self.PROBE)]
            return []                     # the platform's 403 / scoped answer

        seat = {}
        with mock.patch.object(main, "_verify_request", lambda _r: dict(seat)), \
             mock.patch.object(readthrough, "user_group_ids", lambda uid: []), \
             mock.patch.object(readthrough, "workflow_pending", lambda uid, *, role: []), \
             mock.patch.object(readthrough, "automation_pending", lambda uid, g, *, role: []), \
             mock.patch.object(readthrough, "email_pending", _email):
            client = TestClient(main.app)
            seat.update({"user_id": CASEY, "role": 1, "username": "ru_casey",
                         "name": "Casey", "tenant_id": 1})
            r = client.get("/api/work/list")
            self.assertEqual([i for i in r.json()["items"] if i["source"] == "email"], [])
            self.assertNotIn("12345", r.text)
            seat.update({"user_id": ADMIN, "role": 3, "username": "admin"})
            emails = [i for i in client.get("/api/work/list").json()["items"]
                      if i["source"] == "email"]
            self.assertEqual(len(emails), 1)
            self.assertIn("12345", emails[0]["payload"]["body"])
        self.assertEqual([(s["user_id"], s["role"]) for s in seen],
                         [(CASEY, 1), (ADMIN, 3)])

    def test_work_decide_hands_decide_email_the_user_and_relays_a_403(self):
        import main
        import readthrough
        from fastapi.testclient import TestClient
        seen = []

        async def _decide(approval_id, action, final_body, comments, *, user):
            seen.append((approval_id, action, dict(user)))
            return {"status": "error", "message": "Not authorized for this agent"}, 403

        seat = {"user_id": CASEY, "role": 1, "username": "ru_casey", "name": "Casey",
                "tenant_id": 1}
        with mock.patch.object(main, "_verify_request", lambda _r: dict(seat)), \
             mock.patch.object(readthrough, "decide_email", _decide):
            client = TestClient(main.app)
            r = client.post("/api/work/decide", json={"source": "email", "id": 5001,
                                                      "decision": "reject", "title": "PROBE"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(seen, [(5001, "reject", seat)])
        # a refused decision is not mirrored into the lifecycle log (no shadow row)
        with W._LOCK, W._connect() as c:
            n = c.execute("SELECT COUNT(*) FROM work_items WHERE from_kind='readthrough' "
                          "AND blocks_kind='email' AND blocks_ref='5001'").fetchone()[0]
        self.assertEqual(n, 0)


@unittest.skipUnless(HAVE_SDK, "needs the aihub-agent env (claude_agent_sdk)")
class PoolSourcesGetTheVerifiedRole(_StoreCase):
    """F-12 (2026-09-08): the workflow and automation read-throughs carry the
    same "unassigned means everyone" pool as the store, and the floor lives
    INSIDE readthrough.workflow_pending / automation_pending (`role` required).
    The route must hand both the verified role — pinned here; the functions'
    own matrix is in test_readthrough_pending_role_floor.py (pytest)."""

    def test_work_list_threads_the_role_into_both_pool_sources(self):
        import main
        import readthrough
        from fastapi.testclient import TestClient
        seen = {"workflow": [], "automation": []}

        def _wf(uid, *, role):
            seen["workflow"].append((uid, role))
            return []

        def _auto(uid, gids, *, role):
            seen["automation"].append((uid, list(gids), role))
            return []

        async def _email(user):
            return []

        seat = {}
        with mock.patch.object(main, "_verify_request", lambda _r: dict(seat)), \
             mock.patch.object(readthrough, "user_group_ids", lambda uid: [59]), \
             mock.patch.object(readthrough, "workflow_pending", _wf), \
             mock.patch.object(readthrough, "automation_pending", _auto), \
             mock.patch.object(readthrough, "email_pending", _email):
            client = TestClient(main.app)
            for uid, role in ((CASEY, 1), (ERIN, 2), (ADMIN, 3)):
                seat.clear()
                seat.update({"user_id": uid, "role": role, "username": f"u{uid}",
                             "name": f"U{uid}", "tenant_id": 1})
                self.assertEqual(client.get("/api/work/list").status_code, 200)
        self.assertEqual(seen["workflow"], [(CASEY, 1), (ERIN, 2), (ADMIN, 3)])
        self.assertEqual(seen["automation"],
                         [(CASEY, [59], 1), (ERIN, [59], 2), (ADMIN, [59], 3)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
