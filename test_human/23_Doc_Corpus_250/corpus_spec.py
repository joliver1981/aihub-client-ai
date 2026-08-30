"""Pack 23 corpus spec: the fact tables and clause text for the 255-document corpus.

Design rule (inherited from pack 13): the GENERATOR OWNS THE TEXT, so ground truth is
exact by construction -- never inferred from the rendered document, never LLM-judged.

Universe: SKYLINE STORES, INC. (a Delaware corporation) -- a national retailer's
facilities & legal file room. Every document is plausibly held by one department, so
"across all documents" questions stay meaningful, while genre variety supplies the
near-miss distractors the pack-13 monoculture never had.

Tiers
  1  130 store leases .................. fan-out target, 10 graded dimensions
  2   45 near-miss distractors ......... precision: look like the answer, are not
  3   35 multi-hop / supersession ...... amendments, estoppels, assignments, MSA chain
  4   30 needle carriers ............... long, dull, one planted fact
  5      (format diversity is an ATTRIBUTE of the above, not a tier)
  6   15 negative space & governance ... no-answer, contradictions, near-dupes, ACL
     ----
     255 documents
"""

TENANT = "SKYLINE STORES, INC., a Delaware corporation"
TENANT_SHORT = "Skyline Stores"

CITIES = [
    ("Boston", "MA"), ("Hartford", "CT"), ("Providence", "RI"), ("Albany", "NY"),
    ("Buffalo", "NY"), ("Newark", "NJ"), ("Philadelphia", "PA"), ("Pittsburgh", "PA"),
    ("Baltimore", "MD"), ("Richmond", "VA"), ("Raleigh", "NC"), ("Charlotte", "NC"),
    ("Columbia", "SC"), ("Atlanta", "GA"), ("Savannah", "GA"), ("Orlando", "FL"),
    ("Tampa", "FL"), ("Jacksonville", "FL"), ("Nashville", "TN"), ("Memphis", "TN"),
    ("Louisville", "KY"), ("Columbus", "OH"), ("Cleveland", "OH"), ("Detroit", "MI"),
    ("Indianapolis", "IN"), ("Chicago", "IL"), ("Milwaukee", "WI"), ("Minneapolis", "MN"),
    ("St. Louis", "MO"), ("Kansas City", "MO"), ("Omaha", "NE"), ("Des Moines", "IA"),
    ("Oklahoma City", "OK"), ("Dallas", "TX"), ("Houston", "TX"), ("Austin", "TX"),
    ("San Antonio", "TX"), ("Denver", "CO"), ("Salt Lake City", "UT"), ("Phoenix", "AZ"),
    ("Tucson", "AZ"), ("Las Vegas", "NV"), ("Albuquerque", "NM"), ("Boise", "ID"),
    ("Portland", "OR"), ("Seattle", "WA"), ("Spokane", "WA"), ("Sacramento", "CA"),
    ("San Jose", "CA"), ("San Diego", "CA"),
]

CENTERS = [
    "Gateway Plaza", "Northgate Mall", "Riverside Commons", "Summit Center",
    "Oakwood Square", "Harbor Point", "Eastview Crossing", "Liberty Marketplace",
    "Cedar Hills Shopping Center", "Stonebridge Court", "Willow Creek Village",
    "Fairfield Station", "Metro Exchange", "Union Yards", "Lakeshore Galleria",
    "Brookfield Commons", "Highland Park Center", "Copper Ridge Plaza",
    "Silverleaf Town Center", "Maplewood Crossing", "Ironworks District",
    "Whitfield Square", "Ashcroft Landing", "PinehurstMarket", "Belmont Row",
]

LANDLORD_SUFFIX = [
    "RETAIL PROPERTIES LLC", "REALTY TRUST", "PROPERTY HOLDINGS LP",
    "COMMERCIAL VENTURES LLC", "REAL ESTATE PARTNERS LP", "ASSET MANAGEMENT LLC",
    "SHOPPING CENTERS INC.", "CAPITAL GROUP LLC",
]

# ---------------------------------------------------------------------------
# TIER 1 -- the ten graded lease dimensions.
#
# Each dimension maps a ground-truth VALUE to a list of clause phrasings.
# `term=True`  -> the clause contains the obvious query keyword (HVAC, co-tenancy...)
# `term=False` -> SYNONYM-ONLY: the document never uses the query keyword at all.
#                 These are the leases that broke the legacy FANOUT engine in pack 13.
# ---------------------------------------------------------------------------

DIM_HVAC = {
    "landlord": [
        (True,
         "6.3 HVAC Responsibility\n"
         "LANDLORD'S RESPONSIBILITY. Landlord shall perform, at Landlord's sole cost and "
         "expense, all maintenance, repair and replacement of the HVAC equipment serving the "
         "Premises, including the {tons}-ton rooftop package units, condensers, ductwork and "
         "controls. Landlord shall maintain a quarterly preventive maintenance contract with a "
         "licensed mechanical contractor. Tenant shall have no obligation for HVAC capital "
         "replacement, and no portion of HVAC replacement cost shall be passed through to "
         "Tenant as a Common Area Maintenance charge."),
        (True,
         "6.3 Heating and Air Conditioning\n"
         "Landlord, at Landlord's expense, shall keep and maintain in good repair and working "
         "order the HVAC system serving the Premises ({tons} tons of cooling capacity), including "
         "all repairs and replacements thereto. Tenant's sole obligation with respect to the HVAC "
         "system shall be to promptly notify Landlord in writing of any malfunction."),
        (False,
         "6.3 Climate Control Systems\n"
         "Landlord shall maintain, repair and replace at its sole cost and expense all climate "
         "control systems and the heating, ventilation and air-conditioning plant serving the "
         "Premises ({tons}-ton capacity), together with all associated ductwork, thermostats and "
         "rooftop mechanical equipment. Tenant shall bear no share of such cost."),
        (False,
         "6.3 Building Mechanical Plant\n"
         "The temperature control plant, air handling units and rooftop mechanical equipment "
         "serving the Premises ({tons}-ton capacity) shall be maintained, repaired and, when "
         "necessary, replaced by Landlord at Landlord's sole expense, and such costs shall be "
         "excluded from Operating Expenses billed to Tenant."),
    ],
    "tenant": [
        (True,
         "6.3 HVAC Responsibility\n"
         "TENANT'S RESPONSIBILITY. Tenant shall, at Tenant's sole cost and expense, maintain, "
         "repair and replace all HVAC equipment exclusively serving the Premises ({tons}-ton "
         "rooftop units), and shall at all times maintain a service contract with a licensed "
         "mechanical contractor providing not less than quarterly inspection, filter replacement "
         "and coil cleaning. Tenant shall furnish Landlord with a copy of such contract annually "
         "and shall bear 100% of all HVAC repair and replacement costs."),
        (True,
         "6.3 Heating, Ventilation and Air Conditioning\n"
         "Tenant accepts the HVAC system in its as-is condition and shall thereafter be solely "
         "responsible for its maintenance, repair and replacement at Tenant's expense, including "
         "the {tons}-ton rooftop units. Landlord makes no warranty as to the condition or "
         "remaining useful life of the HVAC equipment."),
        (False,
         "6.3 Climate Control Systems\n"
         "Tenant shall, at its sole cost and expense, maintain and service all climate control "
         "systems and the heating, ventilation and air-conditioning plant serving the Premises "
         "({tons}-ton capacity), including all repairs and the eventual replacement thereof."),
    ],
    "split": [
        (True,
         "6.3 HVAC Responsibility\n"
         "SPLIT RESPONSIBILITY. Tenant shall be responsible for routine HVAC maintenance, filter "
         "changes, quarterly service contracts and any single repair not exceeding ${threshold:,} "
         "per occurrence. Landlord shall be responsible for any single repair exceeding "
         "${threshold:,} per occurrence and for complete replacement of any {tons}-ton rooftop "
         "unit when replacement is required. Disputes as to whether a given item constitutes "
         "repair or replacement shall be resolved by an independent mechanical engineer."),
        (True,
         "6.3 HVAC Maintenance and Capital Replacement\n"
         "Tenant shall maintain the HVAC system serving the Premises and bear all costs of "
         "maintenance and repair up to ${threshold:,} in any single instance. Costs in excess of "
         "${threshold:,}, and all capital replacement of the {tons}-ton units, shall be borne by "
         "Landlord and amortized over the useful life of the replacement equipment."),
    ],
    "silent": [
        (True,
         "6.3 Building Systems\n"
         "Tenant shall maintain the interior plumbing fixtures and the interior electrical "
         "distribution within the Premises, including lamps, ballasts and receptacles. Landlord "
         "shall maintain the roof, foundation, structural elements, exterior walls, exterior "
         "lighting and Common Area utility distribution. Each party shall perform its obligations "
         "under this Article promptly and in a good and workmanlike manner."),
    ],
}

DIM_CAM_CAP = {
    "none": [(True,
              "3.4 Common Area Maintenance -- No Cap\n"
              "Tenant shall pay Tenant's Proportionate Share of all Common Area Maintenance costs "
              "actually incurred by Landlord, without cap or limitation. Landlord shall furnish an "
              "annual reconciliation statement within one hundred twenty (120) days after each "
              "calendar year end.")],
    "3%": [(True,
            "3.4 Common Area Maintenance -- Annual Cap\n"
            "Tenant shall pay Tenant's Proportionate Share of Common Area Maintenance costs, "
            "provided that Controllable CAM Costs shall not increase by more than three percent "
            "(3%) per calendar year on a cumulative and compounding basis over the prior year's "
            "Controllable CAM Costs. Non-Controllable Costs (real estate taxes, insurance premiums, "
            "snow removal and utilities) are excluded from this cap.")],
    "4%": [(True,
            "3.4 Common Area Maintenance -- Annual Cap\n"
            "Controllable CAM Costs payable by Tenant shall not increase by more than four percent "
            "(4%) per calendar year, cumulative and compounding. Real estate taxes, insurance and "
            "utilities are Non-Controllable and are excluded from the cap.")],
    "5%": [(True,
            "3.4 Common Area Maintenance -- Annual Cap\n"
            "Controllable CAM Costs payable by Tenant shall not increase by more than five percent "
            "(5%) per calendar year on a non-cumulative, non-compounding basis. Taxes, insurance, "
            "utilities and snow removal are excluded.")],
}

DIM_PCT_RENT = {
    "none": [(True,
              "2.3 Percentage Rent\n"
              "Intentionally omitted. No percentage rent shall be payable under this Lease. Tenant "
              "shall nonetheless furnish Landlord with an annual statement of Gross Sales within "
              "sixty (60) days after each Lease Year for co-tenancy and radius verification "
              "purposes only.")],
    "yes": [(True,
             "2.3 Percentage Rent\n"
             "In addition to Base Rent, Tenant shall pay percentage rent equal to {pct}% of Gross "
             "Sales in each Lease Year in excess of a natural breakpoint of ${breakpoint:,} "
             "(the \"Breakpoint\"). Percentage rent shall be computed and paid annually within "
             "sixty (60) days after the end of each Lease Year. Gross Sales excludes returns, "
             "sales taxes, gift card issuances and inter-store transfers.")],
}

DIM_COTENANCY = {
    "none": [(True,
              "10.2 Co-Tenancy\n"
              "Intentionally omitted. Tenant's obligations under this Lease are not conditioned "
              "upon the continued operation of any other tenant in the Shopping Center.")],
    "anchor": [
        (True,
         "10.2 Co-Tenancy Requirement\n"
         "If at any time the Anchor Tenant ({anchor}) ceases to operate for more than one hundred "
         "twenty (120) consecutive days, Tenant may, upon written notice, pay in lieu of Base Rent "
         "an alternative rent equal to {alt}% of Gross Sales until the co-tenancy requirement is "
         "restored. If the failure continues for twelve (12) months, Tenant may terminate this "
         "Lease upon thirty (30) days' notice."),
        (False,
         "10.2 Operating Requirement of Major Occupant\n"
         "Tenant's obligation to pay full Base Rent is conditioned upon the continued operation of "
         "{anchor} as a major occupant of the Shopping Center. Should that occupant cease "
         "operations for more than one hundred twenty (120) consecutive days, Tenant may pay "
         "substitute rent equal to {alt}% of Gross Sales, and may terminate after twelve (12) "
         "months of continued closure."),
    ],
    "anchor+occupancy": [
        (True,
         "10.2 Co-Tenancy Requirement\n"
         "Tenant's obligations are conditioned upon (a) the continued operation of {anchor} and "
         "(b) the occupancy and operation of not less than {occ}% of the gross leasable area of the "
         "Shopping Center by retail tenants open for business. Upon failure of either condition for "
         "more than ninety (90) consecutive days, Tenant may pay alternative rent equal to {alt}% "
         "of Gross Sales, and may terminate upon nine (9) months of continued failure."),
    ],
}

DIM_EXCLUSIVE = {
    "none": [(True,
              "9.4 Exclusive Use\n"
              "Intentionally omitted. Landlord makes no covenant restricting the use of any other "
              "premises within the Shopping Center, and Tenant acknowledges that competing uses may "
              "be permitted.")],
    "apparel": [(True,
                 "9.4 Exclusive Use -- Apparel\n"
                 "Landlord covenants that it shall not lease any other premises in the Shopping "
                 "Center to a tenant whose primary use is the retail sale of men's, women's or "
                 "children's apparel. This exclusive shall not apply to any tenant occupying more "
                 "than {excl_sf:,} square feet or to any existing lease in effect as of the "
                 "Effective Date.")],
    "footwear": [(True,
                  "9.4 Exclusive Use -- Footwear\n"
                  "Landlord shall not permit any other premises in the Shopping Center to be used "
                  "primarily for the retail sale of footwear. Incidental sale of footwear "
                  "constituting less than ten percent (10%) of a tenant's sales floor shall not "
                  "violate this exclusive.")],
    "home goods": [(True,
                    "9.4 Exclusive Use -- Home Goods\n"
                    "Landlord shall not lease premises in the Shopping Center to any tenant whose "
                    "primary business is the retail sale of housewares, home textiles or "
                    "domestics. This exclusive expressly excludes department stores and any tenant "
                    "occupying more than {excl_sf:,} square feet.")],
}

DIM_ASSIGNMENT = {
    "consent required": [(True,
                          "12.1 Assignment and Subletting\n"
                          "Tenant shall not assign this Lease or sublet all or any portion of the "
                          "Premises without the prior written consent of Landlord, which consent "
                          "Landlord may grant or withhold in its sole and absolute discretion.")],
    "consent not unreasonably withheld": [(True,
                                           "12.1 Assignment and Subletting\n"
                                           "Tenant shall not assign this Lease or sublet the "
                                           "Premises without Landlord's prior written consent, "
                                           "which consent shall not be unreasonably withheld, "
                                           "conditioned or delayed. Landlord shall respond within "
                                           "thirty (30) days of a complete request, failing which "
                                           "consent shall be deemed granted.")],
    "affiliate transfer permitted": [(True,
                                      "12.1 Assignment and Subletting\n"
                                      "Tenant may, without Landlord's consent and upon ten (10) "
                                      "days' prior written notice, assign this Lease or sublet the "
                                      "Premises to (a) any entity controlling, controlled by or "
                                      "under common control with Tenant, or (b) any successor by "
                                      "merger, consolidation or sale of substantially all assets. "
                                      "All other transfers require Landlord's prior written "
                                      "consent, not to be unreasonably withheld.")],
    "change of control triggers recapture": [(True,
                                              "12.1 Assignment, Subletting and Change of Control\n"
                                              "Any transfer of more than fifty percent (50%) of the "
                                              "voting equity of Tenant shall constitute an "
                                              "assignment requiring Landlord's consent. Upon "
                                              "receipt of any request to assign, Landlord may "
                                              "elect, within thirty (30) days, to RECAPTURE the "
                                              "Premises and terminate this Lease effective as of "
                                              "the proposed transfer date.")],
}

DIM_CASUALTY = {
    "terminate if 25% damaged": [(True,
                                  "14.2 Casualty\n"
                                  "If the Premises are damaged by fire or other casualty to the "
                                  "extent of twenty-five percent (25%) or more of their replacement "
                                  "value, either party may terminate this Lease by written notice "
                                  "given within sixty (60) days after the casualty.")],
    "terminate if 50% damaged": [(True,
                                  "14.2 Casualty\n"
                                  "If the Premises are damaged to the extent of fifty percent (50%) "
                                  "or more of their replacement value, either Landlord or Tenant "
                                  "may terminate this Lease upon written notice given within thirty "
                                  "(30) days after the casualty. Below that threshold Landlord "
                                  "shall restore the Premises with reasonable diligence.")],
    "landlord must rebuild": [(True,
                               "14.2 Casualty\n"
                               "Landlord shall, regardless of the extent of damage and to the "
                               "extent of available insurance proceeds, repair and restore the "
                               "Premises to substantially their prior condition with reasonable "
                               "diligence. Neither party shall have a right of termination on "
                               "account of casualty. Rent shall abate proportionately during "
                               "restoration.")],
    "tenant option only": [(True,
                            "14.2 Casualty\n"
                            "Following any casualty rendering more than thirty percent (30%) of the "
                            "Premises untenantable, TENANT (and Tenant alone) may elect to "
                            "terminate this Lease upon sixty (60) days' written notice. Landlord "
                            "shall have no termination right on account of casualty.")],
}

DIM_HOLDOVER = {
    "125%": [(True, "16.1 Holding Over\nIf Tenant remains in possession after expiration without "
                    "Landlord's written consent, Tenant shall be a tenant at sufferance and shall "
                    "pay monthly rent equal to one hundred twenty-five percent (125%) of the Base "
                    "Rent in effect immediately prior to expiration.")],
    "150%": [(True, "16.1 Holding Over\nAny holding over after the Expiration Date shall be at a "
                    "monthly rent equal to one hundred fifty percent (150%) of the Base Rent then "
                    "in effect, and Tenant shall indemnify Landlord against claims by any "
                    "succeeding tenant.")],
    "200%": [(True, "16.1 Holding Over\nShould Tenant hold over beyond the Expiration Date, monthly "
                    "rent shall be two hundred percent (200%) of the Base Rent last in effect, "
                    "payable in advance, and Tenant shall be liable for all consequential damages "
                    "arising from the holdover.")],
}

DIM_RENEWAL = {
    "0": [(True, "17.1 Options to Extend\nIntentionally omitted. Tenant has no option to extend the "
                 "Term of this Lease.")],
    "1x5": [(True, "17.1 Options to Extend\nTenant shall have ONE (1) option to extend the Term for "
                   "an additional period of five (5) years, exercisable by written notice delivered "
                   "not less than two hundred seventy (270) days prior to the Expiration Date, "
                   "provided Tenant is not then in default. Base Rent during the extension shall be "
                   "the greater of the then-current Base Rent or ninety-five percent (95%) of fair "
                   "market rent.")],
    "2x5": [(True, "17.1 Options to Extend\nTenant shall have TWO (2) successive options to extend "
                   "the Term, each for five (5) years, exercisable by written notice not less than "
                   "two hundred seventy (270) days prior to the then-current expiration. Base Rent "
                   "for each extension shall increase by ten percent (10%) over the final year of "
                   "the preceding term.")],
    "3x5": [(True, "17.1 Options to Extend\nTenant shall have THREE (3) successive options to extend "
                   "the Term, each for five (5) years, on the same terms and conditions except that "
                   "Base Rent shall be adjusted to fair market rent, determined by appraisal if the "
                   "parties fail to agree within sixty (60) days.")],
}

DIM_RADIUS = {
    "none": [(True, "9.7 Radius Restriction\nIntentionally omitted. Tenant may operate competing "
                    "stores at any location without restriction.")],
    "3 miles": [(True, "9.7 Radius Restriction\nDuring the Term, Tenant shall not operate, directly "
                       "or through any affiliate, a store of a similar type and name within a "
                       "radius of three (3) miles measured from the exterior boundary of the "
                       "Shopping Center. Gross Sales of any such store shall be included in Gross "
                       "Sales for percentage rent purposes.")],
    "5 miles": [(True, "9.7 Radius Restriction\nTenant covenants not to open or operate a similar "
                       "retail store within five (5) miles of the Shopping Center during the Term "
                       "or any extension thereof. This restriction does not apply to stores open as "
                       "of the Effective Date or to e-commerce fulfillment.")],
    "10 miles": [(True, "9.7 Radius Restriction\nTenant shall not, during the Term, operate a "
                        "competing store within a ten (10) mile radius of the Shopping Center. "
                        "Violation shall entitle Landlord, as its sole remedy, to include the gross "
                        "sales of the violating store in the percentage rent computation.")],
}

# name -> (table, weighted value list). Weights are expressed by repetition so the
# distribution is auditable at a glance and the counts land where the grader expects.
DIMENSIONS = {
    "hvac":       (DIM_HVAC,       ["landlord"] * 42 + ["tenant"] * 40 + ["split"] * 38 + ["silent"] * 10),
    "cam_cap":    (DIM_CAM_CAP,    ["none"] * 40 + ["3%"] * 30 + ["4%"] * 32 + ["5%"] * 28),
    "pct_rent":   (DIM_PCT_RENT,   ["none"] * 62 + ["yes"] * 68),
    "cotenancy":  (DIM_COTENANCY,  ["none"] * 54 + ["anchor"] * 46 + ["anchor+occupancy"] * 30),
    "exclusive":  (DIM_EXCLUSIVE,  ["none"] * 46 + ["apparel"] * 34 + ["footwear"] * 26 + ["home goods"] * 24),
    "assignment": (DIM_ASSIGNMENT, ["consent required"] * 36 + ["consent not unreasonably withheld"] * 40
                                   + ["affiliate transfer permitted"] * 32
                                   + ["change of control triggers recapture"] * 22),
    "casualty":   (DIM_CASUALTY,   ["terminate if 25% damaged"] * 34 + ["terminate if 50% damaged"] * 38
                                   + ["landlord must rebuild"] * 32 + ["tenant option only"] * 26),
    "holdover":   (DIM_HOLDOVER,   ["125%"] * 44 + ["150%"] * 56 + ["200%"] * 30),
    "renewal":    (DIM_RENEWAL,    ["0"] * 26 + ["1x5"] * 40 + ["2x5"] * 44 + ["3x5"] * 20),
    "radius":     (DIM_RADIUS,     ["none"] * 44 + ["3 miles"] * 32 + ["5 miles"] * 34 + ["10 miles"] * 20),
}

ANCHORS = ["Harrow's Department Store", "FreshMart Grocery", "CineStar Theatres",
           "Beckwith & Sons", "MegaSport Outfitters", "Valu-Rite Pharmacy"]

# ---------------------------------------------------------------------------
# TIER 2 -- near-miss distractors. These rank high on the same queries and are
# the WRONG answer. The corpus had none of these before.
# ---------------------------------------------------------------------------

DISTRACTOR_TYPES = [
    ("hvac_pm",      15, "HVAC Preventive Maintenance Agreement"),
    ("roof_warranty", 8, "Roof System Warranty Certificate"),
    ("elevator",      7, "Elevator and Escalator Service Contract"),
    ("fire_insp",     8, "Fire Suppression System Inspection Report"),
    ("equip_lease",   7, "Equipment Lease Schedule"),
]

HVAC_VENDORS = ["Meridian Mechanical Services, LLC", "Apex Climate Solutions, Inc.",
                "Cornerstone HVAC Contractors", "BlueRidge Mechanical Group",
                "Precision Air Systems Co."]

# ---------------------------------------------------------------------------
# TIER 6 -- restricted-category documents, used to test the category ACL gate.
# ---------------------------------------------------------------------------

RESTRICTED_CATEGORY = "Legal-Confidential"
DEFAULT_CATEGORY = "Facilities-Leases"
