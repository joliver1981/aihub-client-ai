"""
Generate a small (~5-10 page) test compliance document that exercises the
"Customer Compliance Requirements" schema (the one defined in
C:\\temp\\compliance-fields.xlsx).

The document is a fictional retailer's vendor manual containing values for
nearly every flat field in the schema, plus several requirements that should
populate the Notes[] repeated group (with explicit topic / requirement /
value / confidence / excerpt content).
"""

import os
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle,
)


OUT = r"C:\temp\Horizon_Customer_Onboarding\test_file\generated\TestCo_DI_Test_Compliance_Doc.pdf"

styles = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=16, spaceAfter=12, textColor=colors.HexColor("#003366"))
H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12, spaceAfter=8, textColor=colors.HexColor("#003366"))
H3 = ParagraphStyle("H3", parent=styles["Heading3"], fontSize=11, spaceAfter=6, textColor=colors.HexColor("#444"))
BODY = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=10, leading=13, alignment=TA_JUSTIFY, spaceAfter=6)
TITLE = ParagraphStyle("Title", parent=styles["Title"], fontSize=20, alignment=TA_CENTER, textColor=colors.HexColor("#003366"))
SUBTITLE = ParagraphStyle("Sub", parent=styles["Heading2"], fontSize=13, alignment=TA_CENTER, textColor=colors.HexColor("#666"))


def _kv_table(rows):
    t = Table(rows, colWidths=[2.4 * inch, 4.0 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#003366")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
    ]))
    return t


def build():
    elems = []

    # Title page
    elems += [
        Spacer(1, 1.0 * inch),
        Paragraph("TestCo Brands, Inc.", TITLE),
        Paragraph("Direct Import Vendor Compliance Manual (Test Edition)", SUBTITLE),
        Spacer(1, 0.3 * inch),
        Paragraph("Version: 1.0 — Effective Date: 2026-04-15", SUBTITLE),
        Spacer(1, 1.0 * inch),
        Paragraph(
            "This is a TEST document used to validate the Retailer Compliance "
            "module's extraction pipeline against the 'Customer Compliance "
            "Requirements' schema. It contains realistic but fictional values "
            "for every required field, and a series of Compliance Notes that "
            "should populate the Notes[] repeated group.",
            ParagraphStyle("center", parent=BODY, alignment=TA_CENTER),
        ),
        PageBreak(),
    ]

    # Section 1: Document Summary fields
    elems += [
        Paragraph("Document Summary", H1),
        _kv_table([
            ["Field", "Value"],
            ["Customer / Retailer", "TestCo Brands, Inc."],
            ["Program Type", "DI (Direct Import)"],
            ["Document Title", "Direct Import Vendor Compliance Manual (Test Edition)"],
            ["Document Version / Date", "Version 1.0 — Effective 2026-04-15"],
            ["Source File", "TestCo_DI_Test_Compliance_Doc.pdf"],
            ["Record ID", "TESTCO-DI-0001"],
        ]),
        Spacer(1, 18),
    ]

    # Section 2: Order, ship, freight (mostly flat fields from the schema)
    elems += [
        Paragraph("Order, Ship, and Freight Requirements", H1),
        _kv_table([
            ["Requirement", "Value"],
            ["PO Received", "All purchase orders are transmitted via EDI 850. Email PO is permitted only as a fallback when EDI is offline."],
            ["Booking Lead Time", "Bookings must be placed with the assigned forwarder no fewer than 14 calendar days before the start ship date."],
            ["Container Port Delivery", "Containers must arrive at the origin port no later than 48 hours before vessel cutoff. No container may be tendered after the cutoff window without written approval."],
            ["Ship Window", "10 calendar days, beginning on the start ship date specified on the PO."],
            ["Ship Window Definition", "The ship window is interpreted as the START ship date through the END ship date inclusive. Goods may not ship before the start date."],
            ["Lead Time (Ex-Factory to Start Ship Date)", "21 calendar days from ex-factory date to start ship date."],
            ["DDP / FCA / FOB", "FOB Origin (named port). Buyer arranges freight via designated forwarder."],
            ["FOB Terms (Prepay / Collect)", "Freight collect under FOB Origin terms."],
            ["Preferred Carrier", "All TestCo shipments must use TestLog Global Forwarding (SCAC: TLGF) or a TestCo-approved alternate. Vendor-selected carriers are not permitted."],
            ["Booking Lead Time Confidence", "HIGH"],
            ["Ship Window Confidence", "HIGH"],
        ]),
        PageBreak(),
    ]

    # Section 3: Samples, testing, audits
    elems += [
        Paragraph("Samples, Testing, and Audits", H1),
        _kv_table([
            ["Requirement", "Value"],
            ["Customer Required Sample Submissions", "Pre-Production Sample (PPT), Top of Production (TOP), Sealed Sample, and final color-approved sample. PPT and TOP must be submitted to TestCo Quality Lab no later than 30 days before first ship."],
            ["Retailer Provided Milestones", "PO confirmation: T-90 days. PPT due: T-60 days. TOP due: T-30 days. Booking: T-14 days. Container delivery to port: T-2 days."],
            ["Testing Requirements", "All consumer products require ASTM F963 toy safety testing where applicable, CPSIA compliance testing, and TestCo's QA-100 internal protocol. Test reports must be issued by an ISO/IEC 17025 accredited lab."],
            ["Factory Audit", "Each producing factory must hold a current SMETA 4-Pillar audit (no older than 12 months) with no critical findings. BSCI is accepted as an alternate."],
            ["PPT Requirements", "PPT must include packaging, labeling, and a functional unit. Submit 6 units to QA Lab. Test report within 7 business days of receipt."],
            ["Transit / ECT Testing", "Master cartons must pass ISTA 3A. ECT rating: minimum 44 lbs/in (ANSI/AICC ECT)."],
            ["TOP", "Top-of-Production sample (3 units) due 30 days before first ship. Reflect production-line conditions."],
        ]),
        PageBreak(),
    ]

    # Section 4: Packaging, cartons, labeling
    elems += [
        Paragraph("Packaging, Cartons, and Labeling", H1),
        _kv_table([
            ["Requirement", "Value"],
            ["Packaging Requirements", "Retail packaging must be 100% recyclable corrugated or PCR-certified poly. PVC, EPS, and oxo-degradable plastics are prohibited. TestCo-branded packaging requires brand-team approval."],
            ["Inner Pack", "12 units per inner pack, with a die-cut chipboard divider preventing unit-to-unit contact."],
            ["Master Pack", "48 units per master carton (4 inner packs of 12)."],
            ["UPC Label Placement", "GS1 UPC-A on lower-right back panel of consumer pack. Minimum X-dimension 0.33 mm. ANSI Grade C or better."],
            ["Country of Origin Requirements", "Country of origin must be permanently marked on each consumer unit and master carton. Format: 'Made in [Country]'. Minimum 12 pt text."],
            ["Carton Markings", "Master cartons must include: TestCo SKU, item description, case pack, gross weight, COO, vendor 3-letter code, and a year-week code (YYWW)."],
            ["Case Pack Instructions", "All units oriented label-up, layered with chipboard separators between layers. Maximum 2 layers per master carton."],
            ["Pallet Configuration", "Tie x High = 8 x 5 (40 cartons per pallet). Maximum total pallet height 80 inches including pallet."],
            ["Type of Pallet Required", "GMA 48\" x 40\" four-way hardwood pallet, ISPM-15 heat-treated."],
            ["Master Carton Min/Max Dimensions", "Minimum 9 x 6 x 2 inches. Maximum 36 x 24 x 24 inches."],
            ["Master Carton Min/Max Weight", "Minimum 2 lbs. Maximum 45 lbs per master carton."],
            ["Master Carton Strength", "Bursting strength: minimum 250 psi (Mullen). ECT: minimum 44 lbs/in."],
            ["Event Codes / Special Labels", "Seasonal SKUs require an event code (e.g., 'HOL2026') applied to outer carton. Promotional SKUs require a Pink-Stripe banner per the brand kit."],
            ["Price Ticketing — Supplier or Buyer Approval", "Vendor applies price tickets at origin. Each ticket layout requires TestCo buyer approval before mass production."],
            ["Master Carton Labels Required", "Yes — GS1-128 shipping label on long side, between 4 and 8 inches from carton bottom. SSCC-18 must be unique within 12 months."],
            ["Packing Slip Required", "Yes — affixed to outside of stretch-wrap on lead pallet of every shipment."],
            ["Pallet Labels Required", "Yes — master pallet label on two adjacent sides containing pallet SSCC, gross weight, and PO number."],
        ]),
        PageBreak(),
    ]

    # Section 5: Documentation, portals, exceptions
    elems += [
        Paragraph("Documentation and Portals", H1),
        _kv_table([
            ["Requirement", "Value"],
            ["Commercial Invoice Required", "Yes — for every shipment. Must include 10-digit HTS classifications and country of origin per SKU."],
            ["Proforma Required", "Yes — submitted at PO acceptance for any shipment over USD 50,000 declared value."],
            ["Order Portal Link", "https://vendors.testco.example/orders"],
            ["Routing Portal Link", "https://logistics.testco.example/routing"],
            ["Special Notes / Waivers", "Container loading waivers are reviewed case-by-case by TestCo Logistics. Submit waiver requests through the Routing Portal at least 5 business days before ready-to-ship."],
        ]),
        Spacer(1, 18),
    ]

    # Section 6: Compliance Notes (these should populate the Notes[] repeated group)
    elems += [
        Paragraph("Compliance Notes", H1),
        Paragraph(
            "The following compliance notes describe specific requirements that should "
            "be captured as individual entries in the Compliance Notes (repeated group). "
            "Each note has an explicit topic, requirement headline, full requirement value, "
            "confidence, and source excerpt.",
            BODY,
        ),
    ]

    notes = [
        {
            "topic": "Transportation",
            "requirement": "Booking window is 14 days minimum",
            "value": "Vendors must place all forwarder bookings no later than 14 calendar days before the start ship date. Late bookings will be rejected.",
            "confidence": "HIGH",
            "excerpt": "Bookings must be placed with the assigned forwarder no fewer than 14 calendar days before the start ship date.",
        },
        {
            "topic": "Transportation",
            "requirement": "Use TestLog Global Forwarding only",
            "value": "All inbound shipments must use TestLog Global Forwarding (SCAC: TLGF) unless TestCo Logistics has approved an alternate carrier in writing. Use of an unapproved carrier is a Critical compliance violation and a 25% freight chargeback applies.",
            "confidence": "HIGH",
            "excerpt": "All TestCo shipments must use TestLog Global Forwarding (SCAC: TLGF) or a TestCo-approved alternate.",
        },
        {
            "topic": "Compliance",
            "requirement": "Factory technical audit must be current",
            "value": "Each producing factory must hold a current SMETA 4-Pillar (or BSCI) audit no older than 12 months. Critical findings block PO acceptance until remediated.",
            "confidence": "HIGH",
            "excerpt": "Each producing factory must hold a current SMETA 4-Pillar audit (no older than 12 months) with no critical findings.",
        },
        {
            "topic": "Labeling",
            "requirement": "UPC placement on consumer packaging",
            "value": "GS1 UPC-A barcode must be placed on the lower-right back panel of consumer packaging. Barcode must meet ANSI Grade C or better with a minimum X-dimension of 0.33 mm.",
            "confidence": "HIGH",
            "excerpt": "GS1 UPC-A on lower-right back panel of consumer pack. Minimum X-dimension 0.33 mm. ANSI Grade C or better.",
        },
        {
            "topic": "Labeling",
            "requirement": "UPC placement on apparel hangtag",
            "value": "For apparel SKUs, UPC must additionally appear on the joker hangtag with a minimum 1.0-inch barcode width. The garment-sewn label retains the COO and care information; UPC is hangtag-only.",
            "confidence": "MED",
            "excerpt": "For apparel SKUs, UPC must additionally appear on the joker hangtag with a minimum 1.0-inch barcode width.",
        },
        {
            "topic": "Cartons",
            "requirement": "Carton construction: double-wall corrugate",
            "value": "Master cartons must be double-wall corrugated (BC flute) with a minimum 250 psi bursting strength (Mullen) or equivalent ECT 44 lbs/in. Single-wall corrugate is not acceptable for any item over 20 lbs.",
            "confidence": "HIGH",
            "excerpt": "Bursting strength: minimum 250 psi (Mullen). ECT: minimum 44 lbs/in.",
        },
    ]

    for n in notes:
        elems.append(Paragraph(f"{n['topic']} — {n['requirement']}", H3))
        elems.append(Paragraph(f"<b>Requirement:</b> {n['value']}", BODY))
        elems.append(Paragraph(f"<b>Confidence:</b> {n['confidence']}", BODY))
        elems.append(Paragraph(f"<i>Source excerpt:</i> “{n['excerpt']}”", BODY))
        elems.append(Spacer(1, 8))

    elems.append(PageBreak())

    # Final reference card
    elems += [
        Paragraph("At-a-Glance Compliance Reference Card", H1),
        _kv_table([
            ["Item", "TestCo Standard"],
            ["Customer", "TestCo Brands, Inc."],
            ["Program", "DI"],
            ["Booking Lead Time", "14 days"],
            ["Ship Window", "10 days"],
            ["Incoterms", "FOB Origin, freight collect"],
            ["Pallet Type", "GMA 48x40 hardwood, ISPM-15"],
            ["Pallet Pattern (Tie x High)", "8 x 5"],
            ["Max Pallet Height", "80 inches (incl. pallet)"],
            ["Master Carton Max Weight", "45 lbs"],
            ["Master Carton Strength", "250 psi / ECT 44"],
            ["Case Pack", "48 units (4 inner packs of 12)"],
            ["Preferred Carrier", "TestLog Global Forwarding (TLGF)"],
            ["Order Portal", "https://vendors.testco.example/orders"],
            ["Routing Portal", "https://logistics.testco.example/routing"],
        ]),
    ]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    doc = SimpleDocTemplate(
        OUT, pagesize=letter,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
    )
    doc.build(elems)
    return OUT


if __name__ == "__main__":
    path = build()
    try:
        import fitz
        d = fitz.open(path)
        print(f"Generated: {path}")
        print(f"Pages: {d.page_count}, size: {os.path.getsize(path)/1024:.0f} KB")
    except Exception:
        print(f"Generated: {path}")
