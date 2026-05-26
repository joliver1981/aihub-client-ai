"""Conversational helpers for competency suites.

Real users don't bail when an agent asks for clarification — they
answer it. These helpers give the test suites the same intelligence:
detect a clarifying question in the agent's first reply and
automatically send a follow-up with the disambiguation hint, then
score the FINAL answer.

Designed to be reused by any agent-knowledge competency suite. The
caller supplies:

   - the http_session (requests.Session with X-API-Key header)
   - the agent_id
   - the original question
   - a `disambiguation_hint` string — what to say if the agent asks
     "which document do you mean?". Typically derived from the
     fixture's filename + a short human-friendly description.

The helper returns a `ChatTurn` dataclass with the full conversation
trace so the report can show whether a follow-up was needed and what
the agent said at each step.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import List, Optional


# Heuristic: does the agent's answer look like a clarifying question
# that we should follow up to instead of accepting as-is?
#
#   - "Which one ...?"               (most common)
#   - "Could you specify ...?"
#   - "I'm not sure which ..."
#   - "I have N <thing>s available ... which ...?"
#   - "It depends on which ..."
#
# Combined with: the answer ends with a '?' OR contains a question
# mark in the last 250 chars (helps avoid false-positives where the
# agent answered + asked a trailing clarifying question).
_CLARIFY_PATTERNS = [
    r"\bwhich\b.*\?",
    r"\bcould you (?:specify|clarify|tell me)\b",
    r"\bi['’]?m\s+not\s+sure\s+which\b",
    r"\bi\s+have\s+(?:multiple|several|three|two|\d+)\b.*\b(?:available|in (?:the )?(?:documents|knowledge))",
    r"\bit\s+depends\s+on\s+which\b",
    r"\bplease\s+specify\b",
    r"\bdid you mean\b.*\?",
    r"\bwhich\s+(?:document|invoice|file|report|one|of these)\b",
]
_CLARIFY_RE = re.compile("|".join(_CLARIFY_PATTERNS), re.IGNORECASE | re.DOTALL)


def looks_like_clarifying_question(answer: str) -> bool:
    """Detect whether the agent is asking the user to disambiguate which
    document/topic they meant.

    Two-stage detector — regex fast-path first, mini-LLM fallback for the
    cases regex misses:

    1. **Regex fast-path.** Scans the FIRST 600 chars AND the LAST 400
       chars against `_CLARIFY_PATTERNS`. Catches the common phrasings
       at both ends of the response. Deterministic, zero cost.
    2. **Mini-LLM fallback.** If regex misses, ask a small LLM to judge.
       Handles novel phrasings ("Pick one of the following…", "Are you
       asking about A or B?", etc.) that regex doesn't cover.

    The fast-path returns True immediately so most calls cost nothing
    extra; only the genuinely-novel phrasings pay the LLM round-trip.
    """
    if not answer:
        return False
    head = answer[:600]
    tail = answer[-400:]
    if _CLARIFY_RE.search(head) or _CLARIFY_RE.search(tail):
        return True

    # Regex missed — try the LLM fallback. Best-effort: if the LLM call
    # fails for any reason (proxy down, parse error, etc.) we fall back
    # to the regex answer (False here).
    try:
        from ._llm_grader import llm_is_clarifying
        verdict = llm_is_clarifying(answer)
    except Exception:
        verdict = None
    return bool(verdict) if verdict is True else False


@dataclass
class ChatTurn:
    """One full (possibly multi-turn) conversation about a single question."""
    question: str
    final_answer: str = ""
    final_chat_status: int = 0
    elapsed_s: float = 0.0
    # If a follow-up was sent, this is the hint we used.
    followup_used: Optional[str] = None
    # Full transcript: list of (role, content) tuples.
    transcript: List[tuple] = field(default_factory=list)
    # Raw agent chat_history from final API response (server-side authoritative)
    server_chat_history: List = field(default_factory=list)


def ask_with_followup(
    *,
    http_session,
    base_url: str,
    agent_id,
    question: str,
    disambiguation_hint: Optional[str] = None,
    timeout_s: int = 90,
    max_followups: int = 1,
) -> ChatTurn:
    """Send a question; if the agent asks for clarification AND a
    `disambiguation_hint` is provided, send the hint as a follow-up
    and use the second response as the final answer.

    Returns a ChatTurn with full transcript + the final answer.

    Args:
        http_session: requests.Session with auth header
        base_url: e.g., "http://localhost:5001"
        agent_id: int or str
        question: the original question text
        disambiguation_hint: text to send if the agent asks "which one?".
            Pass None to disable follow-up (suite behaves like the old
            one-shot test).
        max_followups: cap on follow-up turns (default 1 — the typical
            pattern is "agent asks once, user clarifies once, agent
            answers". Higher caps catch agents that ask twice.)
    """
    t0 = time.time()
    turn = ChatTurn(question=question)
    history = []

    def _send(prompt: str) -> dict:
        try:
            r = http_session.post(
                f"{base_url}/api/agents/{agent_id}/chat",
                json={"prompt": prompt, "history": history},
                headers={"Content-Type": "application/json"},
                timeout=timeout_s,
            )
            turn.final_chat_status = r.status_code
            if r.status_code != 200:
                return {"response": f"<status {r.status_code}>"}
            try:
                return r.json()
            except Exception:
                return {"response": r.text[:300]}
        except Exception as e:
            turn.final_chat_status = 0
            return {"response": f"err:{type(e).__name__}:{str(e)[:200]}"}

    # ── Turn 1: send the original question ──
    body = _send(question)
    answer = str(body.get("response") or body.get("answer") or "")
    turn.transcript.append(("user", question))
    turn.transcript.append(("agent", answer))
    server_history = body.get("chat_history", [])

    # ── Follow-up loop ──
    followups = 0
    while (
        disambiguation_hint
        and followups < max_followups
        and looks_like_clarifying_question(answer)
    ):
        history = server_history if isinstance(server_history, list) else []
        followups += 1
        turn.followup_used = disambiguation_hint
        body = _send(disambiguation_hint)
        answer = str(body.get("response") or body.get("answer") or "")
        turn.transcript.append(("user", disambiguation_hint))
        turn.transcript.append(("agent", answer))
        server_history = body.get("chat_history", [])

    turn.final_answer = answer
    turn.server_chat_history = (
        server_history if isinstance(server_history, list) else []
    )
    turn.elapsed_s = time.time() - t0
    return turn
