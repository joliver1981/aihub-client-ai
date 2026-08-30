"""Deterministic grader for the pack-23 question bank.

    python grade.py --answers answers.json [--out C:\\temp\\doc_corpus_250]

`answers.json` is a list of {"id": "Q001", "answer": "<the agent's reply text>"}.

THE GRADER IS THE MOST DANGEROUS FILE HERE. In pack 13 a store-ID regex that only matched
`S1\\d\\d` silently hid twenty stores and made two engine runs look twenty points worse than
they were; the numbers were published before anyone noticed. So:

  * store IDs are validated against the ACTUAL id universe read from ground truth, never
    against a pattern typed by hand;
  * every mode that cannot be settled by arithmetic is routed to REVIEW rather than guessed;
  * the report prints what it could not decide, instead of scoring it zero and moving on.

Refusal and conflict handling are deliberately conservative: a wrong "pass" on a
hallucination test is worse than a question you have to read yourself.
"""
import argparse
import json
import os
import re
import sys

NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
    "thirteen": "13", "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "twenty-five": "25", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
    "ninety": "90", "hundred": "100",
}

REFUSAL_MARKERS = [
    "no information", "not found", "does not contain", "do not contain", "not available",
    "unable to determine", "cannot determine", "no documents", "not addressed", "nothing in",
    "isn't in", "is not in", "no data", "not present", "couldn't find", "could not find",
    "don't have", "do not have", "no mention", "not specified", "insufficient",
]
CONFLICT_MARKERS = [
    "conflict", "disagree", "discrepan", "inconsisten", "differ", "contradict",
    "two different", "not reconcile", "however", "whereas", "versus",
]


def norm(s):
    s = str(s).lower()
    for w, d in NUMBER_WORDS.items():
        s = re.sub(rf"\b{w}\b", d, s)
    s = s.replace(",", "").replace("$", " $ ")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s$%.\-/]", " ", s)).strip()


def ids_in(text, universe):
    """Store IDs mentioned, split into real ones and ones that do not exist.

    Validated against the ACTUAL universe read from ground truth -- never a hand-written
    range, which is the exact mistake that corrupted pack 13's published numbers. IDs with
    the right shape but no matching document are returned separately: they are hallucinated
    stores or OCR damage, and either way they count against the answer.
    """
    seen = set(re.findall(r"\bS\d{3}\b", text.upper()))
    return seen & universe, seen - universe


def grade_set(ans, qrec, universe):
    exp = set(qrec["expected"]["value"])
    got, unknown = ids_in(ans, universe)
    tp = exp & got
    fp = got - exp
    returned = len(got) + len(unknown)
    recall = len(tp) / len(exp) if exp else 1.0
    prec = len(tp) / returned if returned else (1.0 if not exp else 0.0)
    res = {"recall": round(recall, 4), "precision": round(prec, 4),
           "expected_n": len(exp), "returned_n": returned,
           "missed": sorted(exp - got), "false_positives": sorted(fp),
           "unknown_ids": sorted(unknown)}
    syn = set(qrec.get("synonym_only_subset") or [])
    if syn:
        res["synonym_only_recall"] = round(len(syn & got) / len(syn), 4)
        res["synonym_only_n"] = len(syn)
        res["synonym_only_missed"] = sorted(syn - got)
    also = qrec["expected"].get("must_also_contain") or []
    if also:
        res["value_stated"] = all(norm(a) in norm(ans) for a in also)
    res["pass"] = recall == 1.0 and prec == 1.0 and res.get("value_stated", True)
    return res


def grade_exact(ans, qrec):
    want = norm(qrec["expected"]["value"])
    hay = norm(ans)
    # Compare on the substantive tokens; an answer may legitimately add prose around them.
    toks = [t for t in re.split(r"[\s()]+", want) if len(t) > 1 and t not in ("the", "of", "and")]
    hit = [t for t in toks if t in hay]
    ok = len(hit) == len(toks)
    return {"pass": ok, "matched_tokens": len(hit), "total_tokens": len(toks),
            "missing_tokens": [t for t in toks if t not in hay]}


def grade_contains(ans, qrec, want_present):
    hay = norm(ans)
    vals = qrec["expected"]["value"]
    found = [v for v in vals if norm(v) in hay]
    ok = (len(found) == len(vals)) if want_present else (not found)
    key = "found" if want_present else "forbidden_found"
    return {"pass": ok, key: found, "checked": len(vals)}


def grade_refusal(ans):
    hay = norm(ans)
    refused = any(m in hay for m in REFUSAL_MARKERS)
    # A refusal that still quotes figures is a hedged hallucination, not a refusal.
    figures = re.findall(r"\$ ?\d[\d.]*|\b\d{2,}\b|\b\d+(?:\.\d+)?%", hay)
    if refused and not figures:
        return {"pass": True, "refused": True, "figures_cited": []}
    if refused and figures:
        return {"pass": None, "refused": True, "figures_cited": figures[:6],
                "review": "refused but cited figures -- read it"}
    return {"pass": False, "refused": False, "figures_cited": figures[:6]}


def grade_conflict(ans, qrec):
    hay = norm(ans)
    vals = qrec["expected"]["value"]
    found = [v for v in vals if norm(v) in hay]
    flagged = any(m in hay for m in CONFLICT_MARKERS)
    if len(found) == len(vals) and flagged:
        return {"pass": True, "both_values": found, "flagged_disagreement": True}
    if len(found) == len(vals):
        return {"pass": None, "both_values": found, "flagged_disagreement": False,
                "review": "both values present but disagreement not obviously flagged"}
    return {"pass": False, "both_values": found, "flagged_disagreement": flagged}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", required=True)
    ap.add_argument("--out", default=r"C:\temp\doc_corpus_250")
    ap.add_argument("--report", default=None)
    a = ap.parse_args()

    gt = json.load(open(os.path.join(a.out, "ground_truth.json"), encoding="utf-8"))
    qb = json.load(open(os.path.join(a.out, "questions.json"), encoding="utf-8"))
    universe = {r["store"] for r in gt["documents"] if r.get("type") == "lease"}
    qmap = {x["id"]: x for x in qb["questions"]}

    answers = json.load(open(a.answers, encoding="utf-8"))
    if isinstance(answers, dict):
        answers = [{"id": k, "answer": v} for k, v in answers.items()]

    rows, unanswered = [], [q for q in qmap if q not in {x["id"] for x in answers}]
    for item in answers:
        qrec = qmap.get(item["id"])
        if not qrec:
            print(f"  ! {item['id']} is not in the question bank -- skipped")
            continue
        ans, mode = item.get("answer") or "", qrec["expected"]["mode"]
        if mode == "set":
            r = grade_set(ans, qrec, universe)
        elif mode == "exact":
            r = grade_exact(ans, qrec)
        elif mode == "contains_all":
            r = grade_contains(ans, qrec, True)
        elif mode == "contains_none":
            r = grade_contains(ans, qrec, False)
        elif mode == "refusal":
            r = grade_refusal(ans)
        elif mode == "conflict":
            r = grade_conflict(ans, qrec)
        else:
            r = {"pass": None, "review": f"unknown mode {mode}"}
        rows.append(dict(id=qrec["id"], cls=qrec["class"], mode=mode, **r))

    by_cls = {}
    for r in rows:
        b = by_cls.setdefault(r["cls"], {"pass": 0, "fail": 0, "review": 0, "n": 0})
        b["n"] += 1
        b["pass" if r["pass"] is True else ("fail" if r["pass"] is False else "review")] += 1

    print(f"\n{'class':<11}{'n':>4}{'pass':>7}{'fail':>7}{'review':>8}")
    print("-" * 37)
    for k in sorted(by_cls):
        b = by_cls[k]
        print(f"{k:<11}{b['n']:>4}{b['pass']:>7}{b['fail']:>7}{b['review']:>8}")
    tot = {k: sum(b[k] for b in by_cls.values()) for k in ("n", "pass", "fail", "review")}
    print("-" * 37)
    print(f"{'TOTAL':<11}{tot['n']:>4}{tot['pass']:>7}{tot['fail']:>7}{tot['review']:>8}")

    fan = [r for r in rows if r["cls"] == "fanout"]
    if fan:
        print(f"\nfan-out mean recall    {sum(r['recall'] for r in fan) / len(fan):.1%}")
        print(f"fan-out mean precision {sum(r['precision'] for r in fan) / len(fan):.1%}")
        syn = [r for r in fan if "synonym_only_recall" in r]
        if syn:
            print(f"synonym-only recall    "
                  f"{sum(r['synonym_only_recall'] for r in syn) / len(syn):.1%}  "
                  f"<- the class keyword matching cannot reach")

    if unanswered:
        print(f"\n{len(unanswered)} question(s) had NO answer submitted: "
              f"{', '.join(sorted(unanswered)[:10])}"
              f"{' ...' if len(unanswered) > 10 else ''}")
    review = [r for r in rows if r["pass"] is None]
    if review:
        print(f"\n{len(review)} question(s) need a human read (not scored either way):")
        for r in review:
            print(f"  {r['id']} [{r['cls']}] {r.get('review', '')}")

    path = a.report or os.path.join(a.out, "grade_report.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"by_class": by_cls, "totals": tot, "unanswered": sorted(unanswered),
                   "rows": rows}, fh, indent=1)
    print(f"\nreport -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
