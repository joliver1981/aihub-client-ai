"""
My Work read-through of the EXISTING approval flows (A2).

Today's human work already lives in three places; My Work must show and act on
all of them without changing any of them:

1. Workflow approvals — ApprovalRequests rows (Azure/on-prem SQL). Listed here
   by replicating the /api/workflow/user-approvals visibility SQL read-only
   (that endpoint is session-cookie-only, so a service can't call it), using
   the platform's own service DB pattern: platform ODBC driver + DATABASE_* env
   + EXEC tenant.sp_setTenantContext once per connection. Decisions go through
   the EXISTING generic endpoint POST /api/workflow/approvals/<request_id>,
   which updates the row the paused engine polls.

2. Automation checkpoints/reviews — the JSON-file sidecar rows under
   data/automations/**/_approvals/ (no table exists; DDL denied on Azure —
   see automations/approval_store.py). Read directly (read-only; writers use
   atomic replace). Decisions go through the SAME generic endpoint, which
   settles the sidecar row AND resumes/aborts the paused run.

3. Agent email approvals — AgentEmailApprovals via the existing
   /api/agent-email/approvals endpoints (X-API-Key + an X-AIHub-User
   assertion for the viewing/deciding user, so the platform scopes and
   attributes exactly as it does for the Approvals page). The editing
   contract is preserved exactly: BODY-ONLY, the edited text posts as
   final_body; to/subject are not editable (parity with today's page).
"""

import glob
import json
import os
from typing import Optional

import httpx

from agent_config import APP_ROOT, get_base_url, AI_HUB_API_KEY, get_internal_api_key, logger

_HEADERS = {"X-API-Key": AI_HUB_API_KEY, "Connection": "close"}


# ---------------------------------------------------------------------------
# HTTP read-through (2026-09-03) — the main app runs the fixed SELECTs
# ---------------------------------------------------------------------------
# WHY: the direct-SQL path below needs DATABASE_* in this process's environment.
# That is true in the dev tree (.env) and false on every install: there the
# credentials live only inside the frozen exes' baked _build_config, and the
# loose copy on disk is trimmed to LLM keys on purpose. So on a client every
# direct read failed with "Login failed for user ''" (pack-20 per-tool smoke,
# Latest7). POST /api/internal/readthrough runs the same queries inside the main
# app under its own credentials, for callers holding the machine-bound
# internal key. Order: HTTP first; direct SQL only when the route is absent
# (older main app), the key is rejected, or the app is unreachable — i.e. the
# pre-change behaviour, unchanged wherever it used to work.
# AGENT_READTHROUGH_HTTP=false turns the HTTP path off (rollback switch).

class ReadthroughUnavailable(Exception):
    """The HTTP read-through is not there / not usable: fall back to SQL."""


def http_enabled() -> bool:
    return os.getenv("AGENT_READTHROUGH_HTTP", "true").strip().lower() != "false"


def fetch(op: str, **params):
    """Run one named read-only op on the main app and return its data.
    Raises ReadthroughUnavailable for 401/404/unreachable (callers fall back
    to direct SQL) and RuntimeError for a real server-side failure."""
    if not http_enabled():
        raise ReadthroughUnavailable("disabled by AGENT_READTHROUGH_HTTP")
    headers = dict(_HEADERS)
    try:
        headers["X-Internal-API-Key"] = get_internal_api_key()
    except Exception:
        pass
    try:
        with httpx.Client(timeout=30) as client:
            r = client.post(f"{get_base_url()}/api/internal/readthrough",
                            json={"op": op, "params": params}, headers=headers)
    except Exception as e:
        raise ReadthroughUnavailable(f"main app unreachable: {e}")
    if r.status_code in (401, 404):
        raise ReadthroughUnavailable(f"HTTP {r.status_code}")
    try:
        body = r.json() or {}
    except Exception:
        body = {}
    # A main app older than this service does not know a newer op (400
    # "unknown readthrough op"): that is "unavailable", so the caller's
    # direct-SQL path runs, not a hard failure (2026-09-25, workflow_decided).
    if r.status_code == 400 and "unknown readthrough op" in str(body.get("message") or ""):
        raise ReadthroughUnavailable(f"op '{op}' unknown to this main app")
    if r.status_code >= 400 or body.get("status") != "success":
        raise RuntimeError(f"readthrough '{op}' failed: HTTP {r.status_code} "
                           f"{(body.get('message') or r.text)[:200]}")
    return body.get("data")


def fetch_or_sql(op: str, sql_fn, **params):
    """HTTP read-through, else the given direct-SQL thunk (pre-change path)."""
    try:
        return fetch(op, **params)
    except ReadthroughUnavailable as e:
        logger.debug(f"readthrough '{op}' via HTTP unavailable ({e}); direct SQL")
        return sql_fn()


# ---------------------------------------------------------------------------
# SQL (read-only) — workflow ApprovalRequests + UserGroups membership
# ---------------------------------------------------------------------------

_DB_DRIVER = None


def _driver(pyodbc):
    """Same driver-selection rule as the platform's config.py (this service
    deploys standalone, so it can't import it): DATABASE_DRIVER when that
    driver is installed ('+' and {braces} tolerated), else ODBC Driver 17 for
    SQL Server when installed — the legacy driver's WRITETEXT path is rejected
    on RLS-protected tables — else the legacy 'SQL Server' driver."""
    global _DB_DRIVER
    if _DB_DRIVER is None:
        configured = ' '.join(
            os.getenv("DATABASE_DRIVER", "").strip().strip('{}').replace('+', ' ').split())
        try:
            installed = {d.strip().lower(): d for d in pyodbc.drivers()}
        except Exception:
            installed = {}
        if configured and installed and configured.lower() not in installed:
            logger.warning("DATABASE_DRIVER '%s' is not an installed ODBC driver; "
                           "auto-selecting instead", configured)
        _DB_DRIVER = (installed.get(configured.lower())
                      or installed.get("odbc driver 17 for sql server")
                      or "SQL Server")
    return _DB_DRIVER


def _db():
    import pyodbc  # optional dependency; failures degrade gracefully
    server = os.getenv("DATABASE_SERVER", "localhost")
    name = os.getenv("DATABASE_NAME", "AIHUB")
    uid = os.getenv("DATABASE_UID", "")
    pwd = os.getenv("DATABASE_PWD", "")
    conn = pyodbc.connect(
        f"DRIVER={{{_driver(pyodbc)}}};SERVER={server};DATABASE={name};UID={uid};PWD={pwd}")
    cur = conn.cursor()
    api_key = os.getenv("API_KEY", "")
    if api_key:
        cur.execute("EXEC tenant.sp_setTenantContext ?", api_key)
    cur.close()
    return conn


def user_group_ids(user_id: int) -> list:
    def _sql():
        conn = _db()
        try:
            cur = conn.cursor()
            cur.execute("SELECT group_id FROM UserGroups WHERE user_id = ?",
                        int(user_id))
            return [int(r[0]) for r in cur.fetchall()]
        finally:
            conn.close()
    try:
        return [int(g) for g in (fetch_or_sql("user_group_ids", _sql, user_id=int(user_id)) or [])]
    except Exception as e:
        logger.warning(f"user_group_ids unavailable: {e}")
        return []


def all_groups() -> list:
    """Every platform group as [{id, name}] (the main app's 'groups' op).
    RAISES when the list cannot be read — a caller routing work to a group
    must be able to tell "no such group" from "could not look"."""
    def _sql():
        conn = _db()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, group_name FROM [dbo].[Groups] ORDER BY group_name")
            return [{"id": int(r[0]), "name": str(r[1])} for r in cur.fetchall()]
        finally:
            conn.close()
    return [{"id": int(g["id"]), "name": str(g["name"])}
            for g in (fetch_or_sql("groups", _sql) or [])]


def group_names() -> dict:
    """{group_id: name} for labelling group-routed items; {} when unreadable."""
    try:
        return {g["id"]: g["name"] for g in all_groups()}
    except Exception as e:
        logger.warning(f"group names unavailable: {e}")
        return {}


# ---------------------------------------------------------------------------
# The Developer+ floor on the shared "unassigned" pool (F-12, 2026-09-08)
# ---------------------------------------------------------------------------
# Both pending sources below carry the same "unassigned means everyone" rule
# as workitem_store.list_items: an approval addressed to nobody is a pool item
# anyone can pick up. That audience was implicitly Developer+ only because The
# Agent's front door used to be; AGENT_ALLOW_ALL_USERS removed the guarantee,
# and a no-group role-1 seat read HR names out of Dayforce review items (RU
# retest F-12). F-7 (df05578) restored the floor in list_items; this is the
# same floor on the other two sources, kept INSIDE the functions so every
# caller inherits it. `role` is REQUIRED (keyword-only): a caller that forgets
# it gets a TypeError, not the pool. Missing / zero role fails CLOSED.
#   role >= 2 : direct + group-member + unassigned/NULL (unchanged)
#   role <  2 : direct + group-member ONLY

def _may_see_pool(role) -> bool:
    try:
        return int(role or 0) >= 2
    except (TypeError, ValueError):
        return False


def _is_pool_row(row: dict) -> bool:
    """An approval addressed to nobody: assigned_to_type NULL or 'unassigned'."""
    at = row.get("assigned_to_type")
    return at is None or str(at).strip().lower() in ("", "unassigned")


def workflow_pending(user_id: int, *, role) -> list:
    """Pending ApprovalRequests visible to this user — direct, group-member,
    and (Developer+ only) the unassigned pool. The floor is applied on BOTH
    paths: the main app's readthrough op takes `role` and drops the pool
    branches from its SQL (app._rt_workflow_pending), and the rows that come
    back are filtered again here so an older main app that ignores `role`
    still cannot widen a regular user's queue."""
    pool = _may_see_pool(role)
    try:
        role_param = int(role or 0)
    except (TypeError, ValueError):
        role_param = 0

    def _sql():
        conn = _db()
        try:
            cur = conn.cursor()
            pool_sql = ("   OR assigned_to_type = 'unassigned'\n"
                        "   OR assigned_to_type IS NULL" if pool else "")
            cur.execute(
                f"""
                SELECT request_id, title, description, status, requested_at,
                       due_date, priority, approval_data, assigned_to_type,
                       assigned_to_id
                FROM ApprovalRequests
                WHERE status = 'Pending' AND (
                      (assigned_to_type = 'user'  AND assigned_to_id = ?)
                   OR (assigned_to_type = 'group' AND assigned_to_id IN
                        (SELECT group_id FROM UserGroups WHERE user_id = ?))
                {pool_sql})
                ORDER BY priority DESC, requested_at DESC
                """, int(user_id), int(user_id))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()
    try:
        rows = list(fetch_or_sql("workflow_pending", _sql, user_id=int(user_id),
                                 role=role_param) or [])
    except Exception as e:
        logger.warning(f"workflow_pending unavailable: {e}")
        return []
    if not pool:
        rows = [r for r in rows if not _is_pool_row(r)]
    return rows


# ---------------------------------------------------------------------------
# Automation sidecar rows (files)
# ---------------------------------------------------------------------------

def automation_pending(user_id: int, group_ids: list, *, role) -> list:
    """Pending automation checkpoint / review rows visible to this user —
    direct, group-member, and (Developer+ only) rows assigned to nobody."""
    pool = _may_see_pool(role)
    rows = []
    # Automations live at APP_ROOT/automations/tenant_<id>/ (CommonUtils
    # get_app_path), NOT under data/ — the sidecar sits beside each tenant dir.
    pattern = os.path.join(APP_ROOT, "automations", "tenant_*", "_approvals",
                           "*.json")
    for path in glob.glob(pattern):
        try:
            with open(path, "r", encoding="utf-8") as f:
                row = json.load(f)
        except Exception:
            continue
        if row.get("status") != "Pending":
            continue
        at, aid = row.get("assigned_to_type"), row.get("assigned_to_id")
        visible = ((pool and _is_pool_row(row))
                   or (at == "user" and aid == int(user_id))
                   or (at == "group" and aid in (group_ids or [])))
        if visible:
            rows.append(row)
    rows.sort(key=lambda r: (-(r.get("priority") or 0),
                             r.get("requested_at") or ""), reverse=False)
    return rows


# ---------------------------------------------------------------------------
# Decided rows (My Work history, 2026-09-25) — the same visibility floor as
# the pending fetches, status other than Pending, newest decision first.
# ---------------------------------------------------------------------------

def workflow_decided(user_id: int, *, role, username: str = "", limit: int = 100) -> list:
    """Decided ApprovalRequests addressed to this user (user / group /
    Developer+ pool) or decided by them (responded_by = their id or their
    username). Main-app op first, direct SQL otherwise."""
    pool = _may_see_pool(role)
    try:
        role_param = int(role or 0)
    except (TypeError, ValueError):
        role_param = 0
    try:
        limit = max(1, int(limit or 100))
    except (TypeError, ValueError):
        limit = 100
    uname = str(username or "").strip() or str(int(user_id))

    def _sql():
        conn = _db()
        try:
            cur = conn.cursor()
            pool_sql = ("   OR assigned_to_type = 'unassigned'\n"
                        "   OR assigned_to_type IS NULL" if pool else "")
            cur.execute(
                f"""
                SELECT TOP {int(limit)} request_id, title, description, status, requested_at,
                       due_date, priority, approval_data, assigned_to_type,
                       assigned_to_id, responded_by, response_at, comments
                FROM ApprovalRequests
                WHERE status <> 'Pending' AND (
                      (assigned_to_type = 'user'  AND assigned_to_id = ?)
                   OR (assigned_to_type = 'group' AND assigned_to_id IN
                        (SELECT group_id FROM UserGroups WHERE user_id = ?))
                   OR responded_by = ? OR responded_by = ?
                {pool_sql})
                ORDER BY response_at DESC
                """, int(user_id), int(user_id), str(int(user_id)), uname)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()
    try:
        rows = list(fetch_or_sql("workflow_decided", _sql, user_id=int(user_id),
                                 role=role_param, username=uname, limit=limit) or [])
    except Exception as e:
        logger.warning(f"workflow_decided unavailable: {e}")
        return []
    if not pool:
        rows = [r for r in rows if not _is_pool_row(r)]
    return rows


def automation_decided(user_id: int, group_ids: list, *, role, username: str = "",
                       limit: int = 100) -> list:
    """Decided automation checkpoint / review rows visible to this user by
    the pending rule, plus rows they decided themselves; newest first."""
    pool = _may_see_pool(role)
    uname = str(username or "").strip().lower()
    rows = []
    pattern = os.path.join(APP_ROOT, "automations", "tenant_*", "_approvals", "*.json")
    for path in glob.glob(pattern):
        try:
            with open(path, "r", encoding="utf-8") as f:
                row = json.load(f)
        except Exception:
            continue
        if row.get("status") == "Pending":
            continue
        at, aid = row.get("assigned_to_type"), row.get("assigned_to_id")
        who = str(row.get("responded_by") or "").strip().lower()
        visible = ((pool and _is_pool_row(row))
                   or (at == "user" and aid == int(user_id))
                   or (at == "group" and aid in (group_ids or []))
                   or (who and (who == str(int(user_id)) or (uname and who == uname))))
        if visible:
            rows.append(row)
    rows.sort(key=lambda r: str(r.get("response_at") or ""), reverse=True)
    try:
        limit = max(1, int(limit or 100))
    except (TypeError, ValueError):
        limit = 100
    return rows[:limit]


# ---------------------------------------------------------------------------
# Email approvals (REST, X-API-Key + X-AIHub-User assertion)
# ---------------------------------------------------------------------------
# WHY the user rides along (2026-09-08, RU pack finding F-7 part 2): with the
# tenant key alone the main app collapsed the caller to the admin fallback and
# returned EVERY pending approval on the install — bodies included — which My
# Work then showed to every viewer regardless of role or agent access, and a
# regular user could settle someone else's approval with the row recording
# admin as the approver. Every call here now runs AS the viewing user: a
# short-lived X-AIHub-User assertion (the document_tools._headers() pattern)
# lets /api/agent-email/approvals scope by that user's agent access, deny
# role < 2, and record the real approver. `user` is REQUIRED — a caller that
# forgets it gets a TypeError, not the unscoped list — and no identity / no
# signing secret fails CLOSED (nothing listed, nothing settled) rather than
# degrading to the service-key posture.

class NoUserIdentity(Exception):
    """The caller could not be identified as a real user, so a per-user
    approval call must not be made at all."""


def _user_headers(user: Optional[dict]) -> dict:
    """Service key + X-AIHub-User assertion minted from the verified principal.
    Raises NoUserIdentity when there is no real user (None, the service
    principal user_id=0, or an unsigned assertion)."""
    uid = (user or {}).get("user_id")
    if uid in (None, "", 0, "0"):
        raise NoUserIdentity("no user identity on this request")
    try:
        import shared_auth
        assertion = shared_auth.sign_user_assertion(
            uid, (user or {}).get("tenant_id"), (user or {}).get("role"))
    except Exception as e:
        raise NoUserIdentity(f"cannot mint user assertion: {e}")
    h = dict(_HEADERS)
    h["X-AIHub-User"] = assertion
    return h


async def email_pending(user: dict) -> list:
    """Pending agent-email approvals visible to THIS user — the platform's own
    scoping (accessible_agent_ids; role < 2 sees none), never the whole store."""
    try:
        headers = _user_headers(user)
    except NoUserIdentity as e:
        logger.warning(f"email_pending: {e}; listing nothing")
        return []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{get_base_url()}/api/agent-email/approvals",
                                 params={"status": "pending"}, headers=headers)
            if r.status_code >= 400:
                # 403 is the normal answer for a regular user (role < 2)
                logger.info(f"email_pending HTTP {r.status_code} for user "
                            f"{(user or {}).get('user_id')}")
                return []
            return (r.json() or {}).get("approvals") or []
    except Exception as e:
        logger.warning(f"email_pending unavailable: {e}")
        return []


async def decide_email(approval_id: int, action: str,
                       final_body: Optional[str], comments: str, *,
                       user: dict) -> tuple:
    """Approve (with the possibly-edited body-only draft) or reject, AS the
    given user (the platform enforces agent access and records the approver).
    Exactly the current page's contract otherwise: final_body falls back to
    the stored draft."""
    try:
        headers = _user_headers(user)
    except NoUserIdentity as e:
        return {"error": f"cannot act on an email approval without a user identity ({e})"}, 403
    body = {"action": action, "comments": comments or ""}
    if final_body is not None:
        body["final_body"] = final_body
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{get_base_url()}/api/agent-email/approvals/{int(approval_id)}",
                json=body, headers=headers)
            try:
                return r.json(), r.status_code
            except Exception:
                return {"error": r.text[:300]}, r.status_code
    except Exception as e:
        return {"error": str(e)}, 502


# ---------------------------------------------------------------------------
# Generic decision endpoint (workflow rows AND automation sidecar rows)
# ---------------------------------------------------------------------------

async def decide_generic(request_id: str, status: str, comments: str,
                         user_id: int, corrections: Optional[dict] = None) -> tuple:
    body = {"status": status, "comments": comments or "", "user": int(user_id)}
    if corrections:
        body["corrections"] = corrections
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{get_base_url()}/api/workflow/approvals/{request_id}",
                json=body, headers=_HEADERS)
            try:
                return r.json(), r.status_code
            except Exception:
                return {"error": r.text[:300]}, r.status_code
    except Exception as e:
        return {"error": str(e)}, 502
