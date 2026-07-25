"""Pack 13 — Document Processing & Retrieval Competency battery (single source of truth).

Human-grade questions over the real lease corpus, with deterministic grading rules.
Everything else in this pack (answer key, runner, README tables) derives from this file —
edit HERE, then regenerate.

Design notes
------------
The corpus was chosen because it carries, naturally, every document class and trap the
document-engine analysis measured (docs/document-search-recore-analysis.md):
  - digital text PDFs (13 base leases + one 79-page lease)
  - flattened/vector-outlined amendments (0 images, 0 text layer, heavy ink) — the class
    the blank-page rescue (DOC_HYBRID_BLANK_PAGE_RESCUE) exists for
  - a merged digital+flattened PDF (the packaging that historically lost pages silently)
  - operative terms that live ONLY in an amendment (S003 expiry 2026 -> 2033)
  - genuinely-varied HVAC/roof obligations across leases (portfolio questions have a real,
    checkable per-document answer)
  - "none required" security deposits and absent provisions (honesty probes)

Grading philosophy (pack-12 rule): deterministic checks decide PASS/FAIL; anything a
regex can't settle lands in NEEDS_REVIEW rather than being silently passed.
"""

# ---------------------------------------------------------------------------
# Ground truth per lease (authored from the documents themselves; amendment facts
# verified visually from page renders — the flattened files have no text layer).
# ---------------------------------------------------------------------------
LEASES = {
    'S001': dict(name='Market Square Boston', expiry='March 14, 2025', hvac='landlord',
                 hvac_quote='Landlord shall perform all maintenance, repairs, and replacements of HVAC equipment',
                 roof='landlord', deposit=None),
    'S002': dict(name='Harborview Grocery', expiry='September 9, 2025', hvac='split',
                 hvac_quote='TENANT ... under $5,000 per occurrence / LANDLORD ... exceeding $5,000 + replacement',
                 roof='landlord', deposit='None required (creditworthiness + parent guaranty)'),
    'S003': dict(name='Riverdale Center', expiry='April 21, 2026', expiry_current='April 21, 2033',
                 hvac='split',
                 hvac_quote='Tenant: interior HVAC components / Landlord: all HVAC equipment replacement and major repairs',
                 roof='landlord', deposit=None,
                 amendments={'a1': 'Trade name change to "Skyline Clearance Center" (eff. Oct 1, 2022) + permitted-use rewrite',
                             'a4': 'Fourth Amendment: 7-year extension to April 21, 2033; exclusive-use protections; renewal at 94% FMV; 315 parking spaces; going-dark prohibited'}),
    'S004': dict(name='Lakeside Mall', expiry='May 21, 2027', hvac='tenant',
                 hvac_quote="TENANT'S HVAC RESPONSIBILITY includes: Four (4) rooftop units totaling 60 tons",
                 roof='landlord', deposit='$100,000 (Letter of Credit acceptable)'),
    'S005': dict(name='Central Plaza', expiry='August 2, 2031', hvac='landlord',
                 hvac_quote='Landlord: All HVAC units (maintenance and replacement)',
                 roof='landlord', deposit='First month + security deposit due at signing'),
    'S006': dict(name='Windy City Outlet', expiry='January 17, 2030', hvac='split',
                 hvac_quote='Tenant: routine maintenance/filters / Landlord: repairs over $5,000 + replacement',
                 roof='landlord', deposit="Two months' Base Rent ($68,000)"),
    'S007': dict(name='Sunshine Outlet', expiry='June 30, 2028', hvac='tenant',
                 hvac_quote='Tenant maintains premises systems; landlord side lists only roof/structure/common areas',
                 roof='landlord', deposit='Letter of Credit for $83,334 (reduces to $50,000 after Year 3)'),
    'S008': dict(name='Cypress Mall', expiry='December 11, 2029', hvac='landlord',
                 hvac_quote='LANDLORD maintains all HVAC serving anchors ... No direct HVAC charges to Tenant',
                 roof='landlord', deposit='None (based on credit)'),
    'S009': dict(name='Peach Plaza', expiry='February 4, 2024 (extended in-document to February 4, 2029)',
                 expiry_current='February 4, 2029', hvac='tenant',
                 hvac_quote='TENANT RESPONSIBILITY: All HVAC maintenance for dedicated units',
                 roof=None, deposit='$70,000 (2 months)'),
    'S010': dict(name='Bay Plaza', expiry='September 9, 2029', hvac='split',
                 hvac_quote='Landlord maintains building perimeter HVAC; premises units per 6.3',
                 roof='landlord', deposit='$294,000 (reducing Letter of Credit)'),
    'S011': dict(name='Pacific Heights', expiry='March 24, 2028', hvac='split',
                 hvac_quote='SPLIT RESPONSIBILITY: Tenant under $10,000 / Landlord replacement + over $10,000',
                 roof='landlord', deposit='$110,000 (Letter of Credit)'),
    'S012': dict(name='Sunset Center', expiry='November 7, 2026', hvac='landlord',
                 hvac_quote="LANDLORD'S FULL RESPONSIBILITY ... All HVAC equipment",
                 roof='landlord', deposit='$108,000 (cash)'),
}

# ---------------------------------------------------------------------------
# Phase A — ingestion integrity (deterministic SQL oracle over DocumentPages).
# blank bar matches the product's own "nearly empty" threshold (20 chars).
# ---------------------------------------------------------------------------
INGESTION = [
    dict(id='A1', fixture='DCT13_S001_MarketSquare_base.pdf', pages=9,  max_blank=0,
         # 'Market Square' exists only in the FILENAME — the lease text says '123 Main Street'
         must_contain=['Main Street', 'March 14, 2025'],
         why='digital base lease — fast path, zero loss, no regression'),
    dict(id='A2', fixture='DCT13_S003_a4_amendment.pdf', pages=6, max_blank=0,
         must_contain=['April 21, 2033', 'FOURTH AMENDMENT'],
         why='flattened amendment, 2 pages with images + 4 pure-vector — the blank-page rescue class; '
             'before the 2026-07-25 fix this stored 4 empty pages'),
    dict(id='A3', fixture='DCT13_S003_a1_amendment.pdf', pages=4, max_blank=0,
         must_contain=['Skyline Clearance Center', 'FIRST AMENDMENT'],
         why='FULLY flattened amendment (0 images on every page) — before the fix this stored 100% empty'),
    dict(id='A4', fixture='DCT13_S003_base_plus_a4_MERGED.pdf', pages=16, max_blank=0,
         must_contain=['April 21, 2033', 'Riverdale'],
         why='digital base + flattened amendment bound as ONE pdf — the mixed-packaging loss case'),
    dict(id='A5', fixture='DCT13_R001_LargeRetailLease_79pg.pdf', pages=79, max_blank=0,
         must_contain=['SECURITY DEPOSIT'],
         why='79-page digital lease — fast path at size; also proves rescue adds no cost to normal PDFs'),
    dict(id='A6', fixture='DCT13_S002_a1_amendment.pdf', pages=4, max_blank=0,
         must_contain=[], why='second fully-flattened amendment — rescue must generalize beyond S003'),
]

# Fixtures uploaded for retrieval phases but not individually asserted in Phase A.
EXTRA_UPLOADS = [
    'DCT13_S002_Harborview_base.pdf', 'DCT13_S003_Riverdale_base.pdf',
    'DCT13_S004_Lakeside_base.pdf', 'DCT13_S005_CentralPlaza_base.pdf',
    'DCT13_S006_WindyCity_base.pdf', 'DCT13_S007_Sunshine_base.pdf',
    'DCT13_S008_Cypress_base.pdf', 'DCT13_S009_PeachPlaza_base.pdf',
    'DCT13_S010_BayPlaza_base.pdf', 'DCT13_S011_PacificHeights_base.pdf',
    'DCT13_S012_Sunset_base.pdf', 'DCT13_S001_a1_amendment.pdf',
]

DOCUMENT_TYPE = 'lease_agreement'

# ---------------------------------------------------------------------------
# Phase B — repository retrieval through an agent (human-phrased questions).
# grade types:
#   contains_any  — PASS if any expect-token present (case-insensitive)
#   honesty       — PASS if a refusal/absence marker present AND no fabrication marker
# All B/C grades additionally FAIL on empty/error replies.
# ---------------------------------------------------------------------------
RETRIEVAL = [
    dict(id='B1', tier=1, mode='repo',
         q='What is the security deposit for the Windy City Outlet lease?',
         grade=dict(type='contains_any', expect=['68,000', 'two months', "two (2) months"]),
         key="Two months' Base Rent ($68,000)"),
    dict(id='B2', tier=1, mode='repo',
         q='When does the Sunshine Outlet lease in Dallas expire?',
         grade=dict(type='contains_any', expect=['June 30, 2028', '6/30/2028', '2028']),
         key='June 30, 2028'),
    dict(id='B3', tier=2, mode='repo',
         q='One of our leases splits climate-control equipment upkeep between the parties at a $10,000 threshold — which property is that?',
         grade=dict(type='contains_any', expect=['Pacific Heights', 'S011']),
         key='Pacific Heights (S011) — the lease says "HVAC", never "climate control": pure meaning-match'),
    dict(id='B4', tier=1, mode='repo',
         q='Who is responsible for HVAC maintenance at the Market Square Boston store — the landlord or the tenant?',
         grade=dict(type='contains_any', expect=['landlord'], forbid=['tenant is responsible', 'tenant shall perform']),
         key='Landlord — all maintenance, repairs and replacements at Landlord\'s sole cost'),
    dict(id='B5', tier=3, mode='repo',
         q='What is the current expiration date of the Riverdale Center lease? Check for any amendments before answering.',
         grade=dict(type='contains_any', expect=['2033'], partial=['2026']),
         key='April 21, 2033 (Fourth Amendment supersedes the base lease\'s April 21, 2026). '
             '2026-only = the pre-fix wrong answer: amendment content was silently unindexed'),
    dict(id='B6', tier=2, mode='repo',
         q='What does the Harborview Grocery lease say about rooftop solar panels?',
         grade=dict(type='honesty',
                    absence=['not', 'no mention', 'does not', "doesn't", 'no provision', 'silent', 'unable to find', 'no information', 'not addressed', 'not found'],
                    fabrication=['solar panels shall', 'permitted to install solar', 'landlord shall install solar']),
         key='Nothing — the lease is silent on solar. Correct answer is "not addressed"; any invented clause = FAIL'),
    dict(id='B7', tier=2, mode='repo',
         q='What security deposit does the Cypress Mall lease require?',
         grade=dict(type='contains_any', expect=['none', 'no security deposit', 'not require', 'no deposit', 'waived', 'credit']),
         key='None — waived based on tenant credit. Inventing a dollar amount = FAIL'),
]

# ---------------------------------------------------------------------------
# Phase C — agent-knowledge retrieval (NEEDLE / FANOUT / deleted-doc honesty).
# The runner attaches KNOWLEDGE_DOCS to a dedicated test agent. Dev pins
# KNOWLEDGE_BRUTE_FORCE_PAGE_THRESHOLD=5, so these run on the RETRIEVAL path —
# the path production barely exercises and the one that matters at scale.
# ---------------------------------------------------------------------------
KNOWLEDGE_DOCS = [
    'DCT13_S001_MarketSquare_base.pdf', 'DCT13_S002_Harborview_base.pdf',
    'DCT13_S003_Riverdale_base.pdf', 'DCT13_S003_a4_amendment.pdf',
    'DCT13_S004_Lakeside_base.pdf', 'DCT13_S005_CentralPlaza_base.pdf',
    'DCT13_S009_PeachPlaza_base.pdf', 'DCT13_S012_Sunset_base.pdf',
]

# store -> expected HVAC class among the attached knowledge docs
FANOUT_HVAC_KEY = {
    'Market Square': 'landlord', 'Harborview': 'split', 'Riverdale': 'split',
    'Lakeside': 'tenant', 'Central Plaza': 'landlord', 'Peach Plaza': 'tenant',
    'Sunset': 'landlord',
}

KNOWLEDGE = [
    dict(id='C1', tier=1, mode='knowledge',
         q="What is the tenant's share percentage in the Riverdale Center lease?",
         grade=dict(type='contains_any', expect=['11.35']),
         key="11.35% (21,000 / 185,000)"),
    dict(id='C2', tier=3, mode='knowledge',
         q='Go through every lease in your knowledge and tell me, store by store, whether HVAC maintenance is the landlord\'s responsibility, the tenant\'s, or split between them.',
         grade=dict(type='coverage', key='FANOUT_HVAC_KEY', min_correct=0.7),
         key='Per-store classes per FANOUT_HVAC_KEY; grades BOTH completeness (store mentioned) and '
             'correctness (right class). This is the measured FANOUT weak spot — the score is the point'),
    dict(id='C3', tier=2, mode='knowledge',
         q='When does the Riverdale Center lease actually expire, taking amendments into account?',
         grade=dict(type='contains_any', expect=['2033'], partial=['2026']),
         key='April 21, 2033 — the Fourth Amendment is attached as knowledge; connecting it is the test'),
    dict(id='C4', tier=2, mode='knowledge',
         q='What does the Central Plaza lease say about who maintains the HVAC units?',
         grade=dict(type='contains_any', expect=['landlord']),
         key='Landlord — all HVAC units, maintenance and replacement'),
    # C5 runs AFTER the runner deletes the Lakeside doc from agent knowledge:
    dict(id='C5', tier=3, mode='knowledge_after_delete', delete_doc='DCT13_S004_Lakeside_base.pdf',
         q='What does the Lakeside Mall lease say about HVAC responsibility?',
         # Fabrication markers must be content that could ONLY come from the deleted doc —
         # generic words like 'tenant' false-positive when the agent honestly quotes OTHER leases.
         grade=dict(type='honesty',
                    absence=['no', 'not', "don't", 'unable', 'no longer', 'removed', 'no lease', 'no document', 'no knowledge', 'not in my', 'cannot find', 'could not find'],
                    fabrication=['60 tons', 'rooftop units totaling', 'four (4) rooftop', 'lakeside mall lease states']),
         key='The doc was just DELETED. Answering with its content (tenant / 60-ton units) means deleted-'
             'document vectors are still served — the is_active leak (KNOWLEDGE_FILTER_INACTIVE_VECTORS)'),
]

ALL_QUESTIONS = RETRIEVAL + KNOWLEDGE
