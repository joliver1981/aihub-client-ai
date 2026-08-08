"""
The Agent's Integrations tools — the platform Integrations feature (SharePoint,
Shopify, Stripe, Azure Blob, custom APIs...) exposed conversationally.

These are thin wrappers over the main app's EXISTING internal seam
(/api/internal/integrations*, service-key auth) — the same machinery the
legacy GeneralAgent integration tools use. Credentials never appear here:
instances are configured on the Integrations page, the platform owns the
OAuth/token lifecycle, and operations execute server-side.

ACCESS MODEL (James, 2026-08-08 — optional group scoping):
- role >= 2 (developers/admins): see and use EVERY integration — unchanged
  legacy behavior.
- role < 2 (regular users): see/use ONLY integrations whose
  assigned_group_ids intersect their groups. Unassigned = invisible
  (fail-closed), which matches today's reality where regular users have no
  integrations surface at all. Assigning a group is the deliberate opt-in.
- Enforcement lives HERE, at the chokepoint every Agent surface (chat,
  email sessions, headless runs) converges on. Assignment itself is
  admin-only (role >= 3).
"""

import json
from typing import Any, Optional

from claude_agent_sdk import tool

from platform_tools import CURRENT_USER, _text, _get, _post

MAX_RESULT_CHARS = 2500      # honest preview cap; full payloads stay server-side
DESTRUCTIVE_PREFIXES = ("delete_",)


async def _fetch_integrations() -> list:
    data = await _get("/api/internal/integrations")
    return data.get("integrations") or []


def _user_groups(uid: int) -> set:
    import readthrough
    return set(readthrough.user_group_ids(uid))


def accessible(intg: dict, role: int, group_ids: set) -> bool:
    """The access rule — kept as a pure function so the pack can test it."""
    if int(role) >= 2:
        return True
    assigned = set(int(g) for g in (intg.get("assigned_group_ids") or []))
    return bool(assigned & group_ids)


async def _resolve_accessible(integration_id: int) -> tuple:
    """(integration, err) — resolves AND access-checks for the current user."""
    user = CURRENT_USER.get()
    role = int(user.get("role") or 0)
    groups = _user_groups(int(user.get("user_id") or 0)) if role < 2 else set()
    for intg in await _fetch_integrations():
        if int(intg.get("integration_id") or 0) == int(integration_id):
            if accessible(intg, role, groups):
                return intg, None
            return None, (f"Integration {integration_id} exists but is not "
                          "available to this user (it isn't assigned to any "
                          "of their groups — an admin can assign it).")
    return None, f"No integration with id {integration_id} is configured."


@tool(
    "list_integrations",
    "List the platform integrations THIS USER can use (SharePoint, Shopify, "
    "Stripe, custom APIs...). Developers/admins see everything; regular "
    "users see only integrations assigned to their groups. Call this FIRST "
    "whenever a request involves an external system — never assume what is "
    "connected. Instances are configured (with credentials) on the "
    "Integrations page, never through chat.",
    {},
)
async def list_integrations(args: dict[str, Any]) -> dict[str, Any]:
    try:
        user = CURRENT_USER.get()
        role = int(user.get("role") or 0)
        uid = int(user.get("user_id") or 0)
        groups = _user_groups(uid) if role < 2 else set()
        rows = await _fetch_integrations()
        visible = [i for i in rows if accessible(i, role, groups)]
        if not visible:
            if rows and role < 2:
                return _text(f"{len(rows)} integration(s) exist on this "
                             "install, but none are assigned to this user's "
                             "groups — an admin can assign one to make it "
                             "available.")
            return _text("No integrations are configured on this install "
                         "(they are set up on the Integrations page).")
        lines = []
        for i in visible:
            state = "connected" if i.get("is_connected") else "NOT CONNECTED"
            ag = i.get("assigned_group_ids") or []
            extra = f", groups {ag}" if ag else ""
            lines.append(f"- id {i['integration_id']} — {i['integration_name']} "
                         f"({i.get('platform_name')}, {state}{extra})")
        note = ("" if role >= 2 else
                "\n(Showing only integrations assigned to your groups.)")
        return _text(f"Integrations available to this user ({len(visible)}):\n"
                     + "\n".join(lines) + note)
    except Exception as e:
        return _text(f"Could not list integrations: {e}", is_error=True)


@tool(
    "get_integration_operations",
    "List the operations an integration supports (e.g. SharePoint: "
    "lookup_site_by_url, list_drives, search_files, download_file, "
    "download_to_knowledge...). Check operations before executing — never "
    "guess an operation key or its parameters.",
    {
        "type": "object",
        "properties": {"integration_id": {"type": "integer"}},
        "required": ["integration_id"],
        "additionalProperties": False,
    },
)
async def get_integration_operations(args: dict[str, Any]) -> dict[str, Any]:
    try:
        intg, err = await _resolve_accessible(int(args["integration_id"]))
        if err:
            return _text(err, is_error=True)
        data = await _get(f"/api/internal/integrations/"
                          f"{intg['integration_id']}/operations")
        ops = data.get("operations") or []
        if not ops:
            return _text(f"Integration {intg['integration_name']} reports no "
                         "operations.")
        lines = [f"Operations for {intg['integration_name']} "
                 f"({intg.get('platform_name')}):"]
        for op in ops:
            params = ", ".join(
                f"{p.get('name')}{'*' if p.get('required') else ''}"
                for p in (op.get("parameters") or []))
            lines.append(f"- {op.get('key')} — {op.get('name')}"
                         + (f" (params: {params})" if params else ""))
        lines.append("(* = required parameter)")
        return _text("\n".join(lines))
    except Exception as e:
        return _text(f"Could not fetch operations: {e}", is_error=True)


@tool(
    "execute_integration_operation",
    "Execute an operation on an integration the user can access (e.g. "
    "SharePoint search_files / download_file / download_to_knowledge). The "
    "platform owns auth and runs the operation server-side. Check "
    "get_integration_operations first for the exact key and parameters. "
    "Destructive operations (delete_*) require confirmed=true AFTER the "
    "user explicitly confirms. Large results are previewed here truncated — "
    "report counts honestly, never invent content beyond the preview.",
    {
        "type": "object",
        "properties": {
            "integration_id": {"type": "integer"},
            "operation": {"type": "string", "description": "Operation key"},
            "parameters_json": {"type": "string",
                                "description": "JSON object of parameters"},
            "confirmed": {"type": "boolean",
                          "description": "Required true for delete_* operations"},
        },
        "required": ["integration_id", "operation"],
        "additionalProperties": False,
    },
)
async def execute_integration_operation(args: dict[str, Any]) -> dict[str, Any]:
    try:
        intg, err = await _resolve_accessible(int(args["integration_id"]))
        if err:
            return _text(err, is_error=True)
        op = str(args["operation"]).strip()
        if op.startswith(DESTRUCTIVE_PREFIXES) and not args.get("confirmed"):
            return _text(f"CONFIRMATION REQUIRED: '{op}' on "
                         f"{intg['integration_name']} is destructive. Ask the "
                         "user to confirm, then call again with confirmed=true.")
        params = {}
        if args.get("parameters_json"):
            try:
                params = json.loads(args["parameters_json"])
            except Exception:
                return _text("parameters_json is not valid JSON", is_error=True)
        user = CURRENT_USER.get()
        data, status = await _post(
            f"/api/internal/integrations/{intg['integration_id']}/execute",
            {"operation": op, "parameters": params,
             "context": {"source": "the_agent",
                         "user_id": int(user.get("user_id") or 0),
                         "username": str(user.get("username") or "")}},
            timeout=300)
        if status >= 400 or data.get("status") == "error":
            return _text(f"Operation FAILED (HTTP {status}): "
                         f"{data.get('message', data)}", is_error=True)
        payload = data.get("result", data)
        raw = json.dumps(payload, default=str)
        preview = raw[:MAX_RESULT_CHARS]
        suffix = (f"\n…(truncated — {len(raw)} chars total; report only what "
                  "you can see)" if len(raw) > MAX_RESULT_CHARS else "")
        return _text(f"Operation '{op}' on {intg['integration_name']} "
                     f"succeeded:\n{preview}{suffix}")
    except Exception as e:
        return _text(f"Operation failed: {e}", is_error=True)


@tool(
    "assign_integration_groups",
    "ADMIN ONLY: set which groups can use an integration through The Agent. "
    "Regular users only ever see integrations assigned to at least one of "
    "their groups; developers/admins always see everything. Pass the FULL "
    "list (replaces existing); empty list = back to developers/admins only. "
    "Verified by read-back. Ask the user which groups before calling.",
    {
        "type": "object",
        "properties": {
            "integration_id": {"type": "integer"},
            "group_ids": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["integration_id", "group_ids"],
        "additionalProperties": False,
    },
)
async def assign_integration_groups(args: dict[str, Any]) -> dict[str, Any]:
    user = CURRENT_USER.get()
    if int(user.get("role") or 0) < 3:
        return _text("Assigning integration access requires an admin.",
                     is_error=True)
    try:
        gid_list = sorted({int(g) for g in (args.get("group_ids") or [])})
        data, status = await _post(
            f"/api/internal/integrations/{int(args['integration_id'])}/assign-groups",
            {"group_ids": gid_list}, timeout=30)
        if status >= 400 or data.get("status") != "success":
            return _text(f"Assignment FAILED (HTTP {status}): "
                         f"{data.get('message', data)} — nothing changed.",
                         is_error=True)
        # Read-back: the list seam must now report exactly these groups.
        for intg in await _fetch_integrations():
            if int(intg.get("integration_id") or 0) == int(args["integration_id"]):
                got = sorted(int(g) for g in (intg.get("assigned_group_ids") or []))
                if got == gid_list:
                    who = (f"groups {gid_list}" if gid_list
                           else "developers/admins only (unassigned)")
                    return _text(f"Integration {intg['integration_name']} is now "
                                 f"available to {who} (verified by read-back).")
                return _text(f"Assignment reported success but read-back shows "
                             f"{got} — report this as UNVERIFIED.", is_error=True)
        return _text("Assignment reported success but the integration no longer "
                     "appears in the list — report this as UNVERIFIED.",
                     is_error=True)
    except Exception as e:
        return _text(f"Assignment failed: {e}", is_error=True)


INTEGRATION_TOOLS = [list_integrations, get_integration_operations,
                     execute_integration_operation, assign_integration_groups]
