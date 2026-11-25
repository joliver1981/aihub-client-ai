"""
How much text one tool result of The Agent can carry: one place, for every tool.

The limit is not ours. The runtime the SDK drives replaces any text result longer
than 50,000 characters with a 2 KB preview and the path of a file the model has no
tool to open, so a longer result silently becomes a nearly empty one. A tool can
declare a larger size, up to the runtime's own maximum of 500,000 characters
(measured 2026-11-25 with SDK 0.2.132: 49,000 characters delivered whole, 51,000
replaced by the preview; 450,000 delivered whole with the declaration; the
maximum is a constant in the runtime).

So (James 2026-11-25, "the only caps that have ever hurt this application were
self imposed"):
  - every tool of the server declares that maximum (declare_whole_results), and
    the tools return their text WHOLE instead of previews cut to a size of ours;
  - a result that is larger than the runtime's maximum is never left to become
    the 2 KB preview: it is cut to what one result carries, the cut is marked
    where it is, and the result ends by saying how much is missing and what to do
    (fit_result). search_documents and read_file do better than that (whole
    passages, a file in parts) and manage their own size.
"""

import dataclasses
from typing import Any

from agent_config import logger

RUNTIME_DEFAULT_RESULT_CHARS = 50_000
RUNTIME_MAX_RESULT_CHARS = 500_000
RESULT_HEADROOM_CHARS = 5_000       # room for a tool's own header, notes and code fences
_TAIL_SHARE = 0.2                   # of an oversize result, how much is kept from its end


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


def fit_result(result: Any, tool_name: str = "") -> Any:
    """A tool's result, as it is when its text fits in one result. Otherwise the
    text is cut to what one result carries: its beginning and its end are kept
    (headers and first rows; totals, notes and error lines), the gap is marked
    where it is, and the result ends with what is missing and what to do. The
    alternative is the runtime's 2 KB preview, which says none of that."""
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
    others = [b for b in blocks if b not in texts]
    out = dict(result)
    out["content"] = [{"type": "text", "text": text}] + others
    return out


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
