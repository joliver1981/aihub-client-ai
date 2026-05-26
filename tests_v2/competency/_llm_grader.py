"""LLM-based scoring helpers for competency suites.

Regex patterns are unavoidably brittle: the agent may answer with extra
precision ("23.77%" when ground truth is ~23.8%), with markdown formatting
("**7** facilities" when ground truth is "7 facilities"), or with a
different comma-separator style ("$43,980,000" when the pattern expected
"43.98M"). Each of these is a CORRECT answer to a human reader but a
0/100 score under strict regex matching.

This module wraps `claudeQuickPrompt` (Haiku / ANTHROPIC_MINI) to do two
test-side judgments:

  1. `llm_grade_answer(...)` — given a question, the ground-truth value(s),
     and the agent's answer, decide whether the agent answered correctly.

  2. `llm_is_clarifying(...)` — given an agent response, decide whether
     it's asking the user to disambiguate which document/topic they meant.

Both use a fast-path-then-LLM-fallback strategy: callers should try regex
first (cheap, deterministic), and only invoke the LLM when regex fails.
This keeps test-run cost bounded and keeps results deterministic on the
easy cases.

The LLM judgments themselves are deterministic at temp=0 and structured
JSON output, so reruns of the same answer produce identical scores.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import List, Optional

# Make the project root importable so we can pull claudeQuickPrompt.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


_log = logging.getLogger("competency.llm_grader")


def _haiku_or_mini(prompt: str, system: str, max_tokens: int = 256,
                    temp: float = 0.0) -> Optional[str]:
    """Call claudeQuickPrompt with Haiku, fall back to ANTHROPIC_MINI.

    Returns the response text or None if both attempts fail. Never raises.
    """
    try:
        import config as cfg  # noqa: WPS433
        from claudeQuickPrompt import claudeQuickPrompt  # noqa: WPS433
    except Exception as e:
        _log.warning(f"claudeQuickPrompt unavailable in test env: {e}")
        return None

    haiku_model = getattr(cfg, 'KNOWLEDGE_HAIKU_MODEL', None)
    mini_model = getattr(cfg, 'ANTHROPIC_MINI', None)

    for model in (m for m in (haiku_model, mini_model) if m):
        try:
            resp = claudeQuickPrompt(prompt, system=system, temp=temp, model=model)
            if resp and resp.strip():
                return resp
        except Exception as e:
            _log.warning(f"LLM grader call failed on {model}: {e}")
            continue
    return None


def _strip_json_fences(s: str) -> str:
    """Drop ```json ... ``` fences if the LLM adds them."""
    s = s.strip()
    if s.startswith('```'):
        s = re.sub(r'^```(?:json)?\s*', '', s, flags=re.IGNORECASE)
        s = re.sub(r'\s*```\s*$', '', s)
    return s


def llm_grade_answer(
    question: str,
    agent_answer: str,
    expected_patterns: List[str],
    *,
    extra_context: Optional[str] = None,
) -> Optional[bool]:
    """Ask a mini LLM whether `agent_answer` correctly answers `question`.

    `expected_patterns` is the list of regex patterns the runner uses as
    fast-path matches. They're shown to the grader as a hint at what
    "correct" looks like (e.g., "we expect a number like 23.7% or 23.8%
    or $165,334.13"). The grader is told these are HINTS, not exact
    requirements — minor precision differences, formatting differences,
    and comma-separator differences should still count as correct.

    Returns:
        True  — agent answered correctly
        False — agent answered incorrectly or refused without good reason
        None  — LLM call failed (caller should treat as "unknown" rather
                than fail-closed; in practice we treat None as a fallback
                to the regex result the runner already computed).
    """
    if not agent_answer or not agent_answer.strip():
        return False

    patterns_hint = "\n".join(f"  - {p}" for p in (expected_patterns or []))
    if not patterns_hint.strip():
        patterns_hint = "  (no specific patterns supplied — grade on plausibility)"

    extra = f"\nADDITIONAL CONTEXT:\n{extra_context}\n" if extra_context else ""

    system = (
        "You are a strict but fair grader for an automated test suite. You "
        "decide whether an AI agent answered a question correctly. Treat "
        "the expected-value PATTERNS as hints at the right answer — small "
        "differences in precision, formatting, or units do NOT make an "
        "answer wrong if it conveys the same fact. Reply with strict JSON "
        "only — no markdown, no prose."
    )
    prompt = (
        f"QUESTION:\n{question}\n\n"
        f"EXPECTED VALUE PATTERNS (hints — not strict requirements):\n"
        f"{patterns_hint}\n"
        f"{extra}\n"
        f"AGENT'S ANSWER:\n{agent_answer[:4000]}\n\n"
        "Decide: did the agent answer the question correctly?\n"
        "- An answer is CORRECT if it conveys the same fact as the expected "
        "value, allowing for precision differences (e.g., 23.77% vs 23.8%), "
        "formatting differences (e.g., '$43,980,000' vs '$43.98 million' vs "
        "'**$43,980,000**'), and minor unit differences.\n"
        "- An answer is INCORRECT if it gives the wrong number, names the "
        "wrong document, refuses, asks a clarifying question instead of "
        "answering, or hallucinates a value not supported by any of the "
        "expected patterns.\n\n"
        "Reply with this exact JSON shape:\n"
        "{\n"
        '  "correct": true|false,\n'
        '  "confidence": "high" | "medium" | "low",\n'
        '  "reason": "<one short sentence>"\n'
        "}\n"
    )

    resp = _haiku_or_mini(prompt, system, max_tokens=256, temp=0.0)
    if not resp:
        return None

    try:
        data = json.loads(_strip_json_fences(resp))
    except Exception as e:
        _log.debug(f"LLM grader returned non-JSON: {e}; resp={resp[:200]}")
        return None

    if not isinstance(data.get('correct'), bool):
        return None

    return data['correct']


def llm_is_clarifying(answer: str) -> Optional[bool]:
    """Ask a mini LLM whether `answer` is the agent asking the user to
    clarify which document/topic they meant.

    Used as a fallback when the regex `looks_like_clarifying_question`
    misses a phrasing — e.g., the agent puts "Which invoice do you mean?"
    at the START with a helpful "I can also check all of them..." at the
    end, and the tail-only regex misses the lead-in.

    Returns True / False / None (LLM call failed).
    """
    if not answer or not answer.strip():
        return False

    system = (
        "You decide whether an AI agent's response is asking the user a "
        "clarifying question about WHICH document, file, or topic the "
        "user meant — as opposed to actually answering the user's "
        "original question. Reply with strict JSON only."
    )
    prompt = (
        f"AGENT RESPONSE:\n{answer[:3000]}\n\n"
        "Is this response asking the user to clarify which specific "
        "document/file/invoice/report they want, rather than answering "
        "the original question? Examples that COUNT as clarifying:\n"
        '  - "Which invoice do you mean? I have several available: ..."\n'
        '  - "Could you specify which report you\'re asking about?"\n'
        '  - "It depends on which document — I have ..."\n'
        "Examples that DO NOT count as clarifying (these are real answers):\n"
        '  - "The grand total is $165,334.13 from 04_continental.pdf."\n'
        '  - "I see two values; from doc A it\'s 5, from doc B it\'s 10."\n\n'
        "Reply with this exact JSON:\n"
        '{"clarifying": true|false, "reason": "<short sentence>"}'
    )

    resp = _haiku_or_mini(prompt, system, max_tokens=128, temp=0.0)
    if not resp:
        return None

    try:
        data = json.loads(_strip_json_fences(resp))
    except Exception as e:
        _log.debug(f"LLM clarifying-check returned non-JSON: {e}; resp={resp[:200]}")
        return None

    if not isinstance(data.get('clarifying'), bool):
        return None

    return data['clarifying']
