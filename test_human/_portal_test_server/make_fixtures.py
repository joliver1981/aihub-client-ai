"""Generate the demo documents the Meridian portal serves (files/): two PDF invoices
(PyMuPDF if available, .txt fallback) and a CSV master price list. Deterministic content;
safe to re-run (overwrites in place)."""
import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = os.path.join(HERE, "files")

INVOICES = [
    ("Invoice_2026-07_TC-8841.pdf", "TC-8841", "July 1, 2026", "July 31, 2026", [
        ("Warehouse shelving units, 72in", 12, 189.00),
        ("Pallet jacks, 5500 lb", 4, 412.50),
        ("Stretch wrap, 80ga case", 30, 42.75),
        ("Thermal shipping labels, 4x6 (10k)", 8, 68.90),
    ]),
    ("Invoice_2026-06_TC-8712.pdf", "TC-8712", "June 1, 2026", "June 30, 2026", [
        ("Warehouse shelving units, 72in", 6, 189.00),
        ("Box cutters, safety (24 pk)", 10, 31.20),
        ("Stretch wrap, 80ga case", 22, 42.75),
    ]),
]

PRICE_ROWS = [
    ("MS-1001", "Warehouse shelving units, 72in", "EA", 189.00),
    ("MS-1002", "Pallet jacks, 5500 lb", "EA", 412.50),
    ("MS-1003", "Stretch wrap, 80ga", "CASE", 42.75),
    ("MS-1004", "Thermal shipping labels 4x6", "10K", 68.90),
    ("MS-1005", "Box cutters, safety", "24PK", 31.20),
    ("MS-1006", "Corrugated boxes 18x18x18", "BNDL", 54.10),
    ("MS-1007", "Packing tape, 48mm clear", "36PK", 61.80),
]


def pdf_invoice(path, number, date, due, lines):
    try:
        import fitz
    except ImportError:
        with open(path.replace(".pdf", ".txt"), "w", encoding="utf-8") as fh:
            fh.write(f"MERIDIAN SUPPLY CO. INVOICE {number} ({date})\n")
            for desc, qty, unit in lines:
                fh.write(f"{desc}\t{qty}\t{unit:.2f}\t{qty*unit:.2f}\n")
        return False
    doc = fitz.open()
    pg = doc.new_page()  # US Letter default 612x792
    ink, accent = (0.11, 0.23, 0.36), (0.18, 0.53, 0.67)
    pg.draw_rect(fitz.Rect(0, 0, 612, 90), color=None, fill=ink)
    pg.insert_text((48, 42), "MERIDIAN SUPPLY CO.", fontsize=22, color=(1, 1, 1),
                   fontname="helv", render_mode=0)
    pg.insert_text((48, 64), "Supplier & Customer Portal — Accounts Receivable",
                   fontsize=10, color=(0.75, 0.85, 0.92))
    pg.insert_text((460, 42), "INVOICE", fontsize=18, color=(1, 1, 1))
    pg.insert_text((460, 62), number, fontsize=12, color=(0.75, 0.85, 0.92))
    y = 130
    pg.insert_text((48, y), "Bill to:", fontsize=10, color=accent)
    pg.insert_text((48, y + 16), "Town & Country Retail Group", fontsize=12, color=ink)
    pg.insert_text((48, y + 32), "Corporate Procurement, 500 Commerce Way", fontsize=10, color=ink)
    pg.insert_text((360, y), f"Invoice date:  {date}", fontsize=10, color=ink)
    pg.insert_text((360, y + 16), f"Due date:      {due}", fontsize=10, color=ink)
    pg.insert_text((360, y + 32), "Terms:         Net 30", fontsize=10, color=ink)
    y = 220
    pg.draw_rect(fitz.Rect(48, y - 14, 564, y + 4), color=None, fill=(0.92, 0.95, 0.97))
    for x, h in ((52, "Description"), (380, "Qty"), (430, "Unit"), (505, "Amount")):
        pg.insert_text((x, y), h, fontsize=10, color=accent)
    y += 24
    total = 0.0
    for desc, qty, unit in lines:
        amt = qty * unit
        total += amt
        pg.insert_text((52, y), desc, fontsize=10.5, color=ink)
        pg.insert_text((380, y), str(qty), fontsize=10.5, color=ink)
        pg.insert_text((430, y), f"{unit:,.2f}", fontsize=10.5, color=ink)
        pg.insert_text((505, y), f"{amt:,.2f}", fontsize=10.5, color=ink)
        y += 20
    pg.draw_line((380, y), (564, y), color=(0.8, 0.85, 0.9))
    y += 18
    pg.insert_text((430, y), "TOTAL DUE", fontsize=11, color=accent)
    pg.insert_text((505, y), f"${total:,.2f}", fontsize=12, color=ink)
    pg.insert_text((48, 740), "Generated demo document — Meridian portal fixture "
                              "(test_human/_portal_test_server).", fontsize=8,
                   color=(0.55, 0.6, 0.65))
    doc.save(path)
    doc.close()
    return True


def main():
    os.makedirs(FILES, exist_ok=True)
    for fname, number, date, due, lines in INVOICES:
        ok = pdf_invoice(os.path.join(FILES, fname), number, date, due, lines)
        print(("wrote " if ok else "wrote (txt fallback) ") + fname)
    with open(os.path.join(FILES, "Master_Price_List_2026H2.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["sku", "description", "uom", "unit_price_usd"])
        w.writerows(PRICE_ROWS)
    print("wrote Master_Price_List_2026H2.csv")


if __name__ == "__main__":
    main()
