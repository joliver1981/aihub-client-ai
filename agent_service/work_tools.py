"""
The Agent's My Work tools — raise and inspect work items (A2).

raise_work_item is the seam that lets the assistant route work to humans: a
question it needs answered, something to review, a draft to edit, an FYI. In
interactive chat the human is right there — so the tool is mostly for things
addressed to OTHER people or for work that should outlive the conversation;
headless runs (A3) will lean on it heavily.
"""

import json
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


WORK_TOOLS = [raise_work_item, list_my_work]
