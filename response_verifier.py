"""
response_verifier.py — deterministic post-generation verification (arithmetic).

The model PROPOSES; cheap deterministic checks DISPOSE. This is the first brick
of a general "grounding" layer: each Validator targets a class of claim that has
ONE correct answer derivable WITHOUT the model. Arithmetic is implemented here.

Wired into GeneralAgent.run() behind two flags:
  * RESPONSE_VERIFY_ENABLED (default true)  — run + log (the "gap map").
  * RESPONSE_VERIFY_ENFORCE (default false) — SHADOW (log only) vs ENFORCE (one
    corrective re-invoke when the model's shown arithmetic is wrong).

Design rules:
  * Pure-logic core (find_arithmetic_claims / ResponseVerifier) has NO I/O and is
    unit-tested standalone.
  * Logging is lazy + fail-open — it must NEVER break a response.
  * The model's stated result is never trusted; we recompute it ourselves.
  * Coverage: called from BOTH GeneralAgent.run() (smart-render / web UI) and
    run_text_only() (the API answer path) via the shared helper, so every
    general-agent answer path is logged.
  * Privacy: the gap-map log defaults to counts + per-finding ok flags + request_id
    only (no query text, no figures). RESPONSE_VERIFY_LOG_DETAIL=true adds the raw
    claim/query for debugging; the log is rotated (RESPONSE_VERIFY_LOG_MAX_BYTES /
    _BACKUP).

Run the demo:
  $PY = "$env:USERPROFILE\\miniconda3\\envs\\aihub2.1\\python.exe"
  & $PY response_verifier.py
"""

from __future__ import annotations

import ast
import json
import logging
import operator as _op
import os
import re
import time
from dataclasses import dataclass
from typing import List, Optional


# ----------------------------------------------------------------------------
# Safe arithmetic evaluator (AST whitelist — no eval())
# ----------------------------------------------------------------------------
_BINOPS = {ast.Add: _op.add, ast.Sub: _op.sub, ast.Mult: _op.mul, ast.Div: _op.truediv}
_UNARY = {ast.UAdd: _op.pos, ast.USub: _op.neg}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    raise ValueError("unsafe or non-arithmetic expression")


def safe_eval(expr: str) -> float:
    return _eval(ast.parse(expr, mode="eval"))


# ----------------------------------------------------------------------------
# Claim extraction
# ----------------------------------------------------------------------------
_NUM = r"\$?-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?"

# operators: * / + - × and a free-standing x/X (multiply)
_OP = r"(?:\s*[-+*/×]\s*|\s+[xX]\s+)"

# A = B   where A is an inline expression and B is the stated result
_EQ_RE = re.compile(rf"(?<![\w.,])({_NUM}(?:{_OP}{_NUM})+)\s*=\s*({_NUM})")
_PCT_OF_RE = re.compile(
    rf"(?<![\w.,])(\d{{1,3}}(?:,\d{{3}})*(?:\.\d+)?)\s*%\s*of\s*({_NUM})\s*=\s*({_NUM})",
    re.IGNORECASE,
)


def _to_num(tok: str) -> float:
    tok = tok.strip().replace("$", "").replace(",", "").replace(" ", "")
    if tok.endswith("%"):
        return float(tok[:-1]) / 100.0
    return float(tok)


def _normalize_expr(expr: str) -> str:
    e = expr.replace("×", "*").replace("x", "*").replace("X", "*")
    e = e.replace("$", "").replace(",", "").replace(" ", "")
    e = re.sub(r"(\d+(?:\.\d+)?)%", r"(\1/100)", e)
    return e


@dataclass
class Finding:
    kind: str
    claim: str
    expression: str
    expected: float
    stated: Optional[float]
    ok: bool


def _close(a: float, b: float) -> bool:
    # within two cents — figures are printed to the penny, so this tolerates
    # rounding without masking real discrepancies.
    return abs(a - b) <= 0.02


def find_arithmetic_claims(text: str) -> List[Finding]:
    if not text:
        return []
    findings = []

    # "A op B op C = D"
    for m in _EQ_RE.finditer(text):
        lhs, rhs = m.group(1), m.group(2)
        try:
            expected = safe_eval(_normalize_expr(lhs))
            stated = _to_num(rhs)
        except Exception:
            continue
        findings.append(
            Finding("arithmetic", m.group(0), lhs, expected, stated, _close(expected, stated))
        )

    # "P% of B = R"
    for m in _PCT_OF_RE.finditer(text):
        pct, base, rhs = m.group(1), m.group(2), m.group(3)
        try:
            expected = _to_num(pct + "%") * _to_num(base)
            stated = _to_num(rhs)
        except Exception:
            continue
        claim = m.group(0)
        if any(f.claim == claim for f in findings):
            continue
        findings.append(
            Finding("arithmetic", claim, f"{pct}% of {base}", expected, stated, _close(expected, stated))
        )

    return findings


# ----------------------------------------------------------------------------
# Validators
# ----------------------------------------------------------------------------
class Validator:
    name = "base"

    def check(self, answer: str, context: dict) -> List[Finding]:
        raise NotImplementedError


class ArithmeticValidator(Validator):
    name = "arithmetic"

    def check(self, answer: str, context: dict) -> List[Finding]:
        return find_arithmetic_claims(answer)


class ResponseVerifier:
    """Run every validator over a model answer; return all findings + the bad ones."""

    def __init__(self, validators: Optional[List[Validator]] = None):
        self.validators = validators or [ArithmeticValidator()]

    def verify(self, answer: str, context: Optional[dict] = None):
        ctx = context or {}
        findings = []
        for v in self.validators:
            try:
                findings.extend(v.check(answer, ctx))
            except Exception:
                continue
        bad = [f for f in findings if not f.ok]
        return findings, bad

    @staticmethod
    def correction_prompt(bad: List[Finding]) -> str:
        lines = [
            f'  - You wrote "{f.claim.strip()}" but {f.expression.strip()} = {f.expected:,.2f}'
            for f in bad
        ]
        return (
            "[VERIFIER] Some arithmetic in your previous answer is wrong. Recompute each using the calculator tool and restate the full answer with corrected figures:\n"
            + "\n".join(lines)
        )


# ----------------------------------------------------------------------------
# Lazy singletons + logging (fail-open)
# ----------------------------------------------------------------------------
_VERIFIER: Optional[ResponseVerifier] = None
_LOG: Optional[logging.Logger] = None


def get_verifier() -> ResponseVerifier:
    global _VERIFIER
    if _VERIFIER is None:
        _VERIFIER = ResponseVerifier()
    return _VERIFIER


def _logger() -> logging.Logger:
    global _LOG
    if _LOG is not None:
        return _LOG
    lg = logging.getLogger("ResponseVerify")
    if not lg.handlers:
        try:
            from logging.handlers import RotatingFileHandler

            try:
                from CommonUtils import get_log_path
                path = os.getenv("RESPONSE_VERIFY_LOG", get_log_path("response_verify_log.txt"))
            except Exception:
                path = os.getenv("RESPONSE_VERIFY_LOG", "response_verify_log.txt")
            max_bytes = int(os.getenv("RESPONSE_VERIFY_LOG_MAX_BYTES", str(10485760)))
            backups = int(os.getenv("RESPONSE_VERIFY_LOG_BACKUP", "5"))
            h = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            lg.addHandler(h)
            lg.setLevel(logging.INFO)
            lg.propagate = False
        except Exception:
            lg.addHandler(logging.NullHandler())
    _LOG = lg
    return lg


def log_findings(findings: List[Finding], context: Optional[dict] = None, mode: str = "shadow") -> None:
    """Append one JSONL record per answer-with-arithmetic. Fail-open.

    PRIVACY: by default we log only counts + per-finding ok flags + request_id —
    NO query text and NO figures — so the gap-map carries no financial PII (use the
    request_id to pull the full trace when you need detail). Set
    RESPONSE_VERIFY_LOG_DETAIL=true to also record the raw claim/expr/query/values
    (may contain financial PII; the rotated log then falls under the tenant's log
    retention/access controls).
    """
    if not findings:
        return
    ctx = context or {}
    try:
        detail = os.getenv("RESPONSE_VERIFY_LOG_DETAIL", "false").lower() in ("true", "1", "t", "y", "yes")
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "mode": mode,
            "agent": ctx.get("agent"),
            "request_id": ctx.get("request_id"),
            "retry": ctx.get("retry", False),
            "n_claims": len(findings),
            "n_bad": sum(1 for f in findings if not f.ok),
        }
        if detail:
            record["query"] = (ctx.get("query") or "")[:200]
            record["findings"] = [
                {
                    "kind": f.kind,
                    "claim": f.claim.strip()[:120],
                    "expr": f.expression.strip()[:80],
                    "expected": round(f.expected, 4),
                    "stated": round(f.stated, 4) if f.stated is not None else None,
                    "ok": f.ok,
                }
                for f in findings
            ]
        else:
            record["findings"] = [{"kind": f.kind, "ok": f.ok} for f in findings]
        _logger().info("[RESPONSE-VERIFY] " + json.dumps(record, default=str, allow_nan=False))
    except Exception:
        return


if __name__ == "__main__":
    rv = ResponseVerifier()
    samples = [
        ("correct multiply", "Overcharge = 95 × $6.00 = $570.00."),
        ("WRONG multiply", "Overcharge = 95 × $6.00 = $560.00 on the tents."),
        ("correct pct-of", "Missing rebate = 2.5% of $106,797.50 = $2,669.94."),
        ("WRONG pct-of", "Missing rebate = 2.5% of $106,797.50 = $2,670.50."),
        ("correct subtract", "Net payable = $474,651.12 - $3,814.94 = $470,836.18."),
        ("WRONG subtract", "Variance = 1,160,665 - 1,069,631 = 81,034."),
        ("no shown math", "All lines are correct."),
    ]
    for label, text in samples:
        findings, bad = rv.verify(text)
        if not findings:
            print(f"[ -- no claims ] {label}: {text!r}")
            continue
        for f in findings:
            mark = "OK  " if f.ok else "WRONG"
            extra = "" if f.ok else f"  (correct = {f.expected:,.2f})"
            print(f"[{mark}] {label}: stated {f.stated:,.2f} for '{f.expression.strip()}'{extra}")
    print("\n--- a correction prompt the verifier would send back ---")
    _, bad = rv.verify("It was 95 × $6.00 = $560.00 and 1,160,665 - 1,069,631 = 81,034.")
    print(ResponseVerifier.correction_prompt(bad))
