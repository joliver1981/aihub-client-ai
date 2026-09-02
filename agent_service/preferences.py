"""
Per-user standing preferences (James, 2026-09-02).

The SDK resumes a conversation's transcript, so The Agent remembers everything
WITHIN a conversation. ACROSS conversations the only memory was Skills, which
load on demand when their description matches a request — the wrong shape for
a standing preference ("always use Eastern time", "call me Jim"), which must
be present in every turn whether or not anything "triggers" it.

Design: one user-scope skill named `my-preferences` (so it shows in the Skills
rail, is exportable, and rides the existing per-user tree —
data/agent/users/<uid>/skills/my-preferences/SKILL.md), PLUS the block below,
which main._turn_envelope stamps into EVERY turn next to the [Context: now …]
line — chat, scheduled runs and inbound-email sessions alike. No new store, no
coupling to Command Center's user-memory table.

Items are short bullet lines; the whole block is capped so it stays a footnote
in context, not a document.
"""

import re
from typing import Optional

from agent_config import logger

SKILL_NAME = "my-preferences"
MAX_ITEMS = 40
MAX_ITEM_CHARS = 300
MAX_BLOCK_CHARS = 2500

_DESCRIPTION = ("This user's standing preferences and personal defaults — injected "
                "into every conversation turn automatically (remember_preference / "
                "forget_preference maintain it).")


def _norm(text: str) -> str:
    return " ".join(str(text or "").split())


def parse_items(skill_text: str) -> list:
    """Bullet lines ('- …') from the skill body (frontmatter ignored)."""
    body = skill_text or ""
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            body = body[end + 4:]
    items = []
    for line in body.splitlines():
        m = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        if m:
            items.append(_norm(m.group(1)))
    return items


def render_skill(items: list) -> str:
    lines = ["# My preferences", "",
             "Standing preferences saved from conversation. Each line is honored",
             "in every session without being asked.", ""]
    lines += [f"- {it}" for it in items] or ["(none yet)"]
    return "\n".join(lines) + "\n"


def get(uid: int) -> list:
    import skills_mount
    try:
        return parse_items(skills_mount.read_skill("user", SKILL_NAME, user_id=int(uid)))
    except Exception as e:
        logger.warning(f"preferences read failed for user {uid}: {e}")
        return []


def save(uid: int, items: list) -> None:
    import skills_mount
    if not items:
        skills_mount.delete_skill("user", SKILL_NAME, user_id=int(uid))
        return
    skills_mount.write_skill("user", SKILL_NAME, _DESCRIPTION, render_skill(items),
                             user_id=int(uid))


def remember(uid: int, text: str) -> tuple:
    """Add one preference. Returns (items, added, err)."""
    t = _norm(text)
    if not t:
        return get(uid), False, "The preference is empty."
    if len(t) > MAX_ITEM_CHARS:
        return get(uid), False, (f"Keep a preference under {MAX_ITEM_CHARS} characters "
                                 "(store procedures as a skill instead).")
    items = get(uid)
    if any(it.lower() == t.lower() for it in items):
        return items, False, None
    if len(items) >= MAX_ITEMS:
        return items, False, (f"The preference list is full ({MAX_ITEMS}) — forget one "
                              "first.")
    items.append(t)
    save(uid, items)
    return items, True, None


def forget(uid: int, text: str, clear_all: bool = False) -> tuple:
    """Remove matching preference(s). Returns (items, removed, err). A match is
    an exact line or a UNIQUE case-insensitive substring — never a guess."""
    items = get(uid)
    if clear_all:
        save(uid, [])
        return [], items, None
    t = _norm(text).lower()
    if not t:
        return items, [], "Say which preference to forget."
    exact = [it for it in items if it.lower() == t]
    hits = exact or [it for it in items if t in it.lower()]
    if not hits:
        return items, [], f"No saved preference matches '{_norm(text)}'."
    if len(hits) > 1:
        return items, [], ("More than one preference matches — be specific: "
                           + " | ".join(hits))
    remaining = [it for it in items if it != hits[0]]
    save(uid, remaining)
    return remaining, hits, None


def envelope_block(uid: Optional[int]) -> str:
    """The per-turn context block ('' when the user has no preferences)."""
    if not uid:
        return ""
    items = get(int(uid))
    if not items:
        return ""
    lines = ["[Standing preferences this user saved — honor them without being "
             "asked (remember_preference / forget_preference change them):"]
    total = len(lines[0])
    for it in items:
        line = f"- {it}"
        if total + len(line) > MAX_BLOCK_CHARS:
            lines.append("- … (more preferences not shown; the list is capped)")
            break
        lines.append(line)
        total += len(line)
    lines.append("]")
    return "\n" + "\n".join(lines)
