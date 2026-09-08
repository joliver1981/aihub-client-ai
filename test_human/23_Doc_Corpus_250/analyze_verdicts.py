"""Separate SCOPING error from VERDICT error on the document-store fan-out lane.

    python analyze_verdicts.py [--out C:\\temp\\doc_corpus_250]

The aggregate count an enumerate answer reports mixes two very different failures:

  * SCOPING  -- documents that should not have been counted at all (a trash-compactor
               equipment schedule classified `lease_agreement`, another pack's fixtures)
  * VERDICT  -- documents correctly in scope whose per-document answer is simply wrong

A count that is off by six tells you nothing about which. But `doc_search_v3.enumerate`
persists every per-document verdict to `DocumentFields` (see `_write_back`), so the
verdicts can be read back, restricted to the 130 leases ground truth actually describes,
and scored one document at a time.

Verdicts that cannot be confidently mapped to a ground-truth category are reported as
UNMAPPED, never guessed into a bucket -- an unmappable verdict is a finding about the
engine's output, not a scoring problem to paper over.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, REPO)

import pyodbc                                          # noqa: E402
from CommonUtils import get_db_connection_string       # noqa: E402

FIELD_RE = re.compile(r"Field consulted:\s*(\S+)")

# How a stored verdict string maps to a ground-truth value, per dimension. Matched as
# lowercase substrings, first hit wins; order matters where one phrase contains another.
MAPS = {
    "hvac": [("split", "split"), ("landlord", "landlord"), ("tenant", "tenant"),
             ("not addressed", "silent"), ("does not mention", "silent"),
             ("not mention", "silent"), ("no hvac", "silent"), ("silent", "silent")],
    "cam_cap": [("3%", "3%"), ("4%", "4%"), ("5%", "5%"),
                ("no cap", "none"), ("without cap", "none"), ("no cam cap", "none"),
                ("not addressed", "none"), ("no cam increase cap", "none")],
    "holdover": [("125", "125%"), ("150", "150%"), ("200", "200%")],
    "renewal": [("three", "3x5"), ("3 ", "3x5"), ("two", "2x5"), ("2 ", "2x5"),
                ("one", "1x5"), ("1 ", "1x5"), ("no option", "0"), ("none", "0"),
                ("intentionally omitted", "0")],
    "radius": [("3 mile", "3 miles"), ("three mile", "3 miles"),
               ("5 mile", "5 miles"), ("five mile", "5 miles"),
               ("10 mile", "10 miles"), ("ten mile", "10 miles"),
               ("no radius", "none"), ("none", "none"), ("not addressed", "none")],
}


def map_verdict(dim, raw):
    s = (raw or "").lower()
    for needle, value in MAPS.get(dim, []):
        if needle in s:
            return value
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=r"C:\temp\doc_corpus_250")
    a = ap.parse_args()
    os.environ.setdefault("API_KEY", "DB27D555-03A8-446E-9C23-8DAAA95EAD21")

    gt = json.load(open(os.path.join(a.out, "ground_truth.json"), encoding="utf-8"))
    qb = json.load(open(os.path.join(a.out, "questions.json"), encoding="utf-8"))
    answers = {x["id"]: x["answer"] for x in
               json.load(open(os.path.join(a.out, "answers_docstore.json"), encoding="utf-8"))}
    qmap = {x["id"]: x for x in qb["questions"]}

    # dimension -> the field path the engine chose for it (first question that used it)
    dim_field = {}
    for qid, ans in answers.items():
        q = qmap.get(qid)
        if not q or q["class"] not in ("fanout", "aggregate", "precision"):
            continue
        m = FIELD_RE.search(ans or "")
        if m and q.get("dimension") and q["dimension"] not in dim_field:
            dim_field[q["dimension"]] = m.group(1)

    leases = {r["filename"]: r for r in gt["documents"] if r.get("type") == "lease"}
    conn = pyodbc.connect(get_db_connection_string(), timeout=30)
    cur = conn.cursor()
    cur.execute("EXEC tenant.sp_setTenantContext ?", os.environ["API_KEY"])

    print(f"{'dimension':<12}{'field path':<46}{'scored':>7}{'correct':>9}"
          f"{'acc':>7}{'unmapped':>10}{'offscope':>9}")
    print("-" * 100)
    grand = {"scored": 0, "correct": 0, "unmapped": 0, "offscope": 0}
    detail = {}
    for dim, fp in sorted(dim_field.items()):
        if dim not in MAPS:
            continue
        cur.execute("""SELECT d.filename, f.field_value
                       FROM DocumentFields f
                       JOIN DocumentPages p ON p.page_id = f.page_id
                       JOIN Documents d ON d.document_id = p.document_id
                       WHERE f.field_path = ?""", fp)
        rows = cur.fetchall()
        scored = correct = unmapped = offscope = 0
        misses = []
        for fn, val in rows:
            rec = leases.get(fn)
            if not rec:
                offscope += 1
                continue
            want = rec["dims_effective"][dim]
            got = map_verdict(dim, val)
            if got is None:
                unmapped += 1
                continue
            scored += 1
            if got == want:
                correct += 1
            elif len(misses) < 5:
                misses.append((rec["store"], want, got, str(val)[:40]))
        acc = correct / scored if scored else 0.0
        print(f"{dim:<12}{fp[:45]:<46}{scored:>7}{correct:>9}{acc:>6.0%}"
              f"{unmapped:>10}{offscope:>9}")
        detail[dim] = {"field_path": fp, "scored": scored, "correct": correct,
                       "accuracy": round(acc, 4), "unmapped": unmapped,
                       "offscope_verdicts": offscope, "example_misses": misses}
        for k in ("scored", "correct", "unmapped", "offscope"):
            grand[k] += {"scored": scored, "correct": correct,
                         "unmapped": unmapped, "offscope": offscope}[k]
    conn.close()

    print("-" * 100)
    if grand["scored"]:
        print(f"{'TOTAL':<58}{grand['scored']:>7}{grand['correct']:>9}"
              f"{grand['correct'] / grand['scored']:>6.0%}"
              f"{grand['unmapped']:>10}{grand['offscope']:>9}")
        print(f"\nRead this as: on the {grand['scored']} real-lease verdicts that could be "
              f"mapped, the engine was right {grand['correct'] / grand['scored']:.0%} of the "
              f"time.\n{grand['offscope']} verdicts were written for documents that are not "
              f"leases at all — that is the scoping error, and it is separate from accuracy.")
        if grand["unmapped"]:
            print(f"{grand['unmapped']} verdict(s) could not be mapped to any ground-truth "
                  f"category and were NOT scored either way.")
    else:
        print("no verdicts stored yet for the mapped dimensions — run the questions first")

    for dim, d in detail.items():
        if d["example_misses"]:
            print(f"\n{dim} example misses (store, expected, engine said, raw):")
            for m in d["example_misses"]:
                print(f"  {m[0]}  {m[1]:<10} -> {str(m[2]):<10} {m[3]!r}")

    path = os.path.join(a.out, "verdict_accuracy.json")
    json.dump({"totals": grand, "by_dimension": detail},
              open(path, "w", encoding="utf-8"), indent=1)
    print(f"\nreport -> {path}")


if __name__ == "__main__":
    main()
