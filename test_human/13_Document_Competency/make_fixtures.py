"""Build the pack-13 fixture set (self-contained, generator-driven per test_human rules).

Sources: the real lease corpus at C:\\temp\\leases (13 digital base leases, one 79-page
digital lease, 7 flattened/vector-outlined amendments — the exact document classes the
document-engine work was measured against), plus one generated merged base+amendment PDF.

Every fixture is copied with a DCT13_ prefix so runner uploads are identifiable and
idempotent teardown is safe.
"""
import os
import shutil
import sys

SRC = r'C:\temp\leases'
HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, 'fixtures')

# (source filename, fixture name) — fixture names are stable battery keys.
COPIES = [
    ('S001 - Market Square Boston Lease Agreement.pdf',            'DCT13_S001_MarketSquare_base.pdf'),
    ('S001 -a1 - Market Square Boston Lease Agreement.pdf',        'DCT13_S001_a1_amendment.pdf'),
    ('S002 - Harborview Grocery Lease Agreement.pdf',              'DCT13_S002_Harborview_base.pdf'),
    ('S002 - a1 - Harborview Grocery Lease Agreement.pdf',         'DCT13_S002_a1_amendment.pdf'),
    ('S003 - Riverdale Center Lease Agreement.pdf',                'DCT13_S003_Riverdale_base.pdf'),
    ('S003 - a1 - Riverdale Center Lease Agreement.pdf',           'DCT13_S003_a1_amendment.pdf'),
    ('S003 - a4 - Riverdale Center Lease Agreement.pdf',           'DCT13_S003_a4_amendment.pdf'),
    ('S004 - Lakeside Mall Lease Agreement.pdf',                   'DCT13_S004_Lakeside_base.pdf'),
    ('S005 - Central Plaza Lease Agreement.pdf',                   'DCT13_S005_CentralPlaza_base.pdf'),
    ('S006 - Windy City Outlet Lease Agreement.pdf',               'DCT13_S006_WindyCity_base.pdf'),
    ('S007 - Sunshine Outlet Lease Agreement.pdf',                 'DCT13_S007_Sunshine_base.pdf'),
    ('S008 - Cypress Mall Lease Agreement.pdf',                    'DCT13_S008_Cypress_base.pdf'),
    ('S009 - Peach Plaza Lease Agreement.pdf',                     'DCT13_S009_PeachPlaza_base.pdf'),
    ('S010 - Bay Plaza Lease Agreement.pdf',                       'DCT13_S010_BayPlaza_base.pdf'),
    ('S011 - Pacific Heights Lease Agreement.pdf',                 'DCT13_S011_PacificHeights_base.pdf'),
    ('S012 - Sunset Center Lease Agreement.pdf',                   'DCT13_S012_Sunset_base.pdf'),
    ('retail_lease_agreement.pdf',                                 'DCT13_R001_LargeRetailLease_79pg.pdf'),
]

MERGED = 'DCT13_S003_base_plus_a4_MERGED.pdf'   # the mixed digital+flattened class


def main() -> int:
    if not os.path.isdir(SRC):
        print(f"source corpus not found: {SRC}")
        return 1
    os.makedirs(FIX, exist_ok=True)

    for src_name, fix_name in COPIES:
        src = os.path.join(SRC, src_name)
        dst = os.path.join(FIX, fix_name)
        if not os.path.exists(src):
            print(f"MISSING SOURCE: {src_name}")
            return 1
        shutil.copy2(src, dst)
        print(f"copied  {fix_name}")

    # Merged fixture: digital base lease + flattened amendment bound into ONE pdf —
    # the packaging that historically lost the amendment pages silently.
    import fitz
    base = fitz.open(os.path.join(SRC, 'S003 - Riverdale Center Lease Agreement.pdf'))
    amend = fitz.open(os.path.join(SRC, 'S003 - a4 - Riverdale Center Lease Agreement.pdf'))
    base.insert_pdf(amend)
    base.save(os.path.join(FIX, MERGED))
    amend.close(); base.close()
    print(f"built   {MERGED} (16 pages: 10 digital + 6 flattened)")

    print(f"\nfixtures ready in {FIX}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
