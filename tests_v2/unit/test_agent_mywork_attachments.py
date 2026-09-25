"""My Work (The Agent) renders automation attachments as DOWNLOAD LINKS — parity
with classic My Approvals (templates/approvals.html populateModal), 2026-09-13.

Read-through AUTOMATION rows (checkpoint gates and review items raised by
automations) used to show `attachments: <names>` as plain text in the My Work
detail pane — no way to get the file — while the classic page links the two
gated download routes in automations/api.py:
  gate   : /automations/api/runs/<run_id>/checkpoints/<checkpoint_id>/attachments/<name>
  review : /automations/api/approvals/<request_id>/attachments/<name>
Both routes serve ONLY names the row declared and sit behind the main app's
login session — exactly the auth the classic page relies on — plus (since
2026-09-24) a per-row check: whoever can see the approval row, any role. The Agent UI runs on its own port, so the links are
built with platUrl() (main-app origin) and ride the browser's main-app session
cookie like every other Platform link in index.html.

Pinned here:
  * the frontend contract as text (main-app env, no SDK needed): both URL
    shapes built via platUrl + encodeURIComponent, the review shape keyed by
    item.id (== the approval row's request_id, the same id /api/work/decide
    settles), new-tab anchors, and the old plain-text rendering GONE;
  * parity with the classic page: the path skeletons are extracted from BOTH
    pages and compared, so a route rename on one page fails this test until
    the other follows;
  * the /api/work/list payload the JS depends on (aihub-agent env only): a
    gate row and a review row come through with request_id as the item id and
    run_id / checkpoint_id / kind / attachments carried verbatim.

Run:
  C:\\Users\\james\\miniconda3\\envs\\aihub2.1\\python.exe -m pytest tests_v2/unit/test_agent_mywork_attachments.py -v
  C:\\Users\\james\\miniconda3\\envs\\aihub-agent\\python.exe -m unittest tests_v2/unit/test_agent_mywork_attachments.py -v
  (unittest, like test_workitem_store_role_visibility.py — the agent env has no pytest)
"""
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

try:
    import pytest
    pytestmark = pytest.mark.unit
except ImportError:                             # aihub-agent env: python -m unittest
    pytest = None

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for _p in (APP_ROOT, os.path.join(APP_ROOT, "agent_service")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

INDEX = os.path.join(APP_ROOT, "agent_service", "static", "index.html")
CLASSIC = os.path.join(APP_ROOT, "templates", "approvals.html")
AGENT_MAIN = os.path.join(APP_ROOT, "agent_service", "main.py")
AUTOMATIONS_API = os.path.join(APP_ROOT, "automations", "api.py")

try:
    import claude_agent_sdk as _sdk             # noqa: E402
    # Sibling test files install a bare stub under this name so readthrough
    # imports in the main env. A stub has no __file__; only the real SDK can
    # import agent_service/main.py for the route tests below.
    if not getattr(_sdk, "__file__", None):
        raise ImportError("claude_agent_sdk is a test stub, not the real SDK")
    HAVE_SDK = True
    _IMPORT_ERR = ""
except ImportError as e:                        # main-env sweep: no claude_agent_sdk
    HAVE_SDK = False
    _IMPORT_ERR = str(e)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _automation_branch(html):
    """renderDetail's workflow/automation branch: from its source guard up to
    the review-vs-gate comment that follows the Context section."""
    start = html.index('item.source === "workflow" || item.source === "automation"')
    end = html.index("const isReview = item.source", start)
    return html[start:end]


_ATTACH_URL = re.compile(r"/automations/api/[^`'\"\s]*?/attachments/\$\{name\}")


def _skeletons(src):
    """The attachment-route templates in a page with every ${...} placeholder
    collapsed to {} — what must agree between the two pages."""
    return {re.sub(r"\$\{[^}]*\}", "{}", m) for m in _ATTACH_URL.findall(src)}


# ---------------------------------------------------------------------------
# Frontend contract (pinned as text, like test_agent_rich_output.py)
# ---------------------------------------------------------------------------

class TestMyWorkAttachmentLinks(unittest.TestCase):

    def setUp(self):
        self.html = _read(INDEX)
        self.block = _automation_branch(self.html)

    def test_both_classic_url_shapes_are_built_on_the_main_app_origin(self):
        gate = ("/automations/api/runs/${encodeURIComponent(p.run_id)}"
                "/checkpoints/${encodeURIComponent(p.checkpoint_id)}/attachments/${name}")
        review = "/automations/api/approvals/${encodeURIComponent(item.id)}/attachments/${name}"
        self.assertIn(gate, self.block, "checkpoint-gate download shape missing")
        self.assertIn(review, self.block, "review-item download shape missing")
        # main-app origin (:5001 by default, /api/me's main_port when set) — never
        # The Agent's own port, which has no such route and no session
        self.assertIn("link.href = platUrl(path)", self.block)

    def test_gate_shape_only_for_a_gate_row_with_both_ids(self):
        # Same predicate as approvals.html: `!isReview && run_id && checkpoint_id`.
        # main.py sets verb="review" exactly when kind == "review" or the row has
        # no checkpoint_id, so item.verb is the row-kind signal here.
        self.assertIn('const isGate = item.verb !== "review" && p.run_id && p.checkpoint_id',
                      self.block)
        self.assertIn("const path = isGate", self.block)

    def test_names_are_encoded_once_and_links_open_in_a_new_tab(self):
        self.assertIn("const name = encodeURIComponent(String(a.name))", self.block)
        # Content-Disposition: attachment downloads without leaving My Work; a
        # 403/410 JSON error lands in the new tab instead of replacing the page.
        self.assertIn('link.target = "_blank"; link.rel = "noopener"', self.block)
        # size chip mirrors the classic page: "(N KB)", never "(0 KB)"
        self.assertIn("Math.max(1, Math.round(a.size / 1024))", self.block)

    def test_plain_text_attachment_names_are_gone(self):
        self.assertNotIn("attachments: ${", self.html, "the old text-only rendering is back")
        self.assertIn('el("div", "dlabel", "Attachments")', self.html)

    def test_only_entries_with_a_name_render(self):
        # a row can carry a malformed entry; the serving routes key on name, so
        # a nameless entry has no downloadable meaning
        self.assertIn(".filter(a => a && a.name)", self.block)

    def test_parity_with_classic_my_approvals(self):
        expected = {"/automations/api/runs/{}/checkpoints/{}/attachments/{}",
                    "/automations/api/approvals/{}/attachments/{}"}
        self.assertEqual(_skeletons(_read(CLASSIC)), expected)
        self.assertEqual(_skeletons(self.block), expected)

    def test_serving_routes_exist_and_are_session_gated(self):
        api = _read(AUTOMATIONS_API)
        for route in ('"/api/runs/<run_id>/checkpoints/<checkpoint_id>/attachments/<name>"',
                      '"/api/approvals/<request_id>/attachments/<name>"'):
            i = api.index(route)
            # 2026-09-24: login + feature flag, then a PER-ROW check (whoever can
            # see the approval row, any role) instead of the Developer/Admin
            # gate — behaviour pinned in test_automation_attachment_preview_access.py
            self.assertIn("@automations_signed_in", api[i:i + 200], route)
            body = api[i:api.index("\n@automations_bp.route", i + 10)]
            self.assertIn("_ATTACHMENT_DENIED", body, route)

    def test_review_link_key_is_the_id_decide_settles(self):
        # /api/work/decide hands body.id straight to readthrough.decide_generic —
        # the approval row's request_id — so the review-item link keyed by
        # item.id targets the same row the buttons act on.
        src = _read(AGENT_MAIN)
        self.assertIn('"source": "automation", "id": row.get("request_id")', src)
        i = src.index("async def work_decide")
        self.assertIn('readthrough.decide_generic(\n            str(body.get("id"))',
                      src[i:i + 1200])


# ---------------------------------------------------------------------------
# /api/work/list payload (the ids the JS keys on) — aihub-agent env only
# ---------------------------------------------------------------------------

def _row(rid, title, approval_data):
    return {"request_id": rid, "title": title, "description": "d", "status": "Pending",
            "requested_at": "2026-09-13T10:00:00Z", "due_date": None, "priority": 0,
            "assigned_to_type": "user", "assigned_to_id": 13,
            "approval_data": json.dumps(approval_data)}


# shapes exactly as automations/api.py writes them:
# _create_checkpoint_approval_row (gate: name+size only, no kind) and
# runtime_review_item (review: kind="review", relpath rides along, no checkpoint_id)
GATE_ROW = _row("req-gate", "Automation checkpoint — expense-audit", {
    "source": "automation", "run_id": "run-1", "checkpoint_id": "cp1a2b3c4d5e",
    "automation_id": "auto-1", "automation_name": "expense-audit", "group_name": None,
    "dry_run": True, "attachments": [{"name": "flagged_invoices.csv", "size": 2048}]})
REVIEW_ROW = _row("req-review", "Automation exception — dayforce-doc-upload", {
    "source": "automation", "kind": "review", "run_id": "run-2",
    "automation_id": "auto-2", "automation_name": "dayforce-doc-upload", "group_name": None,
    "correctable": {"employee_id": "3149231"},
    "attachments": [{"name": "NH07 Smith.pdf", "relpath": "in/NH07 Smith.pdf", "size": 51200}]})


@unittest.skipUnless(HAVE_SDK, f"needs the aihub-agent env (claude_agent_sdk): {_IMPORT_ERR}")
class TestWorkListPayloadCarriesTheIds(unittest.TestCase):
    """Throwaway work-item store per test; nothing touches data/agent/mywork.db."""

    def setUp(self):
        import workitem_store as W
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._patch = mock.patch.object(W, "DB_PATH", os.path.join(self._tmp.name, "mywork.db"))
        self._patch.start()
        W.init()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def _automation_items(self, rows):
        import main
        import readthrough
        from fastapi.testclient import TestClient

        async def _no_email(user):
            return []

        admin = {"user_id": 13, "role": 3, "username": "admin", "name": "Admin", "tenant_id": 1}
        with mock.patch.object(main, "_verify_request", lambda _r: dict(admin)), \
             mock.patch.object(readthrough, "user_group_ids", lambda uid: []), \
             mock.patch.object(readthrough, "workflow_pending", lambda uid, *, role: []), \
             mock.patch.object(readthrough, "automation_pending", lambda uid, g, *, role: rows), \
             mock.patch.object(readthrough, "email_pending", _no_email):
            d = TestClient(main.app).get("/api/work/list").json()
        return {i["id"]: i for i in d["items"] if i["source"] == "automation"}

    def test_gate_row_carries_run_and_checkpoint_ids_under_its_request_id(self):
        it = self._automation_items([GATE_ROW])["req-gate"]
        self.assertEqual(it["verb"], "approve_deny")
        p = it["payload"]
        self.assertEqual((p["run_id"], p["checkpoint_id"]), ("run-1", "cp1a2b3c4d5e"))
        self.assertIsNone(p["kind"])
        self.assertIs(p["dry_run"], True)
        self.assertEqual(p["attachments"], [{"name": "flagged_invoices.csv", "size": 2048}])

    def test_review_row_is_keyed_by_request_id_with_attachments_verbatim(self):
        it = self._automation_items([REVIEW_ROW])["req-review"]
        self.assertEqual(it["verb"], "review")
        p = it["payload"]
        self.assertEqual(p["kind"], "review")
        self.assertIsNone(p["checkpoint_id"])
        self.assertEqual(p["run_id"], "run-2")
        self.assertEqual(p["attachments"], [{"name": "NH07 Smith.pdf",
                                             "relpath": "in/NH07 Smith.pdf", "size": 51200}])
        self.assertEqual(p["correctable"], {"employee_id": "3149231"})

    def test_rows_without_attachments_carry_an_empty_list(self):
        bare = _row("req-bare", "Automation checkpoint — x", {
            "source": "automation", "run_id": "run-3", "checkpoint_id": "cp3",
            "automation_id": "auto-3", "automation_name": "x"})
        self.assertEqual(self._automation_items([bare])["req-bare"]["payload"]["attachments"], [])


if __name__ == "__main__":
    unittest.main()
