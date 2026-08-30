"""Pack 23 corpus generator -- 255 documents with ground truth exact by construction.

    python gen_corpus.py [--out C:\\temp\\doc_corpus_250]

Deterministic: seeded RNG, no clock, no LLM. Re-running reproduces the corpus byte-for-byte,
so ground_truth.json can be committed and diffed while the ~1,100 pages of documents stay
out of git.

Why the size matters: production routes on document volume
(`KNOWLEDGE_BRUTE_FORCE_PAGE_THRESHOLD=999` + `KNOWLEDGE_BRUTE_FORCE_CHAR_BUDGET=400000`,
see dist/.env). Under BOTH limits the engine dumps every page of every document into
context and literally cannot miss -- a corpus below that line tests nothing. This corpus is
built to sit comfortably past both so the smart-retrieval path (NEEDLE / FANOUT / AGGREGATE)
is the one under test.
"""
import argparse
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import corpus_spec as S           # noqa: E402
import render as R                # noqa: E402

SEED = 23
DEFAULT_OUT = r"C:\temp\doc_corpus_250"

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def dstr(rng, y0, y1):
    return f"{rng.choice(MONTHS)} {rng.randint(1, 28)}, {rng.randint(y0, y1)}"


# ===========================================================================
# NEEDLES -- the haystack half of the test.
#
# grade A  unique rare token, appears exactly once in the whole corpus
# grade B  plain prose, no distinctive keyword, requires paraphrase to find
# grade C  answer only exists by joining two statements, AND a near-identical
#          decoy fact lives in a DIFFERENT document
# ===========================================================================

NEEDLES = [
    # ---- grade A: rare token, findable by exact match if retrieval reaches the page
    dict(id="N01", grade="A", host="lease:S317", page=3,
         text="Notwithstanding the foregoing, the parties acknowledge the KESTREL PROTOCOL "
              "governing after-hours mechanical access, a copy of which is annexed as Exhibit K.",
         q="What is the Kestrel Protocol and which store's lease refers to it?",
         a="Store S317 -- it governs after-hours mechanical access and is annexed as Exhibit K."),
    dict(id="N02", grade="A", host="lease:S342", page=5,
         text="Tenant's signage allowance under this Lease is designated internally as "
              "ALLOWANCE CODE MARLIN-88 and totals $47,350.",
         q="What dollar amount is attached to allowance code MARLIN-88?",
         a="$47,350 (store S342 signage allowance)."),
    dict(id="N03", grade="A", host="carrier:CAM-004", page=2,
         text="Line 41  ROOF ANOMALY SURVEY -- vendor Talleyrand Surveying -- 18,942.00 -- "
              "billed once, non-recurring, disputed by Tenant on 2026-02-11.",
         q="Which vendor performed the roof anomaly survey, and what did it cost?",
         a="Talleyrand Surveying, $18,942.00."),
    dict(id="N04", grade="A", host="lease:S388", page=4,
         text="The Premises are subject to a recorded easement identified as the "
              "HOLLOWAY SPUR EASEMENT benefiting the adjacent rail siding.",
         q="Which lease is subject to the Holloway Spur Easement?",
         a="Store S388."),
    dict(id="N05", grade="A", host="carrier:MIN-003", page=2,
         text="Motion carried 6-1 to fund PROJECT ANVIL, a $2,400,000 refrigeration retrofit "
              "across nine southeastern stores, with Director Vance dissenting.",
         q="How much was approved for Project Anvil and who dissented?",
         a="$2,400,000; Director Vance dissented (vote 6-1)."),
    dict(id="N06", grade="A", host="distractor:HVACPM-11", page=2,
         text="Service technician badge series QUILL-4400 is required for all rooftop access "
              "at this location under the site security addendum.",
         q="Which badge series is required for rooftop access?",
         a="QUILL-4400."),
    dict(id="N07", grade="A", host="carrier:PCA-002", page=3,
         text="The assessor noted a single deficiency of immediate concern: a failed "
              "BRAMBLE-SERIES backflow preventer at the rear service corridor, remediation "
              "estimated at $6,800.",
         q="What is the estimated remediation cost for the Bramble-series backflow preventer?",
         a="$6,800."),
    dict(id="N08", grade="A", host="lease:S405", page=5,
         text="Landlord has granted Tenant a one-time rent credit referred to herein as the "
              "PEREGRINE CREDIT in the amount of $122,500, applicable to months 13 through 18.",
         q="What is the Peregrine Credit worth and when does it apply?",
         a="$122,500, applied to months 13 through 18 of the S405 lease."),

    # ---- grade B: no distinctive keyword; the answer is in ordinary prose
    dict(id="N09", grade="B", host="lease:S326", page=4,
         text="Tenant shall be permitted to keep the store open until midnight on the final "
              "Friday of each November, notwithstanding the Shopping Center's standard closing "
              "hour of 9:00 p.m.",
         q="Is any store allowed to stay open past the shopping center's normal closing time, "
           "and when?",
         a="Yes -- S326 may stay open until midnight on the final Friday of each November "
           "(standard closing is 9:00 p.m.)."),
    dict(id="N10", grade="B", host="carrier:UTIL-003", page=2,
         text="The audit found that the irrigation meter had been billed to the tenant since "
              "2019 in error; the account is properly a common area charge and the overbilling "
              "totals nineteen thousand four hundred dollars.",
         q="Was anything billed to the tenant in error, and how much?",
         a="Yes -- the irrigation meter was billed to the tenant since 2019 in error; "
           "$19,400 total overbilling."),
    dict(id="N11", grade="B", host="lease:S371", page=5,
         text="Tenant may not display any merchandise on the sidewalk in front of the Premises "
              "except during the four days following Thanksgiving, when a single display rack "
              "not exceeding six feet in width is permitted.",
         q="Are there any restrictions on sidewalk merchandise displays, and any exception?",
         a="S371 forbids sidewalk displays except in the four days after Thanksgiving, when one "
           "rack up to six feet wide is allowed."),
    dict(id="N12", grade="B", host="carrier:COI-002", page=1,
         text="Coverage for flood is excluded at this location because the premises sit within "
              "a designated special flood hazard area; a separate policy was declined by the "
              "insured on cost grounds.",
         q="Is there any location where flood coverage is missing, and why?",
         a="Yes -- the location on COI schedule 002 sits in a special flood hazard area and the "
           "insured declined separate flood coverage on cost grounds."),
    dict(id="N13", grade="B", host="lease:S350", page=2,
         text="For the avoidance of doubt, the first three months of the Term shall be free of "
              "Base Rent, but Tenant remains liable for its proportionate share of taxes and "
              "insurance during that period.",
         q="Does any lease give free rent, and does it cover taxes and insurance too?",
         a="S350 gives three months free Base Rent, but taxes and insurance remain payable."),
    dict(id="N14", grade="B", host="carrier:PCA-005", page=2,
         text="The parking field was resurfaced two years before the assessment and is expected "
              "to require no capital attention for at least eight more years.",
         q="Which property's parking lot needs no capital work for the longest period?",
         a="The property in property condition assessment 005 -- resurfaced two years prior, "
           "no capital attention needed for at least eight more years."),

    # ---- grade C: two-statement join, with a decoy elsewhere in the corpus
    dict(id="N15", grade="C", host="lease:S333", page=2,
         text="Base Rent for Lease Year 1 is $41,000 per month.",
         extra_page=5,
         extra="Commencing with Lease Year 4 and continuing thereafter, Base Rent shall increase "
               "by exactly $3,500 per month over the immediately preceding Lease Year.",
         q="What is the monthly base rent for store S333 in Lease Year 6?",
         a="$51,500 per month ($41,000 base, increasing $3,500/month in each of years 4, 5 and 6).",
         decoy_host="lease:S334", decoy_page=2,
         decoy="Base Rent for Lease Year 1 is $41,000 per month, fixed for the entire Term with "
               "no escalation."),
    dict(id="N16", grade="C", host="carrier:CAM-007", page=1,
         text="Tenant's Proportionate Share at this location is 4.25%.",
         extra_page=3,
         extra="Of the total shown above, Controllable CAM expense for the reconciliation year "
               "was $1,840,000, against which Tenant remitted estimated payments of $71,000.",
         q="Does the tenant owe money or receive a refund on CAM reconciliation 007, and how much?",
         a="Tenant owes $7,200 (4.25% of $1,840,000 = $78,200, less $71,000 estimated payments).",
         decoy_host="carrier:CAM-008", decoy_page=1,
         decoy="Tenant's Proportionate Share at this location is 4.25%, and estimated payments "
               "for the year exactly equalled the reconciled amount, leaving no balance due."),
    dict(id="N17", grade="C", host="lease:S360", page=0,
         text="Exhibit C (Option Rider) fixes the Option Outside Date for the first extension "
              "option at October 31, 2029.",
         extra_page=5,
         extra="Any notice given under the Option Rider must be delivered not less than 270 days "
               "prior to the Option Outside Date, time being of the essence.",
         q="What is the last day store S360 can deliver notice under its Option Rider?",
         a="February 3, 2029 (270 days before the October 31, 2029 Option Outside Date).",
         decoy_host="lease:S361", decoy_page=0,
         decoy="Exhibit C (Option Rider) fixes the Option Outside Date for the first extension "
               "option at October 31, 2029, and notice thereunder must be delivered not less than "
               "180 days prior to that date."),
    dict(id="N18", grade="C", host="distractor:EQUIP-03", page=0,
         text="The monthly equipment lease payment is $1,875, covering four compactors.",
         extra_page=1,
         extra="Two of the four compactors were removed from service in the eleventh month and "
               "the payment was reduced pro rata for the remainder of the 36-month schedule.",
         q="What does the equipment schedule with four compactors cost per month after the "
           "reduction, and for how many months does that reduced amount run?",
         a="$937.50 per month for the final 25 months of the 36-month schedule.",
         decoy_host="distractor:EQUIP-05", decoy_page=1,
         decoy="The monthly equipment lease payment is $1,875, covering four compactors, and "
               "remains unchanged for the full 36-month schedule."),
]


# ===========================================================================
# Filler -- realistic bulk so pages are document-shaped, not clause fragments.
# ===========================================================================

FILLER = {
    "notices": "20.1 Notices\nAll notices required or permitted hereunder shall be in writing and "
               "shall be deemed given when delivered personally, one (1) business day after "
               "deposit with a nationally recognized overnight courier, or three (3) business days "
               "after deposit in the United States mail, certified, return receipt requested, "
               "addressed to the parties at the addresses set forth in Article 1 or such other "
               "address as either party may designate by notice given in accordance herewith.",
    "estoppel": "19.3 Estoppel Certificates\nEach party shall, within fifteen (15) business days "
                "after written request, execute and deliver a certificate stating whether this "
                "Lease is in full force and effect, whether it has been modified, the dates to "
                "which rent has been paid, and whether to the certifying party's knowledge any "
                "default exists. Failure to deliver within such period shall be conclusive "
                "evidence that this Lease is in full force and effect without modification.",
    "insurance": "13.1 Tenant's Insurance\nTenant shall maintain commercial general liability "
                 "insurance with limits of not less than $2,000,000 per occurrence and $5,000,000 "
                 "general aggregate, property insurance on Tenant's trade fixtures and inventory "
                 "at full replacement cost, workers' compensation at statutory limits, and "
                 "business interruption coverage of not less than twelve (12) months. Landlord "
                 "shall be named as an additional insured on the liability policy.",
    "utilities": "5.1 Utilities\nTenant shall pay directly to the applicable utility provider all "
                 "charges for electricity, gas, telephone and data service consumed within the "
                 "Premises. Water and sewer service shall be billed by Landlord to Tenant based "
                 "on Tenant's Proportionate Share unless separately metered. Landlord shall not "
                 "be liable for any interruption in utility service not caused by Landlord's "
                 "gross negligence or wilful misconduct.",
    "alterations": "7.1 Alterations\nTenant shall not make any structural alteration to the "
                   "Premises without Landlord's prior written consent. Non-structural alterations "
                   "costing less than $50,000 in the aggregate in any twelve (12) month period may "
                   "be made without consent upon ten (10) days' prior notice. All alterations shall "
                   "be performed by licensed contractors, in compliance with applicable law, and "
                   "free of mechanic's liens.",
    "default": "15.1 Events of Default\nEach of the following constitutes an Event of Default: "
               "(a) failure to pay Base Rent or Additional Rent within five (5) business days "
               "after written notice; (b) failure to perform any other covenant within thirty (30) "
               "days after written notice, provided that if such cure reasonably requires longer, "
               "no default occurs so long as Tenant commences within said period and diligently "
               "prosecutes the cure to completion; (c) assignment for the benefit of creditors; "
               "(d) abandonment of the Premises for sixty (60) consecutive days.",
    "quiet": "18.2 Quiet Enjoyment\nLandlord covenants that Tenant, upon paying rent and performing "
             "its obligations hereunder, shall peaceably and quietly hold and enjoy the Premises "
             "for the Term without hindrance from Landlord or anyone claiming by, through or "
             "under Landlord, subject to the terms of this Lease and to all matters of record.",
    "compliance": "8.1 Compliance with Law\nTenant shall comply with all applicable federal, state "
                  "and local laws, ordinances and regulations governing its use of the Premises, "
                  "including the Americans with Disabilities Act with respect to the interior of "
                  "the Premises. Landlord shall be responsible for ADA compliance of the Common "
                  "Areas and of the structural elements of the Building.",
    "surrender": "16.4 Surrender\nUpon expiration or earlier termination, Tenant shall surrender "
                 "the Premises broom clean and in good order, ordinary wear and tear excepted, "
                 "shall remove its trade fixtures and signage, and shall repair any damage caused "
                 "by such removal. Any property remaining after ten (10) days shall be deemed "
                 "abandoned and may be disposed of by Landlord at Tenant's expense.",
    "subordination": "18.5 Subordination and Attornment\nThis Lease is subject and subordinate to "
                     "any mortgage or ground lease now or hereafter encumbering the Shopping "
                     "Center, provided that the holder thereof delivers a commercially reasonable "
                     "non-disturbance agreement. Tenant shall attorn to any successor owner "
                     "acquiring title by foreclosure or deed in lieu thereof.",
}


class Corpus:
    def __init__(self, out):
        self.out = out
        self.docs_dir = os.path.join(out, "docs")
        os.makedirs(self.docs_dir, exist_ok=True)
        self.rng = random.Random(SEED)
        self.records = []
        self.needle_pages = {}        # needle id -> [logical page indices actually used]
        self.needle_map = {}          # host key -> list of needle dicts
        self.decoy_map = {}
        for n in NEEDLES:
            self.needle_map.setdefault(n["host"], []).append(n)
            if n.get("decoy_host"):
                self.decoy_map.setdefault(n["decoy_host"], []).append(n)

    # -- needle plumbing -------------------------------------------------
    def _inject(self, key, pages):
        """Insert planted facts and decoys into the authored pages of one document.

        A declared page deeper than the document is clamped to the last page, so record
        where each fact ACTUALLY landed -- ground truth has to describe the file that was
        written, not the file that was intended.
        """
        planted, at = [], {}
        for n in self.needle_map.get(key, []):
            idx = min(n["page"], len(pages) - 1)
            pages[idx] = pages[idx] + "\n\n" + n["text"]
            at[n["id"]] = [idx]
            if n.get("extra"):
                eidx = min(n["extra_page"], len(pages) - 1)
                pages[eidx] = pages[eidx] + "\n\n" + n["extra"]
                at[n["id"]].append(eidx)
            planted.append(n["id"])
        for n in self.decoy_map.get(key, []):
            idx = min(n["decoy_page"], len(pages) - 1)
            pages[idx] = pages[idx] + "\n\n" + n["decoy"]
            planted.append(n["id"] + "-decoy")
            at[n["id"] + "-decoy"] = [idx]
        self.needle_pages.update(at)
        return planted

    def _write(self, key, doc_id, filename, fmt, pages, meta, rows=None):
        planted = self._inject(key, pages)
        # A .jpg is ONE photographed page. A planted fact on page 3 would simply not exist
        # in the delivered file, which would make the needle unanswerable rather than hard.
        if fmt == "jpg" and planted and len(pages) > 1:
            fmt = "scan"
        # xlsx/csv render `rows`, never `pages` -- a needle injected into pages would be
        # silently dropped. Fail the build rather than ship an unanswerable question.
        if fmt in ("xlsx", "csv") and planted:
            raise RuntimeError(f"{key}: needles {planted} would be lost by the {fmt} renderer")
        ext = R.EXT_FOR_FORMAT[fmt]
        path = os.path.join(self.docs_dir, f"{filename}.{ext}")
        if fmt == "xlsx":
            n_pages = R.render_xlsx(path, rows, sheet_title=meta.get("title", "Sheet1")[:31])
        elif fmt == "csv":
            n_pages = R.render_csv(path, rows)
        else:
            n_pages = R.TEXT_RENDERERS[fmt](path, pages, title=meta.get("title", filename))
        rec = dict(doc_id=doc_id, key=key, filename=os.path.basename(path), format=fmt,
                   pages=n_pages, chars=sum(len(p) for p in pages), needles=planted)
        rec.update(meta)
        self.records.append(rec)
        return rec


# ---------------------------------------------------------------------------
# TIER 1 -- 130 store leases
# ---------------------------------------------------------------------------

def build_leases(c):
    rng = c.rng
    plans = {}
    for name, (table, pool) in S.DIMENSIONS.items():
        vals = list(pool)
        rng.shuffle(vals)
        plans[name] = vals[:130]

    for i in range(130):
        store = f"S{300 + i}"
        key = f"lease:{store}"
        city, st = S.CITIES[i % len(S.CITIES)]
        center = S.CENTERS[(i * 7 + 3) % len(S.CENTERS)]
        landlord = f"{city.upper().replace(' ', '')} {S.LANDLORD_SUFFIX[i % len(S.LANDLORD_SUFFIX)]}"
        sf = rng.randrange(8, 46) * 1000
        gla = rng.randrange(180, 900) * 1000          # drawn ONCE -- 1.2 and 1.8 must agree
        share = round(sf / gla * 100, 2)
        rent = rng.randrange(18, 96) * 1000
        deposit = rng.choice([f"${rent * 2:,} (cash)", f"${rent * 3:,} (Letter of Credit)",
                              "Waived", f"${rent:,} (cash)"])
        anchor = rng.choice(S.ANCHORS)
        ctx = dict(tons=rng.choice([15, 20, 25, 30, 40, 50, 60]),
                   threshold=rng.choice([2500, 5000, 7500, 10000, 15000]),
                   pct=rng.choice([3, 4, 5, 6]),
                   breakpoint=rng.randrange(180, 900) * 10000,
                   anchor=anchor, alt=rng.choice([2, 3, 4]), occ=rng.choice([50, 60, 65, 70]),
                   excl_sf=rng.choice([15000, 20000, 25000]))

        dims, uses_term, clauses = {}, {}, {}
        for dname, (table, _pool) in S.DIMENSIONS.items():
            val = plans[dname][i]
            term, tmpl = rng.choice(table[val])
            dims[dname] = val
            uses_term[dname] = term
            clauses[dname] = tmpl.format(**ctx)

        # Expiration is DERIVED from commencement + term. Drawing it independently produced
        # leases saying "Term: 5 years" over an eleven-year date range -- an unintended
        # contradiction that a careful reader would flag and that makes date questions
        # ungradeable.
        term_yrs = rng.choice([5, 7, 10, 15])
        cm, cd, cy = rng.choice(MONTHS), rng.randint(1, 28), rng.randint(2019, 2024)
        commence = f"{cm} {cd}, {cy}"
        expiry = f"{cm} {cd}, {cy + term_yrs}"

        p1 = (f"COMMERCIAL LEASE AGREEMENT\n\n"
              f"Store Identifier: {store}\n"
              f"Property: {center}, {city}, {st}\n"
              f"Landlord: {landlord}\n"
              f"Tenant: {S.TENANT}\n"
              f"Effective Date: {commence}\n\n"
              f"ARTICLE 1 -- BASIC LEASE TERMS\n\n"
              f"1.1 Premises: approximately {sf:,} square feet of gross leasable area, "
              f"as depicted on Exhibit A.\n"
              f"1.2 Shopping Center: {center}, containing approximately "
              f"{gla:,} square feet of gross leasable area.\n"
              f"1.3 Term: {term_yrs} years.\n"
              f"1.4 Commencement Date: {commence}\n"
              f"1.5 Expiration Date: {expiry}\n"
              f"1.6 Base Rent: ${rent:,} per month.\n"
              f"1.7 Security Deposit: {deposit}\n"
              f"1.8 Tenant's Proportionate Share: {share}%\n"
              f"1.9 Permitted Use: general retail merchandise sales.\n"
              f"1.10 Anchor Tenant of record: {anchor}\n"
              f"1.11 Notice Address (Landlord): 400 {rng.choice(['Market', 'Commerce', 'Union', 'Chestnut'])} "
              f"Street, Suite {rng.randrange(200, 1800)}, {city}, {st}\n"
              f"1.12 Notice Address (Tenant): Skyline Stores, Inc., Attn: Real Estate Counsel, "
              f"1 Skyline Plaza, Columbus, OH 43215\n\n"
              f"The parties have executed this Lease as of the Effective Date. The terms defined "
              f"in this Article 1 are used throughout this Lease and, in the event of conflict "
              f"between this Article 1 and any other provision, the other provision controls.")

        p2 = ("ARTICLE 2 -- RENT\n\n"
              "2.1 Base Rent\nTenant shall pay Base Rent in the amount set forth in Article 1, in "
              "advance, on the first day of each calendar month, without demand, deduction or "
              "setoff, at Landlord's notice address or such other place as Landlord may designate. "
              "Base Rent for any partial month shall be prorated on a per diem basis.\n\n"
              "2.2 Additional Rent\nAll sums other than Base Rent payable by Tenant hereunder, "
              "including Tenant's Proportionate Share of Common Area Maintenance costs, real "
              "estate taxes and insurance premiums, constitute Additional Rent and are collectible "
              "as rent.\n\n"
              + clauses["pct_rent"] + "\n\n"
              "2.5 Late Charges\nAny installment of Base Rent or Additional Rent not received "
              "within five (5) days after the due date shall bear a late charge equal to five "
              "percent (5%) of the delinquent amount, plus interest at the lesser of twelve "
              "percent (12%) per annum or the maximum lawful rate.\n\n"
              "2.6 Books and Records\nTenant shall keep complete records of Gross Sales for not "
              "less than three (3) years. Landlord may audit such records not more than once per "
              "Lease Year upon thirty (30) days' notice; if the audit discloses an understatement "
              "exceeding three percent (3%), Tenant shall bear the cost of the audit.")

        p3 = ("ARTICLE 3 -- COMMON AREA, TAXES AND INSURANCE\n\n"
              "3.1 Common Areas\nLandlord shall operate, manage and maintain the Common Areas, "
              "including parking areas, sidewalks, landscaping, lighting, drainage and service "
              "corridors, in a manner consistent with comparable shopping centers in the "
              "metropolitan area.\n\n"
              "3.2 Real Estate Taxes\nTenant shall pay Tenant's Proportionate Share of all real "
              "estate taxes and assessments levied against the Shopping Center. Tenant may, at its "
              "own expense, contest any assessment in Landlord's name upon prior written notice.\n\n"
              + clauses["cam_cap"] + "\n\n"
              + FILLER["insurance"] + "\n\n"
              + FILLER["utilities"])

        p4 = ("ARTICLE 6 -- MAINTENANCE AND REPAIRS\n\n"
              "6.1 Tenant's General Obligations\nTenant shall keep the interior of the Premises in "
              "good order and repair, including storefront glass, entry doors, interior walls, "
              "floor coverings, ceiling grid, lighting within the Premises and all trade fixtures, "
              "and shall provide janitorial service and pest control within the Premises at its "
              "sole cost.\n\n"
              "6.2 Landlord's General Obligations\nLandlord shall maintain the roof, foundation, "
              "load-bearing walls, structural elements, exterior surfaces and utility distribution "
              "systems up to the point of entry into the Premises.\n\n"
              + clauses["hvac"] + "\n\n"
              + FILLER["alterations"] + "\n\n"
              + FILLER["compliance"])

        p5 = ("ARTICLE 9 -- USE OF PREMISES\n\n"
              "9.1 Permitted Use\nThe Premises shall be used solely for the retail sale of general "
              "merchandise and for no other purpose without Landlord's prior written consent.\n\n"
              "9.2 Continuous Operation\nTenant shall continuously operate the Premises during the "
              "Shopping Center's standard hours, fully stocked and staffed, except during "
              "reasonable periods of remodelling not exceeding thirty (30) days in any Lease Year.\n\n"
              + clauses["exclusive"] + "\n\n"
              + clauses["radius"] + "\n\n"
              "ARTICLE 10 -- CO-TENANCY\n\n"
              "10.1 Shopping Center Composition\nLandlord represents that as of the Effective Date "
              f"the Shopping Center is anchored by {anchor} and is not less than eighty percent "
              "(80%) occupied by tenants open for business.\n\n"
              + clauses["cotenancy"])

        p6 = ("ARTICLE 12 -- ASSIGNMENT AND SUBLETTING\n\n"
              + clauses["assignment"] + "\n\n"
              "12.3 Continuing Liability\nNo assignment or subletting shall relieve Tenant of "
              "primary liability for the performance of all obligations under this Lease unless "
              "Landlord expressly releases Tenant in writing.\n\n"
              "ARTICLE 14 -- CASUALTY AND CONDEMNATION\n\n"
              + clauses["casualty"] + "\n\n"
              "14.5 Condemnation\nIf more than twenty percent (20%) of the Premises or more than "
              "thirty percent (30%) of the parking area is taken by eminent domain, either party "
              "may terminate this Lease. The condemnation award belongs to Landlord except that "
              "Tenant may pursue a separate claim for trade fixtures and moving expenses.\n\n"
              "ARTICLE 16 -- END OF TERM\n\n"
              + clauses["holdover"] + "\n\n"
              + FILLER["surrender"] + "\n\n"
              "ARTICLE 17 -- OPTIONS\n\n"
              + clauses["renewal"] + "\n\n"
              + FILLER["default"] + "\n\n"
              + FILLER["subordination"] + "\n\n"
              + FILLER["notices"] + "\n\n"
              "IN WITNESS WHEREOF the parties have executed this Lease as of the Effective Date.\n\n"
              f"LANDLORD: {landlord}          TENANT: {S.TENANT}\n\n"
              "By: ______________________          By: ______________________\n"
              "Name:                                Name:\n"
              "Title:                               Title:")

        long_form = (i % 5) != 4                      # 4 of every 5 run the full six pages
        pages = [p1, p2, p3, p4, p5, p6] if long_form else [p1, p2 + "\n\n" + p3, p4, p5, p6]

        fmt = _lease_format(i)
        meta = dict(tier=1, type="lease", store=store, title=f"{store} {center} Lease Agreement",
                    category=S.DEFAULT_CATEGORY, dims=dims, uses_term=uses_term,
                    scalars=dict(city=f"{city}, {st}", center=center, landlord=landlord,
                                 sqft=sf, center_gla=gla, prop_share=share, base_rent=rent,
                                 deposit=deposit, anchor=anchor, commencement=commence,
                                 expiration=expiry, term_years=term_yrs))
        c._write(key, f"L{i + 1:03d}", f"SKY-LEASE-{store}-{center.replace(' ', '')}", fmt,
                 pages, meta)


def _lease_format(i):
    """Deterministic by index. Scanned share is held near 10% of the whole corpus, which is
    the share james chose -- enough to exercise OCR without dominating ingest time."""
    m = i % 50
    if m in (7, 13, 17, 23, 31, 41, 47):
        return "scan"
    if m == 33:
        return "jpg"
    if m in (3, 11, 19, 27, 35, 43):
        return "docx"
    if m in (5, 15, 29, 39):
        return "txt"
    return "pdf"


# ---------------------------------------------------------------------------
# TIER 2 -- 45 near-miss distractors
# ---------------------------------------------------------------------------

def build_distractors(c):
    rng = c.rng
    n = 0

    for j in range(15):
        n += 1
        key = f"distractor:HVACPM-{j + 1:02d}"
        vendor = S.HVAC_VENDORS[j % len(S.HVAC_VENDORS)]
        store = f"S{300 + (j * 7) % 130}"
        city, st = S.CITIES[(j * 5) % len(S.CITIES)]
        rate = rng.randrange(240, 900) * 5
        pages = [
            (f"HVAC PREVENTIVE MAINTENANCE AGREEMENT\n\n"
             f"Agreement No. PM-{2600 + j}\nService Provider: {vendor}\n"
             f"Customer: {S.TENANT}\nService Location: Store {store}, {city}, {st}\n"
             f"Term: 36 months, auto-renewing\nAnnual Service Fee: ${rate * 12:,}\n\n"
             "1. SCOPE OF SERVICE\nProvider shall perform quarterly preventive maintenance on all "
             "HVAC equipment at the Service Location, including rooftop package units, split "
             "systems, exhaust fans and make-up air units. Each visit includes filter replacement, "
             "belt inspection and tensioning, coil cleaning, condensate line clearing, refrigerant "
             "pressure verification and control calibration.\n\n"
             "2. WHAT THIS AGREEMENT IS NOT\nThis Agreement allocates responsibility for HVAC "
             "SERVICE LABOR between Provider and Customer. It does not determine, modify or "
             "evidence the allocation of HVAC maintenance, repair or replacement responsibility "
             "as between landlord and tenant under any lease. Customer is directed to the "
             "applicable lease for that allocation."),
            ("3. RESPONSIBILITY MATRIX\n\n"
             "Provider is responsible for: scheduled preventive maintenance visits, filters, "
             "belts, refrigerant top-off up to two pounds per visit, and written condition "
             "reporting.\n\n"
             "Customer is responsible for: unobstructed roof access, electrical power at the "
             "unit, replacement of compressors, condensers and complete units, and all repairs "
             "exceeding the labor covered hereunder.\n\n"
             f"4. RATES\nStraight-time labor ${rng.randrange(95, 165)}/hour. Emergency and "
             f"after-hours labor at 1.5x. Parts at cost plus fifteen percent (15%).\n\n"
             "5. EQUIPMENT SCHEDULE\nUnit RTU-1  25 ton  installed 2016\nUnit RTU-2  25 ton  "
             "installed 2016\nUnit RTU-3  15 ton  installed 2019\nExhaust EF-1 through EF-4\n\n"
             "6. LIMITATION\nProvider's aggregate liability under this Agreement shall not exceed "
             "the fees paid in the twelve months preceding the claim."),
            ("7. TERMINATION\nEither party may terminate upon sixty (60) days' written notice. "
             "Customer shall pay for all services rendered through the effective date of "
             "termination.\n\n"
             "8. INSURANCE\nProvider shall maintain commercial general liability of $2,000,000 per "
             "occurrence and shall name Customer as additional insured.\n\n"
             "9. ENTIRE AGREEMENT\nThis Agreement, together with the Equipment Schedule, is the "
             "entire agreement between the parties with respect to HVAC preventive maintenance at "
             "the Service Location and supersedes all prior proposals.\n\n"
             f"ACCEPTED:\n\n{vendor}                    {S.TENANT}\n\n"
             "By: ____________________            By: ____________________"),
        ]
        fmt = "pdf" if j % 6 else "docx"
        c._write(key, f"D{n:03d}", f"SKY-HVACPM-{j + 1:02d}-{store}", fmt, pages,
                 dict(tier=2, type="hvac_service_agreement", store=store,
                      title=f"HVAC PM Agreement {store}", category="Facilities-Vendors",
                      distractor_for=["hvac"]))

    for j in range(8):
        n += 1
        key = f"distractor:ROOF-{j + 1:02d}"
        store = f"S{300 + (j * 13 + 4) % 130}"
        pages = [
            (f"ROOF SYSTEM WARRANTY CERTIFICATE\n\nCertificate No. RW-{9100 + j}\n"
             f"Building: Store {store}\nManufacturer: Corvale Roofing Systems\n"
             f"Warranty Period: {rng.choice([10, 15, 20])} years from substantial completion\n\n"
             "COVERAGE\nManufacturer warrants the membrane, insulation and flashing against leaks "
             "caused by defects in manufacture or in workmanship of the approved applicator. "
             "Manufacturer will, at its option, repair or replace defective materials.\n\n"
             "LANDLORD RESPONSIBILITY NOTE\nThis certificate names the building owner as the "
             "warranty holder. Nothing in this certificate allocates maintenance responsibility "
             "between landlord and tenant; it is a manufacturer's product warranty only."),
            ("EXCLUSIONS\nThis warranty does not cover damage caused by: (a) HVAC equipment "
             "installed, serviced or removed after the original roof installation, including "
             "penetrations for rooftop units; (b) foot traffic other than on designated walkway "
             "pads; (c) ponding water resulting from deck deflection; (d) acts of God; "
             "(e) alteration by any party other than an approved applicator.\n\n"
             "OWNER OBLIGATIONS\nOwner shall inspect the roof semi-annually, keep drains clear, "
             "and notify Manufacturer in writing within thirty (30) days of discovering any leak. "
             "Failure to do so voids this warranty as to that condition.\n\n"
             "TRANSFERABILITY\nThis warranty is transferable once, to a subsequent owner, upon "
             "written application and payment of a transfer fee of $1,500."),
        ]
        c._write(key, f"D{n:03d}", f"SKY-ROOFWTY-{j + 1:02d}-{store}",
                 "pdf" if j % 4 else "scan", pages,
                 dict(tier=2, type="roof_warranty", store=store,
                      title=f"Roof Warranty {store}", category="Facilities-Vendors",
                      distractor_for=["hvac", "maintenance responsibility"]))

    for j in range(7):
        n += 1
        key = f"distractor:ELEV-{j + 1:02d}"
        store = f"S{300 + (j * 17 + 9) % 130}"
        pages = [
            (f"ELEVATOR AND ESCALATOR SERVICE CONTRACT\n\nContract No. EE-{4400 + j}\n"
             f"Contractor: Halvorsen Vertical Transport, Inc.\nLocation: Store {store}\n"
             f"Units Covered: {rng.choice([1, 2, 3])} passenger elevator(s), "
             f"{rng.choice([0, 2])} escalator(s)\nMonthly Fee: ${rng.randrange(60, 220) * 10:,}\n\n"
             "SCOPE\nContractor shall provide monthly examination, lubrication and adjustment of "
             "covered units, and shall furnish all parts required by normal wear, excluding items "
             "listed under Exclusions.\n\n"
             "ALLOCATION\nContractor's responsibility is limited to the covered units. "
             "Responsibility for building systems generally, including climate control equipment, "
             "is outside the scope of this contract and is governed by the applicable lease."),
            ("EXCLUSIONS\nNot covered: hoistway enclosures, car enclosures, doors and door frames, "
             "power feeders, main line switches, cab flooring and finishes, communications "
             "equipment, and any damage caused by misuse, vandalism or water intrusion.\n\n"
             "CALLBACKS\nContractor shall respond to entrapment calls within sixty (60) minutes "
             "and to non-entrapment callbacks within four (4) hours during business days. "
             "Overtime callbacks are billable at 1.5x.\n\n"
             "REGULATORY\nContractor shall perform annual and five-year safety tests as required "
             "by the applicable state elevator code and shall file all certificates.\n\n"
             "TERM\nFive (5) years, with automatic renewal for successive five-year periods "
             "unless terminated by either party upon ninety (90) days' written notice given not "
             "less than one hundred eighty (180) days before the end of the then-current term."),
        ]
        c._write(key, f"D{n:03d}", f"SKY-ELEVSVC-{j + 1:02d}-{store}", "pdf", pages,
                 dict(tier=2, type="elevator_contract", store=store,
                      title=f"Elevator Service {store}", category="Facilities-Vendors",
                      distractor_for=["maintenance responsibility"]))

    for j in range(8):
        n += 1
        key = f"distractor:FIRE-{j + 1:02d}"
        store = f"S{300 + (j * 11 + 6) % 130}"
        result = "PASS" if j % 4 else "FAIL -- deficiencies noted"
        pages = [
            (f"FIRE SUPPRESSION SYSTEM INSPECTION REPORT\n\nReport No. FS-{7700 + j}\n"
             f"Inspector: Ardent Fire Protection, License #FP-{rng.randrange(10000, 99999)}\n"
             f"Location: Store {store}\nInspection Type: Annual, NFPA 25\n"
             f"Result: {result}\n\n"
             "SYSTEMS INSPECTED\nWet pipe sprinkler system, fire pump, backflow preventer, "
             "standpipe, kitchen hood suppression (where present), fire alarm interface, and "
             "portable extinguishers.\n\n"
             "FINDINGS\nControl valves verified open and supervised. Gauges within range. "
             "Inspector's test connection flowed within acceptable time. Sprinkler heads free of "
             "obstruction and corrosion except as noted below. All extinguishers tagged and "
             "within hydrostatic test date."),
            (("DEFICIENCIES\n1. Three sprinkler heads in the stockroom obstructed by pallet "
              "storage within 18 inches of deflector -- correct immediately.\n2. Fire pump "
              "churn test exceeded allowable pressure by 4 psi -- schedule adjustment.\n"
              "3. Backflow preventer tag expired.\n\n" if result.startswith("FAIL") else
              "DEFICIENCIES\nNone noted at the time of inspection.\n\n") +
             "RESPONSIBILITY\nCorrection of deficiencies is the responsibility of the party "
             "designated in the applicable lease or service agreement. This report makes no "
             "determination of that allocation and should not be relied upon for it.\n\n"
             "NEXT INSPECTION DUE\nTwelve (12) months from the date of this report. Quarterly "
             "visual inspections remain required in the interim.\n\n"
             "Inspector signature: ____________________"),
        ]
        c._write(key, f"D{n:03d}", f"SKY-FIREINSP-{j + 1:02d}-{store}",
                 "scan" if j == 2 else "pdf", pages,
                 dict(tier=2, type="fire_inspection", store=store,
                      title=f"Fire Inspection {store}", category="Facilities-Compliance",
                      distractor_for=["maintenance responsibility", "compliance"]))

    for j in range(7):
        n += 1
        key = f"distractor:EQUIP-{j + 1:02d}"
        store = f"S{300 + (j * 19 + 2) % 130}"
        gear = ["four compactors", "two forklifts", "a fleet of point-of-sale terminals",
                "three floor scrubbers", "four compactors", "two box balers",
                "a pallet racking system"][j]
        pay = 1875 if j in (2, 4) else rng.randrange(60, 340) * 25
        pages = [
            (f"EQUIPMENT LEASE SCHEDULE\n\nSchedule No. EQ-{5500 + j} to Master Equipment Lease "
             f"dated {dstr(rng, 2020, 2024)}\nLessor: Wexford Equipment Finance LLC\n"
             f"Lessee: {S.TENANT}\nEquipment Location: Store {store}\n"
             f"Equipment: {gear}\nSchedule Term: 36 months\n"
             f"Monthly Payment: ${pay:,}\n\n"
             "1. LEASE OF EQUIPMENT\nLessor leases to Lessee the equipment described above. This "
             "is a lease of personal property only. It conveys no interest in real property and "
             "is not a lease of the premises."),
            ("2. MAINTENANCE\nLessee shall at its sole expense maintain the Equipment in good "
             "operating condition and shall maintain a manufacturer-approved service contract "
             "throughout the Schedule Term.\n\n"
             "3. LOCATION\nEquipment shall not be moved from the Equipment Location without "
             "Lessor's prior written consent.\n\n"
             "4. INSURANCE\nLessee shall insure the Equipment for its full replacement value and "
             "name Lessor as loss payee.\n\n"
             "5. END OF TERM\nAt expiration Lessee shall return the Equipment freight prepaid, in "
             "good order, or purchase it for its then fair market value.\n\n"
             "6. NO REAL PROPERTY RIGHTS\nFor avoidance of doubt, nothing in this Schedule "
             "affects any lease of real property, and the term 'lease' as used herein refers "
             "solely to the equipment identified above."),
        ]
        fmt = "xlsx" if j == 6 else "pdf"
        rows = ([["Asset Tag", "Description", "Serial", "In Service", "Monthly"]] +
                [[f"EQ-{5500 + j}-{k:02d}", gear.split(",")[0], f"SN{rng.randrange(10**7, 10**8)}",
                  dstr(rng, 2020, 2024), pay] for k in range(1, 9)])
        c._write(key, f"D{n:03d}", f"SKY-EQUIPLEASE-{j + 1:02d}-{store}", fmt, pages,
                 dict(tier=2, type="equipment_lease", store=store,
                      title=f"Equipment Lease Schedule {store}", category="Facilities-Vendors",
                      distractor_for=["lease"]), rows=rows)


# ---------------------------------------------------------------------------
# TIER 3 -- 35 multi-hop / supersession
# ---------------------------------------------------------------------------

AMEND_FLIP = [("S303", "tenant"), ("S311", "landlord"), ("S318", "landlord"), ("S325", "tenant"),
              ("S337", "landlord"), ("S344", "split"), ("S352", "landlord"), ("S369", "tenant")]

FLIP_TEXT = {
    "landlord": "From and after the Amendment Effective Date, Section 6.3 of the Lease is deleted "
                "in its entirety and replaced with the following: 'LANDLORD'S RESPONSIBILITY. "
                "Landlord shall perform, at Landlord's sole cost and expense, all maintenance, "
                "repair and replacement of the HVAC equipment serving the Premises.' Tenant shall "
                "have no further obligation with respect to HVAC maintenance, repair or "
                "replacement accruing on or after the Amendment Effective Date.",
    "tenant": "From and after the Amendment Effective Date, Section 6.3 of the Lease is deleted in "
              "its entirety and replaced with the following: 'TENANT'S RESPONSIBILITY. Tenant "
              "shall, at its sole cost and expense, maintain, repair and replace all HVAC "
              "equipment exclusively serving the Premises, and shall maintain a quarterly service "
              "contract with a licensed mechanical contractor.' In consideration, Base Rent is "
              "reduced by $1,200 per month for the remainder of the Term.",
    "split": "From and after the Amendment Effective Date, Section 6.3 of the Lease is amended to "
             "provide SPLIT RESPONSIBILITY: Tenant shall bear HVAC repair costs up to $7,500 per "
             "occurrence and Landlord shall bear costs above that amount and all unit replacement.",
}


def build_multihop(c):
    rng = c.rng
    n = 0
    by_store = {r["store"]: r for r in c.records if r.get("type") == "lease"}

    # 18 amendments -- 8 flip the HVAC answer, 10 change money/dates
    amend_specs = [(s, v, "hvac") for s, v in AMEND_FLIP]
    money_stores = ["S306", "S314", "S329", "S341", "S356", "S363", "S377", "S384", "S396", "S412"]
    amend_specs += [(s, None, "money") for s in money_stores]

    for j, (store, newval, kind) in enumerate(amend_specs):
        n += 1
        base = by_store[store]
        key = f"amend:{store}"
        eff = dstr(rng, 2024, 2026)
        # Derived from the index, NOT from the RNG: a filename that moves whenever the random
        # stream shifts orphans the previous run's files on every regeneration.
        ordinal = ["FIRST", "SECOND", "THIRD"][j % 3]
        if kind == "hvac":
            # An amendment that restates the base value is not a supersession test. If the
            # dimension draw happened to land on the same value, move the flip elsewhere.
            if base["dims"]["hvac"] == newval:
                newval = next(v for v in ("landlord", "tenant", "split")
                              if v != base["dims"]["hvac"])
            body = ("2. AMENDMENT TO SECTION 6.3 (MAINTENANCE -- HVAC)\n" + FLIP_TEXT[newval])
            note = f"hvac {base['dims']['hvac']} -> {newval}"
        else:
            newrent = base["scalars"]["base_rent"] + rng.randrange(2, 14) * 500
            newexp = dstr(rng, 2031, 2038)
            body = (f"2. AMENDMENT TO ARTICLE 1 (BASIC LEASE TERMS)\n"
                    f"Section 1.5 is amended to extend the Expiration Date to {newexp}. "
                    f"Section 1.6 is amended so that, commencing on the Amendment Effective Date, "
                    f"Base Rent shall be ${newrent:,} per month. Section 3.4 is amended so that "
                    f"Controllable CAM Costs shall not increase by more than four percent (4%) per "
                    f"calendar year, cumulative and compounding, superseding any prior cap.")
            note = f"rent -> ${newrent:,}; expiry -> {newexp}; cam_cap -> 4%"
        pages = [
            (f"{ordinal} AMENDMENT TO COMMERCIAL LEASE AGREEMENT\n\n"
             f"Store Identifier: {store}\nProperty: {base['scalars']['center']}, "
             f"{base['scalars']['city']}\nLandlord: {base['scalars']['landlord']}\n"
             f"Tenant: {S.TENANT}\nAmendment Effective Date: {eff}\n\n"
             f"RECITALS\nA. Landlord and Tenant are parties to that certain Commercial Lease "
             f"Agreement dated {base['scalars']['commencement']} covering approximately "
             f"{base['scalars']['sqft']:,} square feet at the above property (the 'Lease').\n"
             f"B. The parties desire to amend the Lease as set forth below.\n\n"
             "NOW THEREFORE, for good and valuable consideration, the parties agree:\n\n"
             "1. DEFINED TERMS\nCapitalized terms not defined herein have the meanings given in "
             "the Lease.\n\n" + body),
            ("3. NO OTHER CHANGES\nExcept as expressly amended hereby, the Lease remains in full "
             "force and effect and is hereby ratified and confirmed by the parties. In the event "
             "of any conflict between this Amendment and the Lease, THIS AMENDMENT CONTROLS.\n\n"
             "4. REPRESENTATIONS\nEach party represents that it has full authority to enter into "
             "this Amendment and that no consent of any third party is required which has not "
             "been obtained.\n\n"
             "5. COUNTERPARTS\nThis Amendment may be executed in counterparts, each of which is "
             "an original and all of which together constitute one instrument. Electronic "
             "signatures have the same effect as originals.\n\n"
             f"LANDLORD: {base['scalars']['landlord']}      TENANT: {S.TENANT}\n\n"
             "By: ____________________                By: ____________________"),
        ]
        fmt = ["pdf", "pdf", "docx", "pdf", "scan", "pdf", "pdf", "docx"][j % 8]
        c._write(key, f"M{n:03d}", f"SKY-AMEND-{store}-{ordinal.title()}", fmt, pages,
                 dict(tier=3, type="lease_amendment", store=store, amends=base["filename"],
                      title=f"{ordinal.title()} Amendment {store}", category=S.DEFAULT_CATEGORY,
                      supersedes=note, new_hvac=newval))

    # 8 estoppel certificates -- 3 of them deliberately WRONG
    estoppel_stores = ["S301", "S320", "S334", "S348", "S355", "S372", "S390", "S408"]
    wrong = {"S334", "S372", "S408"}
    for j, store in enumerate(estoppel_stores):
        n += 1
        base = by_store[store]
        key = f"estoppel:{store}"
        truth_rent = base["scalars"]["base_rent"]
        stated = truth_rent + 5000 if store in wrong else truth_rent
        stated_exp = "December 31, 2030" if store in wrong else base["scalars"]["expiration"]
        pages = [
            (f"TENANT ESTOPPEL CERTIFICATE\n\nTo: Prospective Purchaser and its Lender\n"
             f"Re: Store {store}, {base['scalars']['center']}, {base['scalars']['city']}\n"
             f"Date: {dstr(rng, 2025, 2026)}\n\n"
             f"The undersigned, {S.TENANT} ('Tenant'), certifies as follows:\n\n"
             f"1. Tenant is the tenant under a Commercial Lease Agreement dated "
             f"{base['scalars']['commencement']} (the 'Lease').\n"
             f"2. The Lease is in full force and effect and has not been modified except as "
             f"listed on Schedule 1 hereto.\n"
             f"3. Current monthly Base Rent is ${stated:,}.\n"
             f"4. The Lease expires on {stated_exp}.\n"
             f"5. Tenant has paid rent through the current month and has not prepaid more than "
             f"one month in advance.\n"
             f"6. To Tenant's knowledge no default exists on the part of either party.\n"
             f"7. Tenant holds a security deposit balance of {base['scalars']['deposit']}.\n\n"
             "This certificate is given with the understanding that the addressees will rely upon "
             "it in connection with a purchase and financing of the Shopping Center." +
             ("\n\nNOTE: This certificate was prepared by the property manager from its own "
              "records and has not been reconciled against the executed Lease and all amendments."
              if store in wrong else "")),
        ]
        c._write(key, f"M{n:03d}", f"SKY-ESTOPPEL-{store}", "pdf" if j % 3 else "docx", pages,
                 dict(tier=3, type="estoppel", store=store, title=f"Estoppel {store}",
                      category=S.DEFAULT_CATEGORY, accurate=store not in wrong,
                      stated_rent=stated, true_rent=truth_rent))

    # 5 assignment & assumption agreements -- tenant entity changes
    for j, store in enumerate(["S308", "S331", "S358", "S379", "S401"]):
        n += 1
        base = by_store[store]
        key = f"assign:{store}"
        newco = ["SKYLINE RETAIL HOLDINGS II LLC", "NORTHVIEW SKY OPERATING CO. LLC",
                 "SKYLINE STORES OF TEXAS, INC.", "BRANTFORD RETAIL GROUP LLC",
                 "SKY-MIDWEST OPERATING LLC"][j]
        pages = [
            (f"ASSIGNMENT AND ASSUMPTION OF LEASE\n\n"
             f"Store Identifier: {store}\nPremises: {base['scalars']['center']}, "
             f"{base['scalars']['city']}\nEffective Date: {dstr(rng, 2024, 2026)}\n\n"
             f"Assignor: {S.TENANT}\nAssignee: {newco}\n"
             f"Landlord: {base['scalars']['landlord']}\n\n"
             f"1. ASSIGNMENT\nAssignor assigns to Assignee all of Assignor's right, title and "
             f"interest in and to the Commercial Lease Agreement dated "
             f"{base['scalars']['commencement']} (the 'Lease').\n\n"
             "2. ASSUMPTION\nAssignee assumes and agrees to perform all obligations of the tenant "
             "under the Lease accruing from and after the Effective Date, including all "
             "maintenance and repair obligations allocated to the tenant thereunder.\n\n"
             "3. LANDLORD CONSENT\nLandlord consents to this assignment. Such consent does not "
             "release Assignor, which remains primarily liable for all obligations under the "
             "Lease unless and until expressly released in writing.\n\n"
             "4. NO MODIFICATION\nThis Assignment does not modify any substantive term of the "
             "Lease. The allocation of maintenance and repair responsibility set out in the Lease "
             "is unchanged and now binds Assignee."),
        ]
        c._write(key, f"M{n:03d}", f"SKY-ASSIGN-{store}", "pdf", pages,
                 dict(tier=3, type="assignment", store=store, new_tenant=newco,
                      title=f"Assignment {store}", category=S.DEFAULT_CATEGORY))

    # 4-document MSA -> SOW -> Change Order chain
    chain = [
        ("MSA", "MASTER SERVICES AGREEMENT",
         "Vantage Facilities Group LLC and Skyline Stores, Inc. enter into this Master Services "
         "Agreement governing all facilities maintenance services. Rates, scope and locations are "
         "established by individual Statements of Work. The blended hourly rate under this MSA is "
         "$118. Payment terms are net forty-five (45) days. Either party may terminate for "
         "convenience on ninety (90) days' notice. No Statement of Work may vary the payment terms "
         "or the limitation of liability set out in this MSA; a Statement of Work may vary rates "
         "and scope only."),
        ("SOW1", "STATEMENT OF WORK NO. 1 -- SOUTHEAST REGION",
         "Covers 22 stores in the southeast region. Scope: quarterly interior and exterior "
         "preventive maintenance, lamp replacement, and door hardware service. Rate for this SOW "
         "is the MSA blended rate of $118 per hour. Term: 24 months from execution. Estimated "
         "annual value $412,000."),
        ("SOW2", "STATEMENT OF WORK NO. 2 -- NORTHEAST REGION",
         "Covers 19 stores in the northeast region. Scope: as SOW No. 1, plus snow and ice "
         "management at 11 of the 19 locations. Rate for this SOW is $131 per hour, varying the "
         "MSA blended rate as expressly permitted by Section 4 of the MSA. Term: 24 months. "
         "Estimated annual value $505,000."),
        ("CO1", "CHANGE ORDER NO. 1 TO STATEMENT OF WORK NO. 2",
         "Effective immediately, snow and ice management is removed from the scope of SOW No. 2 "
         "and the hourly rate for the remaining scope reverts to $118. Eight additional stores are "
         "added to SOW No. 2, bringing the covered count to 27. Payment terms remain net "
         "forty-five (45) days per the MSA. Revised estimated annual value $486,000."),
    ]
    for j, (tag, title, body) in enumerate(chain):
        n += 1
        key = f"chain:{tag}"
        pages = [
            (f"{title}\n\nVantage Facilities Group LLC ('Provider')\n{S.TENANT} ('Client')\n"
             f"Document Reference: VFG-{tag}\nEffective Date: {dstr(rng, 2024, 2025)}\n\n"
             f"{body}\n\n"
             "INTERPRETATION\nThis document is to be read together with the Master Services "
             "Agreement and every Statement of Work and Change Order issued thereunder. In the "
             "event of conflict, the most recently executed Change Order controls as to the "
             "Statement of Work it amends; the Master Services Agreement controls as to payment "
             "terms and limitation of liability in all cases."),
        ]
        c._write(key, f"M{n:03d}", f"SKY-VFG-{tag}", "pdf" if j != 3 else "docx", pages,
                 dict(tier=3, type="services_chain", chain_step=tag, title=title,
                      category="Facilities-Vendors"))


# ---------------------------------------------------------------------------
# TIER 4 -- 30 needle carriers: long, dull, high-volume
# ---------------------------------------------------------------------------

CAM_LINES = [
    "Parking lot sweeping", "Parking lot striping", "Landscaping -- contract",
    "Landscaping -- seasonal color", "Irrigation repair", "Snow removal -- plowing",
    "Snow removal -- ice melt", "Exterior lighting -- maintenance", "Exterior lighting -- utility",
    "Common area electricity", "Common area water and sewer", "Trash removal -- compactor",
    "Trash removal -- recycling", "Security patrol", "Security -- camera system",
    "Pressure washing", "Storm drain cleaning", "Roof inspection -- common",
    "Pest control -- common areas", "Fire system monitoring", "Signage maintenance",
    "Management fee", "Administrative fee", "Property insurance allocation",
    "Sidewalk repair", "Curb and gutter repair", "Bollard replacement", "Painting -- common",
    "HVAC -- common area units", "Elevator service -- common", "Window cleaning -- common",
    "Directory and wayfinding", "Seasonal decor", "Traffic control", "Striping -- fire lanes",
]


def build_carriers(c):
    rng = c.rng
    n = 0

    for j in range(8):
        n += 1
        key = f"carrier:CAM-{j + 1:03d}"
        store = f"S{300 + (j * 23 + 5) % 130}"
        year = 2024 + (j % 2)
        share = round(rng.uniform(2.1, 6.4), 2) if j not in (6, 7) else 4.25
        lines, total = [], 0
        for k, item in enumerate(CAM_LINES):
            amt = rng.randrange(400, 62000)
            total += amt
            lines.append(f"Line {k + 1:2d}  {item:<38}{amt:>12,.2f}")
        pages = [
            (f"COMMON AREA MAINTENANCE RECONCILIATION STATEMENT\n\n"
             f"Property: {S.CENTERS[j % len(S.CENTERS)]}\nTenant: {S.TENANT}\n"
             f"Store: {store}\nReconciliation Year: {year}\n"
             f"Statement Date: {dstr(rng, 2025, 2026)}\n\n"
             f"Tenant's Proportionate Share at this location is {share}%.\n\n"
             "SUMMARY\nThis statement reconciles estimated Common Area Maintenance payments made "
             "by Tenant during the Reconciliation Year against actual costs incurred by Landlord. "
             "Tenant has one hundred twenty (120) days from the Statement Date to audit these "
             "figures as provided in the Lease."),
            ("EXPENSE DETAIL -- PART 1\n\n" + "\n".join(lines[:18])),
            ("EXPENSE DETAIL -- PART 2\n\n" + "\n".join(lines[18:]) +
             f"\n\nTOTAL COMMON AREA MAINTENANCE EXPENSE{total:>22,.2f}\n\n"
             "NOTES\nControllable costs are subject to any cap set out in the Lease. "
             "Non-controllable costs (taxes, insurance, utilities, snow removal) are excluded "
             "from cap computations. Capital expenditures are amortized over useful life in "
             "accordance with the Lease."),
        ]
        c._write(key, f"C{n:03d}", f"SKY-CAMRECON-{j + 1:03d}-{store}-{year}",
                 "xlsx" if j in (1, 5) else "pdf", pages,
                 dict(tier=4, type="cam_reconciliation", store=store, year=year,
                      title=f"CAM Reconciliation {store} {year}",
                      category="Facilities-Accounting"),
                 rows=[["Line", "Description", "Amount"]] +
                      [[k + 1, item, rng.randrange(400, 62000)] for k, item in enumerate(CAM_LINES)])

    for j in range(6):
        n += 1
        key = f"carrier:COI-{j + 1:03d}"
        rows = [["Location", "Store", "Policy No.", "Carrier", "GL Limit", "Property Limit",
                 "Flood", "Effective", "Expires"]]
        for k in range(28):
            sid = f"S{300 + (j * 28 + k) % 130}"
            rows.append([S.CENTERS[k % len(S.CENTERS)], sid,
                         f"CGL-{rng.randrange(10**6, 10**7)}",
                         rng.choice(["Ardmore Mutual", "Ketteridge Casualty", "Northline P&C"]),
                         "2,000,000", f"{rng.randrange(3, 24) * 1000000:,}",
                         "Included" if not (j == 1 and k == 12) else "EXCLUDED",
                         dstr(rng, 2025, 2025), dstr(rng, 2026, 2027)])
        pages = [
            (f"SCHEDULE OF INSURANCE -- CERTIFICATE SUMMARY {j + 1:03d}\n\n"
             f"Named Insured: {S.TENANT}\nBroker: Halloran Risk Advisors\n"
             f"Policy Period: {dstr(rng, 2025, 2025)} to {dstr(rng, 2026, 2026)}\n\n"
             "This schedule summarizes certificates of insurance on file for the locations listed. "
             "Landlords named as additional insured where required by lease. Coverage is placed on "
             "a blanket basis with per-location sublimits as shown.\n\n" +
             "\n".join(f"{r[1]:<6}{r[0]:<32}{r[3]:<22}{r[6]}" for r in rows[1:15])),
            ("SCHEDULE CONTINUED\n\n" +
             "\n".join(f"{r[1]:<6}{r[0]:<32}{r[3]:<22}{r[6]}" for r in rows[15:]) +
             "\n\nCONDITIONS\nAll policies are subject to their own terms, conditions and "
             "exclusions. This schedule is for information only and confers no rights upon the "
             "holder."),
        ]
        coi_fmt = {0: "xlsx", 3: "csv", 4: "xlsx"}.get(j, "pdf")
        c._write(key, f"C{n:03d}", f"SKY-COI-SCHEDULE-{j + 1:03d}", coi_fmt, pages,
                 dict(tier=4, type="insurance_schedule", title=f"COI Schedule {j + 1}",
                      category="Risk-Insurance"), rows=rows)

    for j in range(5):
        n += 1
        key = f"carrier:UTIL-{j + 1:03d}"
        store = f"S{300 + (j * 29 + 8) % 130}"
        pages = [
            (f"UTILITY BILLING AUDIT REPORT\n\nAudit No. UA-{3300 + j}\n"
             f"Auditor: Ledgerfield Utility Advisors\nSubject Location: Store {store}\n"
             f"Period Reviewed: 36 months ending {dstr(rng, 2025, 2026)}\n\n"
             "SCOPE\nWe reviewed all electric, gas, water, sewer and stormwater invoices for the "
             "subject location, together with meter configurations, rate schedules, and the "
             "tenant's proportionate share allocations under the applicable lease.\n\n"
             "METHOD\nInvoices were re-rated against the published tariff for each service. Meter "
             "numbers were traced to physical meters during a site visit. Allocation percentages "
             "were compared against the lease's stated proportionate share."),
            ("FINDINGS\n\n1. RATE CLASSIFICATION. The electric account is billed on a general "
             "service rate. Based on measured load factor the location qualifies for the large "
             "general service rate, which would reduce annual cost by approximately $11,300.\n\n"
             "2. METER MAPPING. Two meters serve the location. One is correctly allocated. "
             "The second requires review as set out below.\n\n"
             "3. SEWER CHARGES. Sewer is billed on full water consumption without deduction for "
             "irrigation. A deduct meter would reduce annual sewer charges by roughly $4,700.\n\n"
             "4. LATE FEES. Three late fees totalling $842 were assessed during the period, all "
             "attributable to invoices delivered to a superseded remittance address.\n\n"
             "5. TAX EXEMPTION. Manufacturing exemption not applicable at this location."),
            ("RECOMMENDATIONS\nPursue rate reclassification with the serving utility. Install a "
             "deduct meter for irrigation. Update the remittance address of record. Request refund "
             "of amounts identified above, subject to the utility's statutory refund window, "
             "which in this jurisdiction is limited to twenty-four (24) months.\n\n"
             "LIMITATIONS\nThis report is based on invoices and records furnished to us and on a "
             "single site visit. We have not audited the landlord's underlying common area meters "
             "and express no opinion on the accuracy of common area allocations beyond the "
             "specific findings stated above."),
        ]
        c._write(key, f"C{n:03d}", f"SKY-UTILAUDIT-{j + 1:03d}-{store}", "pdf", pages,
                 dict(tier=4, type="utility_audit", store=store,
                      title=f"Utility Audit {store}", category="Facilities-Accounting"))

    for j in range(6):
        n += 1
        key = f"carrier:PCA-{j + 1:03d}"
        store = f"S{300 + (j * 31 + 12) % 130}"
        pages = [
            (f"PROPERTY CONDITION ASSESSMENT\n\nReport No. PCA-{8800 + j}\n"
             f"Assessor: Cordray Engineering, PC\nSubject: Store {store}, "
             f"{S.CENTERS[j % len(S.CENTERS)]}\nAssessment Date: {dstr(rng, 2025, 2026)}\n"
             f"Standard: ASTM E2018 Baseline\n\n"
             "EXECUTIVE SUMMARY\nThe subject property is in generally fair to good condition for "
             "its age and use. Immediate repair needs are limited. Replacement reserve "
             "recommendations over a twelve-year evaluation term are summarized in the tables that "
             "follow. No environmental assessment was performed and none is implied."),
            ("BUILDING SYSTEMS\n\nSTRUCTURE. Slab on grade, steel frame, masonry infill. No "
             "evidence of differential settlement observed.\n\n"
             "ROOFING. Single-ply TPO membrane, estimated seven years of remaining useful life. "
             "Drains clear. Minor ponding at the northeast corner.\n\n"
             "MECHANICAL. Rooftop package units serve the sales floor. Units are original to the "
             "most recent renovation and are approaching mid-life. Filters current.\n\n"
             "ELECTRICAL. 1200A service, no deficiencies noted.\n\n"
             "PLUMBING. Domestic water in copper, waste in cast iron. Restroom fixtures "
             "functional.\n\n"
             "SITE. Asphalt paving, concrete walks, and site lighting on time clock control."),
            ("RESERVE TABLE (twelve-year term)\n\n"
             "Roof membrane replacement        year 8    $186,000\n"
             "Rooftop unit replacement (3)     year 6    $ 96,000\n"
             "Parking resurfacing              year 5    $142,000\n"
             "Exterior repainting              year 4    $ 38,000\n"
             "Site lighting retrofit           year 3    $ 27,500\n"
             "Restroom refresh                 year 7    $ 44,000\n\n"
             "IMMEDIATE REPAIRS\nItems classified as immediate are those requiring attention "
             "within twelve months to prevent further deterioration or to address a life-safety "
             "or code condition."),
        ]
        c._write(key, f"C{n:03d}", f"SKY-PCA-{j + 1:03d}-{store}",
                 "scan" if j == 4 else "pdf", pages,
                 dict(tier=4, type="property_condition", store=store,
                      title=f"Property Condition Assessment {store}",
                      category="Facilities-Compliance"))

    for j in range(5):
        n += 1
        key = f"carrier:MIN-{j + 1:03d}"
        pages = [
            (f"SKYLINE STORES, INC.\nFACILITIES AND REAL ESTATE COMMITTEE\n"
             f"MINUTES OF MEETING NO. {214 + j}\n\nDate: {dstr(rng, 2025, 2026)}\n"
             f"Present: Directors Alcott, Bhatt, Carrow, Delgado, Estrada, Fournier, Vance\n"
             f"Also present: General Counsel; VP Real Estate; VP Facilities\n\n"
             "1. CALL TO ORDER AND QUORUM\nThe chair called the meeting to order and confirmed a "
             "quorum. Minutes of the prior meeting were approved without amendment.\n\n"
             "2. PORTFOLIO REVIEW\nVP Real Estate reported on the store portfolio, noting lease "
             "expirations in the next eighteen months and the status of renewal negotiations at "
             "eleven locations. The committee directed management to prioritize renewals where "
             "co-tenancy protections are already in place."),
            ("3. CAPITAL PROJECTS\nThe committee reviewed the capital plan. Discussion focused on "
             "mechanical replacement across the older portfolio, where deferred replacement has "
             "begun to produce elevated repair spend.\n\n"
             "4. VENDOR CONSOLIDATION\nManagement presented a proposal to consolidate facilities "
             "vendors under a single master services agreement. The committee requested a "
             "comparative rate analysis before approval.\n\n"
             "5. LITIGATION UPDATE\nGeneral Counsel provided an update in executive session. No "
             "action was taken in open session.\n\n"
             "6. OTHER BUSINESS\nNone.\n\n"
             "7. ADJOURNMENT\nThere being no further business, the meeting adjourned.\n\n"
             "Respectfully submitted,\nCorporate Secretary"),
        ]
        c._write(key, f"C{n:03d}", f"SKY-MINUTES-{214 + j}",
                 "docx" if j % 2 else "pdf", pages,
                 dict(tier=4, type="meeting_minutes", title=f"Committee Minutes {214 + j}",
                      category="Corporate-Governance"))


# ---------------------------------------------------------------------------
# TIER 6 -- 15 negative space & governance
# ---------------------------------------------------------------------------

def build_governance(c):
    rng = c.rng
    n = 0

    # 5 adjacent-topic documents that contain NO answer to any graded question
    adjacent = [
        ("Parking Lot Restriping Schedule",
         "Seasonal restriping schedule for the parking fields at 41 locations. Crews mobilize in "
         "April and complete by June. Standard stall width is nine feet. Accessible stalls are "
         "restriped to current state accessibility dimensions. Nothing in this schedule allocates "
         "cost responsibility, which is addressed elsewhere."),
        ("Exterior Signage Design Standard",
         "Corporate standard for exterior signage: channel letters, white LED illumination at "
         "6500K, minimum letter height of 24 inches, maximum sign area per local code. Landlord "
         "approval of sign drawings is required at all locations. This standard is a design "
         "document only and creates no obligation on any party."),
        ("Store Associate Uniform Policy",
         "All associates wear the approved polo and name badge. Footwear must be closed-toe and "
         "slip-resistant. Outerwear bearing non-company branding is not permitted on the sales "
         "floor. The company provides three shirts annually at no cost to the associate."),
        ("Holiday Operating Hours Bulletin",
         "Extended hours apply from the day after Thanksgiving through December 24. Stores open at "
         "7:00 a.m. and close at 10:00 p.m. except where shopping center rules or local ordinance "
         "impose different hours. District managers confirm local variances with the property "
         "manager."),
        ("Recycling and Waste Diversion Report",
         "Corporate diversion rate for the reporting year was 38%. Cardboard represents the "
         "largest recovered stream. Twelve locations added organics collection. The report "
         "presents aggregate metrics and does not address cost allocation at any location."),
    ]
    for j, (title, body) in enumerate(adjacent):
        n += 1
        key = f"gov:ADJ-{j + 1:02d}"
        # These must be BULKY enough to be plausible retrieval candidates. A three-line memo
        # never competes for a slot, so it would never actually test the "no answer here"
        # behaviour it exists to test.
        pages = [(f"{title.upper()}\n\n{S.TENANT}\nDocument Reference: ADJ-{j + 1:02d}\n"
                  f"Issued: {dstr(rng, 2025, 2026)}\n\n{body}\n\n"
                  "SCOPE AND LIMITATIONS\nThis document is informational. It does not amend, "
                  "interpret or supersede any lease, service agreement or other contract, and it "
                  "does not allocate responsibility or cost between any parties.\n\n"
                  "APPLICABILITY\nThis document applies to all company-operated locations in the "
                  "United States. Franchise and licensed locations are governed by their "
                  "respective agreements. Where a shopping center's rules and regulations impose "
                  "a stricter requirement, the stricter requirement governs, and the district "
                  "manager shall confirm the local variance with property management before "
                  "deviating from the standard set out here.\n\n"
                  "ROLES\nFacilities Operations owns this document and reviews it annually. "
                  "District managers are accountable for compliance at their locations. Property "
                  "management is consulted where a shopping center approval is required. Store "
                  "managers execute.\n\n"
                  "DISTRIBUTION\nDistrict managers, property management, facilities. Questions to "
                  "the Facilities Service Desk."),
                 ("IMPLEMENTATION NOTES\n\n"
                  "1. SEQUENCING\nWork is sequenced by region to limit disruption during peak "
                  "trading periods. No work is scheduled between the Wednesday before "
                  "Thanksgiving and January 2 without written exception from the Vice President "
                  "of Facilities.\n\n"
                  "2. NOTIFICATION\nStores receive fourteen (14) days' advance notice of any "
                  "scheduled activity affecting the sales floor, customer parking or store "
                  "access. Property management receives the same notice where the work touches "
                  "common areas.\n\n"
                  "3. VENDOR REQUIREMENTS\nVendors shall carry current certificates of insurance "
                  "on file with Risk Management before mobilizing, shall badge in through the "
                  "Facilities Service Desk, and shall remove all debris daily.\n\n"
                  "4. ESCALATION\nIssues that cannot be resolved by the district manager are "
                  "escalated to the regional facilities manager and, if still unresolved after "
                  "five business days, to the Vice President of Facilities.\n\n"
                  "5. RECORDS\nCompletion records are retained in the facilities work order "
                  "system for seven years. Photographs are attached to each work order at open "
                  "and at close.\n\n"
                  "6. REVIEW\nThis document is reviewed annually and reissued when materially "
                  "changed. The revision history is maintained by Facilities Operations.\n\n"
                  "7. QUESTIONS\nDirect questions to the Facilities Service Desk. This document "
                  "does not answer questions about cost responsibility, lease obligations or "
                  "landlord-tenant allocation; those are addressed in the applicable lease.")]
        c._write(key, f"G{n:03d}", f"SKY-ADJ-{j + 1:02d}-{title.replace(' ', '')[:22]}",
                 "pdf" if j % 2 else "txt", pages,
                 dict(tier=6, type="no_answer_adjacent", title=title,
                      category="Corporate-Policy", contains_answer=False))

    # 2 directly contradictory documents about the same fact
    for j, (src, val) in enumerate([("Property Manager memorandum", "$34,500"),
                                    ("Lease Administration register", "$36,900")]):
        n += 1
        key = f"gov:CONTRA-{j + 1:02d}"
        pages = [(f"MONTHLY RENT CONFIRMATION -- STORE S344\n\nSource: {src}\n"
                  f"Prepared: {dstr(rng, 2025, 2026)}\n\n"
                  f"The current monthly Base Rent payable for Store S344 is {val}.\n\n"
                  "This figure is stated for internal reporting purposes. Where a discrepancy "
                  "exists between internal records and the executed lease documents, the executed "
                  "lease documents and any amendments thereto control.\n\n"
                  "Prepared by: Lease Administration")]
        c._write(key, f"G{n:03d}", f"SKY-CONTRA-S344-{j + 1:02d}", "pdf", pages,
                 dict(tier=6, type="contradiction", store="S344", stated_rent=val,
                      title=f"Rent Confirmation S344 ({src})", category="Facilities-Accounting"))

    # 2 near-duplicates -- 98% identical, ONE clause differs
    for j, (tag, clause) in enumerate([
        ("A", "Tenant shall deliver its renewal notice not less than 270 days prior to expiration."),
        ("B", "Tenant shall deliver its renewal notice not less than 120 days prior to expiration.")
    ]):
        n += 1
        key = f"gov:DUP-{tag}"
        pages = [
            ("STANDARD FORM RETAIL LEASE -- RIDER 4 (RENEWAL MECHANICS)\n\n"
             f"{S.TENANT} standard form, revision {tag}\n\n"
             "1. GRANT OF OPTION\nTenant shall have two successive options to extend the Term, "
             "each for five years, on all the terms and conditions of the Lease except as to "
             "Base Rent and except that there shall be no further option beyond those granted "
             "here.\n\n"
             "2. NOTICE\n" + clause + " Time is of the essence with respect to the delivery of "
             "such notice, and no notice delivered late shall be effective regardless of the "
             "absence of prejudice to Landlord.\n\n"
             "3. CONDITIONS\nThe option may be exercised only if, at the time of exercise and at "
             "the commencement of the extension term, no Event of Default has occurred and is "
             "continuing and Tenant is in occupancy of the entire Premises.\n\n"
             "4. RENT DURING EXTENSION\nBase Rent for each extension term shall be the greater of "
             "the Base Rent payable in the final year of the preceding term or ninety-five percent "
             "of fair market rent as of the commencement of the extension term.\n\n"
             "5. PERSONAL TO TENANT\nThe options granted are personal to the originally named "
             "Tenant and any affiliate assignee, and are not available to any other assignee or "
             "subtenant.")
        ]
        c._write(key, f"G{n:03d}", f"SKY-FORM-RIDER4-REV{tag}", "pdf", pages,
                 dict(tier=6, type="near_duplicate", variant=tag,
                      title=f"Standard Rider 4 revision {tag}", category="Corporate-Policy",
                      differing_clause="renewal notice period",
                      value="270 days" if tag == "A" else "120 days"))

    # 6 restricted-category documents -- the category ACL gate
    restricted = [
        ("Confidential Settlement Agreement -- Ashford v. Skyline",
         "The parties settle all claims arising from the 2024 slip-and-fall at Store S312 for "
         "$412,000, inclusive of fees and costs, with no admission of liability. The settlement "
         "amount, its existence and these terms are strictly confidential and may not be disclosed "
         "except as required by law or to the parties' insurers, auditors and counsel."),
        ("Litigation Hold Notice -- Northgate Dispute",
         "All custodians are directed to preserve documents relating to the Northgate Mall CAM "
         "dispute, including email, texts, invoices and reconciliation working papers, from "
         "January 2023 forward. Automatic deletion is suspended for the listed custodians until "
         "counsel lifts this hold in writing."),
        ("Executive Compensation Committee Memorandum",
         "The committee reviewed the long-term incentive design for the executive team, including "
         "the proposed change from a three-year to a four-year vesting schedule and the "
         "introduction of a relative total shareholder return modifier capped at 150%."),
        ("Confidential Store Closure List -- FY27 Planning",
         "Nineteen locations are under evaluation for closure or non-renewal in FY27 based on "
         "four-wall contribution, occupancy cost ratio and market overlap. This list is "
         "pre-decisional and must not be shared outside the planning team."),
        ("Internal Audit Report -- Lease Administration Controls",
         "Testing identified three control deficiencies: incomplete amendment logging, an absence "
         "of secondary review over CAM reconciliation acceptance, and stale delegation of "
         "authority thresholds. Management has accepted all three findings."),
        ("Attorney-Client Privileged Memorandum -- Co-Tenancy Exposure",
         "Counsel's assessment of the company's exposure and remedies where anchor co-tenancy "
         "conditions have failed at seven locations, including an analysis of the likelihood of "
         "success on alternative rent claims and the risk of landlord counterclaims."),
    ]
    for j, (title, body) in enumerate(restricted):
        n += 1
        key = f"gov:REST-{j + 1:02d}"
        pages = [(f"{title.upper()}\n\nCONFIDENTIAL -- RESTRICTED DISTRIBUTION\n"
                  f"{S.TENANT}\nDocument Reference: LC-{j + 1:03d}\n"
                  f"Date: {dstr(rng, 2025, 2026)}\n\n{body}\n\n"
                  "HANDLING\nThis document is restricted to the Legal-Confidential category. "
                  "Access is limited to authorized personnel. Do not forward, copy or discuss "
                  "outside the authorized distribution list.\n\n"
                  "RETENTION\nRetain per the legal retention schedule. Do not destroy while any "
                  "litigation hold is in effect.")]
        c._write(key, f"G{n:03d}", f"SKY-RESTRICTED-{j + 1:02d}",
                 "pdf" if j % 3 else "docx", pages,
                 dict(tier=6, type="restricted", title=title,
                      category=S.RESTRICTED_CATEGORY, acl_restricted=True))


# ---------------------------------------------------------------------------

def build_ground_truth(c):
    leases = [r for r in c.records if r.get("type") == "lease"]
    amendments = {r["store"]: r for r in c.records if r.get("type") == "lease_amendment"}

    # Effective values: an amendment that flips a dimension SUPERSEDES the base lease.
    for r in leases:
        eff = dict(r["dims"])
        am = amendments.get(r["store"])
        if am and am.get("new_hvac"):
            eff["hvac"] = am["new_hvac"]
        if am and am.get("supersedes", "").startswith("rent"):
            eff["cam_cap"] = "4%"
        r["dims_effective"] = eff

    rollup = {}
    for dim in S.DIMENSIONS:
        base, effective = {}, {}
        for r in leases:
            base.setdefault(r["dims"][dim], []).append(r["store"])
            effective.setdefault(r["dims_effective"][dim], []).append(r["store"])
        rollup[dim] = {"base": {k: sorted(v) for k, v in base.items()},
                       "effective": {k: sorted(v) for k, v in effective.items()}}

    # Synonym-only stores, broken out BY VALUE. These are the leases that never use the
    # query keyword -- pack 13's legacy FANOUT engine missed exactly this class, so recall
    # against `synonym_only[dim][value]` is the number that actually matters.
    synonym_only = {}
    for dim in S.DIMENSIONS:
        per_value = {}
        for r in leases:
            if not r["uses_term"][dim]:
                per_value.setdefault(r["dims_effective"][dim], []).append(r["store"])
        synonym_only[dim] = {k: sorted(v) for k, v in per_value.items()}

    total_pages = sum(r["pages"] for r in c.records)
    total_chars = sum(r["chars"] for r in c.records)
    return {
        "corpus": "pack23-doc-corpus-250",
        "seed": SEED,
        "counts": {
            "documents": len(c.records),
            "pages": total_pages,
            "chars": total_chars,
            "by_tier": {str(t): sum(1 for r in c.records if r["tier"] == t) for t in (1, 2, 3, 4, 6)},
            "by_format": {f: sum(1 for r in c.records if r["format"] == f)
                          for f in sorted({r["format"] for r in c.records})},
            "by_category": {k: sum(1 for r in c.records if r["category"] == k)
                            for k in sorted({r["category"] for r in c.records})},
        },
        "routing": {
            "brute_force_page_threshold": 999,
            "brute_force_char_budget": 400000,
            "past_page_threshold": total_pages > 999,
            "past_char_budget": total_chars > 400000,
            "note": "Both must be exceeded for production to leave the cannot-miss brute-force "
                    "branch and exercise smart retrieval.",
        },
        "dimensions": rollup,
        "synonym_only": synonym_only,
        "needles": [dict({k: v for k, v in n.items() if k != "text"},
                         planted_on_pages=c.needle_pages.get(n["id"], []),
                         decoy_on_pages=c.needle_pages.get(n["id"] + "-decoy", []))
                    for n in NEEDLES],
        "documents": c.records,
    }


TIER_NAMES = {1: "store leases (fan-out target)", 2: "near-miss distractors (precision)",
              3: "multi-hop / supersession", 4: "needle carriers (haystack)",
              6: "negative space & governance"}


def write_manifest(out, gt):
    """A self-describing header for the corpus, which lives outside the repo."""
    c = gt["counts"]
    lines = [
        "# Pack 23 document corpus", "",
        f"**{c['documents']} documents · {c['pages']} pages · {c['chars']:,} characters**",
        "",
        "Generated by `test_human/23_Doc_Corpus_250/gen_corpus.py` "
        f"(seed {gt['seed']}, deterministic). Ground truth is exact by construction: the "
        "generator owns every word, so `ground_truth.json` is authoritative and "
        "`questions.json` is derived from it, never hand-typed.", "",
        "## Routing", "",
        f"Production brute-forces every page when a knowledge base is under BOTH "
        f"{gt['routing']['brute_force_page_threshold']} pages and "
        f"{gt['routing']['brute_force_char_budget']:,} characters -- on that branch retrieval "
        f"cannot miss, and a corpus below the line tests nothing. This corpus is past both "
        f"(pages: {gt['routing']['past_page_threshold']}, "
        f"chars: {gt['routing']['past_char_budget']}).", "",
        "## Tiers", "",
        "| tier | documents | what it tests |", "|---|---|---|",
    ]
    for t, name in TIER_NAMES.items():
        lines.append(f"| {t} | {c['by_tier'].get(str(t), 0)} | {name} |")
    lines += ["", "## Formats", "", "| format | documents |", "|---|---|"]
    for f, n in sorted(c["by_format"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| {f} | {n} |")
    lines += ["", "`scan` and `jpg` are rasterised image files with NO text layer -- they "
                  "force the OCR path.", "",
              "## Categories", "", "| category | documents |", "|---|---|"]
    for k, n in sorted(c["by_category"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| {k} | {n} |")
    lines += ["", f"`{S.RESTRICTED_CATEGORY}` exists to test the category ACL gate: those "
                  "documents must be answerable for an authorised user and refused for an "
                  "unauthorised one.", "",
              "## Needles", "", "| id | grade | host | pages | decoy |", "|---|---|---|---|---|"]
    for nd in gt["needles"]:
        lines.append(f"| {nd['id']} | {nd['grade']} | {nd['host']} | "
                     f"{', '.join(str(p + 1) for p in nd['planted_on_pages'])} | "
                     f"{'yes' if nd.get('decoy_host') else 'no'} |")
    lines += ["", "Grade A = unique rare token. Grade B = ordinary prose, no keyword. "
                  "Grade C = two statements must be joined, and a near-identical decoy "
                  "elsewhere in the corpus yields a plausible wrong answer.", ""]
    with open(os.path.join(out, "MANIFEST.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--prune", action="store_true",
                    help="delete files in docs/ that this run did not produce. Regenerating "
                         "into a dirty directory otherwise leaves stale files that would be "
                         "ingested alongside the real corpus and corrupt every count.")
    args = ap.parse_args()

    c = Corpus(args.out)
    print("tier 1: 130 store leases ...")
    build_leases(c)
    print("tier 2: 45 near-miss distractors ...")
    build_distractors(c)
    print("tier 3: 35 multi-hop / supersession ...")
    build_multihop(c)
    print("tier 4: 30 needle carriers ...")
    build_carriers(c)
    print("tier 6: 15 negative space & governance ...")
    build_governance(c)

    gt = build_ground_truth(c)
    with open(os.path.join(args.out, "ground_truth.json"), "w", encoding="utf-8") as fh:
        json.dump(gt, fh, indent=1)
    write_manifest(args.out, gt)

    produced = {r["filename"] for r in c.records}
    orphans = sorted(set(os.listdir(c.docs_dir)) - produced)
    if orphans:
        for f in orphans:
            if args.prune:
                os.remove(os.path.join(c.docs_dir, f))
        verb = "pruned" if args.prune else "STALE (re-run with --prune)"
        print(f"\n{len(orphans)} file(s) not produced by this run -- {verb}:")
        for f in orphans[:12]:
            print(f"    {f}")

    cnt = gt["counts"]
    print(f"\n{cnt['documents']} documents · {cnt['pages']} pages · {cnt['chars']:,} chars")
    print(f"  by tier   {cnt['by_tier']}")
    print(f"  by format {cnt['by_format']}")
    print(f"  past 999-page threshold: {gt['routing']['past_page_threshold']} · "
          f"past 400K-char budget: {gt['routing']['past_char_budget']}")
    print(f"  -> {args.out}")


if __name__ == "__main__":
    main()
