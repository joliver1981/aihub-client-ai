"""Shared runner for competency suites.

Each competency suite (Excel, Word, PDF, ...) describes a different file
shape but the lifecycle is identical:

  1. Provision a fresh agent
  2. Upload every fixture
  3. Wait for the indexer
  4. Ask each (file, question) and score against accept/negative regexes
  5. Aggregate per-file + per-dimension + overall
  6. Write a markdown + JSON report
  7. Tear down the agent
  8. Assert competency floor

Conversational follow-up
------------------------
Real users don't bail when an agent asks "which document do you mean?"
— they answer. The runner accepts an optional `disambiguation_hints`
dict mapping fixture filename → a short hint to send as a follow-up
when the agent asks for clarification. If a suite passes that dict,
each question can take up to one extra turn before scoring.

A suite calls `run_competency(...)` with its question battery and a
report filename. The battery is a list of `Question` tuples:

    (fixture_filename, question_text, accept_patterns,
     dimensions, negative_patterns, weight)
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest
import requests

from ._chat_helpers import ask_with_followup, looks_like_clarifying_question
from ._llm_grader import llm_grade_answer


@dataclass
class QResult:
    fixture: str
    question: str
    dimensions: List[str]
    weight: float
    answer: str = ""
    chat_status: int = 0
    elapsed_s: float = 0.0
    matched: bool = False
    leaked: bool = False
    raw_score: float = 0.0
    # New: did we have to send a clarifying follow-up to get this answer?
    followup_used: bool = False
    # New: short summary of the full conversation transcript (for the
    # report's audit trail). Empty when no follow-up happened.
    transcript_summary: str = ""


def run_competency(
    *,
    suite_name: str,
    fixtures_dir: Path,
    fixture_glob: str,
    questions: list,
    http_session: requests.Session,
    main_base: str,
    api_key: str,
    artifact_prefix: str,
    reports_dir: Path,
    report_basename: str,
    index_wait_seconds: int,
    score_floor: float,
    disambiguation_hints: Optional[Dict[str, str]] = None,
):
    """Execute one competency suite end-to-end. Returns the scoring dict.
    Raises pytest.fail() if floor is breached or a hidden-sheet style leak
    is detected (`leaked=True` on any QResult)."""

    # --- 1. Provision agent ----------------------------------------------
    agent_name = f"{artifact_prefix}{suite_name}_{uuid.uuid4().hex[:8]}"
    print(f"\n[{suite_name}] creating agent {agent_name}", flush=True)
    r = http_session.post(
        f"{main_base}/add/agent",
        json={
            "agent_name": agent_name,
            "agent_description": f"{suite_name} competency test agent",
            "agent_type": "general",
            "agent_system_prompt": (
                "You answer questions based on the uploaded documents. "
                "If a fact is not present in the documents, say so plainly. "
                "Do not guess."
            ),
            "agent_model": "gpt-4o-mini",
            "agent_temperature": 0.1,
        },
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    assert r.status_code in (200, 201), (
        f"agent create failed: {r.status_code} {r.text[:200]}"
    )
    body = r.json()
    agent_id = body.get("agent_id") or body.get("id") or body.get("message")
    assert agent_id, f"agent create returned no id: {body}"
    print(f"[{suite_name}] agent_id={agent_id}", flush=True)

    try:
        # --- 2. Upload every fixture --------------------------------------
        uploaded = []
        for fpath in sorted(fixtures_dir.glob(fixture_glob)):
            if fpath.name.startswith("_"):
                continue
            t0 = time.time()
            with open(fpath, "rb") as fh:
                up = http_session.post(
                    f"{main_base}/add/agent_knowledge",
                    data={"agent_id": str(agent_id)},
                    files={"file": (fpath.name, fh)},
                    timeout=240,
                )
            uploaded.append((fpath.name, up.status_code, time.time() - t0))
            print(f"[{suite_name}] upload {fpath.name}: {up.status_code} "
                  f"in {time.time() - t0:.1f}s", flush=True)
        print(f"[{suite_name}] uploaded {len(uploaded)} fixtures, "
              f"waiting {index_wait_seconds}s for indexer ...", flush=True)
        time.sleep(index_wait_seconds)

        # --- 3. Ask each question ----------------------------------------
        results: List[QResult] = []
        for q in questions:
            fixture, question, accept, dimensions, negative, weight = q
            qr = QResult(
                fixture=fixture, question=question,
                dimensions=list(dimensions), weight=weight,
            )

            # If the suite provided a disambiguation_hints dict for this
            # fixture, use the conversational helper — it'll auto-follow-up
            # if the agent first asks "which document do you mean?".
            hint = (
                disambiguation_hints.get(fixture)
                if disambiguation_hints else None
            )
            turn = ask_with_followup(
                http_session=http_session,
                base_url=main_base,
                agent_id=agent_id,
                question=question,
                disambiguation_hint=hint,
                timeout_s=90,
            )
            qr.chat_status = turn.final_chat_status
            qr.answer = turn.final_answer
            qr.elapsed_s = turn.elapsed_s
            qr.followup_used = turn.followup_used is not None
            # Compact transcript line for the report
            if qr.followup_used:
                qr.transcript_summary = (
                    f"[turn 1] agent asked for clarification → "
                    f"[turn 2] user hint: {turn.followup_used[:120]!r} "
                    f"→ agent re-answered ({len(turn.final_answer)} chars)"
                )

            ans = qr.answer
            if negative:
                for nx in negative:
                    if re.search(nx, ans, re.IGNORECASE | re.DOTALL):
                        qr.leaked = True
                        break

            # Regex fast-path: if any accept pattern matches, that's a
            # PASS. This is the cheap deterministic check — the LLM
            # grader below only runs when regex says NO MATCH, so most
            # questions cost zero extra LLM tokens to grade.
            if not qr.leaked:
                for px in accept:
                    if re.search(px, ans, re.IGNORECASE | re.DOTALL):
                        qr.matched = True
                        break

            # LLM grader fallback: when regex didn't match, ask a mini LLM
            # whether the agent's answer is actually correct (handles
            # precision differences, markdown formatting, comma styles,
            # etc. that brittle regex misses). Skips when there's no
            # answer at all or when the answer leaked a forbidden value.
            llm_grader_used = False
            llm_grader_verdict: Optional[bool] = None
            if not qr.matched and not qr.leaked and ans and ans.strip():
                try:
                    llm_grader_verdict = llm_grade_answer(
                        question=question,
                        agent_answer=ans,
                        expected_patterns=list(accept),
                    )
                except Exception as e:
                    print(f"  [warn] LLM grader raised: {e}", flush=True)
                    llm_grader_verdict = None
                if llm_grader_verdict is True:
                    qr.matched = True
                    llm_grader_used = True

            qr.raw_score = qr.weight if (qr.matched and not qr.leaked) else 0.0
            if qr.matched and llm_grader_used:
                mark = "✅🤖"  # passed via LLM grader (regex missed)
            elif qr.matched:
                mark = "✅"
            elif qr.leaked:
                mark = "🚨LEAK"
            else:
                mark = "❌"
            fu_tag = " 💬" if qr.followup_used else ""
            print(f"  {mark}{fu_tag} ({fixture}) {question[:70]} -> "
                  f"score={qr.raw_score:.1f} ({qr.elapsed_s:.1f}s)",
                  flush=True)
            results.append(qr)

        # --- 4. Score -----------------------------------------------------
        scoring = _score(results)

        # --- 5. Report ----------------------------------------------------
        _write_report(
            suite_name, results, scoring, uploaded, agent_id,
            reports_dir, report_basename,
        )

        # --- 6. Assert floor + leak detection ----------------------------
        overall = scoring["overall_pct"]
        print(f"\n[{suite_name}] OVERALL SCORE = {overall:.1f}% "
              f"(floor {score_floor*100:.0f}%)", flush=True)
        if scoring["leak_count"]:
            pytest.fail(
                f"COMPETENCY FAIL ({suite_name}): {scoring['leak_count']} "
                f"leak(s) detected. See "
                f"{reports_dir / (report_basename + '.md')}."
            )
        if overall < score_floor * 100:
            pytest.fail(
                f"COMPETENCY FAIL ({suite_name}): overall score "
                f"{overall:.1f}% below floor {score_floor*100:.0f}%. See "
                f"{reports_dir / (report_basename + '.md')}."
            )

        return scoring

    finally:
        try:
            http_session.post(
                f"{main_base}/delete/agent",
                json={"agent_id": int(agent_id)},
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            print(f"[{suite_name}] cleaned up agent {agent_id}", flush=True)
        except Exception as e:
            print(f"[{suite_name}] cleanup failed: {e}", flush=True)


# ==========================================================================
# Scoring + report helpers
# ==========================================================================

def _score(results: List[QResult]) -> dict:
    total_weight = sum(r.weight for r in results) or 1.0
    total_earned = sum(r.raw_score for r in results)
    overall_pct = 100.0 * total_earned / total_weight

    by_file = {}
    for r in results:
        b = by_file.setdefault(r.fixture, {"weight": 0.0, "earned": 0.0, "n": 0})
        b["weight"] += r.weight
        b["earned"] += r.raw_score
        b["n"] += 1
    for k, v in by_file.items():
        v["pct"] = 100.0 * v["earned"] / (v["weight"] or 1)

    by_dim = {}
    for r in results:
        for d in r.dimensions:
            b = by_dim.setdefault(d, {"weight": 0.0, "earned": 0.0, "n": 0})
            b["weight"] += r.weight
            b["earned"] += r.raw_score
            b["n"] += 1
    for k, v in by_dim.items():
        v["pct"] = 100.0 * v["earned"] / (v["weight"] or 1)

    return {
        "overall_pct": overall_pct,
        "total_weight": total_weight,
        "total_earned": total_earned,
        "by_file": by_file,
        "by_dim": by_dim,
        "leak_count": sum(1 for r in results if r.leaked),
    }


def _write_report(suite_name, results, scoring, uploaded, agent_id,
                  reports_dir: Path, report_basename: str):
    md = reports_dir / f"{report_basename}.md"
    js = reports_dir / f"{report_basename}.json"

    lines = [
        f"# {suite_name.title()} Agent-Knowledge — Competency Report",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Agent: id={agent_id} (deleted after run)",
        "",
        "## Headline",
        "",
        f"- **Overall score: {scoring['overall_pct']:.1f}%** "
        f"({scoring['total_earned']:.1f} / {scoring['total_weight']:.1f} "
        f"weighted points)",
        f"- Questions asked: **{len(results)}**",
        f"- Fixtures uploaded: **{len(uploaded)}**",
        f"- Leaks / forbidden-pattern hits: "
        f"**{scoring['leak_count']}** {'🚨' if scoring['leak_count'] else '✅'}",
        f"- Questions that needed a clarification follow-up: "
        f"**{sum(1 for r in results if getattr(r, 'followup_used', False))}** "
        f"of {len(results)}",
        "",
        "## Per-fixture competency",
        "",
        "| Fixture | Questions | Score | Earned/Weight |",
        "|---|---:|---:|---|",
    ]
    for fname, b in sorted(scoring["by_file"].items()):
        lines.append(
            f"| `{fname}` | {b['n']} | **{b['pct']:.1f}%** | "
            f"{b['earned']:.1f}/{b['weight']:.1f} |"
        )

    lines += [
        "",
        "## Per-dimension competency",
        "",
        "| Dimension | Questions | Score | Earned/Weight |",
        "|---|---:|---:|---|",
    ]
    for d, b in sorted(scoring["by_dim"].items(),
                       key=lambda kv: kv[1]["pct"]):
        lines.append(
            f"| `{d}` | {b['n']} | **{b['pct']:.1f}%** | "
            f"{b['earned']:.1f}/{b['weight']:.1f} |"
        )

    fails = [r for r in results if not r.matched or r.leaked]
    if fails:
        lines += ["", "## Failed / leaked questions", ""]
        for r in fails:
            tag = "🚨 LEAK" if r.leaked else "❌ FAIL"
            lines.append(f"### {tag} — `{r.fixture}` — {r.question}")
            lines.append(f"- Dimensions: {', '.join(r.dimensions)}")
            lines.append(f"- Weight: {r.weight}")
            lines.append(f"- Chat status: {r.chat_status}")
            lines.append(f"- Elapsed: {r.elapsed_s:.1f}s")
            if getattr(r, 'followup_used', False):
                lines.append(f"- Follow-up sent: yes ({getattr(r, 'transcript_summary', '')})")
            lines.append(f"- Answer:")
            for ln in (r.answer or "<no answer>").splitlines():
                lines.append(f"    {ln}")
            lines.append("")

    lines += ["", "## All Q&A (for audit)", ""]
    for r in results:
        mark = "🚨" if r.leaked else ("✅" if r.matched else "❌")
        lines.append(f"### {mark} `{r.fixture}` — {r.question}")
        lines.append(f"- score: {r.raw_score:.1f} | "
                     f"dimensions: {', '.join(r.dimensions)} | "
                     f"{r.elapsed_s:.1f}s")
        lines.append(f"- answer:")
        for ln in (r.answer or "<no answer>").splitlines()[:8]:
            lines.append(f"    {ln}")
        lines.append("")

    md.write_text("\n".join(lines), encoding="utf-8")
    js.write_text(
        json.dumps({
            "scoring": scoring,
            "results": [asdict(r) for r in results],
            "uploaded": uploaded,
        }, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\n[{suite_name}] wrote {md}", flush=True)
