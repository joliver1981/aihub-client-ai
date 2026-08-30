"""Verify the generated corpus against its own ground truth BEFORE anything is ingested.

Every check here exists because the corresponding failure would be invisible later: a needle
in a format that drops it, a "silent" lease that actually says HVAC, a scanned page that
kept its text layer and so never touches OCR, an amendment that restates rather than flips.
Pack 13's lesson was that the grader was the bug -- so the corpus gets graded first.

    python verify_corpus.py [--out C:\\temp\\doc_corpus_250]
"""
import argparse
import json
import os
import sys

FAIL = []
WARN = []


def check(cond, msg):
    (print(f"  ok   {msg}") if cond else (FAIL.append(msg), print(f"  FAIL {msg}")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=r"C:\temp\doc_corpus_250")
    a = ap.parse_args()
    docs = os.path.join(a.out, "docs")
    gt = json.load(open(os.path.join(a.out, "ground_truth.json"), encoding="utf-8"))
    recs = gt["documents"]
    by_key = {r["key"]: r for r in recs}

    print("\n[1] file inventory")
    on_disk = set(os.listdir(docs))
    expected = {r["filename"] for r in recs}
    check(on_disk == expected,
          f"{len(expected)} expected files, {len(on_disk)} on disk, "
          f"{len(on_disk - expected)} orphans, {len(expected - on_disk)} missing")
    check(all(os.path.getsize(os.path.join(docs, f)) > 900 for f in expected),
          "no zero-length or stub files")

    print("\n[2] routing thresholds (production must leave the cannot-miss branch)")
    check(gt["counts"]["pages"] > 999, f"pages {gt['counts']['pages']} > 999")
    check(gt["counts"]["chars"] > 400000, f"chars {gt['counts']['chars']:,} > 400,000")

    print("\n[3] needles are reachable in their delivered format")
    planted = {n for r in recs for n in r["needles"] if not n.endswith("-decoy")}
    defined = {n["id"] for n in gt["needles"]}
    check(planted == defined, f"all {len(defined)} needles planted ({len(planted)} found)")
    for n in gt["needles"]:
        host = by_key.get(n["host"])
        check(host is not None, f"{n['id']} host {n['host']} exists")
        if not host:
            continue
        check(host["format"] not in ("xlsx", "csv"),
              f"{n['id']} host format {host['format']} preserves page text")
        # What matters is that the fact is BURIED, not that it landed on the page the
        # spec named -- a declared page past the end is clamped to the last page, which is
        # still buried. Two-part needles must additionally straddle pages, or the "join two
        # statements" difficulty collapses into a single-passage lookup.
        at = n["planted_on_pages"]
        check(bool(at), f"{n['id']} recorded a landing page")
        if len(at) > 1:
            check(at[0] != at[1],
                  f"{n['id']} two-part fact straddles pages {at[0] + 1} and {at[1] + 1}")
        else:
            check(at and at[0] >= 1,
                  f"{n['id']} buried on page {at[0] + 1} of {host['pages']}, not page 1")
        if n.get("decoy_host"):
            d = by_key.get(n["decoy_host"])
            check(d is not None and n["id"] + "-decoy" in d["needles"],
                  f"{n['id']} decoy present in {n['decoy_host']}")

    print("\n[4] 'silent' HVAC leases never say HVAC (else the class is not silent)")
    import fitz
    silent = set(gt["dimensions"]["hvac"]["effective"].get("silent", []))
    bad = []
    for r in recs:
        if r.get("store") in silent and r["type"] == "lease" and r["format"] in ("pdf", "txt"):
            p = os.path.join(docs, r["filename"])
            if r["format"] == "txt":
                text = open(p, encoding="utf-8").read()
            else:
                d = fitz.open(p)
                text = "".join(pg.get_text() for pg in d)
                d.close()
            if "HVAC" in text.upper():
                bad.append(r["store"])
    check(not bad, f"{len(silent)} silent leases, {len(bad)} leaked the term {bad}")

    print("\n[5] scanned documents carry NO text layer (so OCR is genuinely exercised)")
    scans = [r for r in recs if r["format"] == "scan"]
    leaky = []
    for r in scans:
        d = fitz.open(os.path.join(docs, r["filename"]))
        if sum(len(pg.get_text().strip()) for pg in d) > 20:
            leaky.append(r["filename"])
        d.close()
    check(not leaky, f"{len(scans)} scanned docs, {len(leaky)} retained extractable text")

    print("\n[6] amendments actually supersede (a restatement tests nothing)")
    noop = [r["store"] for r in recs
            if r.get("type") == "lease_amendment" and r.get("new_hvac")
            and by_key[f"lease:{r['store']}"]["dims"]["hvac"] == r["new_hvac"]]
    check(not noop, f"no amendment restates its base value {noop}")
    flipped = [r["store"] for r in recs if r.get("type") == "lease_amendment" and r.get("new_hvac")]
    for s in flipped:
        base = by_key[f"lease:{s}"]
        check(base["dims_effective"]["hvac"] != base["dims"]["hvac"],
              f"{s} effective hvac differs from base ({base['dims']['hvac']} -> "
              f"{base['dims_effective']['hvac']})")

    print("\n[7] fan-out ground truth is complete and disjoint")
    leases = [r for r in recs if r["type"] == "lease"]
    for dim, roll in gt["dimensions"].items():
        for scope in ("base", "effective"):
            allocated = [s for v in roll[scope].values() for s in v]
            check(len(allocated) == len(leases) and len(set(allocated)) == len(leases),
                  f"{dim}/{scope}: {len(allocated)} allocations over {len(leases)} leases, "
                  f"no duplicates")

    print("\n[8] near-miss distractors exist for the headline question")
    hv = [r for r in recs if "hvac" in (r.get("distractor_for") or [])]
    check(len(hv) >= 15, f"{len(hv)} documents mention HVAC responsibility but are NOT leases")

    print("\n[9] restricted category is populated for the ACL test")
    rest = [r for r in recs if r.get("acl_restricted")]
    check(len(rest) >= 5, f"{len(rest)} documents in the restricted category")

    print("\n[10] format coverage across every accepted ingest path")
    fmts = gt["counts"]["by_format"]
    for f in ("pdf", "docx", "xlsx", "csv", "txt", "scan"):
        check(fmts.get(f, 0) > 0, f"format {f}: {fmts.get(f, 0)} documents")
    scan_share = (fmts.get("scan", 0) + fmts.get("jpg", 0)) / len(recs)
    check(0.07 <= scan_share <= 0.13, f"image/OCR share {scan_share:.1%} (target ~10%)")

    print(f"\n{'=' * 62}")
    if FAIL:
        print(f"FAILED -- {len(FAIL)} check(s):")
        for m in FAIL:
            print("  -", m)
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
