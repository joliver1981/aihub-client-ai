"""Reproduce the Excel-extraction failure mode by calling the production
generate_excel_metadata() function on the human-test F1 fixture and printing
what it actually extracts vs. what the agent would need to answer correctly.
"""
import json
import sys
from pathlib import Path

# Add repo root to path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent_excel_tools import generate_excel_metadata

F1 = ROOT / "test_human" / "01_Finance" / "fixtures" / "F1_monthly_sales_by_region.xlsx"
assert F1.exists(), f"missing fixture: {F1}"

print(f"Extracting metadata from {F1.name} ...\n")
meta = generate_excel_metadata(str(F1))

for sheet in meta["sheets"]:
    print(f"Sheet: {sheet['name']}")
    print(f"  Rows reported: {sheet['row_count']}")
    print(f"  Columns reported: {sheet['column_count']}")
    print(f"  Column names extracted:")
    for c in sheet["columns"]:
        print(f"    - {c['name']!r:60s}  ({c['dtype']})")
    print()
    print(f"  First sample row keys: {list(sheet['sample_rows'][0].keys()) if sheet['sample_rows'] else '(none)'}")
    print(f"  First sample row values: {list(sheet['sample_rows'][0].values()) if sheet['sample_rows'] else '(none)'}")
print()
print("=" * 70)
print("EXPECTED (from the fixture author's intent):")
print("  Title row 1: 'Northwind Outdoor Co. — Monthly Sales by Region'")
print("  Subtitle 2: 'Reporting period: October 2025'")
print("  Headers row 4: ['Region', 'Channel', 'SKU Family', 'Units Sold', 'Net Revenue (USD)']")
print("  60 data rows + 1 grand total row")
print("  Grand total Net Revenue: $3,637,000.00")
