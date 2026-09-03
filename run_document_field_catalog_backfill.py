"""Build / refresh DocumentFieldCatalog from the existing DocumentFields rows.

Run once after applying migrations/021_document_field_catalog.sql (or let this
create the table when the login may), and again any time the catalog should be
recomputed wholesale. Ingest keeps it current afterwards.

    python run_document_field_catalog_backfill.py            # every document type
    python run_document_field_catalog_backfill.py --types lease_agreement,vendor_guide
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402,F401  (.env / secure config -> API_KEY for the tenant context)
import pyodbc  # noqa: E402
from CommonUtils import get_db_connection_string  # noqa: E402
import document_field_catalog as dfc  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--types", default="", help="comma-separated document types (default: all)")
    args = ap.parse_args(argv)
    types = [t.strip() for t in args.types.split(",") if t.strip()] or None

    conn = pyodbc.connect(get_db_connection_string())
    cur = conn.cursor()
    cur.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
    if not dfc.ensure_table(cur):
        print(f"ERROR: {dfc.TABLE} is missing and this login cannot create it — apply "
              f"migrations/021_document_field_catalog.sql first.")
        return 2
    conn.commit()
    t0 = time.perf_counter()
    counts = dfc.rebuild(cur, types)
    conn.commit()
    took = time.perf_counter() - t0
    for t in sorted(counts):
        print(f"  {t:45} {counts[t]:6} field(s)")
    print(f"{sum(counts.values())} catalog row(s) across {len(counts)} type(s) in {took:.1f}s")
    print("stats:", dfc.stats(cur))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
