"""
Chat-upload admit-or-deny policy (2026-08-25).

A file uploaded DIRECTLY to an agent in chat (General Agent "Agent Files",
Command Center attachments) is either FULLY usable by that agent or DENIED at
upload with the real numbers — never accepted and then silently truncated.
See docs/chat-upload-admit-or-deny-plan.md.

Two ways a file is "fully usable":
  1. Full-content lane — the whole extracted text fits the per-file ceiling
     and the conversation's attachment budget.
  2. Structured-query lane — tabular files (.csv/.tsv/.xlsx/.xls) bypass the
     ceilings entirely: the tabular tools / code interpreter compute over the
     complete file on disk, so its size never needs to fit in context.

This module is pure decision logic + message wording, shared by app.py
(/add/agent_knowledge) and command_center_service/routes/upload.py so the two
surfaces can never drift. Tokens are estimated as chars/4.
"""

import os

import config as cfg

# Files served by the structured query lane (agent_excel_tools / CC run_python)
TABULAR_EXTENSIONS = ('.csv', '.tsv', '.xlsx', '.xls')


def estimate_tokens(chars: int) -> int:
    """chars/4 — the sizing rule used across the admit-or-deny policy."""
    return max(0, int(chars)) // 4


def per_file_limit_tokens() -> int:
    return int(getattr(cfg, 'CHAT_UPLOAD_MAX_TOKENS_PER_FILE', 300_000))


def conversation_budget_tokens() -> int:
    return int(getattr(cfg, 'CHAT_CONVERSATION_ATTACHMENT_BUDGET_TOKENS', 600_000))


def soft_warn_tokens() -> int:
    return int(getattr(cfg, 'CHAT_UPLOAD_TOKENS_SOFT_WARN', 150_000))


def is_tabular_filename(filename: str) -> bool:
    return bool(filename) and filename.lower().endswith(TABULAR_EXTENSIONS)


def check_admission(filename: str, extracted_chars: int,
                    existing_conversation_chars: int = 0) -> dict:
    """
    Decide whether a chat upload is admitted.

    Args:
        filename: original filename (drives the tabular bypass)
        extracted_chars: chars of extracted text for THIS file
        existing_conversation_chars: chars already admitted to this
            conversation/agent-file set (non-tabular files only)

    Returns dict:
        admit: bool
        reason: 'ok' | 'tabular_lane' | 'per_file' | 'budget'
        message: user-facing denial text (only when admit=False)
        warning: user-facing large-file notice (only past the soft-warn line)
        file_tokens / existing_tokens / limit_tokens / budget_tokens: numbers
    """
    file_tokens = estimate_tokens(extracted_chars)
    existing_tokens = estimate_tokens(existing_conversation_chars)
    limit_tokens = per_file_limit_tokens()
    budget_tokens = conversation_budget_tokens()

    result = {
        "admit": True, "reason": "ok", "message": None, "warning": None,
        "file_tokens": file_tokens, "existing_tokens": existing_tokens,
        "limit_tokens": limit_tokens, "budget_tokens": budget_tokens,
    }

    # Structured-lane bypass: the tools compute over the full file on disk, so
    # context ceilings don't apply. (Their bounded previews/metadata are what
    # actually reach the model.)
    if is_tabular_filename(filename):
        result["reason"] = "tabular_lane"
        return result

    if file_tokens > limit_tokens:
        result.update({
            "admit": False, "reason": "per_file",
            "message": (
                f"'{filename}' was NOT added: it extracts to about "
                f"{file_tokens:,} tokens of text, and the chat-upload limit is "
                f"{limit_tokens:,} tokens per file. Nothing was stored. "
                f"Options: import it to the document repository instead "
                f"(searchable, no size limit), or split it into smaller parts. "
                f"(Spreadsheet/CSV files are exempt — they are queried with "
                f"data tools without loading them into chat.)"
            ),
        })
        return result

    if existing_tokens + file_tokens > budget_tokens:
        result.update({
            "admit": False, "reason": "budget",
            "message": (
                f"'{filename}' was NOT added: this conversation's attached "
                f"files already total about {existing_tokens:,} tokens, and "
                f"adding this file (~{file_tokens:,} tokens) would exceed the "
                f"{budget_tokens:,}-token attachment budget. Nothing was "
                f"stored. Options: remove an attached file you no longer "
                f"need, start a new conversation for this file, or import it "
                f"to the document repository (searchable, no size limit)."
            ),
        })
        return result

    if file_tokens > soft_warn_tokens():
        result["warning"] = (
            f"'{filename}' is large (~{file_tokens:,} tokens). It was added "
            f"in full, but answers over it may be slower; for many-file work "
            f"the document repository is the better home."
        )
    return result
