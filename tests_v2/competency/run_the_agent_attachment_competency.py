"""
The Agent — chat-attachment competency runner (the apples-to-apples experiment).

WHY THIS EXISTS
---------------
The PDF / Word / Excel competency batteries measure the ONE thing most users
actually do: drag a file into chat and ask questions about it. Until now they
only ever ran against the General Agent lane
(`/add/agent_knowledge` + `/api/agents/<id>/chat`), which is why GA is the only
lane with numbers on the dominant workload.

This runner drives THE SAME batteries — same fixtures, same questions, same
accept/negative regexes, same weights, same LLM-grader fallback — against
The Agent's chat-attachment path instead:

    POST :5111/api/uploads   (raw bytes + X-File-Name)   -> file_id
    POST :5111/api/chat      {message, attachments:[...]} -> SSE stream

Nothing about the batteries is copied or re-typed: the QUESTIONS lists are
IMPORTED from the GA suite modules, and scoring reuses `_runner._score`. If a
question changes there, it changes here. Zero drift, by construction.

PARITY DECISIONS (each one deliberate — read before comparing numbers)
---------------------------------------------------------------------
1. STATELESS TURNS. The GA runner sends `history=[]` on every question
   (`_chat_helpers.ask_with_followup`), so each question is an independent
   turn against an agent whose knowledge base already holds every fixture.
   The Agent's equivalent is a FRESH SESSION per question (`session_id=None`).
   This also keeps a 50-page PDF read on question 3 from inflating the context
   of questions 4..25 — the conversation-budget gap (plan P3) is a real issue
   but it is NOT what these batteries measure.

2. WHOLE CORPUS ATTACHED. GA has all fixtures in its knowledge base for every
   question, so the agent must pick the right document. The Agent gets every
   fixture's file_id attached to every turn — same difficulty, same
   cross-document disambiguation, same hidden-sheet leak exposure. The Agent
   only pays for what it chooses to read (attachments ride in as PATHS, not
   content), so this is cheap as well as faithful.

3. NO PROMPT HELP. The GA suites create an agent with a short "answer from the
   uploaded documents, don't guess" system prompt. The Agent brings its own
   platform doctrine and we do NOT prepend anything to the questions — the
   user's words go in verbatim, exactly as the GA battery sends them. That
   difference is a property of the two products and is reported, not patched.

4. SAME GRADER. Regex fast-path, then the mini-LLM fallback from
   `_llm_grader`, then negative-pattern leak override — byte-identical logic,
   imported from the same module the GA numbers came from.

EXTRA SIGNAL THE GA LANE CANNOT GIVE
------------------------------------
Every turn's tool calls are captured from the SSE stream, so the report can
distinguish "answered correctly after actually reading the file" from
"answered correctly having called no tool at all" (i.e. guessed, or recognised
the fixture). `no_tool_calls` on a correct answer is a soft warning, surfaced
in the report.

USAGE
-----
    $PY = "$env:USERPROFILE\\miniconda3\\envs\\aihub2.1\\python.exe"
    & $PY tests_v2\\competency\\run_the_agent_attachment_competency.py
    & $PY tests_v2\\competency\\run_the_agent_attachment_competency.py --suites pdf --role 1

Reports land next to the GA ones with a `the_agent_` prefix, so the GA
baselines are never overwritten:

    tests_v2/artifacts/competency/the_agent_pdf_competency_report.md|.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from dataclasses import asdict
from pathlib import Path

import requests

# --------------------------------------------------------------------------
# Path + env bootstrap (mirrors test_human/20_The_Agent/runner.py)
# --------------------------------------------------------------------------
APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(APP_ROOT / ".env"))
try:
    import secure_config  # noqa: E402

    secure_config.load_secure_config()
except Exception:
    pass

import shared_auth  # noqa: E402

# The battery modules + the shared scorer. Imported, never copied.
from tests_v2.competency._runner import QResult, _score  # noqa: E402
from tests_v2.competency._llm_grader import llm_grade_answer  # noqa: E402

AGENT_BASE = "http://127.0.0.1:{}".format(
    os.getenv("AGENT_SERVICE_PORT") or int(os.getenv("HOST_PORT", "5001")) + 110
)
BROWSER_TZ = "America/New_York"
TURN_TIMEOUT = int(os.getenv("AGENT_COMPETENCY_TURN_TIMEOUT", "420"))
REPORTS_DIR = APP_ROOT / "tests_v2" / "artifacts" / "competency"
FIXTURES_ROOT = APP_ROOT / "tests_v2" / "fixtures" / "docs"


# --------------------------------------------------------------------------
# Suite registry — fixtures + glob come from the GA suites' own constants
# --------------------------------------------------------------------------
def _load_suites():
    """Import the three GA batteries and return their QUESTIONS verbatim.

    The suite modules import pytest and `.conftest` at module scope; both are
    importable outside a pytest run (conftest only DEFINES fixtures), so a
    plain import is enough and keeps us honest about using the real battery.
    """
    from tests_v2.competency import (
        test_competency_agent_knowledge_pdf as pdf_suite,
        test_competency_agent_knowledge_word as word_suite,
        test_competency_agent_knowledge_excel as excel_suite,
    )

    return {
        "pdf": {
            "questions": pdf_suite.QUESTIONS,
            "fixtures_dir": pdf_suite.FIXTURES_DIR,
            "glob": "*.pdf",
        },
        "word": {
            "questions": word_suite.QUESTIONS,
            "fixtures_dir": word_suite.FIXTURES_DIR,
            "glob": "*.docx",
        },
        "excel": {
            "questions": excel_suite.QUESTIONS,
            "fixtures_dir": excel_suite.FIXTURES_DIR,
            "glob": "*.xlsx",
        },
    }


# --------------------------------------------------------------------------
# The Agent transport
# --------------------------------------------------------------------------
def mint_token(role: int, user_id: int):
    return shared_auth.sign_cc_token({
        "user_id": user_id,
        "role": role,
        "tenant_id": os.getenv("TENANT_ID", ""),
        "username": f"comp-attach-r{role}",
        "name": f"Attachment Competency (role {role})",
    })


def service_info(token: str) -> dict:
    r = requests.get(f"{AGENT_BASE}/api/me",
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def upload_fixture(token: str, path: Path) -> str:
    """POST raw bytes + URL-encoded X-File-Name; return the file_id."""
    data = path.read_bytes()
    r = requests.post(
        f"{AGENT_BASE}/api/uploads",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "X-File-Name": urllib.parse.quote(path.name),
            "Content-Type": "application/octet-stream",
        },
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["file_id"]


def chat_turn(token: str, message: str, attachments, timeout=TURN_TIMEOUT):
    """One stateless turn. Returns (text, tool_names, status, error)."""
    try:
        r = requests.post(
            f"{AGENT_BASE}/api/chat",
            json={
                "message": message,
                "session_id": None,          # fresh session — GA parity
                "attachments": list(attachments),
                "timezone": BROWSER_TZ,
            },
            headers={"Authorization": f"Bearer {token}"},
            stream=True,
            timeout=(10, timeout),
        )
    except Exception as e:
        return "", [], 0, f"{type(e).__name__}: {e}"

    if r.status_code != 200:
        return "", [], r.status_code, r.text[:300]

    texts, tools, err = [], [], None
    try:
        for raw in r.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data: "):
                continue
            try:
                ev = json.loads(raw[6:])
            except Exception:
                continue
            t = ev.get("type")
            if t == "text":
                texts.append(ev.get("text", ""))
            elif t == "tool":
                tools.append(str(ev.get("name", "")).replace("mcp__aihub__", ""))
            elif t == "error":
                err = str(ev.get("error"))[:300]
            elif t == "done":
                break
    except Exception as e:
        err = err or f"stream {type(e).__name__}: {e}"

    return "\n".join(texts).strip(), tools, r.status_code, err


# --------------------------------------------------------------------------
# One suite
# --------------------------------------------------------------------------
def run_suite(name: str, spec: dict, token: str, model_label: str,
              role: int) -> dict:
    questions = spec["questions"]
    fixtures_dir: Path = spec["fixtures_dir"]

    print(f"\n{'=' * 74}\n[{name}] The Agent attachment competency — "
          f"{len(questions)} questions, model={model_label}, role={role}\n"
          f"{'=' * 74}", flush=True)

    # --- upload every fixture once; reuse the ids on every turn ------------
    file_ids, uploaded = [], []
    for fpath in sorted(fixtures_dir.glob(spec["glob"])):
        if fpath.name.startswith("_"):
            continue
        t0 = time.time()
        try:
            fid = upload_fixture(token, fpath)
            file_ids.append(fid)
            uploaded.append((fpath.name, 200, time.time() - t0))
            print(f"[{name}] upload {fpath.name}: ok in "
                  f"{time.time() - t0:.1f}s", flush=True)
        except Exception as e:
            uploaded.append((fpath.name, 0, time.time() - t0))
            print(f"[{name}] upload {fpath.name}: FAILED {e}", flush=True)
    if not file_ids:
        raise SystemExit(f"[{name}] no fixtures uploaded — aborting")

    # --- ask each question, fresh session, whole corpus attached ----------
    results, extras = [], []
    for i, q in enumerate(questions, 1):
        fixture, question, accept, dimensions, negative, weight = q
        qr = QResult(fixture=fixture, question=question,
                     dimensions=list(dimensions), weight=weight)

        t0 = time.time()
        text, tools, status, err = chat_turn(token, question, file_ids)
        qr.elapsed_s = time.time() - t0
        qr.chat_status = status
        qr.answer = text or (f"<error: {err}>" if err else "")

        # --- identical grading ladder to the GA runner --------------------
        if negative:
            for nx in negative:
                if re.search(nx, qr.answer, re.IGNORECASE | re.DOTALL):
                    qr.leaked = True
                    break
        if not qr.leaked:
            for px in accept:
                if re.search(px, qr.answer, re.IGNORECASE | re.DOTALL):
                    qr.matched = True
                    break
        graded_by_llm = False
        if not qr.matched and not qr.leaked and qr.answer.strip():
            try:
                verdict = llm_grade_answer(question=question,
                                           agent_answer=qr.answer,
                                           expected_patterns=list(accept))
            except Exception as e:
                print(f"  [warn] LLM grader raised: {e}", flush=True)
                verdict = None
            if verdict is True:
                qr.matched = True
                graded_by_llm = True

        qr.raw_score = qr.weight if (qr.matched and not qr.leaked) else 0.0

        mark = ("🚨LEAK" if qr.leaked
                else ("✅🤖" if (qr.matched and graded_by_llm)
                      else ("✅" if qr.matched else "❌")))
        no_tools = " ⚠no-tools" if (qr.matched and not tools) else ""
        print(f"  [{i:>2}/{len(questions)}] {mark}{no_tools} ({fixture}) "
              f"{question[:62]} -> {qr.raw_score:.1f} "
              f"({qr.elapsed_s:.1f}s, tools={tools or '-'})", flush=True)

        results.append(qr)
        extras.append({
            "fixture": fixture, "question": question,
            "tools": tools, "graded_by_llm": graded_by_llm,
            "stream_error": err, "no_tool_calls": not tools,
        })

    scoring = _score(results)
    write_report(name, results, extras, scoring, uploaded,
                 model_label, role)
    print(f"\n[{name}] OVERALL = {scoring['overall_pct']:.1f}%  "
          f"leaks={scoring['leak_count']}", flush=True)
    return {"suite": name, "scoring": scoring, "extras": extras}


# --------------------------------------------------------------------------
# Report — same shape as the GA reports so they can sit side by side
# --------------------------------------------------------------------------
def write_report(name, results, extras, scoring, uploaded, model_label, role):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    base = f"the_agent_{name}_competency_report"
    md, js = REPORTS_DIR / f"{base}.md", REPORTS_DIR / f"{base}.json"

    silent = [e for e, r in zip(extras, results)
              if r.matched and e["no_tool_calls"]]
    errored = [e for e in extras if e["stream_error"]]

    lines = [
        f"# The Agent (chat attachments) — {name.title()} Competency Report",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Transport: `POST {AGENT_BASE}/api/uploads` + "
        f"`POST {AGENT_BASE}/api/chat` (fresh session per question, "
        f"whole corpus attached)",
        f"Model: **{model_label}** (role {role})",
        f"Battery: imported verbatim from "
        f"`test_competency_agent_knowledge_{name}.py` — same questions, "
        f"regexes and weights as the General Agent run.",
        "",
        "## Headline",
        "",
        f"- **Overall score: {scoring['overall_pct']:.1f}%** "
        f"({scoring['total_earned']:.1f} / {scoring['total_weight']:.1f} "
        f"weighted points)",
        f"- Questions asked: **{len(results)}**",
        f"- Fixtures uploaded: **{len(uploaded)}**",
        f"- Leaks / forbidden-pattern hits: **{scoring['leak_count']}** "
        f"{'🚨' if scoring['leak_count'] else '✅'}",
        f"- Correct answers with **no tool call at all**: "
        f"**{len(silent)}** {'⚠️' if silent else '✅'}",
        f"- Turns with a stream error: **{len(errored)}**",
        "",
        "## Per-fixture competency",
        "",
        "| Fixture | Questions | Score | Earned/Weight |",
        "|---|---:|---:|---|",
    ]
    for fname, b in sorted(scoring["by_file"].items()):
        lines.append(f"| `{fname}` | {b['n']} | **{b['pct']:.1f}%** | "
                     f"{b['earned']:.1f}/{b['weight']:.1f} |")

    lines += ["", "## Per-dimension competency", "",
              "| Dimension | Questions | Score | Earned/Weight |",
              "|---|---:|---:|---|"]
    for d, b in sorted(scoring["by_dim"].items(), key=lambda kv: kv[1]["pct"]):
        lines.append(f"| `{d}` | {b['n']} | **{b['pct']:.1f}%** | "
                     f"{b['earned']:.1f}/{b['weight']:.1f} |")

    lines += ["", "## Tool usage (grounding evidence)", "",
              "| # | Fixture | Correct | Tools called |", "|---:|---|:--:|---|"]
    for i, (r, e) in enumerate(zip(results, extras), 1):
        ok = "🚨" if r.leaked else ("✅" if r.matched else "❌")
        lines.append(f"| {i} | `{r.fixture}` | {ok} | "
                     f"{', '.join(e['tools']) or '**none**'} |")

    fails = [(r, e) for r, e in zip(results, extras)
             if not r.matched or r.leaked]
    if fails:
        lines += ["", "## Failed / leaked questions", ""]
        for r, e in fails:
            lines.append(f"### {'🚨 LEAK' if r.leaked else '❌ FAIL'} — "
                         f"`{r.fixture}` — {r.question}")
            lines.append(f"- Dimensions: {', '.join(r.dimensions)} | "
                         f"weight {r.weight} | status {r.chat_status} | "
                         f"{r.elapsed_s:.1f}s")
            lines.append(f"- Tools: {', '.join(e['tools']) or 'none'}")
            if e["stream_error"]:
                lines.append(f"- Stream error: `{e['stream_error']}`")
            lines.append("- Answer:")
            for ln in (r.answer or "<no answer>").splitlines():
                lines.append(f"    {ln}")
            lines.append("")

    lines += ["", "## All Q&A (for audit)", ""]
    for r, e in zip(results, extras):
        mark = "🚨" if r.leaked else ("✅" if r.matched else "❌")
        lines.append(f"### {mark} `{r.fixture}` — {r.question}")
        lines.append(f"- score {r.raw_score:.1f} | "
                     f"{', '.join(r.dimensions)} | {r.elapsed_s:.1f}s | "
                     f"tools: {', '.join(e['tools']) or 'none'}")
        lines.append("- answer:")
        for ln in (r.answer or "<no answer>").splitlines()[:8]:
            lines.append(f"    {ln}")
        lines.append("")

    md.write_text("\n".join(lines), encoding="utf-8")
    js.write_text(json.dumps({
        "lane": "the_agent_chat_attachments",
        "suite": name,
        "model": model_label,
        "role": role,
        "transport": {"upload": f"{AGENT_BASE}/api/uploads",
                      "chat": f"{AGENT_BASE}/api/chat",
                      "session": "fresh per question",
                      "attachments": "whole corpus"},
        "scoring": scoring,
        "results": [asdict(r) for r in results],
        "extras": extras,
        "uploaded": uploaded,
    }, indent=2, default=str), encoding="utf-8")
    print(f"[{name}] wrote {md}", flush=True)


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suites", default="pdf,word,excel",
                    help="comma list: pdf,word,excel")
    ap.add_argument("--role", type=int, default=3,
                    help="JWT role. 3 = admin/power user (AGENT_MODEL); "
                         "1 = regular user (AGENT_MODEL_ROLE1)")
    ap.add_argument("--user-id", type=int, default=1)
    args = ap.parse_args()

    token = mint_token(args.role, args.user_id)
    try:
        info = service_info(token)
    except Exception as e:
        raise SystemExit(f"The Agent at {AGENT_BASE} unreachable/refused: {e}")
    model_label = (info.get("model_role1") if args.role < 2
                   else info.get("model")) or "unknown"
    print(f"The Agent {AGENT_BASE} — user={info.get('user')} "
          f"model={model_label}", flush=True)

    suites = _load_suites()
    summary = []
    for nm in [s.strip() for s in args.suites.split(",") if s.strip()]:
        if nm not in suites:
            print(f"[skip] unknown suite {nm}", flush=True)
            continue
        t0 = time.time()
        try:
            out = run_suite(nm, suites[nm], token, model_label, args.role)
            out["elapsed_min"] = (time.time() - t0) / 60.0
            summary.append(out)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[{nm}] SUITE ERROR: {e}", flush=True)

    print(f"\n{'=' * 74}\nSUMMARY — The Agent chat attachments "
          f"(model {model_label}, role {args.role})\n{'=' * 74}")
    print(f"{'suite':<8}{'score':>9}{'leaks':>8}{'no-tool':>9}{'minutes':>10}")
    for s in summary:
        silent = sum(1 for e in s["extras"] if e["no_tool_calls"])
        print(f"{s['suite']:<8}{s['scoring']['overall_pct']:>8.1f}%"
              f"{s['scoring']['leak_count']:>8}{silent:>9}"
              f"{s['elapsed_min']:>10.1f}")


if __name__ == "__main__":
    main()
