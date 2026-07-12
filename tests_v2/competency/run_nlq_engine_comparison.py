"""In-process both-engines competency comparison (NLQ V3 plan P5).

Drives the SAME 24-question competency battery through BOTH engines by calling
`get_answer(agent_id, question)` directly — no app server, no Playwright, no
login. This is the acceptance evidence for P5:
    * agentic overall score >= legacy overall score
    * agentic p50 latency <= 15s
    * (injection safety lives in the P2 sql_gate matrix + the expansion set)

Legacy engine  : nlq_engine_factory._construct_legacy(enhance=False)  (plain,
                 exactly what GeneralAgent uses in prod)
Agentic engine : nlq_agentic.AgenticNLQEngine

A fresh engine is built per question (mirrors the Playwright suite's re-init per
question so chat history can't bias later answers).

Usage (from repo root, aihub2.1 python):
    python -m tests_v2.competency.run_nlq_engine_comparison
    python -m tests_v2.competency.run_nlq_engine_comparison --engines agentic --limit 3
    python -m tests_v2.competency.run_nlq_engine_comparison --battery all

Writes:
    tests_v2/artifacts/competency/nlq_engine_comparison_report.md   (+ .json)
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests_v2.competency.test_competency_data_assistant_nl_to_sql import (
    QUESTIONS, TARGET_AGENT_ID,
)
from tests_v2.competency.nlq_v3_expansion_battery import EXPANSION_QUESTIONS


# ── engine construction ──────────────────────────────────────────────────

def _build_legacy():
    from nlq_engine_factory import _construct_legacy
    return _construct_legacy(enhance=False)


def _build_agentic():
    from nlq_agentic import AgenticNLQEngine
    return AgenticNLQEngine()


ENGINE_BUILDERS = {"legacy": _build_legacy, "agentic": _build_agentic}


# ── result extraction (tuple or rich dict, DataFrame or text) ─────────────

def _unpack(result):
    """Return (sql, answer_text, answer_type) from a get_answer result."""
    if isinstance(result, dict):
        answer = result.get("answer")
        atype = result.get("answer_type", "string")
        query = result.get("query", "") or ""
        special = result.get("special_message", "") or ""
    else:
        answer = result[0]
        atype = result[3] if len(result) > 3 else "string"
        special = result[4] if len(result) > 4 else ""
        query = result[7] if len(result) > 7 else ""

    # SQL comes back as "=== Data Query ===\n<sql>...". Strip the marker.
    sql = ""
    if query:
        m = re.search(r"=== Data Query ===\s*(.+?)(?:=== |\nTotal time|$)", query, re.DOTALL)
        sql = (m.group(1) if m else query).strip()

    # Build the answer text surface for pattern matching.
    parts = []
    try:
        import pandas as pd
        if isinstance(answer, pd.DataFrame):
            parts.append(answer.to_string(index=False))
        elif answer is not None:
            parts.append(str(answer))
    except Exception:
        if answer is not None:
            parts.append(str(answer))
    if special:
        parts.append(str(special))
    return sql, "\n".join(parts)[:4000], atype


def _score_one(q, sql, answer):
    _question, sql_pats, ans_pats, _dims, weight = q
    sql_ok = bool(sql_pats) and any(
        re.search(px, sql, re.IGNORECASE | re.DOTALL) for px in sql_pats
    )
    ans_ok = bool(ans_pats) and any(
        re.search(px, answer, re.IGNORECASE | re.DOTALL) for px in ans_pats
    )
    return sql_ok, ans_ok, (weight if (sql_ok or ans_ok) else 0.0)


# ── run one engine over a battery ────────────────────────────────────────

def run_engine(engine_name, battery, agent_id, limit=None):
    builder = ENGINE_BUILDERS[engine_name]
    rows = []
    qs = battery[:limit] if limit else battery
    for i, q in enumerate(qs, 1):
        question, _sql_pats, _ans_pats, dims, weight = q
        engine = builder()  # fresh per question — no cross-question bias
        t0 = time.time()
        err = None
        try:
            result = engine.get_answer(agent_id, question)
            sql, answer, atype = _unpack(result)
        except Exception as e:
            sql, answer, atype = "", f"EXCEPTION {type(e).__name__}: {e}", "error"
            err = str(e)
        elapsed = time.time() - t0
        sql_ok, ans_ok, score = _score_one(q, sql, answer)
        rows.append({
            "question": question, "dimensions": list(dims), "weight": weight,
            "sql": sql, "answer": answer[:600], "answer_type": atype,
            "sql_ok": sql_ok, "ans_ok": ans_ok, "score": score,
            "elapsed_s": round(elapsed, 1), "error": err,
        })
        mark = "OK " if score else "XX "
        print(f"  [{engine_name}] {i:2}/{len(qs)} {mark}({elapsed:5.1f}s) "
              f"sql={'Y' if sql_ok else '.'} ans={'Y' if ans_ok else '.'} "
              f"{question[:60]}", flush=True)
    return rows


def _summarize(rows):
    total_w = sum(r["weight"] for r in rows) or 1.0
    earned = sum(r["score"] for r in rows)
    lat = [r["elapsed_s"] for r in rows]
    return {
        "n": len(rows),
        "overall_pct": 100.0 * earned / total_w,
        "earned": earned, "total_weight": total_w,
        "sql_hits": sum(1 for r in rows if r["sql_ok"]),
        "ans_hits": sum(1 for r in rows if r["ans_ok"]),
        "both": sum(1 for r in rows if r["sql_ok"] and r["ans_ok"]),
        "errors": sum(1 for r in rows if r["error"]),
        "p50_s": round(statistics.median(lat), 1) if lat else 0.0,
        "mean_s": round(statistics.mean(lat), 1) if lat else 0.0,
        "max_s": round(max(lat), 1) if lat else 0.0,
    }


# ── report ───────────────────────────────────────────────────────────────

def write_report(all_rows, reports_dir, battery_name):
    reports_dir.mkdir(parents=True, exist_ok=True)
    md = reports_dir / f"nlq_engine_comparison_{battery_name}_report.md"
    js = reports_dir / f"nlq_engine_comparison_{battery_name}_report.json"

    summaries = {name: _summarize(rows) for name, rows in all_rows.items()}
    lines = [
        "# NLQ Engine Comparison — legacy vs agentic (in-process)",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Battery: {battery_name} ({len(next(iter(all_rows.values())))} questions)   "
        f"Agent: {TARGET_AGENT_ID}   Path: direct get_answer()",
        "",
        "## Headline",
        "",
        "| Engine | Overall | SQL hits | Ans hits | Both | Errors | p50 | mean | max |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, s in summaries.items():
        lines.append(
            f"| {name} | **{s['overall_pct']:.1f}%** | {s['sql_hits']}/{s['n']} | "
            f"{s['ans_hits']}/{s['n']} | {s['both']}/{s['n']} | {s['errors']} | "
            f"{s['p50_s']}s | {s['mean_s']}s | {s['max_s']}s |"
        )

    if "legacy" in summaries and "agentic" in summaries:
        d = summaries["agentic"]["overall_pct"] - summaries["legacy"]["overall_pct"]
        gate_score = "PASS" if d >= 0 else "FAIL"
        gate_lat = "PASS" if summaries["agentic"]["p50_s"] <= 15 else "FAIL"
        lines += [
            "",
            "## Acceptance gates (plan §7)",
            "",
            f"- agentic >= legacy overall: **{gate_score}** "
            f"(agentic {summaries['agentic']['overall_pct']:.1f}% vs "
            f"legacy {summaries['legacy']['overall_pct']:.1f}%, delta {d:+.1f} pts)",
            f"- agentic p50 <= 15s: **{gate_lat}** ({summaries['agentic']['p50_s']}s)",
        ]

    # Per-question side-by-side (when both engines ran).
    if "legacy" in all_rows and "agentic" in all_rows:
        lines += ["", "## Per-question", "",
                  "| # | Question | legacy | agentic | l.s | a.s |",
                  "|---|---|:--:|:--:|--:|--:|"]
        for i, (lr, ar) in enumerate(zip(all_rows["legacy"], all_rows["agentic"]), 1):
            lm = "OK" if lr["score"] else "XX"
            am = "OK" if ar["score"] else "XX"
            lines.append(f"| {i} | {lr['question'][:60]} | {lm} | {am} | "
                         f"{lr['elapsed_s']} | {ar['elapsed_s']} |")

    for name, rows in all_rows.items():
        lines += ["", f"## {name} — audit trail", ""]
        for r in rows:
            mk = "OK" if r["score"] else "XX"
            lines.append(f"### [{mk}] {r['question']}")
            lines.append(f"- type={r['answer_type']} sql_ok={r['sql_ok']} "
                         f"ans_ok={r['ans_ok']} {r['elapsed_s']}s")
            if r["sql"]:
                lines.append(f"- SQL: `{r['sql'][:200].replace(chr(10), ' ')}`")
            lines.append(f"- Answer: {r['answer'][:200].replace(chr(10), ' ')}")
            lines.append("")

    md.write_text("\n".join(lines), encoding="utf-8")
    js.write_text(json.dumps({"summaries": summaries, "rows": all_rows,
                              "agent_id": TARGET_AGENT_ID, "battery": battery_name},
                             indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {md}", flush=True)
    return summaries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="legacy,agentic",
                    help="comma list: legacy,agentic")
    ap.add_argument("--battery", default="core", choices=["core", "expansion", "all"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--agent", type=int, default=TARGET_AGENT_ID)
    args = ap.parse_args()

    if args.battery == "core":
        battery = list(QUESTIONS)
    elif args.battery == "expansion":
        battery = list(EXPANSION_QUESTIONS)
    else:
        battery = list(QUESTIONS) + list(EXPANSION_QUESTIONS)

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    reports_dir = REPO_ROOT / "tests_v2" / "artifacts" / "competency"

    all_rows = {}
    for name in engines:
        print(f"\n=== {name} over {len(battery[:args.limit] if args.limit else battery)} "
              f"questions ({args.battery}) ===", flush=True)
        all_rows[name] = run_engine(name, battery, args.agent, limit=args.limit)

    summaries = write_report(all_rows, reports_dir, args.battery)
    print("\n=== SUMMARY ===", flush=True)
    for name, s in summaries.items():
        print(f"  {name:8}: {s['overall_pct']:5.1f}%  p50={s['p50_s']}s  "
              f"mean={s['mean_s']}s  errors={s['errors']}", flush=True)


if __name__ == "__main__":
    main()
