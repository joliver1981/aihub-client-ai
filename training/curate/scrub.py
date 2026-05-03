"""Scrub secrets and PII from training records.

Triggered by inspection of workflows/Daily Stripe Payment Summary.json, which
contained a live Stripe test API key embedded in an AI Action prompt. Even
test-mode keys should not be baked into a training corpus we may publish,
share, or ship as a base for a product feature.

Strategy:
  - Regex-match known secret formats (Stripe keys, AWS keys, GitHub tokens,
    generic bearer tokens, long base64-ish blobs in credential positions).
  - Replace with stable placeholder tokens (<STRIPE_KEY>, <AWS_KEY>, etc.)
    so the model learns the *pattern* (agent uses an API key) without the
    actual secret.
  - Replace real-looking emails with test@example.com unless they match an
    allowlist of intentionally-public emails (e.g. ap@somecompany.com in
    docstring examples).
  - Leave file paths and domain names alone by default — they are often
    workflow-relevant (e.g. \\\\server\\invoices\\incoming).

Scrubbing is applied to BOTH user plan and assistant commands.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

# Ordered list: more-specific patterns first so generic catch-alls don't
# preempt them.
_SCRUB_PATTERNS: List[Tuple[str, re.Pattern, str]] = [
    # Note: no leading \b — embedded in serialized JSON, keys are often preceded
    # by `\n` (literal backslash-n), making the `n` a word char and killing the
    # boundary. The prefix shapes are specific enough to avoid false positives.
    ("STRIPE_KEY",    re.compile(r"(?:sk|rk|pk)_(?:test|live)_[A-Za-z0-9]{24,}"), "<STRIPE_KEY>"),
    ("GITHUB_TOKEN",  re.compile(r"ghp_[A-Za-z0-9]{30,}"),                        "<GITHUB_TOKEN>"),
    ("GITHUB_PAT",    re.compile(r"github_pat_[A-Za-z0-9_]{40,}"),                "<GITHUB_TOKEN>"),
    ("AWS_ACCESS",    re.compile(r"AKIA[0-9A-Z]{16}"),                            "<AWS_ACCESS_KEY>"),
    ("AWS_SECRET",    re.compile(r"(?i)aws[_ ]?secret[_ ]?(?:access[_ ]?)?key[\"'=:\s]+[A-Za-z0-9/+=]{35,}"), "aws_secret_key=<AWS_SECRET>"),
    # ANTHROPIC before OPENAI: "sk-ant-..." matches both patterns; whichever
    # fires first wins, and we want the more-specific label.
    ("ANTHROPIC_KEY", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),                     "<ANTHROPIC_KEY>"),
    ("OPENAI_KEY",    re.compile(r"sk-[A-Za-z0-9]{20,}"),                            "<OPENAI_KEY>"),
    ("AZURE_KEY",     re.compile(r"(?i)(?:api[_\- ]?key|subscription[_\- ]?key)[\"'=:\s]+[A-Za-z0-9]{32,}"), "api_key=<AZURE_KEY>"),
    ("BEARER",        re.compile(r"\bBearer\s+[A-Za-z0-9\-_.=]{20,}\b"),              "Bearer <TOKEN>"),
    ("JWT",           re.compile(r"\beyJ[A-Za-z0-9_\-]+?\.[A-Za-z0-9_\-]+?\.[A-Za-z0-9_\-]+\b"), "<JWT>"),
    ("PRIVATE_KEY",   re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----"), "<PRIVATE_KEY_PEM>"),
    # Generic "password: foo" / "password=foo" patterns — common in test specs
    # and DB connection strings. Keep the key name visible, redact the value.
    ("PASSWORD_KV",   re.compile(r"(?i)(password|pwd|passwd)[\"'=:\s]+[A-Za-z0-9!@#$%^&*()_+\-]{6,}"), r"\1=<PASSWORD>"),
    ("CONNSTRING_PW", re.compile(r"(?i)(pwd|password)=([^;\s\"']+)"), r"\1=<PASSWORD>"),
]

# Emails: replace specific-looking real emails but keep obviously-test ones.
_EMAIL_RE = re.compile(r"\b[\w.+\-]+@[\w\-]+\.[\w.\-]+\b")
_KEEP_EMAIL_DOMAINS = {
    "example.com",
    "example.org",
    "test.com",
    "somecompany.com",
    "company.com",
}


def _scrub_email(match: re.Match) -> str:
    email = match.group(0)
    domain = email.rsplit("@", 1)[-1].lower()
    if domain in _KEEP_EMAIL_DOMAINS:
        return email
    # Preserve local-part semantic hint via a generic placeholder.
    return "user@example.com"


def scrub_text(text: str) -> Tuple[str, Dict[str, int]]:
    """Scrub secrets from a text blob.

    Returns (scrubbed_text, counts_per_pattern).
    """
    counts: Dict[str, int] = {}
    result = text
    for name, pat, repl in _SCRUB_PATTERNS:
        result, n = pat.subn(repl, result)
        if n:
            counts[name] = counts.get(name, 0) + n
    # Emails last so earlier patterns don't mask them.
    result, n_email = _EMAIL_RE.subn(_scrub_email, result)
    if n_email:
        counts["EMAIL"] = counts.get("EMAIL", 0) + n_email
    return result, counts


def scrub_record(record: dict) -> Tuple[dict, Dict[str, int]]:
    """Scrub every message in a normalized record."""
    total: Dict[str, int] = {}
    for msg in record.get("messages", []):
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        scrubbed, counts = scrub_text(content)
        msg["content"] = scrubbed
        for k, v in counts.items():
            total[k] = total.get(k, 0) + v
    if total:
        record.setdefault("_meta", {})["scrubbed"] = total
    return record, total
