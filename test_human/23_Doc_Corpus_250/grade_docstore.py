"""Grade the pack-23 question bank as answered by the DOCUMENT STORE lane.

    python grade_docstore.py [--out C:\\temp\\doc_corpus_250]

A separate grader from `grade.py`, deliberately. That one grades the agent-knowledge lane,
where a fan-out answer is a LIST OF STORE IDS. This lane answers fan-out through
`doc_search_v3.enumerate`, which returns a DENOMINATOR and a COUNT BREAKDOWN and never
names the documents. Running the ID-set grader here would score 0% recall on answers that
are substantially correct — a grader artifact, which is the one failure mode this pack
exists to avoid. So fan-out is graded on the count, and the difference is stated rather
than hidden.

Lookup questions (needle, multihop) return PASSAGES, not a synthesised answer. Finding the
planted fact inside the returned passages is therefore a clean measurement of RETRIEVAL,
separate from answer synthesis. That is what is scored, and it is labelled as such.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from grade import (grade_contains, grade_refusal, grade_conflict, norm)   # noqa: E402

DENOM_RE = re.compile(r"COUNT over (\d+) document\(s\)\s*\(([^)]*)\)")
BREAK_RE = re.compile(r"^\s{2,}(\d+)\s\s+(.+?)\s*$", re.M)
PRED_RE = re.compile(r"Predicate applied to each document:\s*(.+?)\s*$", re.M)
VERDICT_RE = re.compile(r"VERDICTS \(enumerated documents\):(.*?)(?:\nBY DOCUMENT TYPE|\Z)",
                        re.S)
VBUCKET_RE = re.compile(r"^\s+(\d+)\s+(yes_qualified|yes|no|not_addressed)\s*$", re.M)

# The engine restates every question as ONE affirmative predicate and answers it per
# document with a normalized verdict. Which bucket holds the answer therefore depends on
# how the question relates to that predicate:
#   affirmative ("which leases make HVAC the landlord's responsibility") -> yes
#   partial     ("which leases SPLIT it")                               -> yes_qualified
#   negative    ("which have NO cap", "which never address it")         -> no / not_addressed
# dim_values that are absences are listed here; everything else is treated as affirmative.
ABSENCE_VALUES = {"none", "silent", "0"}
PARTIAL_VALUES = {"split"}


def parse_verdicts(ans):
    m = VERDICT_RE.search(ans or "")
    if not m:
        return {}
    return {k: int(n) for n, k in VBUCKET_RE.findall(m.group(1))}


def predicate_of(ans):
    m = PRED_RE.search(ans or "")
    return m.group(1).strip() if m else ""


def parse_enumerate(ans):
    """Pull the denominator, the scoped types, and the count breakdown out of the
    enumerate lane's fixed answer shape."""
    m = DENOM_RE.search(ans or "")
    denom = int(m.group(1)) if m else None
    types = [t.strip() for t in m.group(2).split(",")] if m else []
    buckets = [(int(c), lbl.strip()) for c, lbl in BREAK_RE.findall(ans or "")]
    return denom, types, buckets


def bucket_for(buckets, value):
    """Count documents whose verdict label IS the requested value.

    Exact/prefix match only. The tail of bespoke prose labels the engine emits is
    reported separately rather than guessed into a bucket — assigning them by keyword
    would be the grader inventing agreement that is not in the answer.
    """
    v = norm(value)
    hit, tail = 0, []
    for n, lbl in buckets:
        L = norm(lbl)
        if L == v or L.startswith(v + " ") or L.startswith(v + " -"):
            hit += n
        elif L in ("not addressed", "silent", "not mentioned", "none", "unknown"):
            continue
        elif len(L.split()) > 4:
            tail.append((n, lbl))
    return hit, tail


def grade_fanout(ans, q):
    """Score the normalized VERDICTS roll-up, not the free-text ANSWER BREAKDOWN.

    The breakdown fragments near-identical verdicts into separate buckets on nothing but
    capitalisation ("CAM costs" vs "CAM Costs"), so counting it understates every answer.
    That fragmentation is reported as its own finding; it is not used for scoring.
    """
    exp_n = len(q["expected"]["value"])
    denom, types, buckets = parse_enumerate(ans)
    verdicts = parse_verdicts(ans)
    if denom is None or not verdicts:
        return {"pass": False, "mode": "verdicts", "reason": "not the enumerate shape",
                "expected_n": exp_n, "reported_n": None, "denominator": denom}

    val = norm(q.get("dim_value") or "")
    if val in ABSENCE_VALUES:
        bucket = "not_addressed" if val == "silent" else "no"
    elif val in PARTIAL_VALUES:
        bucket = "yes_qualified"
    else:
        bucket = "yes"
    got = verdicts.get(bucket, 0)
    err = abs(got - exp_n)
    tol = max(3, round(exp_n * 0.10))

    _g, tail = bucket_for(buckets, q.get("dim_value") or "")
    return {"pass": err <= tol, "mode": "verdicts", "expected_n": exp_n, "reported_n": got,
            "bucket_used": bucket, "verdicts": verdicts, "abs_error": err, "tolerance": tol,
            "denominator": denom, "types": types,
            "predicate": predicate_of(ans)[:110],
            "breakdown_buckets": len(buckets),
            "unbucketed_tail": sum(n for n, _ in tail)}


def grade_aggregate(ans, q):
    exp = q["expected"]["value"]
    if q["expected"]["mode"] == "set":
        return grade_fanout(ans, q)
    nums = [int(x.replace(",", "")) for x in re.findall(r"\b\d[\d,]*\b", str(ans))]
    want = int(exp) if str(exp).isdigit() else None
    if want is None:
        return {"pass": None, "mode": "exact", "review": f"non-numeric expected {exp!r}"}
    denom, _t, buckets = parse_enumerate(ans)
    got = None
    if buckets:
        got, _ = bucket_for(buckets, q.get("dim_value") or "")
    hit = (got == want) if got is not None else (want in nums)
    return {"pass": hit, "mode": "exact", "expected": want, "reported": got,
            "numbers_seen": nums[:8], "denominator": denom}


def grade_retrieval(ans, q):
    """Did the planted fact reach the caller at all? Token overlap against the passages."""
    want = norm(q["expected"]["value"])
    hay = norm(ans)
    toks = [t for t in re.split(r"[\s()]+", want)
            if len(t) > 2 and t not in ("the", "and", "for", "not", "its", "per")]
    hit = [t for t in toks if t in hay]
    ratio = len(hit) / len(toks) if toks else 0.0
    return {"pass": ratio >= 0.8, "mode": "retrieval", "token_recall": round(ratio, 3),
            "matched": len(hit), "total": len(toks),
            "missing": [t for t in toks if t not in hay][:6]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=r"C:\temp\doc_corpus_250")
    a = ap.parse_args()

    qb = json.load(open(os.path.join(a.out, "questions.json"), encoding="utf-8"))
    qmap = {x["id"]: x for x in qb["questions"]}
    answers = {x["id"]: x["answer"] for x in
               json.load(open(os.path.join(a.out, "answers_docstore.json"), encoding="utf-8"))}
    meta = json.load(open(os.path.join(a.out, "routing_docstore.json"), encoding="utf-8"))

    rows = []
    for qid, ans in answers.items():
        q = qmap.get(qid)
        if not q:
            continue
        cls, mode = q["class"], q["expected"]["mode"]
        if meta.get(qid, {}).get("error"):
            r = {"pass": False, "mode": "error", "reason": meta[qid]["error"][:80]}
        elif cls == "fanout":
            r = grade_fanout(ans, q)
        elif cls == "aggregate":
            r = grade_aggregate(ans, q)
        elif cls in ("needle", "multihop"):
            r = grade_retrieval(ans, q) if mode == "exact" else grade_contains(ans, q, True)
        elif mode == "contains_none":
            r = grade_contains(ans, q, False)
        elif mode == "refusal":
            r = grade_refusal(ans)
        elif mode == "conflict":
            r = grade_conflict(ans, q)
        elif cls == "precision":
            r = grade_fanout(ans, q)
        else:
            r = grade_retrieval(ans, q)
        rows.append(dict(id=qid, cls=cls, engine=meta.get(qid, {}).get("engine"),
                         secs=meta.get(qid, {}).get("secs"), **r))

    by = {}
    for r in rows:
        b = by.setdefault(r["cls"], {"n": 0, "pass": 0, "fail": 0, "review": 0})
        b["n"] += 1
        b["pass" if r["pass"] is True else ("fail" if r["pass"] is False else "review")] += 1

    print(f"\n{'class':<11}{'n':>4}{'pass':>7}{'fail':>7}{'review':>8}   engine")
    print("-" * 62)
    for k in sorted(by):
        b = by[k]
        eng = {r["engine"] for r in rows if r["cls"] == k and r["engine"]}
        print(f"{k:<11}{b['n']:>4}{b['pass']:>7}{b['fail']:>7}{b['review']:>8}   "
              f"{', '.join(sorted(e or '?' for e in eng))[:34]}")
    tot = {k: sum(b[k] for b in by.values()) for k in ("n", "pass", "fail", "review")}
    print("-" * 62)
    print(f"{'TOTAL':<11}{tot['n']:>4}{tot['pass']:>7}{tot['fail']:>7}{tot['review']:>8}")

    fan = [r for r in rows if r["cls"] in ("fanout", "precision") and r.get("denominator")]
    if fan:
        errs = [r["abs_error"] for r in fan if r.get("abs_error") is not None]
        dens = {r["denominator"] for r in fan}
        print(f"\nfan-out count error: mean {sum(errs) / len(errs):.1f} documents, "
              f"max {max(errs)}  (n={len(errs)})")
        print(f"denominators the engine scoped to: {sorted(dens)}")
        tail = sum(r.get("unbucketed_tail") or 0 for r in fan)
        print(f"verdicts that did not land in a clean bucket: {tail} across {len(fan)} questions")

    path = os.path.join(a.out, "grade_docstore_report.json")
    json.dump({"by_class": by, "totals": tot, "rows": rows},
              open(path, "w", encoding="utf-8"), indent=1)
    print(f"\nreport -> {path}")


if __name__ == "__main__":
    main()
