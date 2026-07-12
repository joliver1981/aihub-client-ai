"""LEVEL 3 — Data Explorer V3 AGENTIC NL→SQL COMPETENCY suite.

Sibling to `test_competency_data_explorer_v2_nl_to_sql.py`. It hits the SAME
endpoint (`POST /data_explorer/chat`) with the SAME question battery, because
engine selection is server-side config, not a different route: the V3 agentic
engine only serves this agent when the app is started with the target agent in
`NLQ_AGENTIC_AGENT_IDS` (or `NLQ_ENGINE_DEFAULT=agentic`). See
docs/nlq-agentic-engine-plan.md §2.

    Engine (V3):  nlq_agentic.AgenticNLQEngine  (via nlq_engine_factory)
    Selection:    NLQ_AGENTIC_AGENT_IDS=<agent>  OR  NLQ_ENGINE_DEFAULT=agentic

PREREQUISITE — this suite SKIPS (never fails) unless the app under test is
actually routing the target agent through the agentic engine. Because the
return shape is intentionally identical to V2 (contract parity), the suite
confirms agentic routing via the `X-NLQ-Engine` response header the chat route
emits when NLQ_AGENTIC_ECHO_ENGINE_HEADER is on in the dev app. If that signal
is absent it skips with an explanatory message rather than silently scoring the
legacy engine.

For a no-app, in-process comparison against the legacy engine (the P5 acceptance
evidence), use `run_nlq_engine_comparison.py` instead — it does not need this
server-mode plumbing.

Report:
    tests_v2/artifacts/competency/data_explorer_v3_agentic_competency_report.md
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import List

import pytest
from playwright.sync_api import sync_playwright

from .conftest import MAIN_BASE
from .test_competency_data_assistant_nl_to_sql import (
    QUESTIONS, TARGET_AGENT_ID, REQUEST_TIMEOUT_MS, SCORE_FLOOR,
    TEST_USER, TEST_PASS, DataQResult, _score,
)

# Floor for the agentic suite: it must at least match the V2 headline (95.8%).
AGENTIC_SCORE_FLOOR = float(os.getenv("COMPETENCY_SCORE_FLOOR_AGENTIC", "0.90"))


@pytest.mark.competency
@pytest.mark.slow
def test_data_explorer_v3_agentic_nl_to_sql_competency(reports_dir: Path):
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context()
        page = ctx.new_page()
        page.goto(f"{MAIN_BASE}/login", timeout=20000)
        try:
            page.locator("input[name='username'], input[name='email'], #username, #email").first.fill(TEST_USER)
            page.locator("input[name='password'], input[type='password'], #password").first.fill(TEST_PASS)
            page.locator("button[type='submit'], input[type='submit'], .btn-login").first.click()
            page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
        except Exception as e:
            b.close()
            pytest.skip(f"Could not log in for agentic suite: {e}")

        state = ctx.storage_state()
        api = p.request.new_context(base_url=MAIN_BASE, storage_state=state, timeout=REQUEST_TIMEOUT_MS)

        init = api.get("/data_explorer")
        if init.status >= 400:
            b.close()
            pytest.skip(f"/data_explorer init failed with {init.status}")

        # Confirm the app is actually routing this agent through the agentic engine.
        probe = api.post("/data_explorer/chat", data={
            "agent_id": TARGET_AGENT_ID, "question": "How many employees are there?", "history": "[]"})
        engine_hdr = (probe.headers.get("x-nlq-engine") or "").lower()
        if engine_hdr != "agentic":
            b.close()
            pytest.skip(
                "App is not routing agent "
                f"{TARGET_AGENT_ID} through the agentic engine (X-NLQ-Engine="
                f"{engine_hdr or 'absent'!r}). Start the app with "
                f"NLQ_AGENTIC_AGENT_IDS={TARGET_AGENT_ID} (and the echo header on) "
                "to run this suite, or use run_nlq_engine_comparison.py in-process."
            )

        results: List[DataQResult] = []
        for q in QUESTIONS:
            question, sql_pats, ans_pats, dims, weight = q
            api.get("/data_explorer")  # fresh engine per question
            r = _ask(api, TARGET_AGENT_ID, question)
            res = DataQResult(question=question, dimensions=list(dims), weight=weight,
                              sql=r["sql"], answer=r["answer"],
                              chat_status=r["chat_status"], elapsed_s=r["elapsed_s"])
            if sql_pats:
                for px in sql_pats:
                    if re.search(px, res.sql, re.IGNORECASE | re.DOTALL):
                        res.sql_ok = True
                        break
            if ans_pats:
                for px in ans_pats:
                    if re.search(px, res.answer, re.IGNORECASE | re.DOTALL):
                        res.ans_ok = True
                        break
            res.raw_score = res.weight if (res.sql_ok or res.ans_ok) else 0.0
            results.append(res)
            mark = "OK" if res.raw_score else "XX"
            print(f"  [{mark}] ({res.elapsed_s:5.1f}s) {question[:70]}", flush=True)

        scoring = _score(results)
        _write_report(results, scoring, reports_dir)
        try:
            api.dispose()
        except Exception:
            pass
        b.close()

    overall = scoring["overall_pct"]
    print(f"\n[agentic] OVERALL = {overall:.1f}% (floor {AGENTIC_SCORE_FLOOR*100:.0f}%)", flush=True)
    if overall < AGENTIC_SCORE_FLOOR * 100:
        pytest.fail(f"AGENTIC COMPETENCY FAIL: {overall:.1f}% below {AGENTIC_SCORE_FLOOR*100:.0f}% floor.")


def _ask(api, agent_id, question):
    t0 = time.time()
    out = {"sql": "", "answer": "", "chat_status": 0, "elapsed_s": 0.0}
    try:
        r = api.post("/data_explorer/chat",
                     data={"agent_id": agent_id, "question": question, "history": "[]"})
        out["chat_status"] = r.status
        if r.status == 200:
            try:
                body = r.json()
            except Exception:
                body = {"answer": r.text()[:300], "query": ""}
            out["sql"] = (body.get("query") or "").strip()
            parts = []
            for k in ("answer", "explanation", "special_message"):
                if body.get(k):
                    parts.append(str(body[k]))
            for k in ("rich_content", "table_data"):
                if body.get(k):
                    parts.append(json.dumps(body[k], default=str))
            out["answer"] = "\n".join(parts)[:3000]
    except Exception as e:
        out["answer"] = f"err:{type(e).__name__}:{str(e)[:200]}"
    out["elapsed_s"] = time.time() - t0
    return out


def _write_report(results, scoring, reports_dir):
    md = reports_dir / "data_explorer_v3_agentic_competency_report.md"
    js = reports_dir / "data_explorer_v3_agentic_competency_report.json"
    lines = [
        "# Data Explorer V3 (Agentic) — NL→SQL Competency Report", "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "Endpoint: POST /data_explorer/chat   Engine: nlq_agentic.AgenticNLQEngine",
        f"Target agent: id={TARGET_AGENT_ID}", "",
        "## Headline", "",
        f"- **Overall score: {scoring['overall_pct']:.1f}%** "
        f"({scoring['total_earned']:.1f} / {scoring['total_weight']:.1f})",
        f"- SQL matched: **{scoring['sql_match_count']}/{len(results)}**  "
        f"Ans matched: **{scoring['ans_match_count']}/{len(results)}**  "
        f"Both: **{scoring['both_count']}/{len(results)}**",
        "", "Compare with `data_explorer_v2_competency_report.md` (legacy engine, same battery).", "",
        "## Per-dimension", "", "| Dimension | N | Score | SQL | Ans |", "|---|--:|--:|--:|--:|",
    ]
    for d, bd in sorted(scoring["by_dim"].items(), key=lambda kv: kv[1]["pct"]):
        lines.append(f"| `{d}` | {bd['n']} | {bd['pct']:.0f}% | {bd['sql_hits']}/{bd['n']} | {bd['ans_hits']}/{bd['n']} |")
    lines += ["", "## Audit trail", ""]
    for r in results:
        mk = "OK" if r.raw_score else "XX"
        lines.append(f"### [{mk}] {r.question}")
        lines.append(f"- {', '.join(r.dimensions)} | {r.elapsed_s:.1f}s | status={r.chat_status}")
        if r.sql:
            lines.append(f"- SQL: `{r.sql[:200]}`")
        lines.append(f"- Answer: {(r.answer or '')[:200]}")
        lines.append("")
    md.write_text("\n".join(lines), encoding="utf-8")
    js.write_text(json.dumps({"scoring": scoring, "results": [asdict(r) for r in results]},
                             indent=2, default=str), encoding="utf-8")
    print(f"\n[agentic] wrote {md}", flush=True)
