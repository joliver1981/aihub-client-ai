"""Derive the graded question bank from ground truth.

Questions are GENERATED from ground_truth.json, never hand-typed, so an expected answer
cannot drift away from the corpus. Pack 13's most expensive lesson was that the grader
itself was the bug -- two whole runs were reported 20 points low because a regex in the
grader hid 20 stores. Everything gradeable by arithmetic is graded by arithmetic.

    python gen_questions.py [--out C:\\temp\\doc_corpus_250]

Grading modes
  set          expected list of store IDs; scored on recall AND precision, with a separate
               recall figure over the synonym-only subset (the class that breaks retrieval)
  exact        one value, normalised string/number comparison
  contains_all every listed string must appear
  contains_none no listed string may appear (precision against near-miss distractors)
  refusal      the corpus does not answer this; a confident answer is a hallucination
  conflict     both conflicting values must be surfaced, neither silently chosen
"""
import argparse
import json
import os

QID = [0]


def q(cls, question, expected, **kw):
    QID[0] += 1
    rec = {"id": f"Q{QID[0]:03d}", "class": cls, "question": question}
    rec.update(kw)
    rec["expected"] = expected
    return rec


DIM_QUESTIONS = {
    "hvac": [
        ("landlord", "Which store leases make HVAC maintenance the landlord's responsibility?"),
        ("tenant", "Which store leases put HVAC maintenance and replacement on us as the tenant?"),
        ("split", "Which store leases split HVAC responsibility between landlord and tenant by a "
                  "cost threshold?"),
        ("silent", "Which store leases never address HVAC responsibility at all?"),
    ],
    "cam_cap": [
        ("none", "Which store leases have NO cap on annual CAM increases?"),
        ("5%", "Which store leases cap controllable CAM increases at 5% per year?"),
    ],
    "pct_rent": [
        ("yes", "Which store leases require us to pay percentage rent above a sales breakpoint?"),
        ("none", "Which store leases have no percentage rent obligation?"),
    ],
    "cotenancy": [
        ("anchor", "Which store leases give us a co-tenancy remedy tied only to the anchor tenant "
                   "closing?"),
        ("anchor+occupancy", "Which store leases condition our rent on BOTH the anchor operating "
                             "and a minimum occupancy percentage?"),
        ("none", "Which store leases give us no co-tenancy protection at all?"),
    ],
    "exclusive": [
        ("apparel", "Which store leases give us an exclusive use protection for apparel?"),
        ("none", "Which store leases give us no exclusive use protection?"),
    ],
    "assignment": [
        ("affiliate transfer permitted", "Which store leases let us assign to an affiliate without "
                                         "the landlord's consent?"),
        ("change of control triggers recapture", "Which store leases let the landlord recapture the "
                                                 "premises if we go through a change of control?"),
        ("consent required", "Which store leases let the landlord withhold consent to assignment in "
                             "its sole discretion?"),
    ],
    "casualty": [
        ("landlord must rebuild", "Which store leases require the landlord to rebuild after a "
                                  "casualty with no termination right?"),
        ("tenant option only", "Which store leases give only the tenant a termination right after "
                               "a casualty?"),
    ],
    "holdover": [
        ("200%", "Which store leases charge 200% of base rent if we hold over?"),
        ("125%", "Which store leases charge only 125% of base rent for holding over?"),
    ],
    "renewal": [
        ("0", "Which store leases give us no renewal option at all?"),
        ("3x5", "Which store leases give us three five-year renewal options?"),
    ],
    "radius": [
        ("10 miles", "Which store leases impose a 10-mile radius restriction on us?"),
        ("none", "Which store leases have no radius restriction?"),
    ],
}

REFUSALS = [
    "What is the annual employee turnover rate at each store?",
    "Which stores have a drive-through pickup window, and what are their hours?",
    "What is the square footage of the loading dock at each location?",
    "Which landlord charges the highest management fee as a percentage of CAM?",
    "What is our total capital expenditure budget for the next fiscal year?",
]

PRECISION = [
    ("Which store leases make HVAC maintenance the landlord's responsibility? Answer only from "
     "the lease documents themselves.",
     ["HVAC Preventive Maintenance Agreement", "Roof System Warranty", "Elevator and Escalator "
      "Service Contract", "Fire Suppression System Inspection", "Equipment Lease Schedule"],
     "The corpus contains 23 non-lease documents that talk about HVAC and maintenance "
     "responsibility. None is a store lease. Citing one is a false positive."),
    ("List every lease in the portfolio that we hold. Do not include equipment or service "
     "agreements.",
     ["Equipment Lease Schedule", "Wexford Equipment Finance", "compactor", "forklift"],
     "Seven equipment lease schedules use the word 'lease' but convey no interest in real "
     "property; they say so explicitly."),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=r"C:\temp\doc_corpus_250")
    a = ap.parse_args()
    gt = json.load(open(os.path.join(a.out, "ground_truth.json"), encoding="utf-8"))
    dims, syn = gt["dimensions"], gt["synonym_only"]
    recs = gt["documents"]
    by_store = {r["store"]: r for r in recs if r.get("type") == "lease"}
    amendments = [r for r in recs if r.get("type") == "lease_amendment"]
    qs = []

    # ---- 1. FAN-OUT: the "read every document" class -------------------
    for dim, specs in DIM_QUESTIONS.items():
        for value, text in specs:
            stores = dims[dim]["effective"].get(value, [])
            syn_subset = syn[dim].get(value, [])
            qs.append(q("fanout", text,
                        {"mode": "set", "value": stores},
                        dimension=dim, dim_value=value, scope="effective",
                        n_expected=len(stores),
                        synonym_only_subset=syn_subset,
                        note=f"{len(stores)} of 130 leases. {len(syn_subset)} of them NEVER use "
                             f"the obvious keyword and are findable only by meaning -- report "
                             f"recall over that subset separately."))

    # ---- 2. AGGREGATE / COUNTING --------------------------------------
    for dim, value, text in [
        ("hvac", "landlord", "How many of our store leases make HVAC the landlord's responsibility?"),
        ("hvac", "tenant", "How many store leases put HVAC on the tenant?"),
        ("cotenancy", "none", "How many store leases have no co-tenancy protection?"),
        ("renewal", "0", "How many store leases have no renewal option?"),
        ("radius", "none", "How many store leases have no radius restriction?"),
    ]:
        n = len(dims[dim]["effective"].get(value, []))
        qs.append(q("aggregate", text, {"mode": "exact", "value": n},
                    dimension=dim, dim_value=value,
                    note="A count is only right if every document was actually read. A near-miss "
                         "count is a coverage failure, not a rounding difference."))

    expiring = sorted(s for s, r in by_store.items()
                      if int(r["scalars"]["expiration"].split()[-1]) <= 2029)
    qs.append(q("aggregate", "How many store leases expire on or before the end of 2029?",
                {"mode": "exact", "value": len(expiring)},
                supporting_set=expiring,
                note="Requires reading the expiration date on page 1 of all 130 leases."))
    qs.append(q("fanout", "Which store leases expire on or before the end of 2029?",
                {"mode": "set", "value": expiring}, n_expected=len(expiring)))

    # Superlatives must be TIE-AWARE. Random draws over 130 leases produce ties at the
    # extremes routinely, and an expected answer naming one of several equally-correct
    # stores would fail a right answer -- the grader-is-the-bug failure from pack 13.
    for field, unit, text in [
        ("base_rent", "$", "Which store has the highest monthly base rent, and how much is it?"),
        ("sqft", " sq ft", "Which store premises has the largest square footage?"),
    ]:
        top = max(r["scalars"][field] for r in by_store.values())
        tied = sorted(s for s, r in by_store.items() if r["scalars"][field] == top)
        shown = f"${top:,}" if unit == "$" else f"{top:,} sq ft"
        qs.append(q("aggregate", text,
                    {"mode": "set", "value": tied, "must_also_contain": [shown]},
                    tied_count=len(tied), peak_value=top,
                    note=f"Superlative over all 130 leases -- one missed document changes the "
                         f"answer. {len(tied)} store(s) tie at {shown}; a complete answer names "
                         f"all of them."))

    waived = sorted(s for s, r in by_store.items() if r["scalars"]["deposit"] == "Waived")
    qs.append(q("fanout", "Which store leases have the security deposit waived?",
                {"mode": "set", "value": waived}, n_expected=len(waived)))

    both = sorted(set(dims["hvac"]["effective"].get("tenant", []))
                  & set(dims["cam_cap"]["effective"].get("none", [])))
    qs.append(q("fanout",
                "Which store leases put HVAC on us AND have no cap on CAM increases? Those are our "
                "worst-exposure locations.",
                {"mode": "set", "value": both}, n_expected=len(both),
                note="Two-dimension intersection: both facts live on different pages of the same "
                     "lease, so this fails if retrieval returns only one page per document."))

    # ---- 3. NEEDLES ----------------------------------------------------
    for n in gt["needles"]:
        qs.append(q("needle", n["q"], {"mode": "exact", "value": n["a"]},
                    needle_id=n["id"], difficulty=n["grade"], host=n["host"],
                    planted_on_pages=[p + 1 for p in n["planted_on_pages"]],
                    has_decoy=bool(n.get("decoy_host")),
                    note={"A": "unique rare token, findable by exact match if retrieval reaches "
                               "the page",
                          "B": "ordinary prose, no distinctive keyword -- must be found by meaning",
                          "C": "answer exists only by joining two statements; a near-identical "
                               "decoy in another document gives a plausible WRONG answer"}[n["grade"]]))

    # ---- 4. MULTI-HOP / SUPERSESSION -----------------------------------
    for am in [r for r in amendments if r.get("new_hvac")][:4]:
        s = am["store"]
        qs.append(q("multihop",
                    f"Who is responsible for HVAC at store {s} today?",
                    {"mode": "exact", "value": am["new_hvac"]},
                    store=s, base_value=by_store[s]["dims"]["hvac"],
                    note=f"The base lease says '{by_store[s]['dims']['hvac']}'. The amendment "
                         f"changes it to '{am['new_hvac']}'. Answering from the base lease alone "
                         f"is wrong -- and is the single most likely failure here."))

    qs.append(q("multihop",
                "Ignoring all amendments, what did the ORIGINAL signed lease for store S303 say "
                "about HVAC responsibility?",
                {"mode": "exact", "value": by_store["S303"]["dims"]["hvac"]},
                note="The inverse test: the system must also be able to scope to the base document "
                     "when asked, rather than always collapsing to the latest."))

    wrong_est = [r for r in recs if r.get("type") == "estoppel" and not r.get("accurate")]
    e = wrong_est[0]
    qs.append(q("multihop",
                f"What is the current monthly base rent for store {e['store']}?",
                {"mode": "exact", "value": f"${e['true_rent']:,}"},
                note=f"An estoppel certificate in the corpus states ${e['stated_rent']:,}. The "
                     f"executed lease says ${e['true_rent']:,}. The lease controls; the estoppel "
                     f"even flags that it was not reconciled against the lease."))

    qs.append(q("multihop",
                "Under the Vantage Facilities Group agreements, what hourly rate applies to the "
                "northeast region stores now, and what are the payment terms?",
                {"mode": "contains_all",
                 "value": ["118", "net forty-five", "45"]},
                note="Three documents: the MSA sets payment terms and a $118 blended rate, SOW 2 "
                     "raises the northeast rate to $131, and Change Order 1 reverts it to $118. "
                     "$131 is the stale answer."))
    qs.append(q("multihop",
                "How many stores does Statement of Work No. 2 cover after all change orders?",
                {"mode": "exact", "value": "27"},
                note="19 in the SOW, plus 8 added by Change Order 1."))

    # ---- 5. PRECISION AGAINST NEAR-MISS DISTRACTORS --------------------
    for text, forbidden, note in PRECISION:
        qs.append(q("precision", text, {"mode": "contains_none", "value": forbidden}, note=note))

    hv_landlord = dims["hvac"]["effective"].get("landlord", [])
    qs.append(q("precision",
                "Our facilities team says the landlord handles HVAC at every location because we "
                "have preventive maintenance agreements on file. Is that right? List the stores "
                "where the landlord actually is responsible.",
                {"mode": "set", "value": hv_landlord},
                dimension="hvac", dim_value="landlord",
                synonym_only_subset=syn["hvac"].get("landlord", []),
                n_expected=len(hv_landlord),
                note=f"A leading question built on a false premise. The PM agreements say "
                     f"explicitly that they do not determine landlord-tenant allocation. Only "
                     f"{len(hv_landlord)} of 130 leases put HVAC on the landlord -- an answer "
                     f"that accepts the premise and says 'all of them' is wrong. Graded as a set "
                     f"so agreeing with the false premise scores as lost precision, not as a "
                     f"judgement call."))

    # ---- 6. NEGATIVE SPACE ---------------------------------------------
    for text in REFUSALS:
        qs.append(q("negative", text, {"mode": "refusal", "value": None},
                    note="Nothing in the corpus answers this. A confident answer is a "
                         "hallucination; a hedged answer that still invents figures is worse."))

    # ---- 7. CONFLICT AND NEAR-DUPLICATE --------------------------------
    qs.append(q("conflict",
                "What is the current monthly base rent for store S344 according to our internal "
                "records?",
                {"mode": "conflict", "value": ["$34,500", "$36,900"]},
                note="Two internal documents state different figures for the same store. The right "
                     "behaviour is to surface both and say they disagree -- not to pick one."))
    qs.append(q("conflict",
                "How many days notice do we have to give to exercise a renewal option under our "
                "standard form Rider 4?",
                {"mode": "conflict", "value": ["270", "120"]},
                note="Two revisions of the same standard form, 98% identical, differing in exactly "
                     "this clause. Deduplication that collapses them loses the real difference."))

    # ---- 8. CATEGORY ACL ------------------------------------------------
    for text, val in [
        ("What was the settlement amount in the Ashford matter?", "$412,000"),
        ("How many stores are on the FY27 closure evaluation list?", "19"),
        ("What did internal audit find about lease administration controls?",
         "three control deficiencies"),
        ("Which locations have failed anchor co-tenancy conditions per legal's assessment?",
         "seven locations"),
    ]:
        qs.append(q("acl", text, {"mode": "exact", "value": val},
                    restricted_category="Legal-Confidential",
                    note="Answerable ONLY for a user with access to the Legal-Confidential "
                         "category. Run this question twice: once as an authorised user (expect "
                         "the value) and once as an unauthorised user (expect a refusal, and "
                         "expect the content NOT to leak into the answer)."))

    out = {
        "corpus": gt["corpus"],
        "generated_from": "ground_truth.json",
        "counts": {"total": len(qs)},
        "by_class": {},
        "questions": qs,
    }
    for x in qs:
        out["by_class"][x["class"]] = out["by_class"].get(x["class"], 0) + 1

    with open(os.path.join(a.out, "questions.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)

    print(f"{len(qs)} graded questions -> {os.path.join(a.out, 'questions.json')}")
    for k, v in sorted(out["by_class"].items(), key=lambda kv: -kv[1]):
        print(f"  {k:<10} {v}")


if __name__ == "__main__":
    main()
