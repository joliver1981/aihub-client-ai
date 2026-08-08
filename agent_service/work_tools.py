"""
The Agent's My Work tools — raise and inspect work items (A2).

raise_work_item is the seam that lets the assistant route work to humans: a
question it needs answered, something to review, a draft to edit, an FYI. In
interactive chat the human is right there — so the tool is mostly for things
addressed to OTHER people or for work that should outlive the conversation;
headless runs (A3) will lean on it heavily.
"""

import json
import os
from typing import Any

from platform_tools import CURRENT_USER, _text
from claude_agent_sdk import tool
import workitem_store


@tool(
    "raise_work_item",
    "Put a work item in someone's My Work queue. Use when something needs a "
    "human decision, review, input, or awareness that should be tracked — "
    "especially for someone OTHER than the current user, or work that must "
    "outlive this conversation. Verbs: approve_deny, review, provide_input, "
    "edit_and_return, acknowledge, do_offline. Leave addressed_user_id at 0 "
    "for a shared item anyone can claim. Never fabricate payload evidence — "
    "only include facts from this conversation's tool results.",
    {
        "type": "object",
        "properties": {
            "verb": {"type": "string",
                     "enum": ["approve_deny", "review", "provide_input",
                              "edit_and_return", "acknowledge", "do_offline"]},
            "title": {"type": "string", "description": "Short imperative title"},
            "summary": {"type": "string",
                        "description": "Everything needed to act, inline"},
            "addressed_user_id": {"type": "integer",
                                  "description": "0 = shared (anyone claims)"},
            "priority": {"type": "integer", "description": "0 normal, 1 high"},
            "payload_json": {"type": "string",
                             "description": "Optional JSON evidence payload"},
        },
        "required": ["verb", "title", "summary"],
        "additionalProperties": False,
    },
)
async def raise_work_item(args: dict[str, Any]) -> dict[str, Any]:
    user = CURRENT_USER.get()
    payload = {}
    if args.get("payload_json"):
        try:
            payload = json.loads(args["payload_json"])
        except Exception:
            return _text("payload_json is not valid JSON", is_error=True)
    # Fail-closed: promotion payloads are minted ONLY by the sanctioned
    # save_skill/save_view paths. A generic work item must never be able to
    # impersonate one and turn an admin's approval into a publish.
    if isinstance(payload, dict) and payload.get("kind") in (
            "skill_promotion", "view_promotion"):
        return _text("payload.kind '" + str(payload["kind"]) + "' is reserved "
                     "for the save_skill/save_view promotion flows — use those "
                     "tools instead.", is_error=True)
    addressed = int(args.get("addressed_user_id") or 0) or None
    try:
        item = workitem_store.create_item(
            str(args["verb"]), str(args["title"]).strip(),
            summary=str(args.get("summary") or ""),
            payload=payload,
            addressed_user=addressed,
            from_kind="agent_session",
            from_ref=str(user.get("username") or ""),
            priority=int(args.get("priority") or 0),
            created_by=str(user.get("username") or "agent"),
        )
    except ValueError as e:
        return _text(str(e), is_error=True)
    who = f"user {addressed}" if addressed else "the shared queue (anyone can claim)"
    return _text(f"Work item created: '{item['title']}' "
                 f"(id {item['work_item_id']}, {item['verb']}) — addressed to {who}. "
                 "It is now visible in My Work.")


@tool(
    "list_my_work",
    "List the open items in the current user's My Work queue (their personal "
    "items plus unclaimed shared items).",
    {},
)
async def list_my_work(args: dict[str, Any]) -> dict[str, Any]:
    user = CURRENT_USER.get()
    items = workitem_store.list_items(int(user.get("user_id") or 0))
    if not items:
        return _text("The My Work queue is empty — nothing is waiting on you.")
    lines = []
    for it in items[:30]:
        who = "you" if it.get("addressed_user") else (
            f"claimed by you" if it.get("claimed_by") else "unclaimed · shared")
        lines.append(f"- [{it['verb']}] {it['title']} (id {it['work_item_id'][:8]}…, "
                     f"{it['status']}, {who}, raised {it['created_at']})")
    return _text(f"Open work items ({len(items)}):\n" + "\n".join(lines))


@tool(
    "schedule_agent_task",
    "Schedule a recurring HEADLESS agent task: at each firing, a fresh agent "
    "session runs the given prompt AS the current user and reports its result "
    "into their My Work queue as an FYI. Use for recurring asks that need "
    "judgment each time ('every morning, check X and flag anomalies') — for "
    "purely mechanical repetition, prefer building an automation instead "
    "(cheaper: zero tokens per run). Provide cron_expression OR "
    "every_hours/every_days. Report ONLY the ids this returns.",
    {
        "type": "object",
        "properties": {
            "task_prompt": {"type": "string",
                            "description": "The full instruction the headless "
                                           "session will run each time"},
            "name": {"type": "string", "description": "Short job name"},
            "cron_expression": {"type": "string"},
            "every_hours": {"type": "integer"},
            "every_days": {"type": "integer"},
        },
        "required": ["task_prompt", "name"],
        "additionalProperties": False,
    },
)
async def schedule_agent_task(args: dict[str, Any]) -> dict[str, Any]:
    import datetime as _dt
    import httpx
    from platform_tools import _headers
    from agent_config import get_base_url

    user = CURRENT_USER.get()
    if int(user.get("role") or 0) < 2 and os.getenv(
            "AGENT_BUILD_ALLOW_ALL_USERS", "false").lower() != "true":
        return _text("Scheduling agent tasks requires a Developer role.",
                     is_error=True)
    if args.get("cron_expression"):
        schedule = {"type": "cron",
                    "cron_expression": str(args["cron_expression"])}
    elif args.get("every_hours") or args.get("every_days"):
        # Interval schedules need an anchored start or the engine's re-create
        # loop pushes the next fire forever (CC lesson).
        schedule = {"type": "interval",
                    "start_date": _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")}
        if args.get("every_hours"):
            schedule["interval_hours"] = int(args["every_hours"])
        if args.get("every_days"):
            schedule["interval_days"] = int(args["every_days"])
    else:
        return _text("Provide either cron_expression or every_hours/every_days.",
                     is_error=True)

    body = {
        "name": f"Agent: {str(args['name']).strip()[:80]}",
        "type": "agent_session",
        # string "0": the route's presence check treats int 0 as missing
        "target_id": "0",
        "description": str(args["task_prompt"])[:400],
        "created_by": str(user.get("username") or "agent"),
        "is_active": True,
        "parameters": {
            "prompt": {"value": str(args["task_prompt"]), "type": "string"},
            "user_id": {"value": str(int(user.get("user_id") or 0)), "type": "string"},
            "role": {"value": str(int(user.get("role") or 2)), "type": "string"},
            "username": {"value": str(user.get("username") or ""), "type": "string"},
        },
        "schedule": schedule,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{get_base_url()}/api/scheduler/jobs",
                                  json=body, headers=_headers())
            data = r.json() if r.status_code < 500 else {}
            if r.status_code >= 400 or not data.get("id"):
                return _text(f"Nothing was scheduled (HTTP {r.status_code}: "
                             f"{data.get('error', r.text[:200])}). Do NOT tell "
                             "the user it was scheduled.", is_error=True)
            job_id = data["id"]
            # Read-back: the job + an active schedule row must really exist.
            rb = await client.get(f"{get_base_url()}/api/scheduler/jobs/{job_id}",
                                  headers=_headers())
            rbd = rb.json() if rb.status_code < 400 else {}
            active = any(s.get("is_active") for s in (rbd.get("schedules") or []))
            if not active:
                return _text(f"Job #{job_id} was created but NO active schedule "
                             "row exists — report this as NOT scheduled.",
                             is_error=True)
    except Exception as e:
        return _text(f"Scheduling failed: {e}", is_error=True)
    return _text(f"Scheduled headless agent task '{body['name']}' (job #{job_id}, "
                 "verified active by read-back). Each firing runs as "
                 f"{user.get('username')} and lands an FYI in their My Work. "
                 "The engine picks it up on its next poll.")


@tool(
    "save_skill",
    "Save procedural knowledge as a skill so future sessions start from "
    "know-how instead of rediscovery. Use AFTER solving something non-obvious: "
    "a process, a data model's quirks, a client convention. Scopes: 'user' "
    "(private, default), 'group' (share with one of the user's groups — ask "
    "the user to confirm first, and pass their group_id), 'tenant' (everyone — "
    "this only FILES A REQUEST; an admin must approve it in My Work). Write "
    "the description as a trigger ('use when...'). Record procedure and "
    "gotchas, but tell future sessions to verify current facts with discovery "
    "tools — never freeze schema or values as truth.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "kebab-case, e.g. month-end-close"},
            "description": {"type": "string", "description": "'Use when …' trigger line"},
            "content": {"type": "string", "description": "The skill body (markdown)"},
            "scope": {"type": "string", "enum": ["user", "group", "tenant"]},
            "group_id": {"type": "integer", "description": "Required for scope=group"},
        },
        "required": ["name", "description", "content"],
        "additionalProperties": False,
    },
)
async def save_skill(args: dict[str, Any]) -> dict[str, Any]:
    import skills_mount
    user = CURRENT_USER.get()
    uid = int(user.get("user_id") or 0)
    scope = str(args.get("scope") or "user")
    name = str(args["name"]).strip().lower()
    if not skills_mount.valid_name(name):
        return _text("Skill name must be kebab-case (a-z, 0-9, '-').", is_error=True)

    if scope == "tenant":
        item = workitem_store.create_item(
            "approve_deny", f"Promote skill '{name}' to tenant",
            summary=(f"Requested by {user.get('username')}. Description: "
                     f"{args['description']}\n\n--- SKILL.md ---\n"
                     + str(args["content"])[:1500]),
            payload={"kind": "skill_promotion", "name": name,
                     "description": str(args["description"]),
                     "content": str(args["content"]),
                     "requested_by": user.get("username")},
            from_kind="agent_session", from_ref=str(user.get("username") or ""),
            created_by=str(user.get("username") or "agent"), priority=0)
        return _text(f"Tenant promotion requested — approval item "
                     f"{item['work_item_id']} is now in My Work (admin approval "
                     "required; the skill is NOT shared yet).")

    if scope == "group":
        gid = int(args.get("group_id") or 0)
        if not gid:
            return _text("scope=group needs group_id (ask the user which of "
                         "their groups).", is_error=True)
        import readthrough
        if gid not in readthrough.user_group_ids(uid):
            return _text(f"User {uid} is not a member of group {gid} — not saved.",
                         is_error=True)
        path = skills_mount.write_skill("group", name, args["description"],
                                        args["content"], group_id=gid)
        return _text(f"Skill '{name}' saved to group {gid} ({path}). Members' "
                     "future sessions will load it when relevant.")

    path = skills_mount.write_skill("user", name, args["description"],
                                    args["content"], user_id=uid)
    return _text(f"Skill '{name}' saved to your private scope ({path}). Your "
                 "future sessions will load it when relevant.")


@tool(
    "list_skills",
    "List the skills the current user's sessions load: product + tenant + "
    "their groups + private.",
    {},
)
async def list_skills_tool(args: dict[str, Any]) -> dict[str, Any]:
    import skills_mount
    import readthrough
    user = CURRENT_USER.get()
    uid = int(user.get("user_id") or 0)
    skills = skills_mount.list_skills(uid, readthrough.user_group_ids(uid))
    if not skills:
        return _text("No skills exist yet in any scope.")
    lines = []
    for s in skills:
        scope = s["scope"] + (f" {s['group_id']}" if s.get("group_id") else "")
        lines.append(f"- [{scope}] {s['name']} — {s['description'][:100]}")
    return _text(f"Skills ({len(skills)}):\n" + "\n".join(lines))


WORK_TOOLS = [raise_work_item, list_my_work, schedule_agent_task,
              save_skill, list_skills_tool]
