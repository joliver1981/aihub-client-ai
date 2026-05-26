"""Page inventory + per-page action descriptors for the full-feature tour.

Each PAGE entry describes:
    url            — the route to visit (no host)
    title          — short friendly name used in reports
    primary_ctrl   — a CSS selector that MUST be visible after load (sanity)
    action         — optional callable(page, ctx) → str|None that performs
                     the page's primary CRUD action; returns an artifact id
                     or human-readable summary
    cleanup        — optional callable(ctx, artifact_id) for teardown
    skip_reason    — optional str; if set, page is recorded as SKIPPED
    requires_role  — optional minimum role (1=user, 2=developer, 3=admin)

Actions are intentionally light:
  - "open Save modal, fill name, click Save, verify in list, find Delete,
     click Delete" — full lifecycle as a user would do it
  - Use ARTIFACT_PREFIX = "TOUR_" so every created entity is identifiable
    and can be swept on the next run if cleanup didn't fire.

If we don't know how to drive a page, leave action=None — the reachability
phase still runs and we get "page loads + primary control visible" coverage
for free.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


ARTIFACT_PREFIX = "TOUR_"


@dataclass
class Page:
    url: str
    title: str
    primary_ctrl: Optional[str] = None
    action: Optional[Callable] = None
    cleanup: Optional[Callable] = None
    skip_reason: Optional[str] = None
    requires_role: int = 1
    # Pages that legitimately 302 or render an empty shell (e.g., logout).
    expect_redirect: bool = False
    # Pages that need a path param substituted — set by the test runner.
    needs_param: Optional[str] = None
    # Tags for filtering / reporting groups.
    tags: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Page catalogue
# ---------------------------------------------------------------------------
# Reachability-only entries (no action). The CRUD actions are added in
# actions.py and bound by url below.

PAGES: list[Page] = [
    # ── Core navigation ──────────────────────────────────────────────────
    Page(url="/dashboard",             title="Dashboard",        primary_ctrl="body", tags=["core"]),
    Page(url="/chat",                  title="Chat",             primary_ctrl="#user-input, #chat-input, .chat-input", tags=["chat"]),
    Page(url="/landing",               title="Landing",          primary_ctrl="body", tags=["core"]),
    Page(url="/index",                 title="Index",            primary_ctrl="body", tags=["core"]),
    Page(url="/api_check",             title="API Check",        primary_ctrl="body", tags=["core"]),

    # ── Agent management ────────────────────────────────────────────────
    Page(url="/custom_agent_enhanced", title="Custom Agent Builder",
         primary_ctrl="body", tags=["agent"]),
    Page(url="/custom_data_agent",     title="Custom Data Agent",
         primary_ctrl="body", tags=["agent"]),
    Page(url="/assistants",            title="Assistants list",  primary_ctrl="body", tags=["agent"]),
    Page(url="/agent_dashboard",       title="Agent Dashboard",  primary_ctrl="body", tags=["agent"]),
    Page(url="/agent_communication",   title="Agent Communication",
         primary_ctrl="body", tags=["agent"]),

    # ── Knowledge / Documents ───────────────────────────────────────────
    Page(url="/document-manager",      title="Document Manager", primary_ctrl="body", tags=["docs"]),
    Page(url="/document_processor",    title="Document Processor",
         primary_ctrl="body", tags=["docs"]),
    Page(url="/document_processor/job/new", title="New Document Job",
         primary_ctrl="body", tags=["docs"]),
    Page(url="/document_scheduler",    title="Document Scheduler",
         primary_ctrl="body", tags=["docs"]),
    Page(url="/document_summarizer",   title="Document Summarizer",
         primary_ctrl="body", tags=["docs"]),

    # ── Data assistant ──────────────────────────────────────────────────
    Page(url="/data_assistants",       title="Data Assistants",  primary_ctrl="body", tags=["data"]),
    Page(url="/data_chat",             title="Data Chat",        primary_ctrl="body", tags=["data"]),
    Page(url="/data_dictionary",       title="Data Dictionary",  primary_ctrl="body", tags=["data"]),
    Page(url="/data_explorer",         title="Data Explorer",    primary_ctrl="body", tags=["data"]),
    Page(url="/connections",           title="Connections",      primary_ctrl="body", tags=["data"]),

    # ── Workflows / Jobs ────────────────────────────────────────────────
    Page(url="/workflow_tool",         title="Workflow Builder", primary_ctrl="body", tags=["workflow"]),
    Page(url="/monitoring",            title="Workflow Monitoring",
         primary_ctrl="body", tags=["workflow"]),
    Page(url="/approvals",             title="Approvals",        primary_ctrl="body", tags=["workflow"]),
    Page(url="/jobs",                  title="Scheduled Jobs",   primary_ctrl="body", tags=["jobs"]),

    # ── Integrations / MCP / Tools ──────────────────────────────────────
    Page(url="/integrations",          title="Integrations",     primary_ctrl="body", tags=["integ"]),
    Page(url="/mcp_servers",           title="MCP Servers (admin)",
         primary_ctrl="body", tags=["mcp"]),
    Page(url="/mcp_user_servers",      title="MCP User Servers", primary_ctrl="body", tags=["mcp"]),
    Page(url="/my-connections",        title="My Connections",   primary_ctrl="body", tags=["mcp"]),
    Page(url="/custom_tool",           title="Custom Tools",     primary_ctrl="body", tags=["tool"]),

    # ── Compliance ──────────────────────────────────────────────────────
    Page(url="/compliance",            title="Compliance Mgmt",  primary_ctrl="body", tags=["compliance"]),
    Page(url="/compliance/schemas",    title="Compliance Schemas",
         primary_ctrl="body", tags=["compliance"]),

    # ── Admin / config ──────────────────────────────────────────────────
    Page(url="/users",                 title="Users",            primary_ctrl="body",
         tags=["admin"], requires_role=3),
    Page(url="/groups",                title="Groups",           primary_ctrl="body",
         tags=["admin"], requires_role=3),
    Page(url="/admin/api-keys",        title="API Keys Config",  primary_ctrl="body",
         tags=["admin"], requires_role=3),
    Page(url="/admin/identity/settings", title="Identity Settings",
         primary_ctrl="body", tags=["admin"], requires_role=3),
    Page(url="/admin/tier",            title="Tier Usage",       primary_ctrl="body",
         tags=["admin"], requires_role=3),
    Page(url="/admin/caution-settings", title="Caution Settings",
         primary_ctrl="body", tags=["admin"], requires_role=3),
    Page(url="/admin/feedback-analysis", title="Feedback Analysis",
         primary_ctrl="body", tags=["admin"], requires_role=3),
    Page(url="/assignments/manage",    title="Env Assignments",  primary_ctrl="body",
         tags=["admin"], requires_role=2),
    Page(url="/environments/assignments", title="Env Assignments (alt)",
         primary_ctrl="body", tags=["admin"], requires_role=2),
    Page(url="/local-secrets",         title="Local Secrets",    primary_ctrl="body",
         tags=["admin"], requires_role=3),
    Page(url="/system_logs",           title="System Logs",      primary_ctrl="body",
         tags=["admin"], requires_role=3),
    Page(url="/settings/telemetry",    title="Telemetry Settings",
         primary_ctrl="body", tags=["admin"], requires_role=3),
    Page(url="/llm_unit_test",         title="LLM Unit Test",    primary_ctrl="body",
         tags=["admin"], requires_role=3),

    # ── Solutions ───────────────────────────────────────────────────────
    Page(url="/solutions",             title="Solutions Gallery", primary_ctrl="body", tags=["solutions"]),
    Page(url="/solutions/author",      title="Solutions Author",  primary_ctrl="body", tags=["solutions"]),
    Page(url="/solutions/author/new",  title="New Solution",      primary_ctrl="body", tags=["solutions"]),

    # ── Email / preferences ─────────────────────────────────────────────
    Page(url="/email-processing/history", title="Email Processing History",
         primary_ctrl="body", tags=["email"]),
    Page(url="/preferences/",          title="User Preferences",  primary_ctrl="body", tags=["user"]),
]


def find_page(url: str) -> Optional[Page]:
    for p in PAGES:
        if p.url == url:
            return p
    return None
