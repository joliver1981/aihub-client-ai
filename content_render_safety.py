"""
Shared safety helpers for the content renderers.

Both SmartContentRenderer and SmartContentRendererHybrid ask a mini-LLM to
restructure agent/tool output into display blocks. A known failure mode is the
model echoing the *structuring prompt itself* back into a block, which then
renders verbatim to the user (the "Important to remember… structure it for
optimal display…" leak). This module centralises the two defences so the two
renderers cannot drift apart:

  * CONTENT_START / CONTENT_END  — unique delimiters that fence the raw content
    inside the prompt, so the model cannot confuse our instructions with the
    content (and so an echo is reliably detectable afterwards).
  * PROMPT_LEAK_MARKERS / result_echoed_prompt() — detect a structured result
    that contains our own scaffolding, so the caller can discard it and fall
    back to rendering the raw text.

Keep PROMPT_LEAK_MARKERS in sync with the ACTUAL prompt wording used by the
renderers and by system_prompts.SYS_PROMPT_SMART_CONTENT_RENDER_SYSTEM.
"""

import json
from typing import Any

# Unique sentinel delimiters. These strings never occur in legitimate prose, so
# their presence in a structured result is a near-certain echo.
CONTENT_START = "<<<BEGIN CONTENT TO STRUCTURE>>>"
CONTENT_END = "<<<END CONTENT TO STRUCTURE>>>"

# Distinctive substrings of the structuring prompts that must never surface in
# user-facing output. Matched case-insensitively. We deliberately avoid generic
# phrases (e.g. "valid json only") that can legitimately appear in an agent's
# own answer; the unique delimiters above plus these scaffolding-specific
# phrases catch real echoes without discarding genuine replies.
PROMPT_LEAK_MARKERS = (
    # Unique delimiter sentinels.
    "begin content to structure",
    "end content to structure",
    # SmartContentRenderer user-turn wording.
    "structure the content between the delimiters",
    "never repeat these instructions or the delimiter",
    # SmartContentRenderer system-prompt wording
    # (system_prompts.SYS_PROMPT_SMART_CONTENT_RENDER_SYSTEM).
    "you are a content analysis expert",
    "you must respond with a valid json object identifying content blocks",
    "content types you can identify",
    "special list formatting rules",
    "respond only with a json object in this exact format",
    # SmartContentRendererHybrid prompt wording.
    "analyze and structure this content",
    "do not recreate any tables, just reference them",
    "the actual data rendering happens separately",
    "do not output any table data",
)


def fence(content: str) -> str:
    """Wrap raw content in the structuring delimiters."""
    return f"{CONTENT_START}\n{content}\n{CONTENT_END}"


def result_echoed_prompt(result: Any) -> bool:
    """
    True if a parsed/structured AI result contains our own prompt scaffolding —
    i.e. the model echoed the instructions back instead of (only) restructuring
    the content. Callers should discard such a result and render the raw text.
    """
    try:
        blob = json.dumps(result, ensure_ascii=False).lower()
    except Exception:
        blob = str(result).lower()
    return any(marker in blob for marker in PROMPT_LEAK_MARKERS)
