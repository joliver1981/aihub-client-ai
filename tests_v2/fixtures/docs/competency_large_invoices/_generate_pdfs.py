"""Generate large multi-page FedEx-style invoice PDFs for the
competency suite that probes 100+ page PDF extraction + numeric Q&A.

Four invoices with different shapes:
  01_fedex_invoice_global_logistics_q1_2026.pdf
      Global Logistics Corp, ~700 shipments, mixed services, ~120 pages
      (header REPEATS at the top of every line-items page)
  02_fedex_invoice_megaretail_q1_2026.pdf
      Mega Retail Inc, ~1000 shipments, mostly Ground, ~150 pages
      (header REPEATS at the top of every line-items page)
  03_fedex_invoice_pacific_mfg_q1_2026.pdf
      Pacific Manufacturing, ~400 shipments, Freight-heavy, ~110 pages
      (header REPEATS at the top of every line-items page)
  04_fedex_invoice_continental_no_repeat_headers_q1_2026.pdf
      Continental Distribution Co, ~800 shipments, mixed services, ~30 pages
      (HEADER APPEARS ONLY ON PAGE 1 — production-style "continuation"
      pages have data rows but no re-printed column header. This exercises
      the chunker's ability to find embedded rows whose embedding chunk
      contains no header text.)
  05_fedex_invoice_titan_systems_long_no_repeat_q1_2026.pdf
      Titan Systems Holdings, ~3000 shipments, mixed services, ~100+ pages
      (HEADER APPEARS ONLY ON PAGE 1, like #04, but the table is ~4× longer
      so many MORE chunks will have rows-but-no-header. This is the
      discriminating fixture for Phase 2.5 — per-document header inheritance
      for header-less continuation chunks.)

Each invoice contains:
   - Cover page (account info + invoice metadata)
   - Service summary table (totals by service tier)
   - Hundreds of line items (one per shipment)
   - Surcharge breakdown
   - Tax summary
   - Grand total page
   - Anchor page with fingerprint values for ground truth

Numbers are deterministic (seeded random) so the questions and
expected answers stay stable run-to-run.

Run:
    "$PY" _generate_pdfs.py
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)

OUT_DIR = Path(__file__).resolve().parent
styles = getSampleStyleSheet()

H1 = styles["Heading1"]
H2 = styles["Heading2"]
H3 = styles["Heading3"]
BODY = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9,
                      leading=11)
SMALL = ParagraphStyle("small", parent=styles["BodyText"], fontSize=7,
                       leading=9)


SERVICES = [
    ("FedEx Priority Overnight", "PriorityOvernight",  35.50, 1.0),
    ("FedEx Standard Overnight", "StandardOvernight",  28.75, 1.0),
    ("FedEx 2Day",               "2Day",               18.40, 0.7),
    ("FedEx Express Saver",      "ExpressSaver",       14.20, 0.6),
    ("FedEx Ground",             "Ground",              9.85, 0.45),
    ("FedEx Home Delivery",      "HomeDelivery",       11.20, 0.5),
    ("FedEx Freight Priority",   "FreightPriority",   142.00, 3.0),
    ("FedEx Freight Economy",    "FreightEconomy",    108.50, 2.5),
]

STATES = ["CA","TX","NY","FL","IL","PA","OH","GA","NC","MI","NJ","VA",
          "WA","AZ","MA","TN","IN","MO","MD","WI","CO","MN","SC","AL",
          "LA","KY","OR","OK","CT","UT","IA","NV","AR","MS","KS","NM"]
CITIES = ["Anytown","Centerville","Riverside","Lakewood","Springfield",
          "Madison","Franklin","Clinton","Georgetown","Salem","Bristol",
          "Fairview","Greenville","Kingston","Newport","Oxford","Quincy",
          "Watertown","Westfield","Ashland","Auburn","Burlington"]
COMPANIES = ["Cogswell Industries","Ravenscroft Holdings","Maplewood Trading",
             "Sterling Components","Halcyon Designs","Quartz Imports",
             "Bromley Apparel","Atlas Mercantile","Pelham Engineering",
             "Sycamore Foods","Vellichor Industries","Bramble & Forest",
             "PolarKraft Packaging","Sundial Foods","Tessuto Holdings",
             "Greenline Distributors","Cypress Container","Mira Pharma",
             "Northstar Components","Westbrook Trading","Eastgate Supplies"]


@dataclass
class Shipment:
    tracking: str
    ship_date: str
    sender_company: str
    sender_state: str
    recipient_company: str
    recipient_city: str
    recipient_state: str
    service_key: str
    service_name: str
    weight_lb: float
    base_charge: float
    fuel_surcharge: float
    residential: float
    other_surcharge: float
    total: float


def _fmt_money(v: float) -> str:
    return f"${v:,.2f}"


def _generate_shipments(n: int, period_year: int, period_month_start: int,
                        rng: random.Random, service_mix: dict) -> List[Shipment]:
    """Generate n shipments distributed across 3 months."""
    out = []
    services_pool = []
    for s_name, s_key, base, fuel_pct in SERVICES:
        weight = service_mix.get(s_key, 0.0)
        services_pool.extend([(s_name, s_key, base, fuel_pct)] * int(weight * 100))

    for i in range(n):
        s_name, s_key, base, fuel_pct = rng.choice(services_pool)
        month_offset = i % 3
        month = period_month_start + month_offset
        day = (i % 28) + 1
        date = f"{period_year:04d}-{month:02d}-{day:02d}"
        tracking = f"7926{rng.randint(10_000_000, 99_999_999):08d}"
        sender = rng.choice(COMPANIES)
        sender_state = rng.choice(STATES)
        recip = rng.choice(COMPANIES)
        recip_city = rng.choice(CITIES)
        recip_state = rng.choice(STATES)

        # Weight + charges
        if "Freight" in s_name:
            weight = round(rng.uniform(120, 4500), 1)
            base_charge = base + weight * rng.uniform(0.18, 0.42)
        else:
            weight = round(rng.uniform(0.5, 75), 1)
            base_charge = base + weight * rng.uniform(0.5, 1.8)

        fuel = round(base_charge * fuel_pct * 0.15, 2)
        residential = round(rng.choice([0.0, 0.0, 0.0, 4.85]), 2)
        other_sur = round(rng.choice([0.0] * 8 + [3.50, 5.25, 8.00]), 2)
        total = round(base_charge + fuel + residential + other_sur, 2)

        out.append(Shipment(
            tracking=tracking, ship_date=date,
            sender_company=sender, sender_state=sender_state,
            recipient_company=recip,
            recipient_city=recip_city,
            recipient_state=recip_state,
            service_key=s_key, service_name=s_name,
            weight_lb=weight,
            base_charge=round(base_charge, 2),
            fuel_surcharge=fuel,
            residential=residential,
            other_surcharge=other_sur,
            total=total,
        ))
    return out


@dataclass
class InvoiceSpec:
    filename: str
    company_name: str
    account_number: str
    invoice_number: str
    period_label: str
    period_year: int
    period_month_start: int
    n_shipments: int
    service_mix: dict      # service_key -> weight (proportion)
    seed: int
    notes: str             # for the cover page
    # When True, render the line-items table as a SINGLE large table with no
    # repeated header row on continuation pages — header only appears on the
    # first page where the table starts. This matches the production pattern
    # where FedEx (and most invoicing systems) print the column header once
    # and let subsequent pages show raw data rows. Forces the chunker /
    # retrieval pipeline to handle chunks that contain rows but no header.
    single_table_no_repeat_headers: bool = False
    # Filled in after generation:
    shipments: List[Shipment] = field(default_factory=list)
    # Computed totals (will appear in anchor section + grading):
    total_base: float = 0.0
    total_fuel: float = 0.0
    total_residential: float = 0.0
    total_other: float = 0.0
    grand_total: float = 0.0
    by_service: dict = field(default_factory=dict)
    by_state: dict = field(default_factory=dict)
    count_priority_overnight: int = 0
    count_ground: int = 0
    count_freight: int = 0
    max_single_charge: float = 0.0
    max_single_tracking: str = ""


def _build_pdf(spec: InvoiceSpec) -> None:
    out = OUT_DIR / spec.filename
    doc = SimpleDocTemplate(
        str(out), pagesize=letter,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
        leftMargin=0.55 * inch, rightMargin=0.55 * inch,
    )
    rng = random.Random(spec.seed)
    spec.shipments = _generate_shipments(
        spec.n_shipments, spec.period_year, spec.period_month_start,
        rng, spec.service_mix,
    )
    # Aggregate
    for s in spec.shipments:
        spec.total_base += s.base_charge
        spec.total_fuel += s.fuel_surcharge
        spec.total_residential += s.residential
        spec.total_other += s.other_surcharge
        spec.grand_total += s.total
        spec.by_service.setdefault(
            s.service_name,
            {"count": 0, "total": 0.0, "weight": 0.0},
        )
        spec.by_service[s.service_name]["count"] += 1
        spec.by_service[s.service_name]["total"] += s.total
        spec.by_service[s.service_name]["weight"] += s.weight_lb
        spec.by_state[s.recipient_state] = spec.by_state.get(
            s.recipient_state, 0) + 1
        if "Priority Overnight" in s.service_name:
            spec.count_priority_overnight += 1
        if s.service_name == "FedEx Ground":
            spec.count_ground += 1
        if "Freight" in s.service_name:
            spec.count_freight += 1
        if s.total > spec.max_single_charge:
            spec.max_single_charge = s.total
            spec.max_single_tracking = s.tracking

    story = []

    # ── Cover page ──
    story.append(Paragraph("<b>FedEx INVOICE</b>", H1))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"<b>Account holder:</b> {spec.company_name}<br/>"
        f"<b>Account number:</b> {spec.account_number}<br/>"
        f"<b>Invoice number:</b> {spec.invoice_number}<br/>"
        f"<b>Billing period:</b> {spec.period_label}<br/>"
        f"<b>Total shipments:</b> {spec.n_shipments:,}<br/>",
        BODY,
    ))
    story.append(Spacer(1, 10))
    story.append(Paragraph(spec.notes, BODY))
    story.append(Spacer(1, 14))

    # Service summary table
    story.append(Paragraph("Service summary", H2))
    svc_data = [["Service", "Shipments", "Total weight (lb)",
                 "Total charges"]]
    for sname, info in sorted(spec.by_service.items()):
        svc_data.append([
            sname,
            f"{info['count']:,}",
            f"{info['weight']:,.1f}",
            _fmt_money(info["total"]),
        ])
    svc_data.append([
        "TOTAL",
        f"{sum(b['count'] for b in spec.by_service.values()):,}",
        f"{sum(b['weight'] for b in spec.by_service.values()):,.1f}",
        _fmt_money(spec.grand_total),
    ])
    t = Table(svc_data, colWidths=[2.6 * inch, 1.0 * inch, 1.4 * inch,
                                    1.4 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), colors.HexColor("#3D1A78")),
        ("TEXTCOLOR",    (0, 0), (-1, 0), colors.white),
        ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME",     (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND",   (0, -1), (-1, -1), colors.HexColor("#EFE6FF")),
        ("ALIGN",        (1, 0), (-1, -1), "RIGHT"),
        ("GRID",         (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE",     (0, 0), (-1, -1), 9),
    ]))
    story.append(t)
    story.append(PageBreak())

    # ── Line items ──
    story.append(Paragraph("Shipment line items", H2))
    story.append(Spacer(1, 6))

    li_header = [
        "Tracking", "Ship date", "Recipient", "ST",
        "Service", "Lb", "Base", "Fuel", "Resi", "Other", "Total",
    ]

    def _shipment_row(s):
        recip_short = s.recipient_company[:18]
        svc_short = (s.service_name
                     .replace("FedEx ", "")
                     .replace("Standard ", "Std ")
                     .replace("Priority ", "Pri ")
                     .replace("Freight ", "Frt "))
        return [
            s.tracking,
            s.ship_date,
            recip_short,
            s.recipient_state,
            svc_short,
            f"{s.weight_lb:,.1f}",
            _fmt_money(s.base_charge),
            _fmt_money(s.fuel_surcharge),
            _fmt_money(s.residential),
            _fmt_money(s.other_surcharge),
            _fmt_money(s.total),
        ]

    li_colwidths = [
        0.95 * inch, 0.62 * inch, 1.10 * inch, 0.22 * inch,
        0.70 * inch, 0.38 * inch, 0.58 * inch, 0.55 * inch,
        0.45 * inch, 0.55 * inch, 0.65 * inch,
    ]
    li_table_style = TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), colors.HexColor("#3D1A78")),
        ("TEXTCOLOR",    (0, 0), (-1, 0), colors.white),
        ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, 0), 7),
        ("FONTSIZE",     (0, 1), (-1, -1), 7),
        ("ALIGN",        (5, 0), (-1, -1), "RIGHT"),
        ("GRID",         (0, 0), (-1, -1), 0.25, colors.grey),
    ])

    if spec.single_table_no_repeat_headers:
        # Production-style rendering: one big Table with repeatRows=0 so the
        # header is printed only on page 1 and continuation pages show raw
        # data rows. ReportLab handles the natural page splits.
        all_rows = [li_header] + [_shipment_row(s) for s in spec.shipments]
        big_table = Table(all_rows, repeatRows=0, colWidths=li_colwidths)
        big_table.setStyle(li_table_style)
        story.append(big_table)
        story.append(PageBreak())
    else:
        # Standard rendering: every page-sized chunk gets its own Table()
        # with the header repeated. ~22 rows + header ≈ one US-letter page.
        li_rows = [li_header]
        chunk = 22
        for idx, s in enumerate(spec.shipments):
            li_rows.append(_shipment_row(s))
            if (idx + 1) % chunk == 0 or idx == len(spec.shipments) - 1:
                t = Table(li_rows, repeatRows=1, colWidths=li_colwidths)
                t.setStyle(li_table_style)
                story.append(t)
                # Force a page break after each chunk so each "page" of line
                # items is one chunk — giving us deterministic page counts
                # matching the n_shipments/chunk ratio. This is what makes
                # the 2000-3000 shipment invoices legitimately 100+ pages.
                if idx < len(spec.shipments) - 1:
                    story.append(PageBreak())
                li_rows = [li_header]

        story.append(PageBreak())

    # ── Surcharge + tax summary ──
    story.append(Paragraph("Charge components", H2))
    comp = [
        ["Component", "Amount"],
        ["Base transportation charges", _fmt_money(spec.total_base)],
        ["Fuel surcharges",             _fmt_money(spec.total_fuel)],
        ["Residential delivery fees",   _fmt_money(spec.total_residential)],
        ["Other surcharges",            _fmt_money(spec.total_other)],
        ["GRAND TOTAL",                 _fmt_money(spec.grand_total)],
    ]
    t = Table(comp, colWidths=[3.0 * inch, 1.5 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), colors.HexColor("#3D1A78")),
        ("TEXTCOLOR",    (0, 0), (-1, 0), colors.white),
        ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME",     (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND",   (0, -1), (-1, -1), colors.HexColor("#EFE6FF")),
        ("ALIGN",        (1, 0), (-1, -1), "RIGHT"),
        ("GRID",         (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    story.append(t)
    story.append(Spacer(1, 16))

    # ── Top-10 destination states ──
    story.append(Paragraph("Top destination states", H3))
    top_states = sorted(spec.by_state.items(), key=lambda kv: -kv[1])[:10]
    ts_rows = [["Rank", "State", "Shipment count"]]
    for i, (st, cnt) in enumerate(top_states, 1):
        ts_rows.append([i, st, f"{cnt:,}"])
    t = Table(ts_rows, colWidths=[0.6 * inch, 1.0 * inch, 1.6 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#3D1A78")),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN",      (0, 0), (-1, -1), "CENTER"),
        ("GRID",       (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    story.append(t)
    story.append(PageBreak())

    # ── Anchor fingerprint page (for the test to ground-truth against) ──
    story.append(Paragraph("Anchor — fingerprint totals (for audit)", H2))
    anchor_lines = [
        f"<b>Invoice {spec.invoice_number}</b> for <b>{spec.company_name}</b> "
        f"(account {spec.account_number}), period {spec.period_label}.",
        f"Total shipments: <b>{spec.n_shipments:,}</b>",
        f"Grand total due: <b>{_fmt_money(spec.grand_total)}</b>",
        f"Base transportation: {_fmt_money(spec.total_base)}",
        f"Fuel surcharges: {_fmt_money(spec.total_fuel)}",
        f"Residential delivery fees: {_fmt_money(spec.total_residential)}",
        f"Other surcharges: {_fmt_money(spec.total_other)}",
        f"FedEx Priority Overnight shipments: <b>{spec.count_priority_overnight:,}</b>",
        f"FedEx Ground shipments: <b>{spec.count_ground:,}</b>",
        f"FedEx Freight (Priority+Economy) shipments: <b>{spec.count_freight:,}</b>",
        f"Largest single shipment charge: <b>{_fmt_money(spec.max_single_charge)}</b> "
        f"(tracking {spec.max_single_tracking})",
    ]
    # Top-3 service totals
    top3 = sorted(spec.by_service.items(),
                  key=lambda kv: -kv[1]["total"])[:3]
    for i, (sname, info) in enumerate(top3, 1):
        anchor_lines.append(
            f"#{i} service by total charges: <b>{sname}</b> — "
            f"{info['count']:,} shipments, {_fmt_money(info['total'])}"
        )
    for ln in anchor_lines:
        story.append(Paragraph(ln, BODY))
        story.append(Spacer(1, 3))

    doc.build(story)
    print(f"Wrote {out}  ({out.stat().st_size:,} bytes)")


# =============================================================================
# Invoice specifications
# =============================================================================

INVOICES = [
    InvoiceSpec(
        filename="01_fedex_invoice_global_logistics_q1_2026.pdf",
        company_name="Global Logistics Corp",
        account_number="2189-4471-X",
        invoice_number="GLC-FX-Q1-026114",
        period_label="January 1 – March 31, 2026",
        period_year=2026,
        period_month_start=1,
        n_shipments=2400,
        # Mixed services with a moderate Express / Ground split
        service_mix={
            "PriorityOvernight": 0.10,
            "StandardOvernight": 0.06,
            "2Day":              0.18,
            "ExpressSaver":      0.12,
            "Ground":            0.32,
            "HomeDelivery":      0.10,
            "FreightPriority":   0.07,
            "FreightEconomy":    0.05,
        },
        seed=20260101,
        notes="This invoice covers all FedEx shipments billed to Global "
              "Logistics Corp's primary account during Q1 2026. Mixed "
              "service usage reflects standard B2B distribution patterns.",
    ),
    InvoiceSpec(
        filename="02_fedex_invoice_megaretail_q1_2026.pdf",
        company_name="Mega Retail Inc",
        account_number="3104-9982-R",
        invoice_number="MRI-FX-Q1-026891",
        period_label="January 1 – March 31, 2026",
        period_year=2026,
        period_month_start=1,
        n_shipments=3000,
        # Heavy Ground + Home Delivery (consumer ecommerce profile)
        service_mix={
            "PriorityOvernight": 0.04,
            "StandardOvernight": 0.03,
            "2Day":              0.08,
            "ExpressSaver":      0.05,
            "Ground":            0.45,
            "HomeDelivery":      0.32,
            "FreightPriority":   0.02,
            "FreightEconomy":    0.01,
        },
        seed=20260102,
        notes="Quarterly invoice for Mega Retail Inc's e-commerce "
              "fulfillment operations. The mix is dominated by Ground "
              "and Home Delivery reflecting direct-to-consumer shipping.",
    ),
    InvoiceSpec(
        filename="03_fedex_invoice_pacific_mfg_q1_2026.pdf",
        company_name="Pacific Manufacturing LLC",
        account_number="5572-1003-M",
        invoice_number="PMF-FX-Q1-026230",
        period_label="January 1 – March 31, 2026",
        period_year=2026,
        period_month_start=1,
        n_shipments=2200,
        # Freight-heavy industrial profile
        service_mix={
            "PriorityOvernight": 0.05,
            "StandardOvernight": 0.04,
            "2Day":              0.06,
            "ExpressSaver":      0.05,
            "Ground":            0.15,
            "HomeDelivery":      0.05,
            "FreightPriority":   0.35,
            "FreightEconomy":    0.25,
        },
        seed=20260103,
        notes="Industrial shipment invoice for Pacific Manufacturing's "
              "Q1 2026 freight activity. Heavy use of FedEx Freight for "
              "palletized goods to distribution centers.",
    ),
    InvoiceSpec(
        filename="04_fedex_invoice_continental_no_repeat_headers_q1_2026.pdf",
        company_name="Continental Distribution Co",
        account_number="7841-2206-D",
        invoice_number="CDC-FX-Q1-026557",
        period_label="January 1 – March 31, 2026",
        period_year=2026,
        period_month_start=1,
        n_shipments=800,
        # Mixed services with a moderate Express / Ground split
        service_mix={
            "PriorityOvernight": 0.08,
            "StandardOvernight": 0.05,
            "2Day":              0.14,
            "ExpressSaver":      0.10,
            "Ground":            0.35,
            "HomeDelivery":      0.15,
            "FreightPriority":   0.08,
            "FreightEconomy":    0.05,
        },
        seed=20260104,
        notes="Quarterly invoice for Continental Distribution Co's Q1 2026 "
              "shipping activity. Generated with production-style rendering: "
              "the column header appears only on page 1 of the line-items "
              "table; continuation pages show data rows without re-printing "
              "the header. This exercises retrieval over chunks that contain "
              "table rows but no header text.",
        single_table_no_repeat_headers=True,
    ),
    InvoiceSpec(
        filename="05_fedex_invoice_titan_systems_long_no_repeat_q1_2026.pdf",
        company_name="Titan Systems Holdings",
        account_number="9265-7748-T",
        invoice_number="TSH-FX-Q1-026998",
        period_label="January 1 – March 31, 2026",
        period_year=2026,
        period_month_start=1,
        n_shipments=3000,
        # Mixed services with a Ground-leaning distribution profile
        service_mix={
            "PriorityOvernight": 0.06,
            "StandardOvernight": 0.04,
            "2Day":              0.12,
            "ExpressSaver":      0.08,
            "Ground":            0.40,
            "HomeDelivery":      0.20,
            "FreightPriority":   0.06,
            "FreightEconomy":    0.04,
        },
        seed=20260105,
        notes="Quarterly invoice for Titan Systems Holdings' Q1 2026 "
              "national distribution operations. ~3,000 shipments rendered "
              "as a SINGLE continuous line-items table; the column header "
              "is printed only on page 1 and continuation pages (which span "
              "~100 pages) show raw data rows without a re-printed header. "
              "This is the worst-case test for the no-repeat-header pattern: "
              "the table is long enough that the smart chunker emits many "
              "embedding chunks containing rows but no header text. Phase "
              "2.5's per-document header inheritance is what keeps each of "
              "those chunks usable for retrieval.",
        single_table_no_repeat_headers=True,
    ),
]


if __name__ == "__main__":
    for inv in INVOICES:
        _build_pdf(inv)
    # Emit the computed expected values so the test file can pick them up
    print("\n=== Computed anchor values (for test battery) ===")
    for inv in INVOICES:
        print(f"\n--- {inv.filename} ---")
        print(f"company={inv.company_name!r}")
        print(f"invoice_number={inv.invoice_number!r}")
        print(f"account={inv.account_number!r}")
        print(f"n_shipments={inv.n_shipments}")
        print(f"grand_total={inv.grand_total:.2f}")
        print(f"total_base={inv.total_base:.2f}")
        print(f"total_fuel={inv.total_fuel:.2f}")
        print(f"total_residential={inv.total_residential:.2f}")
        print(f"total_other={inv.total_other:.2f}")
        print(f"priority_overnight_count={inv.count_priority_overnight}")
        print(f"ground_count={inv.count_ground}")
        print(f"freight_count={inv.count_freight}")
        print(f"max_charge={inv.max_single_charge:.2f}")
        print(f"max_tracking={inv.max_single_tracking}")
        top3 = sorted(inv.by_service.items(),
                      key=lambda kv: -kv[1]["total"])[:3]
        for i, (s, info) in enumerate(top3, 1):
            print(f"top_service_{i}={s!r}  total={info['total']:.2f}  "
                  f"count={info['count']}")
        top_state = max(inv.by_state.items(), key=lambda kv: kv[1])
        print(f"top_state={top_state[0]!r}  count={top_state[1]}")
