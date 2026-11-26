"""
How much text one tool result of The Agent can carry: one place, for every tool.

The limit is not ours. The runtime the SDK drives replaces any text result longer
than 50,000 characters with a 2 KB preview and the path of a file the model has no
tool to open, so a longer result silently becomes a nearly empty one. A tool can
declare a larger size, up to a HARD CEILING of 500,000 characters: that is the
runtime vendor's documented maximum ("Raise the limit for a specific tool" in its
MCP documentation), a constant in the runtime (the same in its August and its
November 2026 builds), and no setting raises it. Measured 2026-11-25 with SDK
0.2.132: 49,000 characters delivered whole, 51,000 replaced by the preview;
450,000 delivered whole with the declaration.

So (James 2026-11-25, "the only caps that have ever hurt this application were
self imposed"):
  - every tool of the server declares that maximum (declare_whole_results), and
    the tools return their text WHOLE instead of previews cut to a size of ours;
  - a result that is longer than one result carries is handed over in PARTS,
    with nothing left out: the first part comes back from the tool itself, the
    following ones from read_more. What bounds a result then is the model's
    context window, not a number of characters. search_documents and read_file
    manage their own size the same way (whole passages, a file in parts).
"""

import dataclasses
import time
import uuid
from typing import Any

from claude_agent_sdk import tool

from agent_config import logger

RUNTIME_DEFAULT_RESULT_CHARS = 50_000
RUNTIME_MAX_RESULT_CHARS = 500_000
RESULT_HEADROOM_CHARS = 5_000       # room for a tool's own header, notes and code fences
_TAIL_SHARE = 0.2                   # of a result that cannot be kept in parts, how much is kept from its end


def result_ceiling():
    """(characters one result may carry, the annotation that declares it).

    The larger size only exists when the installed SDK forwards the declaration to
    the runtime. Checked from the SDK itself, not assumed: with an SDK that does
    not, declaring 500,000 would change nothing and every long result would come
    back as the 2 KB preview. Then the ceiling stays at the runtime's default."""
    try:
        import inspect
        import claude_agent_sdk
        from mcp.types import ToolAnnotations
        if "maxResultSizeChars" in inspect.getsource(claude_agent_sdk.create_sdk_mcp_server):
            return (RUNTIME_MAX_RESULT_CHARS,
                    ToolAnnotations(maxResultSizeChars=RUNTIME_MAX_RESULT_CHARS))
    except Exception as e:
        logger.warning(f"tool results stay at the runtime's default size "
                       f"({RUNTIME_DEFAULT_RESULT_CHARS:,} characters): {e}")
    return RUNTIME_DEFAULT_RESULT_CHARS, None


RESULT_MAX_CHARS, WHOLE_RESULT = result_ceiling()


def result_budget() -> int:
    """Characters of text one result has room for."""
    return RESULT_MAX_CHARS - RESULT_HEADROOM_CHARS


CHARS_PER_TOKEN = 2.6               # measured on real documents (manual pages: 2.55 to 2.72)


def context_window_chars() -> int:
    """Characters of text the context window of the model this turn runs on holds
    at all. Since results come in parts, this is what bounds a text a tool hands
    over: a longer one cannot be read within one conversation, in however many
    parts, so it has to be worked on as a file instead."""
    try:
        import agent_config
        from platform_tools import CURRENT_USER
        role = (CURRENT_USER.get() or {}).get("role")
        tokens = int(agent_config.context_window_tokens(agent_config.get_effective_model(role)))
    except Exception as e:
        logger.warning(f"context window unknown, taking the runtime's default: {e}")
        tokens = 200_000
    return int(tokens * CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# A result in parts
# ---------------------------------------------------------------------------
# The parts of a long result are kept for a short while, for the person and the
# conversation that got the first one, and read_more hands them out.
_KEPT_RESULT_SECONDS = 900.0
# Memory this service spends on kept results, not a limit on any result: the
# oldest go first, and a result that is no longer kept is simply produced again.
_KEPT_RESULT_TOTAL_CHARS = 60_000_000
_kept_results: dict = {}           # result id -> {"when", "owner", "tool", "parts", "chars"}


def _owner():
    """(user id, conversation) of the turn that is running, or None for the
    service itself: there is nobody to hand further parts to."""
    try:
        from platform_tools import CURRENT_USER
        user = CURRENT_USER.get() or {}
    except Exception:
        return None
    uid = user.get("user_id")
    if not uid:
        return None
    return (str(uid), str(user.get("session_id") or ""))


def _split_parts(text: str, size: int) -> list:
    """The text in pieces of at most ``size`` characters, cut at the end of a
    line where one ends within the last stretch of the piece."""
    parts, pos, n = [], 0, len(text)
    while pos < n:
        end = min(pos + size, n)
        if end < n:
            nl = text.rfind("\n", max(pos, end - 4_000), end)
            if nl > pos:
                end = nl + 1
        parts.append(text[pos:end])
        pos = end
    return parts


def _keep(owner, tool_name: str, parts: list) -> str:
    now = time.monotonic()
    for rid in [r for r, v in _kept_results.items() if now - v["when"] > _KEPT_RESULT_SECONDS]:
        _kept_results.pop(rid, None)
    rid = uuid.uuid4().hex[:10]
    _kept_results[rid] = {"when": now, "owner": owner, "tool": tool_name, "parts": parts,
                          "chars": sum(len(p) for p in parts)}
    total = sum(v["chars"] for v in _kept_results.values())
    for old in list(_kept_results):                 # the dict keeps the order of arrival
        if total <= _KEPT_RESULT_TOTAL_CHARS:
            break
        if old != rid:
            total -= _kept_results.pop(old)["chars"]
    return rid


def _part_note(rid: str, part: int, parts: int, total: int, tool_name: str) -> str:
    if part >= parts:
        return (f"\n\n[PART {part} OF {parts}, the last one, of the result of "
                f"{tool_name or 'the tool'} ({total:,} characters in all). You now have all of it.]")
    return (f"\n\n[PART {part} OF {parts}: this result of {tool_name or 'the tool'} is {total:,} "
            f"characters long and one tool result carries {RESULT_MAX_CHARS:,}. Nothing is left "
            f"out, but this is not all of it yet: read the next part with read_more "
            f"(result_id=\"{rid}\", part={part + 1}) and go on to part {parts} before you "
            f"treat the result as complete.]")


def fit_result(result: Any, tool_name: str = "") -> Any:
    """A tool's result, as it is when its text fits in one result. A longer one is
    handed over in parts with nothing left out: this call returns the first part,
    which ends by saying how to read the next (read_more).

    Only when there is nobody to hand further parts to (a call outside a user's
    turn) the text is cut instead: its beginning and its end are kept, the gap is
    marked where it is, and the result ends with what is missing. Either way the
    runtime's 2 KB preview, which says none of that, never happens."""
    if not isinstance(result, dict) or not isinstance(result.get("content"), list):
        return result
    blocks = result["content"]
    texts = [b for b in blocks if isinstance(b, dict) and b.get("type") == "text"
             and isinstance(b.get("text"), str)]
    total = sum(len(b["text"]) for b in texts)
    budget = result_budget()
    if total <= budget:
        return result
    whole = "\n".join(b["text"] for b in texts)
    total = len(whole)
    others = [b for b in blocks if b not in texts]
    out = dict(result)

    owner = _owner()
    if owner is not None:
        parts = _split_parts(whole, budget - 1_000)             # room for the note under each part
        rid = _keep(owner, tool_name, parts)
        logger.info(f"tool result in parts: {tool_name or '(tool)'} returned {total:,} characters, "
                    f"handed over in {len(parts)} parts (result {rid})")
        out["content"] = [{"type": "text",
                           "text": parts[0] + _part_note(rid, 1, len(parts), total, tool_name)}] + others
        return out

    note = (f"\n\n[NOT ALL SHOWN: this result is {total:,} characters long and one tool "
            f"result carries {RESULT_MAX_CHARS:,}. Its beginning and its end are above; "
            f"{{left:,}} characters in between were left out, at the place marked. Do not "
            f"treat what is above as complete. Ask for less at a time (a narrower "
            f"question, a filter, fewer rows or columns), or have the data written to a "
            f"file and work on the file.]")
    marker = "\n\n[... {left:,} characters left out here ...]\n\n"
    room = max(0, budget - len(note) - len(marker) - 40)
    tail_chars = int(room * _TAIL_SHARE)
    head_chars = room - tail_chars
    left = total - head_chars - tail_chars
    text = (whole[:head_chars] + marker.format(left=left)
            + (whole[total - tail_chars:] if tail_chars else "") + note.format(left=left))
    logger.warning(f"tool result larger than one result carries: {tool_name or '(tool)'} returned "
                   f"{total:,} characters, {left:,} left out (marked in the result)")
    out["content"] = [{"type": "text", "text": text}] + others
    return out


def _text(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "is_error": is_error}


@tool(
    "read_more",
    "Read the next part of a tool result that was too long for one result. Call it "
    "only when a result ended with a note that names a result_id and a part number: "
    "the note says which part to read next. Read the parts in order to the last one "
    "before you treat that result as complete.",
    {
        "type": "object",
        "properties": {
            "result_id": {"type": "string", "description": "The result_id named in the note"},
            "part": {"type": "integer", "description": "The part to read, as the note says"},
        },
        "required": ["result_id", "part"],
        "additionalProperties": False,
    },
    annotations=WHOLE_RESULT,
)
async def read_more(args: dict) -> dict:
    rid = str(args.get("result_id") or "").strip()
    try:
        part = int(args.get("part") or 0)
    except (TypeError, ValueError):
        part = 0
    kept = _kept_results.get(rid)
    owner = _owner()
    gone = (f"There is no kept result with the id \"{rid}\" for this conversation. The parts of a "
            f"long result are kept for {int(_KEPT_RESULT_SECONDS // 60)} minutes: call the tool "
            f"that produced it again and read its parts in one go.")
    # another person's or another conversation's result is answered exactly like one that is gone
    if (not kept or owner is None or kept["owner"] != owner
            or time.monotonic() - kept["when"] > _KEPT_RESULT_SECONDS):
        return _text(gone, is_error=True)
    parts = kept["parts"]
    if part < 1 or part > len(parts):
        return _text(f"That result has {len(parts)} parts; there is no part {part}. "
                     f"Parts are numbered from 1.", is_error=True)
    kept["when"] = time.monotonic()                 # still being read: keep it for the next part
    return _text(parts[part - 1] + _part_note(rid, part, len(parts), kept["chars"], kept["tool"]))


RESULT_TOOLS = [read_more]


def _guarded(handler, name: str):
    async def call(args):
        return fit_result(await handler(args), name)
    call.__name__ = getattr(handler, "__name__", name)
    call.__doc__ = getattr(handler, "__doc__", None)
    return call


def declare_whole_results(tools: list) -> list:
    """The tools of a server, each declaring the runtime's maximum result size and
    guarded by fit_result. A tool that already declares a size manages its own
    results and is passed through untouched. The module-level tool objects are not
    changed: these are copies for the server."""
    if WHOLE_RESULT is None:
        return list(tools)             # an SDK that cannot declare it: nothing to gain
    out = []
    for t in tools:
        existing = getattr(t, "annotations", None)
        if getattr(existing, "maxResultSizeChars", None):
            out.append(t)
            continue
        try:
            annotations = (existing.model_copy(update={"maxResultSizeChars": RESULT_MAX_CHARS})
                           if existing is not None else WHOLE_RESULT)
            out.append(dataclasses.replace(t, annotations=annotations,
                                           handler=_guarded(t.handler, t.name)))
        except Exception as e:          # never lose a tool over this
            logger.warning(f"tool '{getattr(t, 'name', '?')}' keeps the runtime's default result "
                           f"size: {e}")
            out.append(t)
    return out
