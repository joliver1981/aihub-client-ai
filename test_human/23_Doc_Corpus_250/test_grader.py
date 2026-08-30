"""Test the grader before trusting a single number it prints.

Pack 13 published two engine comparisons that were twenty points wrong because the grader
had a bad regex. Pack 19's lesson was recorded as, literally, "test the judge". So this
builds two answer sheets from ground truth -- one PERFECT, one DELIBERATELY DEGRADED -- and
asserts the grader scores each the way it must.

    python test_grader.py [--out C:\\temp\\doc_corpus_250]

A grader that cannot tell a perfect run from a broken one cannot tell you anything.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def perfect_answer(q):
    e = q["expected"]
    mode = e["mode"]
    if mode == "set":
        extra = " ".join(e.get("must_also_contain") or [])
        return f"The following stores qualify: {', '.join(e['value'])}. {extra}"
    if mode == "exact":
        return f"Based on the documents, the answer is {e['value']}."
    if mode == "contains_all":
        return "From the agreements: " + "; ".join(str(v) for v in e["value"])
    if mode == "contains_none":
        return ("Only the executed store leases are responsive here; I have excluded the "
                "vendor service agreements and equipment schedules.")
    if mode == "refusal":
        return ("The documents provided do not contain information answering this question. "
                "I could not find anything on this topic in the corpus.")
    if mode == "conflict":
        return (f"The documents conflict: one states {e['value'][0]} and another states "
                f"{e['value'][1]}. These two figures disagree and should be reconciled "
                f"against the executed lease.")
    return ""


def degraded_answer(q):
    """What a plausibly-broken engine returns: partial fan-out, distractors cited,
    confident answers where it should refuse, one value where two conflict."""
    e = q["expected"]
    mode = e["mode"]
    if mode == "set":
        half = e["value"][: max(1, len(e["value"]) // 2)]
        return f"The following stores qualify: {', '.join(half)}, and also S999."
    if mode == "exact":
        return "The answer is 3 units, approximately."
    if mode == "contains_all":
        return "The rate is $131 per hour under Statement of Work No. 2."
    if mode == "contains_none":
        return ("Per the HVAC Preventive Maintenance Agreement and the Roof System Warranty, "
                "the landlord is responsible. See also the Equipment Lease Schedule.")
    if mode == "refusal":
        return "The average across the portfolio is 34% based on the documents reviewed."
    if mode == "conflict":
        return f"The monthly base rent is {e['value'][0]}."
    return ""


def run(answers, tag, out):
    path = os.path.join(out, f"_selftest_{tag}.json")
    json.dump(answers, open(path, "w", encoding="utf-8"), indent=1)
    p = subprocess.run([sys.executable, os.path.join(HERE, "grade.py"),
                        "--answers", path, "--out", out,
                        "--report", os.path.join(out, f"_selftest_{tag}_report.json")],
                       capture_output=True, text=True)
    if p.returncode != 0:
        print(p.stdout, p.stderr)
        raise SystemExit(f"grader crashed on the {tag} sheet")
    return json.load(open(os.path.join(out, f"_selftest_{tag}_report.json"), encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=r"C:\temp\doc_corpus_250")
    a = ap.parse_args()
    qb = json.load(open(os.path.join(a.out, "questions.json"), encoding="utf-8"))
    qs = qb["questions"]

    fails = []

    perfect = run([{"id": q["id"], "answer": perfect_answer(q)} for q in qs], "perfect", a.out)
    t = perfect["totals"]
    print(f"PERFECT sheet   -> pass {t['pass']}/{t['n']}  fail {t['fail']}  review {t['review']}")
    if t["fail"]:
        bad = [r["id"] for r in perfect["rows"] if r["pass"] is False]
        fails.append(f"grader FAILED {t['fail']} question(s) on a perfect sheet: {bad}")
    fan = [r for r in perfect["rows"] if r["cls"] == "fanout"]
    if any(r["recall"] < 1.0 or r["precision"] < 1.0 for r in fan):
        off = [(r["id"], r["recall"], r["precision"]) for r in fan
               if r["recall"] < 1.0 or r["precision"] < 1.0]
        fails.append(f"fan-out not scored 100% on a perfect sheet: {off[:5]}")

    degraded = run([{"id": q["id"], "answer": degraded_answer(q)} for q in qs], "degraded", a.out)
    d = degraded["totals"]
    print(f"DEGRADED sheet  -> pass {d['pass']}/{d['n']}  fail {d['fail']}  review {d['review']}")
    if d["pass"] > d["n"] * 0.12:
        fails.append(f"grader passed {d['pass']} of {d['n']} on a deliberately broken sheet "
                     f"-- it is not discriminating")

    dfan = [r for r in degraded["rows"] if r["cls"] == "fanout"]
    mean_recall = sum(r["recall"] for r in dfan) / len(dfan)
    if mean_recall > 0.65:
        fails.append(f"degraded fan-out recall came out {mean_recall:.0%}; the half-answer "
                     f"sheet should score near 50%")
    # S999 has the shape of a store ID but no document behind it. The grader must surface it
    # as a hallucinated id AND count it against precision, not quietly drop it.
    if not any(r["unknown_ids"] for r in dfan):
        fails.append("grader did not surface the injected S999 as an unknown store id")
    if not all(r["precision"] < 1.0 for r in dfan):
        fails.append("a hallucinated store id did not cost the answer any precision")

    dref = [r for r in degraded["rows"] if r["cls"] == "negative"]
    if any(r["pass"] is True for r in dref):
        fails.append("grader passed a hallucinated answer on a refusal question")

    dprec = [r for r in degraded["rows"] if r["cls"] == "precision"]
    if any(r["pass"] is True for r in dprec):
        fails.append("grader passed an answer that cited the near-miss distractors")

    print(f"\ndegraded fan-out mean recall {mean_recall:.1%} (expected ~50%)")
    print("=" * 60)
    if fails:
        print("GRADER SELF-TEST FAILED:")
        for f in fails:
            print("  -", f)
        return 1
    print("GRADER SELF-TEST PASSED -- it separates a perfect run from a broken one")
    return 0


if __name__ == "__main__":
    sys.exit(main())
