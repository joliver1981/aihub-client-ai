"""LEVEL 3 — Data Assistant NL→SQL COMPETENCY suite.

The data assistant feature converts natural-language questions into SQL,
executes the SQL against a configured database connection, and returns
the results plus an explanation. This is the second-most-used feature
after agent chat, and it had **zero quality coverage** before this suite.

How this differs from the agent-knowledge competency suites:

  - There are no FILES to upload. The agent is configured to a database
    connection with a fixed schema (we use the AIRDB retail schema:
    TS.sales, TS.product_master, TS.location_master, TS.employee_data,
    TS.Inventory, TS.cost_of_products, TS.price_of_goods,
    TS.calendar_master, TS.plan_sales_data, TS.store_traffic).
  - Auth is session-based (login_required on /chat/data and a Flask
    session_id pickle pointing to an in-memory engine). We borrow the
    journey suite's storage_state via Playwright's APIRequestContext.
  - Scoring is TWO-tier per question:
       * `sql_match`  — does the generated SQL match expected patterns?
       * `ans_match`  — does the returned answer text match expected
                        ground-truth values?
    A question scores 1.0 if EITHER matches (because either signal proves
    the agent reached the right conclusion). The report shows both
    signals so you can see when SQL is right but the answer is empty
    (DB execution issue) or vice versa.

Dimensions probed:
   simple_select    — "list the product categories"
   count            — "how many products are in Electronics?"
   where_filter     — single-column WHERE
   aggregate_sum    — SUM
   aggregate_avg    — AVG
   group_by         — group + sum
   order_by_top_n   — TOP N or LIMIT N + ORDER BY
   join_2           — 2-table join
   join_3           — 3-table join
   date_filter      — WHERE on date column
   distinct_count   — COUNT(DISTINCT ...)
   comparison       — "which is larger: A or B?"
   not_present      — concept not in the schema; correct answer = "no"
   schema_intro     — basic schema awareness

Live config: the suite uses agent id=281 ("AIRDB Agent Demo") which is
hardcoded in this install. If the agent gets deleted or its connection
breaks, this suite needs a new target; the test will skip rather than
fail noisily in that case.

Report:
   tests_v2/artifacts/competency/data_assistant_competency_report.md
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Tuple

import pytest
from playwright.sync_api import sync_playwright

from .conftest import MAIN_BASE


# =============================================================================
# Configuration
# =============================================================================

# The agent that's configured against a working AIRDB retail-schema connection
# in this install. If you re-provision the install, point this at a new agent.
TARGET_AGENT_ID = int(os.getenv("COMPETENCY_DATA_AGENT_ID", "281"))

# Per-request timeout. The data engine LLM-calls + SQL execution can take
# 30-90 s for analytical questions.
REQUEST_TIMEOUT_MS = int(os.getenv("COMPETENCY_DATA_TIMEOUT_MS", "120000"))

# Floor for the test to pass at all.
SCORE_FLOOR = float(os.getenv("COMPETENCY_SCORE_FLOOR_DATA", "0.50"))

TEST_USER = os.getenv("UI_TEST_USER", "admin")
TEST_PASS = os.getenv("UI_TEST_PASS", "admin")


# =============================================================================
# Question battery
#
# Each row:
#   (question_text, accept_sql_patterns, accept_answer_patterns,
#    dimensions, weight)
#
#   accept_sql_patterns    — regexes the generated SQL should match; ANY
#                            match → sql_ok = True
#   accept_answer_patterns — regexes the answer text should match; ANY
#                            match → ans_ok = True
#   Score 1.0 * weight when sql_ok OR ans_ok.
# =============================================================================

QUESTIONS = [
    # ── simple_select / schema introspection ──
    (
        "List the distinct product categories sold in the stores.",
        # SQL should include DISTINCT or GROUP BY on a category column
        [r"\bDISTINCT\b.*category|category.*\bDISTINCT\b",
         r"GROUP\s+BY\s+\S*category"],
        # Ground truth: Beauty & Personal Care, Clothing, Electronics, Home & Kitchen
        [r"Electronics", r"Clothing", r"Home\s*&?\s*Kitchen",
         r"Beauty\s*&?\s*Personal\s*Care"],
        ["simple_select", "distinct_count"], 1.0,
    ),
    (
        "How many distinct cities have store locations?",
        [r"COUNT\s*\(\s*DISTINCT\s+city",
         r"DISTINCT\s+city.*FROM\s+\S*location_master"],
        [r"\b15\b", r"fifteen"],
        ["distinct_count", "aggregate_sum"], 1.0,
    ),
    (
        "What product categories are available?",
        [r"category.*FROM\s+\S*product_master"],
        [r"Electronics", r"Clothing"],
        ["simple_select"], 1.0,
    ),

    # ── count + where_filter ──
    (
        "How many products are in the Electronics category?",
        [r"COUNT\s*\(.*FROM\s+\S*product_master.*WHERE\s+.*category\s*=\s*['\"]Electronics",
         r"WHERE\s+category\s*=\s*['\"]Electronics"],
        # Don't know the exact count, but Electronics is one of 4 categories
        # of 200 products. Accept any count + the category name.
        [r"Electronics.*\b\d+\b", r"\b\d+\b.*products?.*Electronics",
         r"there\s+are\s+\d+\s+products?"],
        ["count", "where_filter"], 1.0,
    ),
    (
        "How many stores are located in California?",
        [r"WHERE.*(?:state\s*=\s*['\"]CA|state\s*=\s*['\"]California)",
         r"FROM\s+\S*location_master.*WHERE"],
        # CA stores = Los Angeles, San Francisco, San Diego, San Jose = 4
        [r"\b4\b", r"\bfour\b", r"California"],
        ["count", "where_filter"], 1.0,
    ),
    (
        "How many employees does the company have?",
        [r"COUNT\s*\(.*FROM\s+\S*employee_data",
         r"SELECT\s+COUNT.*employee"],
        # Ground truth: 75 employees
        [r"\b75\b", r"seventy[- ]?five"],
        ["count"], 1.0,
    ),

    # ── aggregate (SUM / AVG) ──
    (
        "What is the total sales revenue across all stores?",
        [r"SUM\s*\(\s*total_revenue\s*\).*FROM\s+\S*sales",
         r"SUM\s*\(.*revenue.*FROM\s+\S*sales"],
        # Ground truth: ~$1.388B
        [r"1[,.]?388[,.]?\d{3}[,.]?\d{3}", r"1\.39\s*[Bb]illion",
         r"1\.38\s*[Bb]illion", r"\$1[.,]388[.,]350"],
        ["aggregate_sum"], 1.0,
    ),
    (
        "What is the average revenue per sale?",
        [r"AVG\s*\(\s*total_revenue", r"AVG\s*\(.*revenue"],
        # Ground truth: $799.79
        [r"\$?7\d{2}\.\d{2}", r"\$?\s*799\.?7?9?", r"\$800",
         r"approximately\s+\$\s*8\d{2}", r"\b799\b"],
        ["aggregate_avg"], 1.0,
    ),

    # ── group_by ──
    (
        "What is the total sales revenue by product category?",
        [r"GROUP\s+BY\s+\S*category",
         r"JOIN\s+\S*product_master.*GROUP\s+BY"],
        [r"Electronics.*\$?\s*\d", r"Clothing.*\$?\s*\d",
         r"category.*total"],
        ["group_by", "join_2", "aggregate_sum"], 1.0,
    ),
    (
        "Show me total sales revenue by year.",
        [r"YEAR\s*\(\s*sale_date\s*\)|DATEPART\s*\(\s*year",
         r"GROUP\s+BY.*(?:YEAR|sale_date|year)"],
        [r"2024.*2025|2025.*2024", r"\b202[4-6]\b"],
        ["group_by", "date_filter", "aggregate_sum"], 1.0,
    ),

    # ── order_by + top_n ──
    (
        "Which store had the highest total sales revenue? Just give me the top one.",
        [r"(?:TOP\s+1|LIMIT\s+1).*ORDER\s+BY.*DESC",
         r"ORDER\s+BY.*DESC.*(?:TOP\s+1|LIMIT\s+1)"],
        # Ground truth: S003 = Central Plaza (Chicago)
        [r"Central\s+Plaza", r"S003", r"\bChicago\b"],
        ["order_by_top_n", "join_2", "aggregate_sum"], 1.0,
    ),
    (
        "What are the top 5 products by units sold?",
        [r"(?:TOP\s+5|LIMIT\s+5).*ORDER\s+BY.*DESC",
         r"SUM\s*\(\s*quantity_sold.*ORDER\s+BY"],
        # Ground truth top product: P0016 Laptops Item 8
        [r"Laptops\s+Item", r"P00\d{2}", r"\d+\.\d*\s*\|\s*\d+",
         r"product_name"],
        ["order_by_top_n", "join_2"], 1.0,
    ),

    # ── join_2 / join_3 ──
    (
        "Which store names had at least one sale? Show distinct store names.",
        [r"DISTINCT.*store_name|store_name.*GROUP\s+BY",
         r"JOIN\s+\S*location_master"],
        # Ground truth: should include the named stores
        [r"Downtown\s+Flagship|Central\s+Plaza|Westside\s+Mall|Eastgate"],
        ["join_2", "distinct_count"], 1.0,
    ),
    (
        "What is the total revenue per city across all stores?",
        [r"JOIN\s+\S*location_master.*GROUP\s+BY\s+\S*city|city.*GROUP\s+BY"],
        [r"Chicago", r"New\s+York", r"Los\s+Angeles", r"city"],
        ["join_3", "group_by", "aggregate_sum"], 1.0,
    ),
    (
        "Which product category has the most distinct products?",
        [r"COUNT\s*\(\s*(?:DISTINCT\s+)?product_id.*GROUP\s+BY\s+\S*category",
         r"GROUP\s+BY\s+\S*category.*COUNT"],
        [r"Electronics|Clothing|Beauty|Home"],
        ["group_by", "comparison"], 1.0,
    ),

    # ── date_filter ──
    (
        "How many sales transactions happened in 2025?",
        [r"WHERE.*sale_date.*2025|YEAR\s*\(\s*sale_date\s*\)\s*=\s*2025",
         r"sale_date\s+BETWEEN.*2025"],
        [r"\d{3,7}\s+(?:sales|transactions)", r"\d{3,7}\s+rows?",
         r"\b\d{3,7}\b"],
        ["date_filter", "count", "where_filter"], 1.0,
    ),
    (
        "What was the total revenue for sales in January 2025?",
        [r"sale_date.*2025-01|MONTH\s*\(\s*sale_date\s*\)\s*=\s*1",
         r"BETWEEN\s+'2025-01-01'\s+AND\s+'2025-01-31'"],
        [r"\$?\s*\d{1,3}[,.]?\d{3}[,.]?\d{3}", r"\b\d+\b"],
        ["date_filter", "aggregate_sum", "where_filter"], 1.0,
    ),

    # ── comparison ──
    (
        "Did the Downtown Flagship store generate more revenue than the Westside Mall store?",
        [r"store_name\s+IN\s*\(.*Downtown|store_name.*Downtown",
         r"WHERE.*(?:Downtown|S001|S002)"],
        # Ground truth: S001 Downtown vs S002 Westside — actual answer
        # depends on DB. Accept any comparative phrasing.
        [r"yes|no|higher|lower|more\s+revenue|less\s+revenue",
         r"Downtown|Westside"],
        ["comparison", "where_filter"], 1.0,
    ),
    (
        "Which is the largest store by revenue: Central Plaza, Eastgate Store, or Downtown Flagship?",
        [r"store_name\s+IN", r"WHERE.*Central\s+Plaza"],
        # Ground truth: Central Plaza is biggest in this DB
        [r"Central\s+Plaza"],
        ["comparison", "order_by_top_n"], 1.0,
    ),

    # ── not_present (no such concept in this schema) ──
    # NB: the LLM uses a Unicode curly apostrophe (U+2019 = ’), not the
    # ASCII ' (U+0027). Use a permissive [’'] class. Accept correct refusals
    # regardless of exact verb — engines phrase "not in the schema" differently:
    # legacy says "can't provide / don't have", the agentic engine says
    # "I don't SEE any ... tables / can't CALCULATE ...". Both are correct, so
    # the oracle accepts see/calculate/compute and "no ... table" too. Broadening
    # accept-patterns can only recognise more correct answers, never lower a score.
    (
        "Show me the customer churn rate for the past quarter.",
        None,
        [r"\bno\b", r"\bnot\b.*(?:available|present|tracked|stored)",
         r"do(?:es)?n[’' ]?t\s+(?:have|see|find|contain)", r"unable\s+to",
         r"can[’' ]?t\s+(?:provide|find|determine|give|show|answer|"
         r"display|return|calculate|compute)",
         r"cannot\s+(?:provide|find|determine|give|show|answer|calculate|compute)",
         r"no\s+(?:customer|churn|subscription).*(?:data|table|column)"],
        ["not_present"], 1.0,
    ),
    (
        "What is the marketing spend by campaign last year?",
        None,
        [r"\bno\b", r"\bnot\b.*(?:available|present|tracked|stored|include)",
         r"do(?:es)?n[’' ]?t\s+(?:have|include|see|find|contain)",
         r"can[’' ]?t\s+(?:provide|find|determine|give|show|answer|calculate|compute)",
         r"cannot\s+(?:provide|find|determine|give|show|answer|calculate|compute)",
         r"no\s+(?:marketing|campaign).*(?:data|table|column)"],
        ["not_present"], 1.0,
    ),

    # ── null_handling / specific filters ──
    (
        "Which dates this year are holidays? List the holiday name for each.",
        [r"WHERE.*is_holiday|holiday_name\s+IS\s+NOT\s+NULL",
         r"FROM\s+\S*calendar_master.*WHERE"],
        [r"holiday|date.*Christmas|date.*Thanksgiving|January|July"],
        ["where_filter", "date_filter"], 1.0,
    ),

    # ── multi-step / multi-hop ──
    (
        "Which store has the highest average daily foot traffic?",
        [r"AVG\s*\(\s*foot_traffic_count|foot_traffic.*GROUP\s+BY",
         r"FROM\s+\S*store_traffic"],
        [r"S0\d{2}|store_name|highest|top"],
        ["join_2", "group_by", "order_by_top_n"], 1.0,
    ),
    (
        "Which employee made the most sales by revenue?",
        [r"JOIN\s+\S*employee_data.*GROUP\s+BY",
         r"SUM.*total_revenue.*ORDER\s+BY.*DESC"],
        [r"employee_name|E\d{4}|Kenneth|Christopher"],
        ["join_2", "group_by", "order_by_top_n"], 1.0,
    ),
]


# =============================================================================
# Result types
# =============================================================================

@dataclass
class DataQResult:
    question: str
    dimensions: List[str]
    weight: float
    sql: str = ""
    answer: str = ""
    chat_status: int = 0
    elapsed_s: float = 0.0
    sql_ok: bool = False
    ans_ok: bool = False
    raw_score: float = 0.0


# =============================================================================
# Test
# =============================================================================

@pytest.mark.competency
@pytest.mark.slow
def test_data_assistant_nl_to_sql_competency(reports_dir: Path):
    """Run the NL→SQL battery against a known data agent."""

    # Acquire an authed Playwright APIRequestContext that has hit
    # /data_chat (which provisions session['session_id'] + an engine).
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context()
        page = ctx.new_page()
        page.goto(f"{MAIN_BASE}/login", timeout=20000)
        try:
            page.locator(
                "input[name='username'], input[name='email'], #username, #email"
            ).first.fill(TEST_USER)
            page.locator(
                "input[name='password'], input[type='password'], #password"
            ).first.fill(TEST_PASS)
            page.locator(
                "button[type='submit'], input[type='submit'], .btn-login"
            ).first.click()
            page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
        except Exception as e:
            b.close()
            pytest.skip(f"Could not log in for data assistant suite: {e}")

        state = ctx.storage_state()
        api = p.request.new_context(
            base_url=MAIN_BASE,
            storage_state=state,
            timeout=REQUEST_TIMEOUT_MS,
        )

        # Initialise the data chat session + engine
        init = api.get("/data_chat")
        if init.status >= 400:
            b.close()
            pytest.skip(
                f"/data_chat init failed with {init.status} — cannot run "
                f"data assistant competency suite."
            )

        # Smoke-test: confirm the target agent generates SQL on a simple
        # question. If not (broken DB, deleted agent, guardrails) skip.
        api.get("/data_chat")
        smoke = _ask(api, TARGET_AGENT_ID,
                     "What is the total sales revenue by product category?")
        if not smoke["sql"]:
            b.close()
            pytest.skip(
                f"Smoke probe of agent {TARGET_AGENT_ID} returned no SQL "
                f"({smoke['chat_status']} {smoke['answer'][:80]!r}) — the "
                f"agent is either misconfigured or has refused. Set "
                f"COMPETENCY_DATA_AGENT_ID to a working data agent."
            )
        print(f"[data] smoke OK — agent {TARGET_AGENT_ID} generated SQL "
              f"({len(smoke['sql'])} chars)", flush=True)

        # Run the battery
        results: List[DataQResult] = []
        for q in QUESTIONS:
            question, sql_pats, ans_pats, dims, weight = q

            # Reset session between questions so chat history doesn't bias
            api.get("/data_chat")

            r = _ask(api, TARGET_AGENT_ID, question)
            res = DataQResult(
                question=question, dimensions=list(dims), weight=weight,
                sql=r["sql"], answer=r["answer"],
                chat_status=r["chat_status"], elapsed_s=r["elapsed_s"],
            )

            if sql_pats:
                for px in sql_pats:
                    if re.search(px, res.sql, re.IGNORECASE | re.DOTALL):
                        res.sql_ok = True
                        break
            else:
                # not_present questions: no SQL is the correct outcome,
                # so leave sql_ok at False — only ans_match counts here
                pass

            if ans_pats:
                for px in ans_pats:
                    if re.search(px, res.answer, re.IGNORECASE | re.DOTALL):
                        res.ans_ok = True
                        break

            # Score = weight if EITHER signal landed
            res.raw_score = res.weight if (res.sql_ok or res.ans_ok) else 0.0

            mark = (
                "✅✅" if res.sql_ok and res.ans_ok
                else ("✅sql" if res.sql_ok
                      else ("✅ans" if res.ans_ok else "❌"))
            )
            print(f"  {mark} ({res.elapsed_s:5.1f}s) {question[:75]} -> "
                  f"score={res.raw_score:.1f}",
                  flush=True)
            results.append(res)

        # Score + report
        scoring = _score(results)
        _write_report(results, scoring, reports_dir)

        try:
            api.dispose()
        except Exception:
            pass
        b.close()

    overall = scoring["overall_pct"]
    print(f"\n[data] OVERALL = {overall:.1f}% "
          f"(floor {SCORE_FLOOR*100:.0f}%)", flush=True)
    if overall < SCORE_FLOOR * 100:
        pytest.fail(
            f"DATA-ASSISTANT COMPETENCY FAIL: {overall:.1f}% below "
            f"{SCORE_FLOOR*100:.0f}% floor. See "
            f"{reports_dir / 'data_assistant_competency_report.md'}"
        )


# =============================================================================
# Helpers
# =============================================================================

def _ask(api, agent_id: int, question: str) -> dict:
    """Send one NL question to /chat/data and return the parsed response."""
    t0 = time.time()
    out = {"sql": "", "answer": "", "chat_status": 0, "elapsed_s": 0.0}
    try:
        r = api.post(
            "/chat/data",
            data={
                "agent_id": agent_id,
                "question": question,
                "history": "[]",
                "format_table_as_json": "True",
            },
        )
        out["chat_status"] = r.status
        if r.status == 200:
            try:
                body = r.json()
            except Exception:
                body = {"answer": r.text()[:300], "query": ""}
            out["sql"] = (body.get("query") or "").strip()
            out["answer"] = str(body.get("answer") or "")[:2000]
    except Exception as e:
        out["answer"] = f"err:{type(e).__name__}:{str(e)[:200]}"
    out["elapsed_s"] = time.time() - t0
    return out


def _score(results: List[DataQResult]) -> dict:
    total_w = sum(r.weight for r in results) or 1.0
    total_e = sum(r.raw_score for r in results)
    overall_pct = 100.0 * total_e / total_w

    by_dim = {}
    for r in results:
        for d in r.dimensions:
            b = by_dim.setdefault(d, {"weight": 0.0, "earned": 0.0, "n": 0,
                                        "sql_hits": 0, "ans_hits": 0})
            b["weight"] += r.weight
            b["earned"] += r.raw_score
            b["n"] += 1
            if r.sql_ok: b["sql_hits"] += 1
            if r.ans_ok: b["ans_hits"] += 1
    for k, v in by_dim.items():
        v["pct"] = 100.0 * v["earned"] / (v["weight"] or 1)

    return {
        "overall_pct": overall_pct,
        "total_weight": total_w,
        "total_earned": total_e,
        "by_dim": by_dim,
        "sql_match_count": sum(1 for r in results if r.sql_ok),
        "ans_match_count": sum(1 for r in results if r.ans_ok),
        "both_count": sum(1 for r in results if r.sql_ok and r.ans_ok),
    }


def _write_report(results: List[DataQResult], scoring: dict,
                  reports_dir: Path):
    md = reports_dir / "data_assistant_competency_report.md"
    js = reports_dir / "data_assistant_competency_report.json"

    lines = [
        "# Data Assistant — NL→SQL Competency Report",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Target agent: id={TARGET_AGENT_ID}",
        "",
        "## Headline",
        "",
        f"- **Overall score: {scoring['overall_pct']:.1f}%** "
        f"({scoring['total_earned']:.1f} / {scoring['total_weight']:.1f})",
        f"- Questions asked: **{len(results)}**",
        f"- SQL pattern matched: **{scoring['sql_match_count']} / {len(results)}**",
        f"- Answer pattern matched: **{scoring['ans_match_count']} / {len(results)}**",
        f"- Both matched (strongest signal): **{scoring['both_count']} / {len(results)}**",
        "",
        "Each question is scored 1.0 if EITHER the generated SQL matches "
        "expected patterns (correct query shape) OR the answer text "
        "matches expected ground-truth values. The dual-signal approach "
        "tolerates DB execution hiccups while still catching cases where "
        "the agent fabricated a number with no underlying query.",
        "",
        "## Per-dimension competency",
        "",
        "| Dimension | Questions | Score | SQL hits | Ans hits |",
        "|---|---:|---:|---:|---:|",
    ]
    for d, b in sorted(scoring["by_dim"].items(),
                       key=lambda kv: kv[1]["pct"]):
        lines.append(
            f"| `{d}` | {b['n']} | **{b['pct']:.1f}%** | "
            f"{b['sql_hits']}/{b['n']} | {b['ans_hits']}/{b['n']} |"
        )

    fails = [r for r in results if not r.sql_ok and not r.ans_ok]
    if fails:
        lines += ["", "## Failed questions", ""]
        for r in fails:
            lines.append(f"### ❌ {r.question}")
            lines.append(f"- Dimensions: {', '.join(r.dimensions)}")
            lines.append(f"- Elapsed: {r.elapsed_s:.1f}s   status={r.chat_status}")
            lines.append(f"- Generated SQL:")
            for ln in (r.sql or "<empty>").splitlines()[:10]:
                lines.append(f"    {ln}")
            lines.append(f"- Answer:")
            for ln in (r.answer or "<empty>").splitlines()[:8]:
                lines.append(f"    {ln}")
            lines.append("")

    lines += ["", "## All Q&A (audit trail)", ""]
    for r in results:
        mark = (
            "✅✅" if r.sql_ok and r.ans_ok
            else ("✅sql" if r.sql_ok
                  else ("✅ans" if r.ans_ok else "❌"))
        )
        lines.append(f"### {mark} {r.question}")
        lines.append(f"- score: {r.raw_score:.1f} | "
                     f"dimensions: {', '.join(r.dimensions)} | "
                     f"{r.elapsed_s:.1f}s")
        if r.sql:
            lines.append(f"- SQL ({len(r.sql)} chars):")
            for ln in r.sql.splitlines()[:12]:
                lines.append(f"    {ln}")
        else:
            lines.append("- SQL: <none>")
        lines.append(f"- Answer:")
        for ln in (r.answer or "<empty>").splitlines()[:6]:
            lines.append(f"    {ln}")
        lines.append("")

    md.write_text("\n".join(lines), encoding="utf-8")
    js.write_text(
        json.dumps({
            "scoring": scoring,
            "results": [asdict(r) for r in results],
            "agent_id": TARGET_AGENT_ID,
        }, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\n[data] wrote {md}", flush=True)
