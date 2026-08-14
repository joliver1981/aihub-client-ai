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
    # Fail-closed: reserved payload kinds are minted ONLY by their sanctioned
    # tools. A generic work item must never be able to impersonate one and
    # turn a human's approval into a publish or an email send.
    if isinstance(payload, dict) and payload.get("kind") in (
            "skill_promotion", "view_promotion", "agent_email_reply"):
        return _text("payload.kind '" + str(payload["kind"]) + "' is reserved "
                     "— use save_skill / save_view / draft_email_reply "
                     "instead.", is_error=True)
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
    "Schedule a HEADLESS agent task — recurring OR one-shot delayed: at each "
    "firing, a fresh agent session runs the given prompt AS the current user "
    "and reports its result into their My Work queue as an FYI. Recurring "
    "('every morning, check X'): cron_expression OR every_hours/every_days. "
    "ONE-SHOT DELAYED ('check my email in 2 minutes', 'follow up in an "
    "hour'): run_in_minutes — fires once, then the job deactivates. The "
    "engine polls about every minute, so timing is minute-granular, not "
    "exact seconds. For purely mechanical repetition prefer an automation "
    "(zero tokens per run). Report ONLY the ids this returns.",
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
            "run_in_minutes": {"type": "integer",
                               "description": "One-shot: fire once this many "
                                              "minutes from now (min 1)"},
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
    one_shot = False
    if args.get("run_in_minutes"):
        # ONE-SHOT (James 2026-08-09): a 'date' schedule = the engine's
        # DateTrigger — fires once (pending-execution dedupe engine-side).
        mins = max(int(args["run_in_minutes"]), 1)
        fire_at = _dt.datetime.utcnow() + _dt.timedelta(minutes=mins)
        schedule = {"type": "date",
                    "start_date": fire_at.strftime("%Y-%m-%d %H:%M:%S")}
        one_shot = True
    elif args.get("cron_expression"):
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
        return _text("Provide cron_expression, every_hours/every_days, or "
                     "run_in_minutes for a one-shot.", is_error=True)

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
    if one_shot:
        return _text(f"One-shot task '{body['name']}' scheduled (job #{job_id}, "
                     "verified active by read-back). It fires ONCE in about "
                     f"{max(int(args['run_in_minutes']), 1)} minute(s) (the "
                     "engine polls ~every minute), runs as "
                     f"{user.get('username')}, and lands its result as an FYI "
                     "in their My Work.")
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


def _refresh_summary(tiles: list) -> str:
    """How the refresh ACTUALLY went, in one line.

    The model never sees tile data, which is what stops it inventing numbers —
    but it also means it cannot tell a live dashboard from four stale tiles. On
    the first live run it wrote "the tiles carry the current figures" about a
    board where every tile had failed and was serving 4-day-old cache. The
    rendered email was honest; the covering note was not. So the tool reports
    freshness explicitly and the model is told to pass it on.
    """
    fresh = sum(1 for t in tiles if not t.get("error"))
    stale = sum(1 for t in tiles if t.get("error") and (t.get("cache") or {}).get("rows") is not None)
    dead = len(tiles) - fresh - stale
    if fresh == len(tiles):
        return f"all {fresh} tiles refreshed live"
    bits = [f"{fresh} of {len(tiles)} tiles refreshed live"]
    if stale:
        bits.append(f"{stale} could NOT refresh and show older cached values")
    if dead:
        bits.append(f"{dead} failed with no data at all")
    # Carry a real reason, not just a count: "0 of 6 refreshed" in a scheduler
    # execution record is a mystery a week later, and this is the only place
    # that still has the tile errors in hand.
    why = next((str(t.get("error")) for t in tiles if t.get("error")), "")
    if why:
        bits.append(f"first error: {' '.join(why.split())[:200]}")
    return "; ".join(bits)


async def render_view_for_email(view_name: str, scope: str, group_id: int,
                                principal: dict) -> tuple:
    """Resolve + refresh a View server-side and render it for email.
    Returns (html, text, error, refresh_summary).

    NOT a tool — a plain helper, and it must stay ABOVE the @tool block below:
    a bare function sitting between a decorator and its intended target silently
    steals the decoration, leaving the real tool undecorated and crashing
    create_sdk_mcp_server at import ('function' object has no attribute 'name').

    The MODEL never sees the tile data: numbers travel from the governed
    refresh path straight into the message, so they cannot be paraphrased,
    rounded, or invented. Visibility is enforced by views_store.get() — a
    guessed name resolves to nothing, not to data.

    Shared with the approval path in main.py, which re-runs this at SEND time
    so an approved email carries current numbers rather than draft-time ones.
    """
    import readthrough
    import views_store
    import email_render
    from views_tools import run_view

    uid = int(principal.get("user_id") or 0)
    view = views_store.get(str(view_name).strip(), uid,
                           readthrough.user_group_ids(uid), str(scope or ""),
                           int(group_id or 0))
    if not view:
        return "", "", (f"No saved View named '{view_name}' is visible to this "
                        "user — nothing was drafted or sent."), ""
    # Automation tiles run through the governed seam AS this principal.
    CURRENT_USER.set(principal)
    result = await run_view(view)
    html, text = email_render.render_view(
        result, base_url=os.getenv("APP_PUBLIC_BASE_URL", ""))
    return html, text, None, _refresh_summary(result.get("tiles") or [])


@tool(
    "draft_email_reply",
    "Send/draft an outbound email FROM the current user's personal agent "
    "address, honoring their address settings: with auto-send OFF (default) "
    "it files an EDITABLE approval into My Work and NOTHING sends until they "
    "approve; with auto-send ON it sends immediately via the platform's "
    "governed transport and reports so. Outbound can be disabled entirely on "
    "the Email screen. Report exactly what happened — never claim SENT unless "
    "the result says so. Requires an active agent email address. "
    "FORMATTING: write `body` as plain text with light markdown — '# ' / '## ' "
    "headings, '- ' bullets, '1. ' numbered lists, **bold**, `code`, "
    "[links](https://…), and | pipe | tables |. The service renders that to "
    "styled HTML and sends the text you wrote as the plain-text alternative, "
    "so write it to read well BOTH ways. Do NOT write raw HTML. "
    "EMBED A DASHBOARD: pass view_name to append a saved View's live tiles to "
    "the email. The View is refreshed and rendered BY THE SERVICE at send time "
    "— you never see or retype the numbers, so never restate them in `body`; "
    "write the covering note and let the tiles carry the data. A refresh can "
    "PARTIALLY FAIL, in which case those tiles show older cached values (the "
    "email labels them). The tool result tells you exactly how many refreshed — "
    "never call the figures 'current' or 'live' unless it says all tiles did.",
    {
        "type": "object",
        "properties": {
            "to": {"type": "array", "items": {"type": "string"},
                   "description": "Recipient addresses"},
            "subject": {"type": "string"},
            "view_name": {"type": "string",
                          "description": "Optional: a saved View to refresh and "
                                         "embed as a dashboard in the email"},
            "view_scope": {"type": "string",
                           "enum": ["user", "group", "tenant"],
                           "description": "Only when View names collide across scopes"},
            "view_group_id": {"type": "integer"},
            "body": {"type": "string",
                     "description": "Draft body: plain text with light markdown "
                                    "(see FORMATTING above). Never raw HTML."},
            "rich": {"type": "boolean",
                     "description": "Default true — send a formatted HTML version "
                                    "alongside the plain text. Set false only for "
                                    "a deliberately plain-text-only message."},
            "context": {"type": "string",
                        "description": "Optional: what this replies to (shown "
                                       "to the approver)"},
        },
        "required": ["to", "subject", "body"],
        "additionalProperties": False,
    },
)
async def draft_email_reply(args: dict[str, Any]) -> dict[str, Any]:
    import email_store
    user = CURRENT_USER.get()
    uid = int(user.get("user_id") or 0)
    addr = email_store.get_address(uid)
    if not addr or not addr.get("is_active"):
        return _text("This user has no active agent email address — nothing "
                     "was drafted. They can create one on the Email screen.",
                     is_error=True)
    if not addr.get("outbound_enabled", 1):
        return _text("Outbound email is DISABLED for this address (Email "
                     "screen setting) — nothing was drafted or sent.",
                     is_error=True)
    to = [str(a).strip() for a in (args.get("to") or []) if str(a).strip()]
    if not to:
        return _text("At least one recipient is required.", is_error=True)
    subject = str(args["subject"]).strip()[:300]
    body = str(args["body"])
    # HTML is opt-OUT per message and kill-switchable install-wide
    # (AGENT_EMAIL_HTML=false). The markdown-ish body is ALWAYS sent as the
    # plain-text alternative, so a client that can't render HTML loses nothing.
    import email_render
    rich = bool(args.get("rich", True)) and email_render.html_enabled()

    # Optional embedded dashboard (Phase 2). Rendered NOW for the auto-send
    # path; the approval path stores the reference and re-renders at send.
    view_html = view_text = ""
    view_ref = None
    if str(args.get("view_name") or "").strip():
        principal = {"user_id": uid, "role": int(user.get("role") or 2),
                     "username": str(user.get("username") or ""),
                     "name": str(user.get("name") or "")}
        view_html, view_text, view_err, view_status = await render_view_for_email(
            str(args["view_name"]), str(args.get("view_scope") or ""),
            int(args.get("view_group_id") or 0), principal)
        if view_err:
            return _text(view_err, is_error=True)
        view_ref = {"name": str(args["view_name"]).strip(),
                    "scope": str(args.get("view_scope") or ""),
                    "group_id": int(args.get("view_group_id") or 0),
                    # Stored principal, same idea as a view_refresh JSS job: the
                    # approval may be actioned by an admin who cannot see the
                    # drafter's private View, so the re-run uses THIS envelope.
                    "as_user": principal}

    plain_body = body + (("\n\n" + view_text) if view_text else "")

    if addr.get("auto_send"):
        # AUTO-SEND (James 2026-08-09, opt-in per address): send now through
        # the same cloud transport the approval path uses; leave a closed
        # FYI audit item. A failed send falls back to the approval queue —
        # never silently dropped.
        import email_client
        result = await email_client.send_reply(
            to, subject, plain_body, addr["email_address"],
            f"{addr.get('prefix', 'agent')} via The Agent",
            html_body=email_render.render_email_with_view(
                body, view_html, title=subject) if rich else None)
        if result.get("success"):
            workitem_store.create_item(
                "acknowledge", f"✉ Auto-sent: {subject or '(no subject)'}",
                summary=(f"To: {', '.join(to)}\nFrom: {addr['email_address']}\n"
                         f"(auto-send is ON for this address)"
                         + (f"\nEmbedded View: {view_ref['name']}" if view_ref else "")
                         + f"\n\n{body[:1500]}"),
                payload={"kind": "agent_email_autosent", "to": to,
                         "subject": subject, "from_user": uid},
                addressed_user=uid, from_kind="agent_email",
                from_ref=addr["email_address"],
                created_by=str(user.get("username") or "agent"))
            return _text(f"Email SENT to {', '.join(to)} from "
                         f"{addr['email_address']} (auto-send is enabled for "
                         "this address; an FYI audit item was added to "
                         "My Work)."
                         + (f" The View '{view_ref['name']}' was embedded as a "
                            f"dashboard: {view_status}. You did not see its "
                            "numbers, so do not describe them — and if any tile "
                            "did not refresh, say that plainly rather than "
                            "calling the figures current."
                            if view_ref else ""))
        logger_note = str(result.get("error", result))[:200]
        # fall through to the approval path so the message isn't lost
        fallback_note = (f"Auto-send FAILED ({logger_note}) — filed for "
                         "manual approval instead. ")
    else:
        fallback_note = ""

    item = workitem_store.create_item(
        "edit_and_return", f"Send: {subject or '(no subject)'}",
        summary=(f"To: {', '.join(to)}\nFrom: {addr['email_address']}\n"
                 + (f"Context: {str(args.get('context'))[:400]}\n" if args.get('context') else "")
                 + "\nEdit the body if needed — what you approve is what sends."
                 + ("\nFormatting (headings, lists, tables) is applied to the "
                    "text you approve; the plain text is sent alongside it."
                    if rich else "")
                 + (f"\nThe View '{view_ref['name']}' is embedded below your "
                    "text and is REFRESHED when you approve, so the email "
                    "carries current numbers — not these."
                    if view_ref else "")),
        payload={"kind": "agent_email_reply", "to": to, "subject": subject,
                 "body": body, "from_address": addr["email_address"],
                 "from_user": uid, "rich": rich, "view": view_ref,
                 "context": str(args.get("context") or "")[:500]},
        addressed_user=uid,
        from_kind="agent_email", from_ref=addr["email_address"],
        created_by=str(user.get("username") or "agent"))
    return _text(f"{fallback_note}Draft filed for approval (work item "
                 f"{item['work_item_id']}). It is in My Work now; NOTHING has "
                 "been sent — the user approves (and may edit) the body first."
                 + (f" The View '{view_ref['name']}' will be refreshed and "
                    "embedded when they approve."
                    if view_ref else ""))


@tool(
    "setup_agent_email",
    "Create (or re-activate) the current user's personal agent email address "
    "— WITH THEIR PERMISSION. TWO-STEP: first call WITHOUT confirmed to get "
    "the proposed address; present it, tell them they can pick a different "
    "prefix, and only after they explicitly agree call again with "
    "confirmed=true (and their chosen prefix if they gave one). The address "
    "becomes <prefix>-agent.<tenant>@<domain>; sending stays approval-gated "
    "by default and all options live on the Email screen.",
    {
        "type": "object",
        "properties": {
            "prefix": {"type": "string",
                       "description": "Optional prefix; defaults to their "
                                      "username (sanitized)"},
            "confirmed": {"type": "boolean"},
        },
        "required": [],
        "additionalProperties": False,
    },
)
async def setup_agent_email(args: dict[str, Any]) -> dict[str, Any]:
    import email_store
    import email_client
    user = CURRENT_USER.get()
    uid = int(user.get("user_id") or 0)
    existing = email_store.get_address(uid)
    if existing and existing.get("is_active") and not args.get("prefix"):
        return _text(f"Already set up: {existing['email_address']} is ACTIVE — "
                     "nothing to create. Settings live on the Email screen.")
    info = await email_client.tenant_info()
    if not info:
        return _text("The cloud email service is unreachable, so the address "
                     "suffix can't be resolved — nothing was created. Try "
                     "again shortly.", is_error=True)
    prefix = email_store.sanitize_prefix(
        args.get("prefix")
        or (existing or {}).get("prefix")
        or email_store.sanitize_prefix(user.get("username"))
        or str(uid))
    if not prefix:
        return _text("That prefix has no email-safe characters (a-z, 0-9, "
                     "hyphen) — pick another.", is_error=True)
    address = email_client.compose_address(prefix, info["tenant_id"],
                                           info["domain"])
    if not args.get("confirmed"):
        return _text(f"PROPOSAL (nothing created yet): their agent address "
                     f"would be {address}. Ask the user to confirm — and tell "
                     "them they can choose a different prefix (letters, "
                     "numbers, hyphens). Only call again with confirmed=true "
                     "after they explicitly agree.")
    try:
        row = email_store.upsert_address(
            uid, prefix, address, str(user.get("username") or ""),
            int(user.get("role") or 2), True)
    except ValueError as e:
        return _text(f"Not created: {e} — suggest a different prefix.",
                     is_error=True)
    return _text(f"Done — {row['email_address']} is ACTIVE. Mail sent there "
                 "reaches me as a session run as them, results land in "
                 "My Work, and any replies I draft wait for their approval "
                 "(auto-send and other options are on the Email screen).")


@tool(
    "get_agent_email_status",
    "Your INBOX VIEW for the current user: their agent address (or that none "
    "exists yet), settings, poller state, and recent inbound activity "
    "(sender/subject/outcome). Questions like 'did you get any email?' are "
    "answered from THIS — call it and report the activity directly, without "
    "capability disclaimers. Use it whenever a user asks about receiving, "
    "getting, or handling email.",
    {},
)
async def get_agent_email_status(args: dict[str, Any]) -> dict[str, Any]:
    import email_store
    import email_poller
    import email_client
    user = CURRENT_USER.get()
    uid = int(user.get("user_id") or 0)
    row = email_store.get_address(uid)
    info = await email_client.tenant_info()
    suffix = (f"-agent.{info['tenant_id']}@{info['domain']}" if info
              else "(cloud email service unreachable)")
    lines = []
    if row:
        state = "ENABLED" if row.get("is_active") else "DISABLED"
        lines.append(f"Address: {row['email_address']} ({state})")
        lines.append(
            "Settings: outbound "
            + ("ON" if row.get("outbound_enabled", 1) else "OFF")
            + ", auto-send " + ("ON (replies send immediately)"
                                if row.get("auto_send")
                                else "OFF (replies wait for approval)")
            + (", notify-on-receive → " + row["notification_email"]
               if row.get("notify_on_receive") and row.get("notification_email")
               else "")
            + (f", cooldown {row['cooldown_minutes']}m"
               if row.get("cooldown_minutes") is not None else "")
            + ("; standing reply instructions are set"
               if str(row.get("reply_instructions") or "").strip() else ""))
        recent = email_store.recent(row["email_address"], 5)
        if recent:
            lines.append(f"Recent inbound activity ({len(recent)} shown):")
            for e in recent:
                lines.append(f"  - {e['processed_at'][:16]} [{e['outcome']}] "
                             f"from {e.get('sender') or '?'}: "
                             f"{(e.get('subject') or '(no subject)')[:60]}")
        else:
            lines.append("No inbound mail processed yet.")
    else:
        default = email_store.sanitize_prefix(user.get("username")) or str(uid)
        lines.append("No agent email address set up yet for this user. OFFER "
                     "TO SET IT UP: with their permission you can create it "
                     f"yourself via setup_agent_email — suggest '{default}"
                     f"{suffix}' and tell them they may pick a different "
                     "prefix. (The Email screen is the manual alternative.)")
    lines.append(f"Inbound poller: {'RUNNING (every ' + str(email_poller.POLL_SECONDS) + 's)' if email_poller.enabled() else 'OFF (AGENT_EMAIL_ENABLED=false — an admin must enable it)'}")
    lines.append("How it works: mail sent to the address becomes a headless "
                 "agent session run as this user; results land in My Work, "
                 "and replies the agent drafts always wait for their approval.")
    return _text("\n".join(lines))


WORK_TOOLS = [raise_work_item, list_my_work, schedule_agent_task,
              save_skill, list_skills_tool, draft_email_reply,
              get_agent_email_status, setup_agent_email]
