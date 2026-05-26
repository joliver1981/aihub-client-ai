"""
Generate realistic, long retailer compliance test PDFs for the Compliance Module.

Creates:
  - DollarGeneral_DI_v2_2026.pdf (modified version of the 11/03/2025 manual)
  - MegaMart_VendorCompliance_v1.pdf
  - MegaMart_VendorCompliance_v2.pdf
  - MegaMart_VendorCompliance_v3.pdf

Outputs to: C:/temp/Horizon_Customer_Onboarding/test_file/generated/
"""

import os
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    PageBreak,
    Table,
    TableStyle,
    KeepTogether,
)


OUT_DIR = r"C:\temp\Horizon_Customer_Onboarding\test_file\generated"
os.makedirs(OUT_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Style setup
# ---------------------------------------------------------------------------

styles = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=16, spaceAfter=14, textColor=colors.HexColor("#003366"))
H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=13, spaceAfter=10, textColor=colors.HexColor("#003366"))
H3 = ParagraphStyle("H3", parent=styles["Heading3"], fontSize=11, spaceAfter=8, textColor=colors.HexColor("#444444"))
BODY = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=10, leading=14, alignment=TA_JUSTIFY, spaceAfter=8)
BULLET = ParagraphStyle("Bullet", parent=styles["BodyText"], fontSize=10, leading=14, leftIndent=18, spaceAfter=4)
TITLE = ParagraphStyle("Title", parent=styles["Title"], fontSize=22, spaceAfter=20, alignment=TA_CENTER, textColor=colors.HexColor("#003366"))
SUBTITLE = ParagraphStyle("SubTitle", parent=styles["Heading2"], fontSize=14, alignment=TA_CENTER, textColor=colors.HexColor("#666666"))


def page_footer(canvas, doc, retailer, version, date):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.drawString(0.75 * inch, 0.5 * inch, f"{retailer} - {version}")
    canvas.drawCentredString(letter[0] / 2, 0.5 * inch, f"CONFIDENTIAL - {date}")
    canvas.drawRightString(letter[0] - 0.75 * inch, 0.5 * inch, f"Page {doc.page}")
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Compliance content blocks (parameterized by config)
# ---------------------------------------------------------------------------

def title_page(cfg):
    return [
        Spacer(1, 1.5 * inch),
        Paragraph(cfg["retailer_full_name"], TITLE),
        Paragraph(cfg["doc_title"], SUBTITLE),
        Spacer(1, 0.4 * inch),
        Paragraph(f"Version: {cfg['version']}", SUBTITLE),
        Paragraph(f"Effective Date: {cfg['effective_date']}", SUBTITLE),
        Spacer(1, 2.5 * inch),
        Paragraph(
            "CONFIDENTIAL - For authorized vendors only. Reproduction or distribution outside the vendor relationship is prohibited.",
            ParagraphStyle("conf", parent=BODY, alignment=TA_CENTER, fontSize=9, textColor=colors.HexColor("#888888")),
        ),
        PageBreak(),
    ]


def revision_history(cfg):
    rows = [["Version", "Date", "Summary of Changes"]]
    for r in cfg.get("revision_history", []):
        rows.append([r["version"], r["date"], Paragraph(r["summary"], BODY)])
    t = Table(rows, colWidths=[0.8 * inch, 1.2 * inch, 4.5 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#003366")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
    ]))
    return [Paragraph("Document Revision History", H1), t, PageBreak()]


def _detail_subsection(cfg, title, paragraphs):
    """Emit a labeled subsection block."""
    out = [Paragraph(title, H3)]
    out += [Paragraph(p, BODY) for p in paragraphs]
    out.append(Spacer(1, 6))
    return out


def _extra_carton_content(cfg):
    out = []
    out += _detail_subsection(cfg, "2.10 Carton Material Sourcing Approval", [
        f"Corrugated suppliers used to produce {cfg['retailer_name']} cartons must be on the approved supplier list maintained by the {cfg['retailer_name']} Packaging Engineering team. Onboarding a new corrugated supplier requires submission of a material certification (BCT, ECT, Mullen burst, basis weight) and a sample audit at vendor expense.",
        f"Approved suppliers are reviewed annually. Vendors using a non-approved corrugated supplier are subject to chargeback and may be required to re-pack at the vendor's cost. Approval requests are submitted through the vendor portal under Packaging > Material Approvals and are typically reviewed within 30 business days.",
        f"In situations where the approved supplier list cannot meet capacity, vendors may petition for a temporary alternate-source authorization (TASA). TASAs are valid for up to 90 days and are granted only when accompanied by a quality improvement plan demonstrating how the vendor will return to an approved supplier.",
    ])
    out += _detail_subsection(cfg, "2.11 Recycled Content and Sustainability", [
        f"{cfg['retailer_name']} encourages corrugated suppliers to provide cartons made with at least 30% post-consumer recycled (PCR) content. Vendors are encouraged to specify PCR content in their packaging procurement and to share quarterly PCR percentage reports through the Sustainability portal.",
        f"Cartons must remain fully recyclable in standard municipal streams. Wax-coated, foil-laminated, or otherwise non-recyclable cartons are prohibited except where required by category-specific regulation (e.g., refrigerated meal kits requiring moisture barriers).",
        f"For 2026 and beyond, the corporate target for recyclable / compostable packaging content is {cfg['packaging_recyclable_pct']}% by weight. Vendors falling below this target should consult the Sustainable Packaging Playbook on the portal.",
    ])
    out += _detail_subsection(cfg, "2.12 Sample Submission and Approval", [
        f"For new SKUs, the vendor must submit packaging samples (master carton, inner pack, retail unit, and any display fixtures) to the {cfg['retailer_name']} Packaging Engineering lab at least 30 days prior to first production. Samples will be evaluated for compliance with this section and for category-specific design considerations.",
        "Sample evaluation outcomes: approved (proceed to production), conditional (proceed with specified modifications), rejected (re-submit with corrections). Conditional approvals must be addressed within 14 business days.",
        "Sample shipping address and submission form are published on the vendor portal under Packaging > Sample Submission.",
    ])
    out += _detail_subsection(cfg, "2.13 Common Carton Defects and Remediation", [
        "Defect: Burst seam during drop test. Remediation: increase carton bursting strength, reinforce seam with high-bond tape, or upgrade to double-wall corrugate. Submit revised sample for re-test.",
        "Defect: Crushed corners on receipt. Remediation: re-evaluate stack pattern; consider corner protectors for high-stack pallet positions; verify stretch-wrap tension.",
        "Defect: Open flaps or insecure top. Remediation: increase tape application length; verify tape adhesion on cold or dusty cartons; consider mechanized taping rather than hand-application.",
        "Defect: Smudged or unscannable carton markings. Remediation: switch to permanent ink; verify drying time before stacking; perform monthly print-quality audits at the manufacturing facility.",
        "Defect: Inner-pack collapse during transit. Remediation: add die-cut partitions; increase inner-pack board weight; redesign inner pack to fully fill the master carton without void space.",
    ])
    out += _detail_subsection(cfg, "2.14 Carton Reuse Policy", [
        f"Cartons may not be reused, recycled into outbound packaging, or marked over from prior shipments. Each shipment to {cfg['retailer_name']} must use new, clean cartons free from prior printing, labels, or damage.",
        "Cartons that arrive with prior carrier labels, prior PO markings, or other contamination are subject to a carton non-compliance chargeback and may delay receipt processing.",
    ])
    out += _detail_subsection(cfg, "2.15 Climate-Controlled Shipments", [
        f"Refrigerated, frozen, and temperature-sensitive products must use cartons rated for the temperature regime of the shipment. The {cfg['retailer_name']} Cold Chain Annex (published separately) governs additional packaging, time-temperature monitoring, and unloading requirements.",
        "Vendors of cold-chain products must include a temperature monitor in each pallet and report excursions to the Quality team within 24 hours of receipt confirmation.",
    ])
    out += _filler_paragraphs(cfg, "carton and packaging", 18)
    return out


def _extra_shipping_content(cfg):
    out = []
    out += _detail_subsection(cfg, "3.6 Pallet Construction Quality", [
        f"All pallets must be free of broken or splintered boards, missing nails, and protruding fasteners. The {cfg['retailer_name']} receiving team will reject pallets that present a worker-safety hazard.",
        "Pallets that have been previously used must be inspected before reuse and must meet GMA Grade A specifications. Repaired pallets (Grade B) are not acceptable for shipments to {cfg['retailer_name']}.",
        "For international shipments, all wood pallets must comply with ISPM-15 (heat-treated and stamped). Non-compliant wood will be rejected at port and the cost of disposal billed back to the vendor.",
    ])
    out += _detail_subsection(cfg, "3.7 Container Loading Requirements (Ocean Freight)", [
        f"Ocean containers must be loaded to maximize cube utilization while protecting freight. The {cfg['retailer_name']} Container Load Plan (CLP) tool generates an optimal load plan from the PO and packaging dimensions; vendors should validate against the CLP before sealing the container.",
        "Container seals must be high-security (ISO 17712 compliant) and the seal number must be transmitted in the EDI 856 (BSN segment).",
        f"Maximum gross container weight must not exceed VGM (Verified Gross Mass) limits and must be transmitted to the carrier prior to vessel loading.",
    ])
    out += _detail_subsection(cfg, "3.8 LTL and Parcel Shipments", [
        f"Less-than-truckload (LTL) shipments must be palletized except for specific small-quantity orders explicitly designated as 'parcel-eligible' on the PO.",
        "Parcel shipments must use the {cfg['retailer_name']}-designated parcel carrier and account number provided by the Routing team. Use of any other carrier is a chargeback event.",
        "Each parcel must bear a routing label printed from the Routing portal; hand-written or generic shipping labels are not acceptable.",
    ])
    out += _detail_subsection(cfg, "3.9 Demurrage and Detention", [
        f"Vendor-caused detention (driver wait time at origin or destination resulting from vendor delays) is the responsibility of the vendor. {cfg['retailer_name']} will pass through detention charges via deduction from the next open invoice.",
        "Vendor-caused demurrage at the port (from late documentation, customs holds, or chassis issues) is similarly passed through to the vendor.",
        "Drivers detained beyond 2 hours at the receiving DC due to {cfg['retailer_name']}-caused issues will be eligible for detention payment to the carrier; the vendor is not responsible for these charges.",
    ])
    out += _detail_subsection(cfg, "3.10 Routing Guide Compliance", [
        f"The {cfg['retailer_name']} Routing Guide is the authoritative source for ship-from / ship-to lane assignments, mode selection (LTL, TL, intermodal), and carrier selection. The Routing Guide is updated quarterly.",
        "Vendors must check the Routing Guide for every shipment. Use of an unapproved lane or carrier results in a routing-violation chargeback.",
        f"For shipments not covered by the Routing Guide (new lanes, exception orders), vendors must submit an EDI 753 (Routing Request) or use the routing portal to obtain a routing instruction prior to ship.",
    ])
    out += _detail_subsection(cfg, "3.11 Driver Conduct and Receiving Etiquette", [
        f"Carriers and drivers must observe {cfg['retailer_name']}'s receiving facility rules, including PPE requirements, traffic flow, and any safety briefings provided at the gate.",
        "Drivers may not enter the warehouse beyond the dock office and must wait in the designated driver area during unloading.",
        "Reports of inappropriate driver conduct may be escalated to the carrier and to {cfg['retailer_name']} Safety. Repeat issues may result in carrier de-listing.",
    ])
    out += _filler_paragraphs(cfg, "shipping and logistics", 18)
    return out


def _extra_labeling_content(cfg):
    out = []
    out += _detail_subsection(cfg, "4.7 Label Print Quality and Verification", [
        f"All barcodes printed by the vendor must be verified using a barcode verifier conforming to ISO/IEC 15416 (linear) or ISO/IEC 15415 (2D). Minimum verification grade: ANSI Grade C / ISO Grade 1.5.",
        "Vendors must maintain a quarterly verification log and produce verification reports on request. Failed verification grades require corrective action including print head replacement, ribbon swap, or substrate change.",
        "Field-printed labels (printed on a portable printer at the dock) must use the same approved label stock and ribbon combinations as production labels; substituting consumer-grade label paper is not acceptable.",
    ])
    out += _detail_subsection(cfg, "4.8 Carton-Level Identifier Reuse Window", [
        "SSCC-18 values may be reused only after a 12-month rolling window from the original ASN transmission. Re-use within the window will create ambiguous receiving and is a zero-tolerance violation.",
        "GTIN-14 values are not subject to a reuse window because they identify the item rather than the specific carton.",
    ])
    out += _detail_subsection(cfg, "4.9 Multi-Lingual and Bilingual Labeling", [
        f"For products distributed in regions requiring bilingual labeling (e.g., Quebec, Puerto Rico, EU), labels must include all required languages. {cfg['retailer_name']} is responsible for indicating the destination region on the PO; the vendor is responsible for ensuring the resulting product complies with the regional rules.",
        "Translations must be performed by qualified translators; machine-translated content is not acceptable for regulatory text.",
    ])
    out += _detail_subsection(cfg, "4.10 Special-Handling Symbols", [
        "Cartons containing fragile, hazardous, perishable, or otherwise special-handling merchandise must be marked with the appropriate ISO 780 handling symbols. Common symbols include This Way Up, Fragile, Keep Dry, Stack Limit, and Temperature Limit.",
        f"Symbols must be at least {cfg['coo_text_height_pt']} pt for legibility. Use of unauthorized icons (e.g., decorative or branded handling icons) is not permitted.",
    ])
    out += _detail_subsection(cfg, "4.11 Hazmat Marking", [
        "Hazmat shipments must include all DOT placards and labels required by 49 CFR. Lithium battery shipments must include the appropriate Lithium Battery Mark and contact phone number.",
        "Hazmat marking on the carton must match the marking on the BOL and must be visible during loading; placards may not be obscured by stretch wrap.",
    ])
    out += _filler_paragraphs(cfg, "labeling and barcodes", 18)
    return out


def _extra_edi_content(cfg):
    out = []
    out += _detail_subsection(cfg, "5.7 EDI Communication Setup", [
        f"Supported transmission protocols: AS2 (preferred for new vendors), sFTP (with SSH key authentication), and Value-Added Network (VAN) interconnect. Direct API integration is available for high-volume vendors.",
        "Production cutover requires successful test transactions for each transaction set in scope. The cutover decision is made jointly by {cfg['retailer_name']} EDI Operations and the vendor's EDI lead.",
        "Vendors are responsible for monitoring their EDI traffic and addressing 997 functional acknowledgment failures within 24 hours.",
    ])
    out += _detail_subsection(cfg, "5.8 Sample Acknowledgment Patterns", [
        "Full Acceptance: vendor returns 855 with all line items at 'IA' (Item Accepted) status, confirming the original PO quantity, price, and ship date.",
        "Partial Acceptance with Adjustment: vendor returns 855 with one or more line items at 'IB' (Item Backorder) or 'IQ' (Item Accepted with Quantity Change). Adjustments above 5% require buyer confirmation prior to ship.",
        "Rejection: vendor returns 855 with one or more line items at 'IR' (Item Rejected). All rejections must be supported by a documented reason and communicated to the buyer of record.",
    ])
    out += _detail_subsection(cfg, "5.9 ASN Construction Examples", [
        "Single-SKU pallet shipment: SHIPMENT > ORDER > TARE (pallet SSCC) > PACK (carton SSCC) > ITEM (GTIN, qty). Each carton on the pallet appears once with the pallet SSCC as parent.",
        "Mixed-SKU pallet shipment: SHIPMENT > ORDER > TARE (pallet SSCC) > PACK (carton SSCC) for each unique carton; ITEM levels reflect the actual contents of each carton including mixed items if applicable.",
        "Multi-PO shipment on one BOL: SHIPMENT level once; multiple ORDER groups under the SHIPMENT, each with its own TARE/PACK/ITEM hierarchy.",
    ])
    out += _detail_subsection(cfg, "5.10 EDI Error Handling", [
        "Common errors: invalid GTIN (validate check digit), incorrect SSCC structure (verify GS1 prefix and serial reference), missing mandatory segments (use the {cfg['retailer_name']} 856 implementation guide as the authoritative source), and out-of-window timestamps.",
        "When the {cfg['retailer_name']} EDI engine rejects a transaction, a 999 Implementation Acknowledgment will be returned with detailed error codes. Vendors must address errors and retransmit corrected documents.",
    ])
    out += _detail_subsection(cfg, "5.11 EDI Holiday and Weekend Schedules", [
        f"The EDI mailbox operates 24/7. {cfg['retailer_name']} EDI Operations is staffed 8am-6pm Central, Monday-Friday excluding US federal holidays.",
        "Time-bound transactions (855 acknowledgment) that fall during a non-business window must still be acknowledged within the SLA - vendors should automate acknowledgment generation rather than rely on staffed business hours.",
    ])
    out += _filler_paragraphs(cfg, "EDI and electronic transactions", 18)
    return out


def _extra_quality_content(cfg):
    out = []
    out += _detail_subsection(cfg, "6.8 Toy Safety", [
        f"Toy products must comply with ASTM F963 (US), EN 71 (EU), and ISO 8124 (international). All toys for children under 3 must additionally comply with the small-parts rule (16 CFR 1501).",
        f"Toy testing must be performed by a CPSC-accepted laboratory. Test reports must be submitted via the vendor portal and linked to the item record before first ship.",
    ])
    out += _detail_subsection(cfg, "6.9 Food and Beverage", [
        f"Food and beverage products must comply with FDA FSMA, CGMP (21 CFR 117), and any applicable state regulations. {cfg['retailer_name']} requires a current Preventive Controls Plan and supporting HACCP documentation.",
        "Allergen labeling must conform to the FDA Big-9 list. Cross-contamination controls at the manufacturing facility are subject to audit.",
        "Lot codes and best-by dates must be legible and consistent across the case, the inner pack, and the consumer unit. Best-by dates within 30 days of receipt may be rejected.",
    ])
    out += _detail_subsection(cfg, "6.10 Personal Care, OTC, and Cosmetics", [
        f"Personal care and cosmetics products must comply with FDA cosmetics regulations and applicable state-level rules. OTC drugs require an NDC number and proper drug facts labeling.",
        "Stability and microbial testing reports must be available upon request. Tamper-evident packaging is required for OTC and most cosmetics SKUs.",
    ])
    out += _detail_subsection(cfg, "6.11 Electrical and Battery Products", [
        f"Electrical products must carry a recognized safety certification mark (UL, ETL, CSA, or equivalent). Power supplies must meet DOE Level VI efficiency.",
        "Battery products must include the proper UN battery markings and shipping documentation. Lithium batteries are subject to the additional requirements in Section 3.5.",
    ])
    out += _detail_subsection(cfg, "6.12 Recall and Safety Incident Protocol", [
        f"Vendors must notify the {cfg['retailer_name']} Quality team within 24 hours of any safety incident, regulatory action, or media report involving a product supplied to {cfg['retailer_name']}.",
        f"Recall execution is coordinated by {cfg['retailer_name']}. Vendors must provide accurate inventory data, batch traceability, and corrective action documentation. Vendor cooperation is binding regardless of fault.",
        "Recall-related costs (return logistics, customer reimbursement, advertising the recall) are the responsibility of the vendor unless otherwise determined.",
    ])
    out += _detail_subsection(cfg, "6.13 Animal Welfare and Sustainable Sourcing", [
        f"Where applicable (e.g., leather, wool, down, palm oil, fish, eggs), vendors must provide documentation of sustainable sourcing per the {cfg['retailer_name']} Sustainable Sourcing Policy.",
        f"Animal-derived materials must comply with applicable CITES rules and any species-specific protections.",
    ])
    out += _detail_subsection(cfg, "6.14 Audit Findings Severity", [
        "Audit findings are categorized as Critical, Major, or Minor. Critical findings (e.g., child labor, forced labor, falsified records) result in immediate suspension. Major findings require remediation and re-audit. Minor findings are tracked but do not block ordering.",
        f"Severity definitions follow the {cfg['retailer_name']} Audit Standard published on the vendor portal.",
    ])
    out += _filler_paragraphs(cfg, "quality and audit", 18)
    return out


def _extra_legal_content(cfg):
    out = []
    out += _detail_subsection(cfg, "7.7 Anti-Bribery and Corruption", [
        f"Vendors must comply with the US Foreign Corrupt Practices Act (FCPA), the UK Bribery Act, and all applicable anti-corruption laws in any jurisdiction where they operate. {cfg['retailer_name']} maintains a zero-tolerance policy.",
        f"Gifts, entertainment, and hospitality between vendor representatives and {cfg['retailer_name']} associates are limited to nominal amounts and must be reported through the {cfg['retailer_name']} ethics portal.",
    ])
    out += _detail_subsection(cfg, "7.8 Data Privacy and Information Security", [
        f"Vendors with access to {cfg['retailer_name']} data systems must comply with the {cfg['retailer_name']} Information Security Standard and applicable privacy laws (CCPA, GDPR, PIPEDA).",
        "Personally identifiable information (PII) and payment card data may not be stored on vendor systems unless explicitly authorized and contractually required.",
    ])
    out += _detail_subsection(cfg, "7.9 Intellectual Property", [
        f"Vendors warrant that all merchandise supplied to {cfg['retailer_name']} does not infringe third-party intellectual property rights. {cfg['retailer_name']} indemnification rights apply to any IP claim involving vendor-supplied product.",
        f"Use of {cfg['retailer_name']} trademarks, logos, or copyrighted material requires advance written authorization.",
    ])
    out += _detail_subsection(cfg, "7.10 Termination and Wind-Down", [
        f"Either party may terminate the vendor relationship per the Master Vendor Agreement. Upon termination, the vendor is responsible for completing all in-flight POs and cooperating with the orderly transition of inventory.",
        f"Termination for cause (repeated material non-compliance, unethical conduct, insolvency) may be effective immediately and waives the wind-down period.",
    ])
    out += _detail_subsection(cfg, "7.11 Performance Improvement Plans (PIP)", [
        f"Vendors with composite scorecard scores below {cfg['scorecard_threshold']} for two consecutive months enter a 90-day PIP. The PIP includes specific KPIs, weekly check-ins with the assigned compliance liaison, and a documented improvement roadmap.",
        f"Successful PIP completion (returning the scorecard to {cfg['scorecard_threshold']}+ for three consecutive months) restores the vendor to standing. PIP failure may result in suspension, reduced order volume, or termination.",
    ])
    out += _detail_subsection(cfg, "7.12 Force Majeure", [
        f"Neither party will be liable for failure to perform due to force majeure events (natural disasters, war, government action, public health emergencies). Affected parties must notify the other in writing within 5 business days of the event and use commercially reasonable efforts to resume performance.",
        f"Routine supply chain disruption (carrier shortages, port congestion, raw material price increases) does not constitute force majeure unless an extraordinary event is documented.",
    ])
    out += _filler_paragraphs(cfg, "legal and contractual", 18)
    return out


def _extended_appendices(cfg):
    """Add additional appendices E through K with realistic content."""
    out = []

    out.append(Paragraph("Appendix E: Sample Documents", H1))
    out.append(Paragraph("Sample Bill of Lading (BOL)", H3))
    out.append(Paragraph(
        f"A compliant BOL for shipments to {cfg['retailer_name']} must include: shipper name and address; consignee name, address, and DC number; carrier name and SCAC code; PO number(s); total cartons and pallets; gross weight; freight class; declared value (if any); special handling notes; and shipper / driver signatures.",
        BODY,
    ))
    out.append(Paragraph("Sample Packing List", H3))
    out.append(Paragraph(
        "A compliant packing list itemizes each PO line with vendor SKU, GTIN, item description, quantity shipped, quantity per case, total cases, and gross weight. The packing list is affixed to the outside of the lead pallet's stretch wrap.",
        BODY,
    ))
    out.append(Paragraph("Sample COI (Certificate of Insurance)", H3))
    out.append(Paragraph(
        f"A compliant COI lists {cfg['retailer_full_name']} as an additional insured, includes per-occurrence and aggregate limits, identifies the policy carrier and policy number, and is signed by the issuing agent. Email to {cfg['coi_email']}.",
        BODY,
    ))
    out.append(PageBreak())

    out.append(Paragraph("Appendix F: Compliance Pre-Ship Checklist", H1))
    checklist = [
        "PO acknowledgment (855) transmitted within SLA",
        "Items match the PO (SKU, quantity, price)",
        "Cartons within dimensional and weight limits",
        "Cartons properly marked (SKU, case pack, COO, gross weight, etc.)",
        "Color banner applied where required",
        "Fragile / hazmat / handling symbols applied where applicable",
        "Inner packs configured per item master",
        "Pallets within height and weight limits",
        "Stretch wrap applied with required revolutions",
        "GS1-128 shipping label printed and affixed to each carton",
        "SSCC-18 values unique within rolling window",
        "Pallet master label affixed",
        "ASN (856) prepared and ready to transmit per SLA",
        "Routing instruction confirmed and approved carrier dispatched",
        "BOL completed and signed",
        "Delivery appointment scheduled and confirmation received",
        "Hazmat documentation complete (if applicable)",
        "Temperature monitor installed (if cold chain)",
        "COA / COI / test reports submitted (if applicable)",
        "Invoice (810) prepared for transmission within SLA after ship",
    ]
    for item in checklist:
        out.append(Paragraph(f"[ ] {item}", BULLET))
    out.append(PageBreak())

    out.append(Paragraph("Appendix G: Hazmat Quick Reference", H1))
    out += [
        Paragraph(p, BODY)
        for p in [
            "This appendix provides a high-level reference for hazmat shipments. Vendors are responsible for full compliance with 49 CFR (US DOT), IATA Dangerous Goods Regulations, and IMDG (ocean) as applicable.",
            "Common hazard classes encountered in retail merchandise include: Class 2 (gases, including aerosols), Class 3 (flammable liquids), Class 4 (flammable solids, including some matches), Class 8 (corrosives, including some cleaning products), Class 9 (miscellaneous, including lithium batteries).",
            f"Vendors must provide current Safety Data Sheets (SDS) for every hazmat SKU. SDS sheets are submitted via the vendor portal and refreshed annually or upon any formulation change.",
            f"Lithium battery shipments require additional documentation: UN test summary, watt-hour rating documentation, and the IATA-style lithium battery mark on the outer carton.",
            "Aerosol shipments are limited to {cfg['aerosol_max_per_pallet']} units per pallet and must be palletized separately from non-hazmat freight.",
        ]
    ]
    out.append(PageBreak())

    out.append(Paragraph("Appendix H: Vendor Scorecard Formula", H1))
    out += [
        Paragraph(p, BODY)
        for p in [
            f"The {cfg['retailer_name']} Composite Scorecard is computed monthly as a weighted average of six metrics. Each metric is scored on a 0-100 scale and weighted as shown.",
        ]
    ]
    sc_table = Table([
        ["Metric", "Weight", "Definition"],
        ["ASN Accuracy", "20%", "Percentage of ASNs that match the physical shipment exactly"],
        ["On-Time Delivery", "20%", "Percentage of shipments arriving within the appointment window"],
        ["Fill Rate", "20%", "Units shipped / units ordered, excluding buyer-approved reductions"],
        ["Packaging Compliance", "15%", "Cartons accepted at receipt without packaging-related chargebacks"],
        ["EDI Compliance", "15%", "Percentage of EDI transactions accepted without re-transmission"],
        ["Audit Currency", "10%", "Factory audit currency and absence of critical findings"],
    ], colWidths=[1.8 * inch, 0.8 * inch, 3.4 * inch])
    sc_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#003366")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    out.append(sc_table)
    out.append(Spacer(1, 12))
    out.append(Paragraph(
        f"Scorecard reports are published on the vendor portal by the 10th business day of each month. Vendors may dispute scorecard data within 15 days of publication.",
        BODY,
    ))
    out.append(PageBreak())

    out.append(Paragraph("Appendix I: Country of Origin Marking Reference", H1))
    out += [
        Paragraph(p, BODY)
        for p in [
            f"Country of origin (COO) marking must comply with 19 USC 1304 and 19 CFR 134. Marking must be in English, legible, indelible, conspicuous, and as permanent as the article itself permits.",
            f"Acceptable formats: 'Made in [Country]', 'Product of [Country]', 'Manufactured in [Country]'. Abbreviations of country names are not acceptable except where listed in the Customs Regulations.",
            f"Substantial transformation rules govern COO determination for products produced from materials sourced in multiple countries. When in doubt, vendors should consult their licensed customs broker.",
            f"Examples of correct marking: 'Made in China', 'Product of Vietnam', 'Manufactured in Mexico'. Examples of incorrect marking: 'Made in PRC', 'Country of origin: see invoice', 'Origin: Asia'.",
        ]
    ]
    out.append(PageBreak())

    out.append(Paragraph("Appendix J: GS1-128 Shipping Label Reference", H1))
    out += [
        Paragraph(p, BODY)
        for p in [
            f"The GS1-128 shipping label is the operational backbone of {cfg['retailer_name']}'s receiving process. The label must include the SSCC-18, GTIN-14, ship-to address, PO number, carrier, and carton sequence.",
            f"SSCC-18 structure: Application Identifier (00) + extension digit (0-9) + GS1 company prefix (7-10 digits) + serial reference (filled to 17 digits) + check digit (Modulo 10).",
            f"GTIN-14 structure: Indicator digit (1-8 for case, 9 for variable) + GTIN-12 or GTIN-13 + recalculated check digit.",
            f"Label dimensions: {cfg['label_size_in']} inches. Bottom of label between {cfg['label_min_height_in']} and {cfg['label_max_height_in']} inches from bottom of carton, on the long side.",
            f"Print quality: ANSI Grade C or better. Verifier reports must be retained for 12 months.",
        ]
    ]
    out.append(PageBreak())

    out.append(Paragraph("Appendix R: Extended FAQ", H1))
    extra_faqs = [
        ("How are chargebacks settled?", f"Chargebacks are deducted from open invoices within 30 days of the violating event. Vendor sees the deduction on the next remittance advice."),
        ("Can I be reimbursed for the cost of stretch wrap?", "No. Packaging materials are at vendor expense and are part of the cost of doing business."),
        ("How do I dispute a scorecard metric?", f"Submit a scorecard dispute via the vendor portal within 15 days of the monthly scorecard publication. Provide source data supporting your dispute."),
        ("What happens if my carrier loses freight?", "File a claim with the carrier directly. {cfg['retailer_name']} is not the cargo claimant. Notify {cfg['retailer_name']} so the missing items can be excluded from the receipt."),
        ("Are there discounts for early payment?", f"Standard payment terms are {cfg['payment_terms']}. Early-pay discounts may be negotiated with the buyer of record."),
        ("How often does the routing guide change?", "Quarterly, with material changes communicated 30 days in advance via email and the vendor portal."),
        ("Can I deliver outside of normal receiving hours?", f"Only with prior approval from the destination DC. Standard hours are listed in Section 9."),
        ("What is the difference between an inner pack and a retail pack?", "Inner pack is a sub-divider within a master case (often 6, 12, or 24 units). Retail pack is the consumer-facing unit."),
        ("Do drop-ship vendors follow the same packaging rules?", f"Drop-ship has its own program-specific addendum that overrides the master carton rules in Section 2. See the Drop-Ship Vendor Manual."),
        ("How do I become an approved carrier?", f"Carriers apply through the {cfg['retailer_name']} Carrier Portal. Vendors do not control this list; vendors choose from approved carriers per the routing guide."),
        ("Can I co-load with another vendor?", "Co-loading is permitted with prior approval. Each vendor's freight must be separately invoiced and palletized; mixed-vendor pallets are not acceptable."),
        ("What if my product fails third-party testing?", f"Notify the buyer immediately. Affected SKUs are placed on hold. Vendor must address root cause and submit fresh test results."),
        ("How are sustainability scorecard points earned?", f"Points are earned for: meeting recyclable content target ({cfg['packaging_recyclable_pct']}%), reducing emissions year over year, providing optional disclosures, and completing sustainability training."),
        ("What is required for a new SKU launch?", f"Item setup (832 or vendor portal entry) at least {cfg['item_setup_lead_time_days']} days in advance, with all mandatory attributes; packaging samples submitted; test reports / COAs uploaded as applicable."),
        ("Can I update my GS1 prefix?", "Yes, with at least 60 days notice. All in-flight SKUs continue to use the original prefix; new SKUs may use the new prefix once registered."),
        ("How do I re-test a failed audit?", f"Schedule a re-audit through the same audit body within {cfg.get('audit_remediation_days', 60)} days. Submit the new audit report to {cfg['compliance_email']}."),
        ("What if my factory address changes?", "Update the Factory Information Form (VR-07) within 7 days. New facility may be subject to fresh audit before resuming production."),
        ("Are kosher / halal / organic certifications required?", "Required only for SKUs marketed with the corresponding claim. Certifications must be from recognized bodies and must be current."),
        ("How do I report a compliance concern about another vendor?", f"Submit confidentially to {cfg['compliance_email']}. {cfg['retailer_name']} investigates all credible reports."),
        ("Are there tools to help me automate compliance?", f"The {cfg['retailer_name']} vendor portal provides several free tools: routing helper, ASN validator, label preview, scorecard simulator. Third-party EDI providers offer integrated solutions."),
    ]
    for q, a in extra_faqs:
        out.append(Paragraph(f"Q: {q}", H3))
        out.append(Paragraph(f"A: {a}", BODY))
        out.append(Spacer(1, 4))
    out += _filler_paragraphs(cfg, "vendor education", 24)
    out.append(PageBreak())

    out.append(Paragraph("Appendix P: Country-Specific Shipping Notes", H1))
    out.append(Paragraph(
        f"This appendix provides notes for vendors shipping from specific origin countries. The notes are not exhaustive; vendors are responsible for full compliance with origin-country export rules and US import rules.",
        BODY,
    ))
    countries = [
        ("China", [
            "China Customs export documentation required: commercial invoice, packing list, customs declaration form.",
            "VAT export refund (rebate) handled by exporter; not relevant to US import.",
            "Section 301 tariffs apply to many HTS classifications; verify current tariff schedule.",
            "ISPM-15 wood treatment required for all wood pallets and dunnage.",
            "Chinese New Year impact on lead times: factories typically close 2-4 weeks; plan POs accordingly.",
            "Consolidator: US-based broker recommended for entry filings.",
        ]),
        ("Vietnam", [
            "Required documents: commercial invoice, packing list, certificate of origin, technical compliance documents (e.g., toy CPSC if applicable).",
            "Country of origin marking: 'Made in Vietnam'.",
            "Common port of origin: Ho Chi Minh City (Cat Lai). Saigon Newport for some categories.",
            "Vietnam-US Trade Agreement may affect some HTS classifications - verify current.",
            "Note rising labor costs and infrastructure bottlenecks; plan extra lead time during peak seasons.",
        ]),
        ("India", [
            "Required documents: commercial invoice, packing list, export packing list, certificate of origin.",
            "Country of origin marking: 'Made in India'.",
            "Major ports: Mumbai (Nhava Sheva), Chennai, Kolkata.",
            "Specific category considerations: textiles (high quality), pharmaceuticals (separate FDA pathway), seafood (FDA seafood HACCP).",
            "Currency considerations: invoicing in USD recommended.",
        ]),
        ("Mexico", [
            "USMCA preferential treatment available for many HTS classifications - verify origin rules.",
            "Required documents: commercial invoice, packing list, USMCA Certificate of Origin (if claiming preference).",
            "Country of origin marking: 'Made in Mexico' or 'Hecho en Mexico'.",
            "Land border crossings: Laredo, El Paso, Nogales most common. Coordinate with broker for entry.",
            "Trucking from Mexico: most freight transloaded at the border; through-truck operations limited.",
        ]),
        ("Bangladesh", [
            "Garments and textiles primary export; subject to GSP provisions where applicable.",
            "Factory safety: must be RMG Sustainability Council compliant (post-Rana Plaza framework).",
            "Country of origin marking: 'Made in Bangladesh'.",
            "Port of origin: Chattogram (Chittagong).",
        ]),
        ("Indonesia", [
            "Furniture, home goods, textiles common categories.",
            "Country of origin marking: 'Made in Indonesia'.",
            "Port of origin: Tanjung Priok (Jakarta), Tanjung Perak (Surabaya).",
        ]),
    ]
    for country, notes in countries:
        out.append(Paragraph(country, H3))
        for n in notes:
            out.append(Paragraph(f"- {n}", BULLET))
        out.append(Spacer(1, 6))
    out += _filler_paragraphs(cfg, "country-specific shipping", 22)
    out.append(PageBreak())

    out.append(Paragraph("Appendix Q: Detailed Change Log Across All Sections", H1))
    out.append(Paragraph(
        f"This appendix consolidates all material and editorial changes from the prior version into a single reference. Vendors with established operations should review this appendix carefully and update internal SOPs accordingly.",
        BODY,
    ))
    for entry in cfg.get("change_log", []):
        out.append(Paragraph(entry["heading"], H3))
        out.append(Paragraph(entry["body"], BODY))
        out.append(Spacer(1, 4))
    out += _filler_paragraphs(cfg, "change management", 24)
    out.append(PageBreak())

    out.append(Paragraph("Appendix L: Audit Checklist - Detailed", H1))
    out.append(Paragraph(
        f"This checklist is used by {cfg['retailer_name']} compliance auditors during routine and unannounced audits. Vendors should self-assess against this list quarterly to identify gaps proactively.",
        BODY,
    ))
    audit_areas = [
        ("Documentation Controls", [
            "Master vendor agreement on file and current",
            "All required onboarding forms (VR-01 through VR-12) on file",
            "Insurance certificate current; matches required limits",
            "Anti-Bribery and Sustainability attestations signed within last 12 months",
            "Factory information form on file for each producing facility",
            "Current social compliance audit (no critical findings)",
            "Test reports / COAs for all SKUs requiring them, indexed by SKU",
            "Production records and batch traceability for each lot",
            "Training records for vendor personnel involved in compliance functions",
            "Corrective action records for prior findings (if any)",
        ]),
        ("Production and Quality", [
            "In-line quality controls documented and active",
            "SPC records maintained for at least 24 months",
            "AQL inspection records on file for each lot",
            "Non-conforming product disposition records",
            "Customer complaint tracking and root-cause analyses",
            "Annual management review of quality system",
            "Corrective and preventive action (CAPA) program active",
            "Supplier qualification records for raw materials and components",
        ]),
        ("Packaging and Labeling", [
            "Approved corrugated suppliers in use; certifications current",
            "Sample packaging on file for each SKU",
            "Drop test records for each carton SKU",
            "Print quality verification records (ANSI grade)",
            "GS1 prefix registered and renewed",
            "SSCC pool management; no reuse within 12 months",
            "GTIN management; check-digit validation in place",
            "Sustainable packaging attestation; PCR percentage tracked",
        ]),
        ("Logistics and EDI", [
            "Routing portal access and current routing guide on file",
            "EDI trading partner profile current",
            "EDI transaction logs retained per SLA",
            "ASN accuracy tracked against scorecard",
            "On-time delivery tracked against scorecard",
            "Carrier compliance tracked",
            "BOL accuracy reviewed quarterly",
        ]),
        ("Social Compliance and Ethics", [
            "Annual factory audit completed",
            "No outstanding critical findings",
            "Major findings remediated within agreed timelines",
            "Worker grievance mechanism active and documented",
            "Forced labor / human trafficking due diligence in place",
            "Anti-bribery training delivered to all employees with relevant responsibilities",
            "Supplier code of conduct flowed down to subcontractors",
        ]),
        ("Sustainability and ESG", [
            "Packaging recyclable / compostable percentage tracked",
            "Material restrictions (PVC, EPS, oxo-degradable) compliance verified",
            "Energy and emissions tracking at the manufacturing level",
            "Water and waste management programs documented",
            "Sustainability scorecard data submitted on time",
        ]),
        ("Legal and Insurance", [
            "Insurance certificate current; correctly names additional insured",
            "Indemnification clauses understood and signed",
            "Anti-bribery and corruption training current",
            "Data privacy compliance for any customer or system data",
            "Intellectual property warranties on file",
            "Recall and safety incident protocol documented and tested",
        ]),
    ]
    for area, items in audit_areas:
        out.append(Paragraph(area, H3))
        for i in items:
            out.append(Paragraph(f"[ ] {i}", BULLET))
        out.append(Spacer(1, 8))
    out.append(PageBreak())

    out.append(Paragraph("Appendix M: Performance Improvement Plan Template", H1))
    out += [
        Paragraph(p, BODY)
        for p in [
            f"This template is provided to vendors entering a Performance Improvement Plan (PIP) per Section 7.11. The PIP runs for 90 days and must address all six scorecard metrics that contributed to the below-threshold composite.",
            "Section 1: Vendor Information. Company name, primary contact, account number, current scorecard, target scorecard.",
            "Section 2: Root Cause Analysis. For each metric below threshold, identify the root cause(s) using a structured method (5-Whys, fishbone, etc.). Distinguish between systemic causes and one-time events.",
            "Section 3: Corrective Action Plan. For each root cause, specify the corrective action, owner, target completion date, and verification method.",
            "Section 4: Preventive Action Plan. Identify preventive actions to avoid recurrence in adjacent areas. These often involve training, system changes, or process documentation updates.",
            "Section 5: Monitoring Plan. Define interim metrics, reporting cadence to the compliance liaison, and decision points (e.g., 'if ASN accuracy still below 95% at day 30, escalate to vendor leadership').",
            "Section 6: Resource Commitment. Identify any new resources (people, technology, training) the vendor is committing to support PIP execution.",
            "Section 7: Executive Sponsor. Senior vendor executive responsible for PIP outcomes. This person attends mid-PIP and exit reviews.",
            "Section 8: Sign-off. Vendor leadership and {cfg['retailer_name']} compliance liaison sign at PIP entry and at exit.",
        ]
    ]
    out += _filler_paragraphs(cfg, "performance improvement", 22)
    out.append(PageBreak())

    out.append(Paragraph("Appendix N: Acronyms and Abbreviations Index", H1))
    acronyms = [
        ("AQL", "Acceptable Quality Level"), ("ASN", "Advance Ship Notice"),
        ("AS2", "Applicability Statement 2 (EDI transmission protocol)"),
        ("BOL", "Bill of Lading"), ("BSN", "Beginning Segment for Ship Notice"),
        ("CAPA", "Corrective And Preventive Action"),
        ("CBP", "US Customs and Border Protection"),
        ("CCPA", "California Consumer Privacy Act"),
        ("CGL", "Commercial General Liability"), ("COA", "Certificate of Analysis"),
        ("COI", "Certificate of Insurance"), ("COO", "Country of Origin"),
        ("COPPA", "Children's Online Privacy Protection Act"),
        ("CPC", "Children's Product Certificate"),
        ("CPSC", "Consumer Product Safety Commission"),
        ("CPSIA", "Consumer Product Safety Improvement Act"),
        ("DC", "Distribution Center"), ("DOT", "US Department of Transportation"),
        ("DSCSA", "Drug Supply Chain Security Act"),
        ("DSHEA", "Dietary Supplement Health and Education Act"),
        ("ECT", "Edge Crush Test"), ("EDI", "Electronic Data Interchange"),
        ("EPR", "Extended Producer Responsibility"),
        ("ETL", "Intertek's Electrical Testing Laboratories Mark"),
        ("FALCPA", "Food Allergen Labeling and Consumer Protection Act"),
        ("FCPA", "Foreign Corrupt Practices Act"), ("FDA", "US Food and Drug Administration"),
        ("FFA", "Flammable Fabrics Act"), ("FHSA", "Federal Hazardous Substances Act"),
        ("FOB", "Free On Board"), ("FSC", "Forest Stewardship Council"),
        ("FSMA", "Food Safety Modernization Act"),
        ("FTC", "Federal Trade Commission"), ("GDPR", "General Data Protection Regulation"),
        ("GMA", "Grocery Manufacturers Association"), ("GRI", "Global Reporting Initiative"),
        ("GS1", "Global Standards organization"),
        ("GTIN", "Global Trade Item Number"), ("HACCP", "Hazard Analysis and Critical Control Points"),
        ("HMR", "Hazardous Materials Regulations"),
        ("HTS", "Harmonized Tariff Schedule"), ("IATA", "International Air Transport Association"),
        ("IMDG", "International Maritime Dangerous Goods"),
        ("INCI", "International Nomenclature of Cosmetic Ingredients"),
        ("ISO", "International Organization for Standardization"),
        ("ISPM-15", "International Standard for Phytosanitary Measures No. 15"),
        ("ISTA", "International Safe Transit Association"),
        ("ITF-14", "Interleaved Two-of-Five 14-digit barcode"),
        ("LTL", "Less Than Truckload"), ("MoCRA", "Cosmetic Regulation Modernization Act"),
        ("NDC", "National Drug Code"), ("NFP", "Nutrition Facts Panel"),
        ("OTC", "Over-the-Counter"), ("PCR", "Post-Consumer Recycled"),
        ("PDQ", "Pretty Darn Quick (display carton)"),
        ("PFAS", "Per- and Polyfluoroalkyl Substances"),
        ("PIP", "Performance Improvement Plan"), ("PII", "Personally Identifiable Information"),
        ("PLI", "Product Liability Insurance"), ("PO", "Purchase Order"),
        ("PPE", "Personal Protective Equipment"),
        ("PSI", "Pounds per Square Inch"), ("RoHS", "Restriction of Hazardous Substances"),
        ("RSPO", "Roundtable on Sustainable Palm Oil"), ("RTV", "Return to Vendor"),
        ("SCAC", "Standard Carrier Alpha Code"), ("SDS", "Safety Data Sheet"),
        ("SIA", "Sunscreen Innovation Act"), ("SLCP", "Social and Labor Convergence Program"),
        ("SMETA", "Sedex Members Ethical Trade Audit"),
        ("SPC", "Statistical Process Control"), ("SSCC", "Serial Shipping Container Code"),
        ("TASA", "Temporary Alternate Source Authorization"),
        ("TL", "Truckload"), ("TSCA", "Toxic Substances Control Act"),
        ("UFLPA", "Uyghur Forced Labor Prevention Act"),
        ("UL", "Underwriters Laboratories"), ("UPC", "Universal Product Code"),
        ("VAN", "Value-Added Network"), ("VGM", "Verified Gross Mass"),
        ("WRAP", "Worldwide Responsible Accredited Production"),
    ]
    a_table = Table(
        [["Acronym", "Definition"]] + list(acronyms),
        colWidths=[1.0 * inch, 5.5 * inch],
    )
    a_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#003366")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    out.append(a_table)
    out.append(PageBreak())

    out.append(Paragraph("Appendix O: Sustainability Reporting Guide", H1))
    out += [
        Paragraph(p, BODY)
        for p in [
            f"{cfg['retailer_name']} requires vendors to participate in annual sustainability reporting. The reporting period runs January 1 - December 31, with submissions due by March 31 of the following year.",
            f"Required disclosures: Scope 1 and Scope 2 GHG emissions; energy intensity (kWh per unit of production); water use; waste diverted from landfill; recyclable / compostable packaging percentage (target {cfg['packaging_recyclable_pct']}%); social compliance audit status; supplier diversity attestation.",
            "Optional disclosures: Scope 3 emissions, biodiversity initiatives, community investment, employee engagement scores. Vendors providing optional disclosures may earn additional sustainability scorecard points.",
            f"Reporting platform: {cfg['retailer_name']} uses the Higg / GRI-aligned vendor portal module. Vendors enter data directly; supporting documentation is uploaded for audit verification.",
            "Verification: 5% of vendors are randomly selected for third-party verification each year. Selected vendors must provide source documentation supporting reported figures.",
            "Recognition: top-quartile vendors are recognized in the annual {cfg['retailer_name']} Sustainability Report and may be invited to participate in joint sustainability initiatives with corporate.",
        ]
    ]
    out += _filler_paragraphs(cfg, "sustainability reporting", 22)
    out.append(PageBreak())

    out.append(Paragraph("Appendix K: Onboarding Forms Index", H1))
    forms = [
        ("VR-01", "Vendor Master Setup Form", "Initial onboarding"),
        ("VR-02", "EDI Trading Partner Profile", "EDI configuration"),
        ("VR-03", "Banking and Tax Information (W-9 / W-8BEN)", "Payment setup"),
        ("VR-04", "Sustainable Packaging Attestation", "Packaging program"),
        ("VR-05", "Anti-Bribery / Anti-Corruption Attestation", "Ethics compliance"),
        ("VR-06", "Insurance Certificate Submission", "COI submission"),
        ("VR-07", "Factory Information Form", "Per factory; required prior to first PO"),
        ("VR-08", "Audit and Test Report Authorization", "Auditor / lab authorization"),
        ("VR-09", "Item Setup (per SKU)", "Catalog / EDI 832"),
        ("VR-10", "Drop-Ship Program Enrollment", "Drop-ship vendors only"),
        ("VR-11", "Hazmat Vendor Attestation", "Hazmat-supplying vendors"),
        ("VR-12", "Cold Chain Vendor Attestation", "Refrigerated / frozen vendors"),
    ]
    f_table = Table(
        [["Form", "Title", "When Required"]] + [list(f) for f in forms],
        colWidths=[0.7 * inch, 3.0 * inch, 2.3 * inch],
    )
    f_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#003366")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    out.append(f_table)

    return out


def section_regulatory(cfg):
    """Section 11: Regulatory and Standards Reference."""
    elems = [
        Paragraph("Section 11: Regulatory and Standards Reference", H1),
        Paragraph(
            f"This section summarizes the key regulations, standards, and certifications that apply to merchandise sold by {cfg['retailer_name']}. Vendors are responsible for full compliance with all applicable laws; this section is provided as orientation, not as legal advice.",
            BODY,
        ),
    ]
    sections = [
        ("11.1 US Federal Regulations - Consumer Products", [
            "Consumer Product Safety Improvement Act (CPSIA, 2008): governs lead, phthalates, tracking labels, and third-party testing requirements for children's products.",
            "Federal Hazardous Substances Act (FHSA, 15 USC 1261): labeling and packaging requirements for hazardous household substances.",
            "Flammable Fabrics Act (FFA, 16 CFR 1610): flammability requirements for clothing and certain textile products.",
            "Toxic Substances Control Act (TSCA, 15 USC 2601): chemical reporting and restrictions, especially formaldehyde and PFAS.",
            "Federal Trade Commission (FTC) labeling rules: textile fiber identification, country of origin, care labeling, MAP (Made in USA), warranty disclosures.",
        ]),
        ("11.2 US Federal Regulations - Food and Drug", [
            "Food Safety Modernization Act (FSMA, 21 USC 2201 et seq.): preventive controls (21 CFR 117), Foreign Supplier Verification (21 CFR 1 Subpart L), Food Defense (21 CFR 121).",
            "Federal Food, Drug, and Cosmetic Act (FD&C Act, 21 USC 301): governs cosmetics, OTC drugs, dietary supplements, and food.",
            "Drug Supply Chain Security Act (DSCSA): track-and-trace for prescription drugs (limited applicability for retail).",
            "Cosmetic Regulation Modernization Act (MoCRA, 2022): facility registration, product listing, adverse event reporting for cosmetics.",
            "Dietary Supplement Health and Education Act (DSHEA): supplement labeling and structure-function claims.",
        ]),
        ("11.3 US Federal Regulations - Transportation and Hazmat", [
            "49 CFR 100-185: comprehensive hazmat regulations (HMR) governing classification, packaging, marking, labeling, and transportation of hazardous materials.",
            "Lithium Battery Guidance (PHMSA, IATA, IMDG): specific rules for lithium-ion and lithium-metal cells and batteries.",
            "Pipeline and Hazardous Materials Safety Administration (PHMSA) registration for hazmat shippers.",
            "Federal Motor Carrier Safety Administration (FMCSA) regulations: Hours of Service, vehicle inspections (do not apply directly to vendors but affect carrier selection).",
        ]),
        ("11.4 US Federal Regulations - Trade and Customs", [
            "Customs and Border Protection (CBP) regulations: 19 CFR governs entry, classification (HTS), valuation, country of origin, marking.",
            "Section 301 China tariffs: monitor list updates that affect HTS classifications subject to additional duties.",
            "Forced Labor regulations: Uyghur Forced Labor Prevention Act (UFLPA), 19 USC 1307 (Tariff Act forced labor provisions).",
            "Lacey Act: declarations for wood and paper products.",
            "Toxic Substances Control Act (TSCA) import certifications.",
        ]),
        ("11.5 US State Regulations", [
            "California Proposition 65: warning labels for chemicals causing cancer or reproductive harm. Vendors are responsible for accurate determinations.",
            "California Transparency in Supply Chains Act (CTSCA): supply-chain disclosure for slavery and human trafficking.",
            "New York State packaging EPR: producer responsibility for packaging materials placed on market.",
            "Maine PFAS reporting: PFAS use disclosure for products placed on market in Maine.",
            "Various state-level extended producer responsibility (EPR) frameworks for batteries, electronics, paint, mercury, etc.",
        ]),
        ("11.6 International Standards", [
            "ISO 9001 (Quality Management): vendors are encouraged to maintain ISO 9001 certification at the manufacturing facility level.",
            "ISO 14001 (Environmental Management): supports sustainability scorecard.",
            "ISO 17025 (Testing Laboratories): required for any lab providing test reports submitted to {cfg['retailer_name']}.",
            "ISPM-15 (Wood Packaging): required for international wood pallets.",
            "ASTM standards: F963 (toy safety), F2110 (polybag suffocation warning), F2057 (clothing storage units), and many others.",
        ]),
        ("11.7 Sustainability and ESG Frameworks", [
            "Global Reporting Initiative (GRI): disclosure framework used in {cfg['retailer_name']} sustainability reporting.",
            "Sustainable Apparel Coalition Higg Index: facility and product modules used to assess environmental impact.",
            "How2Recycle program: standardized labeling for consumer packaging recyclability.",
            "Forest Stewardship Council (FSC) certification for paper and wood products.",
            "Roundtable on Sustainable Palm Oil (RSPO) for palm-derived ingredients.",
        ]),
        ("11.8 Social Compliance Frameworks", [
            "SMETA (Sedex Members Ethical Trade Audit): the most common framework {cfg['retailer_name']} accepts.",
            "BSCI (Business Social Compliance Initiative): also accepted.",
            "SLCP (Social and Labor Convergence Program): accepted, primarily for apparel.",
            "WRAP (Worldwide Responsible Accredited Production): accepted for apparel and footwear.",
            f"Audits must show no Critical findings and no unresolved Major findings to be accepted by {cfg['retailer_name']}.",
        ]),
        ("11.9 Data Privacy", [
            "California Consumer Privacy Act (CCPA) and CPRA amendments: applies to vendors handling California consumer data.",
            "General Data Protection Regulation (GDPR): applies to vendors handling EU personal data.",
            "Personal Information Protection and Electronic Documents Act (PIPEDA): Canadian privacy law.",
            "Children's Online Privacy Protection Act (COPPA): applies to data collected from children under 13.",
        ]),
        ("11.10 Industry-Specific Standards", [
            "GS1 standards: GTIN, SSCC, GS1-128, GS1 DataBar, GS1 DataMatrix - the foundation of retail identification and data exchange.",
            "ANSI X12 EDI standards: 850, 855, 856, 810, 832, 753, 754, 997 - the standard transaction sets for retail.",
            "VICS / GS1 US Item Authentication Standards.",
            "ISO/IEC 15416 and 15415: barcode quality verification standards.",
        ]),
    ]
    for title, items in sections:
        elems.append(Paragraph(title, H2))
        elems += [Paragraph("- " + i, BULLET) for i in items]
        elems.append(Spacer(1, 6))
        elems += _filler_paragraphs(cfg, "regulatory compliance", 10)
        elems.append(Spacer(1, 8))

    elems.append(PageBreak())
    return elems


def section_scenarios(cfg):
    """Section 10: Worked Scenarios and Walkthroughs - long-form content."""
    elems = [
        Paragraph("Section 10: Worked Scenarios and Walkthroughs", H1),
        Paragraph(
            f"This section walks through detailed scenarios that vendors and {cfg['retailer_name']} compliance staff commonly encounter. Each walkthrough describes the situation, the applicable requirements, the expected vendor actions, and the typical outcome.",
            BODY,
        ),
    ]

    scenarios = [
        ("10.1 New Vendor Onboarding - Direct Import Apparel", [
            f"Scenario: A new apparel vendor based in Vietnam wants to begin shipping private-brand garments to {cfg['retailer_name']} via the Direct Import program. The vendor has been selected by the buyer and has signed the Master Vendor Agreement.",
            f"Step 1 (Day 1-5): Submit Vendor Master Setup Form (VR-01) and Banking/Tax Information (VR-03). Provide GS1 prefix and confirm onboarding lead.",
            f"Step 2 (Day 5-10): Complete Factory Information Form (VR-07) for the production facility. Submit current SMETA / BSCI / SLCP audit. Audit must be no older than {cfg['audit_validity_months']} months. If audit is expired or has critical findings, vendor must remediate before proceeding.",
            f"Step 3 (Day 10-20): Submit Insurance Certificate (VR-06) listing {cfg['retailer_full_name']} as additional insured. CGL per-occurrence ${cfg['cgl_per_occurrence_usd']:,}, aggregate ${cfg['cgl_aggregate_usd']:,}. Submit Sustainable Packaging Attestation (VR-04) and Anti-Bribery Attestation (VR-05).",
            f"Step 4 (Day 20-30): EDI testing. Configure AS2 or sFTP connection. Test 850, 855, 856, 810, 832 transactions. EDI Operations confirms readiness for production.",
            f"Step 5 (Day 30-{cfg['onboarding_days']}): Submit packaging samples to Packaging Engineering. First-production approval issued. Buyer issues first PO.",
            f"Common pitfalls: late audit submission delays onboarding; audit with critical findings requires factory remediation; packaging samples that fail drop test require re-submission.",
        ]),
        ("10.2 First Shipment - Pre-Ship Compliance Walkthrough", [
            f"Scenario: A vendor has received its first PO and is preparing the shipment.",
            f"Step 1: Acknowledge the 850 with an 855 within {cfg['ack_lead_time_hours']} hours. Use 'IA' status for full acceptance. If quantity adjustment is needed, contact buyer first.",
            f"Step 2: Produce merchandise meeting all applicable category requirements. Retain test reports and COAs as required.",
            f"Step 3: Pack merchandise in cartons meeting Section 2 requirements. Maximum carton weight {cfg['carton_max_weight_lbs']} lbs, maximum dimensions {cfg['carton_max_length_in']}x{cfg['carton_max_width_in']}x{cfg['carton_max_height_in']} inches. Apply color banner and required markings.",
            f"Step 4: Print GS1-128 shipping labels on each carton. Verify SSCC-18 uniqueness. Verify ANSI grade on a sample of labels.",
            f"Step 5: Palletize per Section 3. Maximum height {cfg['pallet_max_height_in']} inches; maximum weight {cfg['pallet_max_weight_lbs']} lbs. Apply {cfg['stretch_wrap_passes']} revolutions of stretch wrap. Affix master pallet label.",
            f"Step 6: Schedule routing via the routing portal at least {cfg['routing_lead_time_hours']} hours before ready time. Receive carrier and routing instruction.",
            f"Step 7: Schedule delivery appointment at least {cfg['appt_lead_time_hours']} hours in advance.",
            f"Step 8: Transmit 856 ASN at least {cfg['asn_lead_time_hours']} hours before physical arrival. Verify SSCCs match physical labels.",
            f"Step 9: Tender shipment to carrier with completed BOL. Driver departs.",
            f"Step 10: Transmit 810 invoice within {cfg['invoice_lead_time_days']} days of ship.",
            f"On-time delivery is measured against the appointment window. Late delivery beyond {cfg['delivery_window_hours_late']} hour grace period results in ${cfg['chargeback_late_delivery_usd']} chargeback.",
        ]),
        ("10.3 Chargeback Dispute - Late ASN", [
            f"Scenario: Vendor receives a chargeback notification deducting {cfg['chargeback_late_asn_pct']}% of invoice for a late ASN.",
            f"Step 1: Review chargeback details on the vendor portal. Note the deduction date, reference PO, and invoice number.",
            f"Step 2: Investigate root cause. Check ASN transmission logs, EDI 997 acknowledgment timestamps, carrier dispatch records.",
            f"Step 3: Determine whether to dispute. Disputable cases: vendor transmitted on time but {cfg['retailer_name']} ingest was delayed; carrier-attributable delay with documentation; force-majeure event.",
            f"Step 4: Submit dispute through vendor portal within {cfg['dispute_window_days']} days of deduction. Include EDI logs, carrier records, and a brief written summary.",
            f"Step 5: Compliance Operations responds within {cfg['dispute_response_days']} business days. Approved disputes are credited to the next invoice cycle.",
            f"Step 6: If denied and vendor disagrees, escalate once to the Vendor Compliance Council via the buyer of record. The Council's decision is final.",
            f"Step 7 (regardless of dispute outcome): implement corrective action to prevent recurrence. Update ASN transmission timing, increase monitoring, or change communication protocol as needed.",
        ]),
        ("10.4 Failed Drop Test - Packaging Engineering Review", [
            f"Scenario: An inspection company reports a failed drop test on a vendor's master carton.",
            f"Step 1: Receive failure report from inspection company through {cfg['retailer_name']}. Report includes photos, drop count, and failure mode (burst seam, damaged product, etc.).",
            f"Step 2: Vendor receives failed-inspection chargeback (${cfg['chargeback_drop_test_usd']}) and shipment hold pending re-inspection.",
            f"Step 3: Vendor analyzes root cause. Common causes: insufficient bursting strength (need {cfg['bursting_strength_psi']} psi or {cfg['ect_value']} ECT); poor seam tape application; void space allowing product to shift.",
            f"Step 4: Implement corrective action. Options include: upgrade corrugate weight, change seam tape, add internal partition, redesign inner pack.",
            f"Step 5: Submit revised packaging sample to Packaging Engineering. Sample must pass {cfg['ista_test_required']} - level testing.",
            f"Step 6: Once approved, vendor may resume shipping. Re-inspection of held shipment is at vendor expense.",
            f"Repeated failures may trigger a 'Packaging Engineering Required' designation, requiring approval of every new SKU's packaging before first ship.",
        ]),
        ("10.5 Cross-Dock Shipment Walkthrough", [
            f"Scenario: Vendor is participating in a cross-dock program where pre-allocated, store-ready cartons flow through the DC without staging.",
            f"Step 1: Receive cross-dock PO. Cartons are pre-allocated by store. Each carton's destination store number must appear on the GS1-128 label.",
            f"Step 2: Pack each carton with the contents intended for a single store. Mixed-store cartons are not permitted in cross-dock.",
            f"Step 3: Build pallets grouping cartons by destination DC and, where possible, by destination store cluster.",
            f"Step 4: Apply pallet master label and cross-dock indicator (XDOCK) prominently on the pallet. Stretch-wrap with {cfg['stretch_wrap_passes']} revolutions.",
            f"Step 5: Transmit cross-dock-flagged ASN. Each carton in the ASN includes destination store data.",
            f"Step 6: Deliver to assigned cross-dock DC within the appointment window. Cross-dock DCs typically have tighter receiving windows than full DCs.",
            f"Cross-dock specific chargebacks: mixed-store cartons (per carton); incorrect store designation (per carton); missing XDOCK pallet indicator.",
        ]),
        ("10.6 Hazmat Shipment Walkthrough", [
            f"Scenario: Vendor needs to ship aerosol products (DOT Hazard Class 2.1).",
            f"Step 1: Confirm the SKU is approved for shipment. Hazmat SKUs require advance approval per Section 3.5. Without approval, the shipment is rejected and ${cfg['chargeback_hazmat_usd']} chargeback applies.",
            f"Step 2: Verify SDS is current (annual refresh required). Submit SDS through vendor portal if not already on file.",
            f"Step 3: Confirm aerosol pallet limit: {cfg['aerosol_max_per_pallet']} units per pallet. Mixed pallets with non-hazmat freight are not permitted.",
            f"Step 4: Mark each carton with required UN markings, hazard class label, and proper shipping name.",
            f"Step 5: Apply DOT placards to the vehicle (driver responsibility); BOL must include hazmat declaration.",
            f"Step 6: Transmit ASN with hazmat indicators in appropriate segments.",
            f"Step 7: Deliver to a DC equipped to handle hazmat - check the DC capability list before scheduling. Some DCs do not accept hazmat.",
        ]),
        ("10.7 Performance Improvement Plan Walkthrough", [
            f"Scenario: Vendor's composite scorecard score has fallen below {cfg['scorecard_threshold']} for two consecutive months.",
            f"Step 1: Receive PIP notification from Compliance Operations. PIP duration: 90 days. Required composite score for exit: {cfg['scorecard_threshold']}+ for three consecutive months.",
            f"Step 2: Compliance liaison is assigned. Initial PIP meeting within 5 business days.",
            f"Step 3: Vendor presents root-cause analysis and improvement plan covering all six scorecard metrics.",
            f"Step 4: Weekly check-ins with the compliance liaison; biweekly metric reviews.",
            f"Step 5: Mid-PIP assessment at day 45. Course corrections as needed.",
            f"Step 6: PIP exit assessment at day 90. Successful: scorecard restoration. Unsuccessful: extension (rare), reduced order volume, or termination.",
            f"During PIP: vendor remains eligible for orders but may experience reduced volume or category restrictions. Buyer is notified of PIP status and adjusts order patterns accordingly.",
        ]),
        ("10.8 Recall Walkthrough", [
            f"Scenario: Vendor identifies a quality defect in product already shipped to {cfg['retailer_name']}.",
            f"Step 1: Notify {cfg['retailer_name']} Quality / Safety team within 24 hours of vendor awareness. Email {cfg['quality_email']} with description, affected lot/batch numbers, and risk severity.",
            f"Step 2: {cfg['retailer_name']} Quality team initiates the recall protocol. Vendor cooperates fully with information requests, including production records, SOP documentation, and any prior complaints.",
            f"Step 3: Determine recall classification (Class I/II/III for FDA-regulated; equivalent for CPSC-regulated). Vendor is responsible for any required regulator notifications.",
            f"Step 4: {cfg['retailer_name']} pulls affected inventory from DCs and stores. Customer-facing communications are coordinated by {cfg['retailer_name']} corporate communications.",
            f"Step 5: Vendor pays for return logistics, customer reimbursement, advertising the recall (Class I), and any regulatory penalties.",
            f"Step 6: Vendor implements root-cause corrective action and submits documentation. {cfg['retailer_name']} Quality team verifies effectiveness before resuming orders for the affected SKU.",
        ]),
    ]

    for title, paras in scenarios:
        elems.append(Paragraph(title, H2))
        elems += [Paragraph(p, BODY) for p in paras]
        elems += _filler_paragraphs(cfg, "scenario walkthrough", 14)
        elems.append(Spacer(1, 8))

    elems.append(PageBreak())
    return elems


def section_category_overlays(cfg):
    """Section 8: Category-specific addenda - adds substantial content."""
    elems = [
        Paragraph("Section 8: Category-Specific Addenda", H1),
        Paragraph(
            f"This section contains additional requirements for specific merchandise categories. Where a category-specific requirement conflicts with a general requirement in Sections 1-7, the category-specific requirement governs.",
            BODY,
        ),
    ]

    categories = [
        ("8.1 Apparel and Textiles", [
            f"Apparel SKUs must include fiber content labeling per the FTC Textile Fiber Products Identification Act and the Wool Products Labeling Act where applicable.",
            f"Country of origin must be sewn into the garment per 19 CFR 134.43. Hangtags are not a substitute for the sewn-in label.",
            f"Care labels must be permanently attached and meet 16 CFR 423 (Care Labeling Rule) requirements.",
            f"Apparel cartons should use the polybag exception for inner packaging where individual hanging is not required. Polybags must be at least {cfg.get('apparel_polybag_mil', 1.5)} mil thick and include the suffocation warning per ASTM F2110.",
            f"For Core Reset master cartons, sticker-based identification is permitted in lieu of pre-printed markings, provided the stickers are durable and waterproof.",
            f"Apparel packaging samples must be submitted to the Packaging Engineering team alongside garment samples; both must be approved before first production.",
        ]),
        ("8.2 Toys and Juvenile Products", [
            f"Toys must comply with ASTM F963, CPSIA Section 101 (lead content), CPSIA Section 108 (phthalates), and 16 CFR 1500 (small parts).",
            f"Children's product certificates (CPCs) must be on file for every toy SKU and must reference the specific test reports.",
            f"Tracking labels per CPSIA Section 103 are required on the product and on the packaging. The label must include: manufacturer name; production location and date; cohort information; and any other batch identifier needed for recall.",
            f"Magnet-containing toys must comply with the magnet safety standard (16 CFR 1262) regardless of intended age.",
            f"Battery-operated toys must use the battery compartment and screw-secured cover requirement per ASTM F963.",
            f"Bicycle and scooter products are subject to the additional CPSC regulations 16 CFR 1512 and 16 CFR 1500.86.",
        ]),
        ("8.3 Food, Beverage, and Pet Food", [
            f"Food SKUs must comply with FDA Food Safety Modernization Act (FSMA) preventive controls (21 CFR 117) and applicable state regulations.",
            f"Nutrition Facts Panel (NFP) must conform to the current 21 CFR 101.9 format. Allergens must be declared in the 'Contains' statement per FALCPA.",
            f"Best-by, sell-by, and use-by dates must be printed in clear, indelible ink on every consumer unit and on the case. Date format must be MM/DD/YYYY or follow the bilingual format where applicable.",
            f"Pet food must comply with AAFCO model regulations and the FDA pet food rules. Lot codes are required for traceability.",
            f"Refrigerated and frozen items must include a temperature monitor in each pallet. Temperature data is downloaded at receipt; excursions outside of {cfg.get('temp_monitor_window', '34-40F (refrigerated) or 0-10F (frozen)')} are subject to a quality hold.",
            f"Foreign-language packaging is acceptable provided the English-language version meets all US labeling requirements.",
        ]),
        ("8.4 OTC Drugs and Supplements", [
            f"Over-the-Counter (OTC) drugs require an NDC number and proper Drug Facts labeling per 21 CFR 201.66. Tamper-evident packaging is required.",
            f"Dietary supplements must comply with DSHEA labeling requirements and include a Supplement Facts panel per 21 CFR 101.36.",
            f"FDA Establishment Registration (and NDC if applicable) must be current. Vendors are responsible for FDA listing of all SKUs.",
            f"Cold-FLU products must be packaged to comply with the Combat Methamphetamine Epidemic Act (CMEA) where applicable.",
        ]),
        ("8.5 Electronics, Appliances, and Battery Products", [
            f"Electrical and electronic products must carry a recognized safety mark (UL, ETL, CSA) and must meet FCC Part 15 or Part 18 emissions standards.",
            f"Power supplies must meet DOE Level VI energy efficiency standards.",
            f"Wireless devices must include the FCC ID and any required IC ID for Canadian distribution.",
            f"Lithium-ion batteries (loose or in-product) must include UN 38.3 testing documentation and the IATA/IMDG-required markings on the carton.",
            f"Bluetooth and Wi-Fi devices must include the proper certification logos on the packaging.",
            f"E-waste/RoHS compliance: All electronics must be RoHS-compliant unless specifically exempted. Vendors must retain RoHS test reports.",
        ]),
        ("8.6 Cosmetics and Personal Care", [
            f"Cosmetics products must comply with the FD&C Act and 21 CFR 700-740. Color additives must be from the FDA-approved list.",
            f"INCI ingredient labeling is required and must conform to the Cosmetic Ingredient Review (CIR) standards.",
            f"Tamper-evident packaging is required for liquid and semi-solid cosmetics where the product can be contaminated through removal of the cap.",
            f"Sunscreens (regulated as OTC drugs) must comply with the Sunscreen Innovation Act (SIA) and proper Drug Facts labeling.",
        ]),
        ("8.7 Seasonal and Holiday Merchandise", [
            f"Seasonal merchandise has limited shelf time and must arrive in pristine condition. Damage allowances are not increased for seasonal SKUs.",
            f"Christmas tree lights and other electrical decorations must include the UL/ETL mark and meet UL 588 / UL 1573 requirements.",
            f"Halloween costumes and accessories must comply with the flammability standard 16 CFR 1610.",
            f"Holiday cartons must be marked with the program code (e.g., Easter 2026, Halloween 2026) on the carton in addition to the standard markings.",
            f"Floor display units (FDUs) for seasonal programs must be drop-tested and able to be assembled by a single store associate in under 5 minutes.",
        ]),
        ("8.8 Hazmat-Specific Categories", [
            f"Aerosols (DOT Hazard Class 2.2 or 2.1) require advance approval and pallet limits per Section 3.5.",
            f"Flammable liquids (lighter fluid, alcohol, certain cleaning products) must include flame symbols, UN markings, and proper inner packaging.",
            f"Lithium battery products require dedicated documentation and packaging per UN 38.3 and IATA Lithium Battery Guidance.",
            f"Cleaning products containing corrosives require child-resistant packaging per 16 CFR 1700.",
            f"Vendor must complete a hazmat attestation annually for each hazmat SKU and renew the SDS annually.",
        ]),
    ]
    for title, paras in categories:
        elems.append(Paragraph(title, H2))
        elems += [Paragraph(p, BODY) for p in paras]
        elems += _filler_paragraphs(cfg, title.split(' ', 1)[1].lower(), 14)
        elems.append(Spacer(1, 8))

    elems.append(PageBreak())
    return elems


def section_dc_reference(cfg):
    """Section 9: Distribution Center and Port Reference."""
    elems = [
        Paragraph("Section 9: Distribution Center and Port Reference", H1),
        Paragraph(
            f"This section provides reference information about {cfg['retailer_name']} distribution centers, port assignments, and standard operating windows. The authoritative list is on the vendor portal; this section reflects the published schedule as of the effective date.",
            BODY,
        ),
    ]

    dcs = [
        ("DC-101", "Atlanta, GA", "Mon-Sat 6am-10pm", "TL, LTL, Parcel"),
        ("DC-102", "Dallas, TX", "Mon-Sat 5am-11pm", "TL, LTL"),
        ("DC-103", "Indianapolis, IN", "Mon-Fri 6am-8pm; Sat 6am-2pm", "TL, LTL"),
        ("DC-104", "Reno, NV", "Mon-Fri 5am-10pm", "TL, LTL, Intermodal"),
        ("DC-105", "Lakeland, FL", "Mon-Sat 6am-9pm", "TL, LTL, Parcel"),
        ("DC-106", "Joliet, IL", "Mon-Sat 5am-11pm", "TL, LTL, Intermodal"),
        ("DC-107", "Charlotte, NC", "Mon-Fri 6am-8pm; Sat 6am-2pm", "TL, LTL"),
        ("DC-108", "Phoenix, AZ", "Mon-Sat 5am-10pm", "TL, LTL"),
        ("DC-109", "Portland, OR", "Mon-Fri 6am-8pm; Sat 6am-12pm", "TL, LTL, Intermodal"),
        ("DC-110", "Newark, NJ", "Mon-Sat 5am-11pm", "TL, LTL, Parcel"),
    ]
    dc_table = Table(
        [["DC #", "Location", "Receiving Hours", "Modes"]] + list(dcs),
        colWidths=[0.7 * inch, 1.7 * inch, 2.6 * inch, 1.6 * inch],
    )
    dc_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#003366")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
    ]))
    elems.append(dc_table)
    elems.append(Spacer(1, 12))

    elems += [
        Paragraph(p, BODY)
        for p in [
            f"Each DC operates an appointment-based receiving system. Appointment windows are 30-minute slots; arrival within {cfg['delivery_window_hours_early']} hour early to {cfg['delivery_window_hours_late']} hour late of the appointment counts as on-time.",
            f"DCs may have specific commodity restrictions based on equipment (e.g., refrigerated, frozen, hazmat capability). Vendors are responsible for ensuring the destination DC accepts the merchandise type before tendering.",
            f"For port-of-entry shipments, {cfg['retailer_name']} maintains consolidation operations at Los Angeles / Long Beach, Savannah, Houston, and Newark. Port assignment is controlled by the routing instruction; vendors should not deviate from the assigned port without written approval.",
        ]
    ]

    elems.append(Paragraph("9.1 Receiving Operating Procedures", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"Carriers arriving at the gate must present the appointment confirmation, BOL, and PO list. Drivers should expect a security check and a yard assignment. Vehicles without appointments will be turned away.",
            f"Live-load appointments require the carrier to remain on-site for unloading. Drop-trailer appointments allow the carrier to detach the trailer and depart, with retrieval coordinated separately.",
            f"Unloading is performed by {cfg['retailer_name']} associates using powered material handling equipment. Vendor / carrier personnel may not enter the warehouse beyond the dock office.",
        ]
    ]

    elems.append(Paragraph("9.2 Port and Customs Information", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"Direct Import vendors ship to one of the {cfg['retailer_name']} consolidation facilities or directly to a US port. Customs clearance is coordinated by {cfg['retailer_name']}'s designated customs broker.",
            f"Required customs documentation includes: commercial invoice (with HTS classifications), packing list, bill of lading or air waybill, certificate of origin (where applicable), and any product-specific certifications.",
            f"FDA-regulated products must include the FDA Prior Notice. CBP entry is filed on the vendor's behalf by the broker; vendors must provide accurate documentation to support the filing.",
        ]
    ]

    elems.append(PageBreak())
    return elems


def _filler_paragraphs(cfg, section_topic, count=10):
    """Generate realistic filler paragraphs for a given section topic to add depth."""
    templates = [
        f"In practice, vendors who consistently meet {cfg['retailer_name']}'s {section_topic} requirements report fewer chargebacks, higher fill rates, and shorter receiving cycle times. {cfg['retailer_name']} encourages vendors to invest in compliance tooling, automated label generation, and ongoing operator training to embed these standards into daily operations rather than treating them as an audit-time exercise.",
        f"Vendor representatives are invited to attend the quarterly {cfg['retailer_name']} Compliance Webinar, where the Compliance Operations team reviews trending non-compliance categories, demonstrates corrective actions, and previews upcoming changes. Recordings are posted to the vendor portal under the Education tab.",
        f"For categories with elevated risk - food, supplements, infant care, electronics, and seasonal hard goods - {cfg['retailer_name']} may impose category-specific overlays on top of the requirements in this section. These overlays are issued through the buyer of record and are binding for the affected SKUs.",
        f"Vendors operating in multiple {cfg['retailer_name']} programs (e.g., Direct Import, Domestic, Drop-Ship) must ensure that all programs follow the requirements in this manual unless a written program-specific exception is on file with the Compliance Operations team.",
        f"Documentation referenced throughout this {section_topic} guidance must be retained by the vendor for a minimum of 36 months and made available to {cfg['retailer_name']} or its designated auditors within 7 calendar days of a written request. Documentation may include test reports, factory inspection records, COIs, COAs, training rosters, and corrective action plans.",
        f"Where this section refers to a 'designated' party (carrier, lab, auditor), the current designation list is maintained on the vendor portal and may be updated by {cfg['retailer_name']} from time to time. Vendors are expected to verify the current designation prior to acting on it; reliance on outdated lists does not excuse non-compliance.",
        f"Continuous improvement: vendors with three or more consecutive months of full {section_topic} compliance are eligible for the {cfg['retailer_name']} Preferred Vendor program, which offers expedited dispute resolution, priority dock scheduling, and reduced inspection cadence. Eligibility is reviewed monthly.",
        f"For all {section_topic}-related disputes, vendors must follow the dispute process outlined in Section 7. Escalations submitted outside the dispute process - including direct emails to buyers - will not be considered.",
        f"International vendors should pay particular attention to {section_topic} requirements that may differ from domestic norms, including documentation in English, currency conversion conventions, and customs declarations. {cfg['retailer_name']}'s designated customs broker is identified on the vendor portal.",
        f"Subcontracting any aspect of {section_topic} responsibility (to a 3PL, factory, packaging supplier, or service provider) does not relieve the primary vendor of responsibility. The vendor remains the single point of accountability for any failure regardless of which party caused it.",
        f"Training: {cfg['retailer_name']} expects that all vendor personnel involved in {section_topic} have received documented training on these requirements. Training rosters and completion records must be available for audit on 7 days' notice.",
        f"Audit trail: every {section_topic} action that triggers a chargeback, dispute, or corrective action must be supported by primary records (timestamps, signatures, logs). Recreated or reconstructed records are not acceptable evidence in disputes.",
        f"Continuous audit: {cfg['retailer_name']} reserves the right to audit any {section_topic} aspect at any time without prior notice. Audit findings will be communicated within 30 calendar days, with a corrective action plan required within an additional 30 days.",
        f"Communication: changes to {section_topic} requirements are communicated through the vendor portal, email distribution list, and the quarterly Compliance Webinar. Vendors are responsible for keeping their distribution-list contacts current.",
        f"Vendor portal: the {cfg['retailer_name']} Vendor Portal is the authoritative source for current {section_topic} requirements. Printed copies of this manual become outdated quickly and should not be relied upon as the operational source of truth.",
        f"Record retention: all records related to {section_topic} compliance must be retained for the longer of: (a) 36 months from the date of the activity; (b) the period required by applicable law; or (c) any longer period specified in a category-specific overlay.",
        f"Cooperation: vendors must cooperate fully and in good faith with {cfg['retailer_name']} compliance investigations. Failure to cooperate is itself a violation that may trigger immediate suspension of order eligibility.",
        f"Remediation: when a {section_topic} non-compliance event occurs, the vendor must implement a corrective action that addresses the root cause - not just the immediate symptom. {cfg['retailer_name']}'s Compliance Operations team may request evidence of root-cause analysis and effectiveness verification.",
        f"Governance: {cfg['retailer_name']}'s Compliance Council reviews vendor performance monthly. Vendors with sustained non-compliance may be invited to a Council meeting to present a remediation plan and acknowledge accountability.",
        f"Escalation: vendor concerns about {section_topic} requirements that cannot be resolved through normal channels may be escalated to the Vendor Ombudsman at {cfg.get('compliance_email', 'compliance@example.com')} marked Attention: Ombudsman.",
        f"Practical guidance: vendors are encouraged to designate a single point of contact for {section_topic} matters within their organization. This individual is the primary liaison with {cfg['retailer_name']} Compliance Operations and is responsible for cascading requirements internally.",
        f"Process maturity: top-quartile vendors typically have documented standard operating procedures for {section_topic} that are reviewed annually and updated whenever {cfg['retailer_name']} requirements change.",
        f"Technology: investments in compliance technology (label printing systems, EDI middleware, automated label verifiers, document management) are strongly correlated with sustained scorecard performance in {section_topic} categories.",
        f"Periodic reviews: it is good practice to review {section_topic} compliance posture quarterly with internal stakeholders (operations, IT, quality, legal). Many vendors integrate this into existing supplier business reviews.",
        f"Exception management: where a vendor cannot meet a {section_topic} requirement on a specific shipment, the proper course is a written exception request submitted in advance through the vendor portal. After-the-fact justifications are not accepted.",
        f"Pre-emptive communication: when a vendor anticipates a {section_topic} compliance issue (factory closure, raw material substitution, system outage), early notification to {cfg['retailer_name']} can mitigate impact and may reduce or waive the associated chargeback.",
        f"Mutual benefit: {section_topic} requirements exist to ensure consistent customer experience and operational efficiency. Vendors who internalize this perspective find compliance becomes a competitive differentiator rather than a cost center.",
        f"Industry forums: {cfg['retailer_name']} participates in industry forums (RVCF, ECCMA, GS1 US) to align retailer requirements where possible. Vendor engagement in these forums is encouraged.",
        f"Continuous learning: {section_topic} requirements evolve with regulation, technology, and consumer expectations. Vendors are expected to maintain current knowledge through industry publications, webinars, and professional development.",
    ]
    return [Paragraph(t, BODY) for t in templates[:count]]


def section_introduction(cfg):
    elems = [
        Paragraph("Section 1: Introduction", H1),
        Paragraph(f"Doing Business with {cfg['retailer_full_name']}", H2),
    ]
    intro_paras = [
        f"{cfg['retailer_full_name']} is committed to operational excellence and to providing customers with quality merchandise at compelling value. We are equally committed to building strong, long-term relationships with our vendor partners.",
        f"This Vendor Manual establishes the requirements that all vendors must meet in order to do business with {cfg['retailer_name']}. The requirements documented herein are designed to ensure consistent quality, accurate logistics, regulatory compliance, and on-time delivery of merchandise to our distribution centers and stores.",
        "Adherence to these requirements is a condition of every purchase order. Failure to comply may result in chargebacks, return-to-vendor (RTV) actions, removal from active vendor lists, and recovery of damages. Vendors are responsible for ensuring that their carriers, manufacturers, packaging suppliers, and 3PLs are aware of and follow these requirements.",
        f"Effective Date: {cfg['effective_date']}. This version supersedes all prior versions of the {cfg['doc_title']}. Vendors must implement the requirements no later than 30 days after the effective date unless otherwise stated.",
        f"For questions regarding this manual, contact: {cfg['vendor_relations_email']}.",
    ]
    elems += [Paragraph(p, BODY) for p in intro_paras]
    elems += _filler_paragraphs(cfg, "operational compliance", 22)

    elems.append(Paragraph(f"Our Mission and Values", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"{cfg['retailer_name']} serves millions of customers each week. Every product on our shelves represents a promise: that it is safe, that it is well-priced, and that it arrived through a supply chain that respects workers, communities, and the environment.",
            f"Our merchandising philosophy is to combine private-brand value with a curated assortment of trusted national brands. We expect every vendor partner to share this philosophy and to bring their own commitment to quality and ethics to the relationship.",
            f"Vendors agree to abide by our Supplier Code of Conduct, which is published separately on the vendor portal and incorporated by reference into this manual.",
            f"We measure success not only in dollars and units shipped, but in the long-term health of the partnership. Our scorecard, audit, and continuous improvement programs are designed to give vendors clear, actionable signal about where they are excelling and where attention is required.",
        ]
    ]

    elems.append(Paragraph("Vendor Categorization", H3))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"{cfg['retailer_name']} classifies vendors into the following operating categories. Specific provisions in this manual may apply to one or more categories; where not specified, the requirement applies to all categories:",
            "(a) Direct Import - vendors shipping merchandise produced outside the United States directly to a {cfg['retailer_name']} consolidation facility or US port.",
            "(b) Domestic - vendors with US-based production or US-based distribution shipping to a {cfg['retailer_name']} domestic DC.",
            "(c) Drop-Ship - vendors fulfilling individual customer orders directly via the {cfg['retailer_name']} e-commerce program.",
            "(d) Cross-Dock - vendors shipping pre-allocated, store-ready cartons through a flow-through DC.",
            "(e) Specialty - vendors operating in regulated categories (food, OTC, supplements, infant) with category-specific overlays.",
        ]
    ]

    elems.append(Paragraph("Vendor Onboarding Process", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"All new vendors must complete the {cfg['retailer_name']} onboarding workflow before the first purchase order is issued. The workflow includes business verification, banking and tax documentation, EDI testing, packaging certification, and a compliance attestation.",
            f"Onboarding is coordinated by the {cfg['retailer_name']} Vendor Relations team. Estimated time to complete onboarding is {cfg['onboarding_days']} business days, assuming all documentation is provided promptly.",
            "Vendors must provide a single point of contact who is authorized to receive compliance correspondence. Updates to the contact must be communicated within five (5) business days of the change.",
        ]
    ]

    elems.append(Paragraph("Scope of This Manual", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"This manual applies to all merchandise shipped to {cfg['retailer_name']} distribution centers, cross-dock facilities, direct-to-store deliveries, and any third-party fulfillment locations operated on behalf of {cfg['retailer_name']}.",
            "It covers shipping and logistics, packaging and case-pack requirements, labeling and barcode standards, electronic data interchange (EDI), quality and testing, social compliance and audit requirements, legal and insurance requirements, and the chargeback / penalty structure for non-compliance events.",
            "Section-specific contact information is provided at the end of each section. The official, current version of this manual is published on the vendor portal and replaces any printed copies in vendor possession.",
        ]
    ]
    elems.append(PageBreak())
    return elems


def section_carton(cfg):
    elems = [
        Paragraph("Section 2: Carton and Inner-Pack Requirements", H1),
        Paragraph(
            "Failure to follow these guidelines may result in chargebacks, additional handling fees, or rejection of the shipment.",
            BODY,
        ),
    ]

    elems.append(Paragraph("2.1 Carton Material and Construction", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"All cartons produced for {cfg['retailer_name']} must be manufactured using virgin or post-consumer fiber meeting a minimum bursting strength of {cfg['bursting_strength_psi']} lbs. per square inch (Mullen test) or an equivalent edge-crush test (ECT) value of {cfg['ect_value']}.",
            f"Cartons must be {cfg['carton_corrugation']} corrugated using water-soluble adhesive. Cartons must be securely sealed using kraft paper tape or pressure-sensitive tape rated for the carton weight.",
            f"All cartons must be strong enough to support the contents through automated material-handling, conveyor sortation, and stacked transit conditions. The bottom carton in a stack may be required to support up to {cfg['stack_support_lbs']} lbs of additional weight without failure.",
            "Cartons must hold their contents in place using internal void fill, partitions, or molded inserts as appropriate. Loose-pack shipments are not permitted.",
        ]
    ]

    elems.append(Paragraph("2.2 Carton Dimensional and Weight Limits", H3))
    dim_table = Table(
        [
            ["", "Minimum", "Maximum"],
            ["Weight", f"{cfg['carton_min_weight_lbs']} lbs", f"{cfg['carton_max_weight_lbs']} lbs"],
            ["Length", f"{cfg['carton_min_length_in']} in", f"{cfg['carton_max_length_in']} in"],
            ["Width", f"{cfg['carton_min_width_in']} in", f"{cfg['carton_max_width_in']} in"],
            ["Height", f"{cfg['carton_min_height_in']} in", f"{cfg['carton_max_height_in']} in"],
        ],
        colWidths=[1.5 * inch, 1.7 * inch, 1.7 * inch],
    )
    dim_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#003366")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 1), (-1, -1), "CENTER"),
    ]))
    elems.append(dim_table)
    elems.append(Spacer(1, 12))
    elems.append(Paragraph(
        f"Cartons outside of the dimensional limits above will not be accepted at the receiving dock. Vendors must contact the {cfg['retailer_name']} Logistics team in advance for any exception requests; written approval is required.",
        BODY,
    ))

    elems.append(Paragraph("2.3 Drop Test Requirements", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"Cartons will be drop-tested by {cfg['retailer_name']}'s designated inspection company. Drop tests confirm that the carton design and packaging will protect merchandise during shipping and handling.",
            "General Merchandise (not marked FRAGILE): six-side drop test - three cartons will be dropped on each of the six sides.",
            "Fragile Merchandise (ceramics, glass, candles, polyresin): four-side drop test - four cartons will be dropped on one side each.",
        ]
    ]

    drop_table = Table(
        [
            ["Packaged Weight", "Free Fall Drop Height"],
            [f"{cfg['carton_min_weight_lbs']} - 20.99 lbs", f"{cfg['drop_height_light_in']} in"],
            ["21.00 - 40.99 lbs", f"{cfg['drop_height_med_in']} in"],
            [f"41.00 - {cfg['carton_max_weight_lbs']} lbs", f"{cfg['drop_height_heavy_in']} in"],
        ],
        colWidths=[2.5 * inch, 2.5 * inch],
    )
    drop_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#003366")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 1), (-1, -1), "CENTER"),
    ]))
    elems.append(drop_table)
    elems.append(Spacer(1, 12))
    elems += [
        Paragraph(p, BODY)
        for p in [
            "Drop test pass/fail criteria: minor crush is acceptable. A burst seam, opened seal, internal product damage, or product malfunction constitutes failure.",
            "Failed drop tests result in a re-inspection chargeback and may delay shipment release. Repeated failures may result in mandatory packaging engineering review at vendor expense.",
        ]
    ]

    elems.append(Paragraph("2.4 Carton Markings", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            "Every carton must be pre-printed with the following information on two opposite sides. Top and bottom markings are not required. Hand-written markings are only acceptable as corrections to pre-printed information and must be legible and waterproof.",
        ]
    ]
    markings = [
        "(1) SKU Number / Item Number",
        "(2) Special Carton Marking (color banner, fragile, hazmat, etc. as applicable)",
        f"(3) Year Code (for non-core items - format: YY{cfg['year_code_format']})",
        "(4) Case Pack",
        "(5) Gross Weight / Cubic Feet",
        "(6) Country of Origin (e.g., Made in China)",
        "(7) Item Description",
        f"(8) Purchase Order number (digits {cfg['po_digits']})",
        "(9) Vendor identifier (3-letter prefix issued during onboarding)",
    ]
    elems += [Paragraph(m, BULLET) for m in markings]
    elems.append(Paragraph(
        f"Marking ink must be {cfg['marking_ink']} and must remain legible after exposure to water, abrasion, and standard warehouse conditions for at least {cfg['marking_durability_days']} days.",
        BODY,
    ))

    elems.append(Paragraph("2.5 Fragile Markings", H3))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"Cartons containing fragile merchandise must be marked FRAGILE on all four sides. Fragile markings must be printed in {cfg['fragile_color']} ink and must be the most prominent graphic on the carton.",
            "Fragile classifications include but are not limited to: glass, ceramic, porcelain, polyresin, candles, stoneware, and any product where structural failure could create a safety hazard.",
            "A broken-glass icon must accompany the FRAGILE text. Generic 'handle with care' text is not a substitute for the FRAGILE marking.",
        ]
    ]

    elems.append(Paragraph("2.6 Inner Pack and Display Pack (PDQ) Specifications", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"PDQ (Pretty Darn Quick) display cartons enable direct-to-floor merchandising. PDQs must not exceed {cfg['pdq_max_depth_in']} inches deep, {cfg['pdq_max_width_in']} inches wide, or {cfg['pdq_max_height_in']} inches tall.",
            f"PDQ inner box material: {cfg['pdq_material']}. PDQs must be re-shippable and must be approved by the buyer prior to first production.",
            "Inner packs must be designed to allow associates to break down master cartons quickly while maintaining individual unit protection. Excessive over-packaging that adds material cost without protection benefit is discouraged.",
        ]
    ]

    elems.append(Paragraph("2.7 Color Banner Requirements", H3))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"The color band must be {cfg['banner_width_in']} inches wide and printed on all four sides of the master carton. Color stripes within the banner should be 2 inches wide with 1-inch spacing.",
            "If the carton is too small to accommodate the full banner, the band may be trimmed proportionally using a 2:1 ratio while preserving the visual identifier.",
            "No color banner is required when the master carton serves as the retail packaging. Tape and stickers are not permitted on master or inner cartons except as explicitly authorized for Apparel Items and Core Reset master cartons.",
        ]
    ]

    elems.append(Paragraph("2.8 Banding and Strapping", H3))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"Cartons may not be banded together. Single-carton straps are {cfg['single_carton_strap']}. Pallet-level strapping is permitted and required where the load otherwise lacks stability.",
        ]
    ]

    elems.append(Paragraph("2.9 Carton Examples", H3))
    for i in range(4):
        elems.append(Paragraph(f"Example {i + 1}: A representative carton marking layout illustrating SKU placement, case pack, gross weight, country of origin, color banner, and fragile / hazmat marking when applicable.", BODY))
        elems.append(Spacer(1, 14))
    elems += _extra_carton_content(cfg)
    elems.append(PageBreak())
    return elems


def section_shipping(cfg):
    elems = [
        Paragraph("Section 3: Shipping and Logistics", H1),
        Paragraph(
            f"This section establishes the requirements for transportation, freight terms, pallet construction, routing, and delivery appointments for all merchandise destined to {cfg['retailer_name']} facilities.",
            BODY,
        ),
    ]

    elems.append(Paragraph("3.1 Pallet Specifications", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"All shipments must be palletized using {cfg['pallet_type']} pallets meeting GMA standards or, where specified, four-way entry block pallets. Pallets must be heat-treated (HT) and stamped to comply with ISPM-15 if produced from solid wood.",
            f"Maximum total pallet height including product: {cfg['pallet_max_height_in']} inches. Maximum gross pallet weight: {cfg['pallet_max_weight_lbs']} lbs. Pallets exceeding these limits will be rejected at receipt.",
            f"Pallets must be stretch-wrapped with a minimum of {cfg['stretch_wrap_passes']} full revolutions of clear, machine-grade stretch film. Wrap must encapsulate the load from the top of the product to the underside of the top deck of the pallet.",
            f"Single-SKU pallets are required for purchase order quantities above {cfg['single_sku_pallet_threshold']} units of the same SKU. Mixed-SKU pallets must include a packing list affixed to the outside of the wrap.",
            f"Pallets must use a {cfg['pallet_pattern']} pallet pattern unless otherwise specified by the buyer. Overhang from the pallet edge may not exceed {cfg['pallet_overhang_in']} inch.",
        ]
    ]

    elems.append(Paragraph("3.2 Routing and Carrier Requirements", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"All inbound shipments must be routed through the {cfg['retailer_name']} Routing Guide published on the vendor portal. The routing guide is updated quarterly and supersedes any prior routing instructions.",
            f"Vendors are responsible for confirming carrier eligibility before tendering a shipment. Use of an unapproved carrier results in a chargeback equal to {cfg['chargeback_unapproved_carrier_pct']}% of the freight invoice plus any reroute costs.",
            f"For prepaid shipments, freight terms are {cfg['freight_terms_prepaid']}. For collect shipments, freight terms are {cfg['freight_terms_collect']}.",
            f"Minimum lead time for routing requests: {cfg['routing_lead_time_hours']} hours prior to ready-to-ship time.",
        ]
    ]

    elems.append(Paragraph("3.3 Delivery Appointments", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"All deliveries to {cfg['retailer_name']} distribution centers require a scheduled appointment. Appointments must be scheduled at least {cfg['appt_lead_time_hours']} hours in advance via the {cfg['retailer_name']} Carrier Portal.",
            f"On-time delivery window: arrival within {cfg['delivery_window_hours_early']} hour early to {cfg['delivery_window_hours_late']} hour late of the scheduled appointment.",
            f"Late deliveries (more than {cfg['delivery_window_hours_late']} hour past appointment) are subject to a chargeback of ${cfg['chargeback_late_delivery_usd']} per appointment plus any unloading delay fees.",
            f"Missed appointments require a minimum of {cfg['missed_appt_notice_hours']} hours' notice for rescheduling. No-shows result in a chargeback of ${cfg['chargeback_no_show_usd']}.",
        ]
    ]

    elems.append(Paragraph("3.4 Freight Documentation", H3))
    elems += [
        Paragraph(p, BODY)
        for p in [
            "Every shipment must be accompanied by a Bill of Lading (BOL) listing the PO number, total cartons, total pallets, gross weight, and freight class.",
            "Vendors must transmit an Advance Ship Notice (ASN, EDI 856) prior to physical arrival. See Section 5 for ASN requirements.",
            f"Commercial invoices for international shipments must include 10-digit HTS classification, country of origin, and certified declarations as required by {cfg['retailer_name']}'s import compliance program.",
        ]
    ]

    elems.append(Paragraph("3.5 Hazmat and Restricted Commodities", H3))
    elems += [
        Paragraph(p, BODY)
        for p in [
            "Hazardous materials must be classified, packaged, and labeled in accordance with 49 CFR (US DOT) and IATA / IMDG regulations as applicable. Vendors must provide current Safety Data Sheets (SDS) for all hazmat SKUs.",
            f"Lithium battery shipments must comply with the most recent IATA Lithium Battery Guidance and {cfg['retailer_name']}'s Battery Handling Standard published on the vendor portal.",
            f"Aerosol products require advance approval and are limited to {cfg['aerosol_max_per_pallet']} units per pallet.",
        ]
    ]
    elems += _extra_shipping_content(cfg)
    elems.append(PageBreak())
    return elems


def section_labeling(cfg):
    elems = [
        Paragraph("Section 4: Labeling and Barcode Requirements", H1),
        Paragraph(
            "Accurate labeling is critical to receiving, sortation, and inventory accuracy. Labels that cannot be scanned will result in chargebacks and may delay payment.",
            BODY,
        ),
    ]

    elems.append(Paragraph("4.1 Item-Level Barcodes", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"All retail-ready units must bear a valid GS1 UPC-A barcode (12 digits) registered to the vendor's GS1 prefix. EAN-13 is acceptable for international items provided the GS1 registration is current.",
            f"Barcode minimum X-dimension: {cfg['barcode_x_dim_mm']} mm. Quiet zone: {cfg['barcode_quiet_zone_x']}X on each side. Print contrast: {cfg['barcode_print_contrast']}% minimum (ANSI Grade C or better).",
            f"Barcode placement on consumer packaging must allow scanning at the point of sale without manipulation. The preferred location is the lower-right corner of the back panel.",
            f"GTIN-14 case-level barcodes are required on all master cartons in addition to the GS1-128 shipping label described in Section 4.3.",
        ]
    ]

    elems.append(Paragraph("4.2 Variable-Measure Items", H3))
    elems += [
        Paragraph(p, BODY)
        for p in [
            "Variable-weight or variable-priced items must use UPC-A with prefix '2' encoding the random-weight indicator. Item record setup in EDI 832 must flag the item as variable-measure.",
        ]
    ]

    elems.append(Paragraph("4.3 Shipping Labels (GS1-128 / SSCC-18)", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"Each carton in a shipment must bear a GS1-128 shipping label encoding an SSCC-18 serial shipping container code. The SSCC must be unique within a 12-month rolling window.",
            f"Label size: {cfg['label_size_in']}. Label placement: long side of the carton, with the bottom of the label between {cfg['label_min_height_in']} and {cfg['label_max_height_in']} inches from the bottom of the carton.",
            f"Label content (top to bottom): vendor name and address; ship-to facility name, address, and DC number; carrier name; PO number; carton sequence (e.g., 23 of 144); SSCC-18 barcode; GTIN-14 barcode.",
            "Labels must be thermal-printed on direct-thermal or thermal-transfer stock rated for at least 12 months of shelf life. Inkjet or laser-printed labels are not acceptable.",
        ]
    ]

    elems.append(Paragraph("4.4 Carton Markings (Country of Origin and Handling)", H3))
    elems += [
        Paragraph(p, BODY)
        for p in [
            "Country of origin must be marked in English on the master carton in a permanent, legible manner. Use the format 'Made in [Country]' or 'Product of [Country]'.",
            "Handling symbols (this side up, fragile, keep dry, etc.) must conform to ISO 780 and be sized appropriately for the carton.",
            f"Required minimum text height for country-of-origin marking: {cfg['coo_text_height_pt']} pt.",
        ]
    ]

    elems.append(Paragraph("4.5 ASN Linkage and Label Integrity", H3))
    elems += [
        Paragraph(p, BODY)
        for p in [
            "SSCC-18 values printed on shipping labels must match the SSCCs transmitted in the EDI 856 ASN. Mismatches will trigger an ASN-fail event and a chargeback (see Section 5).",
            "Damaged, illegible, or unscannable labels result in a label re-application chargeback at the per-carton rate published in Section 7.",
        ]
    ]

    elems.append(Paragraph("4.6 Pallet Labeling", H3))
    elems += [
        Paragraph(p, BODY)
        for p in [
            "Each pallet must bear a master pallet label on two adjacent sides. The pallet label encodes a single SSCC representing the pallet, with carton-level SSCCs nested under it in the EDI 856.",
            "Mixed-SKU pallets must additionally bear a printed pallet-content list affixed to the outside of the stretch wrap.",
        ]
    ]
    elems += _extra_labeling_content(cfg)
    elems.append(PageBreak())
    return elems


def section_edi(cfg):
    elems = [
        Paragraph("Section 5: Electronic Data Interchange (EDI)", H1),
        Paragraph(
            f"All vendors transacting with {cfg['retailer_name']} must exchange business documents electronically using ANSI X12 EDI standards or the published JSON API equivalents.",
            BODY,
        ),
    ]

    elems.append(Paragraph("5.1 Required EDI Transaction Sets", H2))
    edi_table = Table([
        ["Transaction", "Direction", "Purpose", "SLA"],
        ["EDI 850", "DG -> Vendor", "Purchase Order", "N/A"],
        ["EDI 855", "Vendor -> DG", "PO Acknowledgment", f"Within {cfg['ack_lead_time_hours']} hours"],
        ["EDI 856", "Vendor -> DG", "Advance Ship Notice (ASN)", f"At least {cfg['asn_lead_time_hours']} hours pre-arrival"],
        ["EDI 810", "Vendor -> DG", "Invoice", f"Within {cfg['invoice_lead_time_days']} days of ship"],
        ["EDI 832", "Vendor -> DG", "Item Setup / Catalog", "Per item launch schedule"],
        ["EDI 753/754", "Vendor <-> DG", "Routing Request / Response", "Per shipment"],
        ["EDI 997", "Both", "Functional Acknowledgment", "Within 1 hour of receipt"],
    ], colWidths=[0.9 * inch, 1.1 * inch, 2.2 * inch, 1.6 * inch])
    edi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#003366")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elems.append(edi_table)
    elems.append(Spacer(1, 12))

    elems.append(Paragraph("5.2 PO Acknowledgment (EDI 855)", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"The 855 must be returned within {cfg['ack_lead_time_hours']} hours of EDI 850 receipt. Late or missing 855s are subject to a ${cfg['chargeback_late_855_usd']} chargeback per occurrence.",
            "Vendors may acknowledge with full acceptance, partial acceptance with quantity changes, or rejection. Quantity changes greater than 5% of the original PO require buyer approval.",
            "Price changes are not permitted on the 855. Pricing disputes must be resolved through the contract amendment process before shipment.",
        ]
    ]

    elems.append(Paragraph("5.3 Advance Ship Notice (EDI 856)", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"The 856 ASN must be transmitted no later than {cfg['asn_lead_time_hours']} hours before the shipment arrives at the receiving facility. Early transmission is preferred, but the data must reflect the actual shipment as tendered.",
            "ASN structure must include shipment, order, tare (pallet), pack (carton), and item levels with proper hierarchy. SSCC-18 values must match physical labels.",
            f"ASN accuracy is measured against physical receipt. Vendors must maintain a minimum {cfg['asn_accuracy_pct']}% ASN accuracy. ASN failures are subject to a chargeback equal to {cfg['chargeback_late_asn_pct']}% of invoice value plus per-carton re-handling fees.",
            f"ASN tolerances: quantity variance must be within {cfg['asn_qty_tolerance_pct']}%. Carton count variance must be exact. SSCC mismatches are zero-tolerance events.",
        ]
    ]

    elems.append(Paragraph("5.4 Invoice (EDI 810)", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"Invoices must be transmitted within {cfg['invoice_lead_time_days']} days of physical ship date. Invoices received after this window may be denied or held for compliance review.",
            f"Payment terms: {cfg['payment_terms']}. Discount terms apply only when invoices are EDI-compliant and the shipment fully matches the ASN and PO.",
            "Invoice line totals must reconcile to the PO at the unit price level. Adjustments require a debit/credit memo (EDI 812) and prior buyer approval.",
        ]
    ]

    elems.append(Paragraph("5.5 Item Setup and Catalog (EDI 832)", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"New item setup must be submitted at least {cfg['item_setup_lead_time_days']} days before the requested first ship date. Item records must include all attributes required by the {cfg['retailer_name']} item master.",
            "Mandatory attributes include: GTIN, item description, dimensions, gross / net weight, country of origin, HTS code, hazmat classification, retail pack quantity, master pack quantity, and any handling notes.",
            "Item changes (price, pack, dimensions) require a 60-day notice via EDI 832 update or vendor portal entry.",
        ]
    ]

    elems.append(Paragraph("5.6 EDI Testing and Certification", H3))
    elems += [
        Paragraph(p, BODY)
        for p in [
            "All EDI transaction sets must be tested in the sandbox environment prior to production cutover. Certification is performed by the EDI Operations team and must be completed before the first PO is transmitted.",
            "Communication protocols supported: AS2, sFTP, VAN. Direct API integrations are available for select transaction sets via the vendor portal.",
        ]
    ]
    elems += _extra_edi_content(cfg)
    elems.append(PageBreak())
    return elems


def section_quality(cfg):
    elems = [
        Paragraph("Section 6: Quality, Testing, and Compliance", H1),
        Paragraph(
            f"{cfg['retailer_name']} requires that all merchandise meet applicable safety, regulatory, and quality standards. Vendors are responsible for product testing, certification, and ongoing audit compliance.",
            BODY,
        ),
    ]

    elems.append(Paragraph("6.1 Product Testing Standards", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"All consumer products must be tested in accordance with applicable CPSC, FDA, ASTM, and {cfg['retailer_name']}-specified protocols. Testing must be performed by an ISO/IEC 17025 accredited laboratory.",
            f"Approved test labs are listed on the vendor portal. {cfg['retailer_name']} reserves the right to designate the testing lab for any SKU.",
            f"ISTA packaging testing is {cfg['ista_test_required']} for all e-commerce-eligible items. ISTA-3A or ISTA-6 protocols apply based on shipment type.",
            f"Children's products (CPSIA-covered) require third-party testing certificates. Submission window: at least {cfg['cpsia_lead_time_days']} days before first ship.",
        ]
    ]

    elems.append(Paragraph("6.2 Certificates of Analysis (COA)", H3))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"COAs are {cfg['coa_required_text']} for food, supplement, and personal care items. COAs must be issued for each production lot and submitted via the vendor portal prior to ship.",
            "COA content must include lot number, manufacture date, expiration / best-by date, and test results for the parameters specified in the item master.",
        ]
    ]

    elems.append(Paragraph("6.3 Inspection Program", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"{cfg['retailer_name']} maintains a third-party inspection program covering pre-shipment, in-process, and during-production inspections at the factory level. Vendors must provide reasonable access for inspectors and produce documentation upon request.",
            f"AQL standards: {cfg['aql_general']} for general merchandise; {cfg['aql_critical']} for critical defects. Inspection failure may result in shipment hold, re-inspection at vendor cost, or PO cancellation.",
            "Vendors are responsible for in-line quality controls in the factory and must maintain SPC records for at least 24 months.",
        ]
    ]

    elems.append(Paragraph("6.4 Social Compliance and Audits", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"All factories producing {cfg['retailer_name']} merchandise must maintain a current social compliance audit (SMETA, BSCI, SLCP, or {cfg['retailer_name']}-approved equivalent) with no critical findings.",
            f"Audits must be no older than {cfg['audit_validity_months']} months at the time of order acceptance. Renewals must be completed before the previous audit expires.",
            f"Unannounced audits may be conducted at any time at {cfg['retailer_name']}'s discretion. Refusal of an unannounced audit constitutes a Critical violation.",
        ]
    ]

    elems.append(Paragraph("6.5 Forced Labor and Human Rights", H3))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"{cfg['retailer_name']} prohibits forced labor, human trafficking, child labor, and any form of involuntary servitude in the supply chain. Vendors must comply with the UFLPA, the Modern Slavery Act, and all other applicable laws.",
            "Vendors must maintain documentation tracing raw materials through the supply chain to country of origin. Documentation must be available within 7 calendar days of request.",
        ]
    ]

    elems.append(Paragraph("6.6 Fire and Structural Safety", H3))
    elems += [
        Paragraph(p, BODY)
        for p in [
            "Factories must maintain current fire-safety certification, marked emergency exits, and operational fire-suppression equipment. Bangladesh-based factories must additionally comply with the RMG Sustainability Council requirements.",
            "Structural and electrical safety audits are required for all factories with 50 or more workers and must be repeated at the cadence specified by the local jurisdiction.",
        ]
    ]

    elems.append(Paragraph("6.7 Sustainability and Packaging Materials", H3))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"Packaging materials should target a minimum of {cfg['packaging_recyclable_pct']}% recyclable or compostable content by weight.",
            f"PVC, expanded polystyrene (EPS), and oxo-degradable plastics are {cfg['plastic_restrictions']} for new item launches.",
            "Vendors are encouraged to participate in the annual Sustainable Packaging Survey administered through the vendor portal.",
        ]
    ]
    elems += _extra_quality_content(cfg)
    elems.append(PageBreak())
    return elems


def section_legal(cfg):
    elems = [
        Paragraph("Section 7: Legal, Insurance, and Penalties", H1),
    ]

    elems.append(Paragraph("7.1 Product Liability Insurance (PLI)", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"All vendors must maintain Commercial General Liability (CGL) insurance with a minimum per-occurrence limit of ${cfg['cgl_per_occurrence_usd']:,} and a minimum aggregate limit of ${cfg['cgl_aggregate_usd']:,}.",
            f"Product Liability Insurance must include coverage for products supplied to {cfg['retailer_name']} and must name {cfg['retailer_full_name']} as an additional insured.",
            f"Certificates of Insurance (COI) must be submitted to {cfg['coi_email']} prior to the first PO and renewed annually. Lapsed insurance is a Critical violation that suspends order eligibility.",
            f"Excess / umbrella coverage of ${cfg['umbrella_coverage_usd']:,} is required for high-risk categories (electrical, infant, juvenile, food).",
        ]
    ]

    elems.append(Paragraph("7.2 Indemnification", H3))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"Vendor agrees to indemnify, defend, and hold harmless {cfg['retailer_full_name']} against any claims arising from product defects, personal injury, intellectual property infringement, or breach of vendor obligations under the Master Vendor Agreement.",
        ]
    ]

    elems.append(Paragraph("7.3 Chargeback Schedule", H2))
    elems.append(Paragraph(
        f"The following chargebacks apply to vendors of {cfg['retailer_name']}. Amounts are billed via deduction from open invoices unless otherwise agreed.",
        BODY,
    ))

    cb_table = Table([
        ["Violation", "Penalty", "Notes"],
        ["Late ASN (EDI 856)", f"{cfg['chargeback_late_asn_pct']}% of invoice", "Per shipment"],
        ["Missing or unscannable carton label", f"${cfg['chargeback_missing_label_per_carton']} / carton", "Per carton"],
        ["Late PO Acknowledgment (855)", f"${cfg['chargeback_late_855_usd']}", "Per PO"],
        ["Late delivery", f"${cfg['chargeback_late_delivery_usd']}", "Per appointment"],
        ["Missed appointment / no-show", f"${cfg['chargeback_no_show_usd']}", "Per appointment"],
        ["Unapproved carrier", f"{cfg['chargeback_unapproved_carrier_pct']}% of freight", "Per shipment"],
        ["Failed drop test re-inspection", f"${cfg['chargeback_drop_test_usd']}", "Per inspection"],
        ["Carton dimensional / weight non-compliance", f"${cfg['chargeback_carton_noncompliance_usd']}", "Per carton"],
        ["Pallet non-compliance (height, wrap, pattern)", f"${cfg['chargeback_pallet_usd']}", "Per pallet"],
        ["Item setup error (832 missing data)", f"${cfg['chargeback_item_setup_usd']}", "Per SKU"],
        ["Failed social compliance audit", f"{cfg['chargeback_audit_pct']}% of monthly invoice", "Per occurrence"],
        ["Hazmat documentation missing", f"${cfg['chargeback_hazmat_usd']}", "Per shipment"],
    ], colWidths=[2.6 * inch, 1.5 * inch, 1.5 * inch])
    cb_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#003366")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
    ]))
    elems.append(cb_table)
    elems.append(Spacer(1, 12))

    elems.append(Paragraph("7.4 Dispute Process", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"Chargebacks may be disputed within {cfg['dispute_window_days']} calendar days of the deduction notification. Disputes received after this window are not eligible for review.",
            f"Disputes must be submitted via the {cfg['retailer_name']} Vendor Portal with supporting documentation. The Compliance Operations team will respond with a decision within {cfg['dispute_response_days']} business days.",
            "Approved disputes are credited to the next invoice cycle. Denied disputes may be escalated once to the Vendor Compliance Council via the buyer of record.",
        ]
    ]

    elems.append(Paragraph("7.5 Vendor Scorecard", H2))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"Vendor performance is measured monthly across the following metrics: ASN accuracy, on-time delivery, fill rate, packaging compliance, EDI accuracy, and audit currency. Each metric is scored 0-100 and rolled into a composite score.",
            f"Minimum acceptable composite score: {cfg['scorecard_threshold']}. Vendors falling below threshold for two consecutive months enter a Performance Improvement Plan (PIP). Failure to recover within 90 days may result in delisting.",
        ]
    ]

    elems.append(Paragraph("7.6 Confidentiality", H3))
    elems += [
        Paragraph(p, BODY)
        for p in [
            f"This manual and all related operational data are the confidential property of {cfg['retailer_full_name']}. Vendors may not disclose, copy, or distribute the contents to any unauthorized party.",
        ]
    ]
    elems += _extra_legal_content(cfg)
    elems.append(PageBreak())
    return elems


def appendices(cfg):
    elems = [Paragraph("Appendix A: Glossary", H1)]
    glossary = [
        ("ASN", "Advance Ship Notice - EDI 856 transaction set transmitted by the vendor before a shipment arrives."),
        ("AQL", "Acceptable Quality Level - statistical sampling standard for inspection."),
        ("BOL", "Bill of Lading - document accompanying every shipment."),
        ("CGL", "Commercial General Liability - insurance coverage required of all vendors."),
        ("COA", "Certificate of Analysis - lot-level documentation for regulated categories."),
        ("CPSIA", "Consumer Product Safety Improvement Act - US federal regulation governing children's products."),
        ("DC", "Distribution Center."),
        ("ECT", "Edge Crush Test - corrugated board strength rating."),
        ("EDI", "Electronic Data Interchange."),
        ("GMA", "Grocery Manufacturers Association - standard pallet specification."),
        ("GS1", "Global Standards organization - issuer of GTINs and barcode standards."),
        ("GTIN", "Global Trade Item Number - 14-digit case-level identifier."),
        ("HTS", "Harmonized Tariff Schedule - US customs classification."),
        ("ISPM-15", "International Standards for Phytosanitary Measures No. 15 - heat treatment for wood packaging."),
        ("ISTA", "International Safe Transit Association - packaging test protocols."),
        ("PDQ", "Pretty Darn Quick - display-ready merchandising carton."),
        ("PO", "Purchase Order."),
        ("PIP", "Performance Improvement Plan."),
        ("RTV", "Return to Vendor."),
        ("SDS", "Safety Data Sheet (formerly MSDS)."),
        ("SMETA", "Sedex Members Ethical Trade Audit - social compliance audit framework."),
        ("SPC", "Statistical Process Control."),
        ("SSCC", "Serial Shipping Container Code - 18-digit unique container identifier."),
        ("UFLPA", "Uyghur Forced Labor Prevention Act - US federal law."),
        ("UPC", "Universal Product Code - 12-digit retail barcode."),
    ]
    g_table = Table(
        [["Term", "Definition"]] + [[t, Paragraph(d, BODY)] for t, d in glossary],
        colWidths=[1.2 * inch, 5.0 * inch],
    )
    g_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#003366")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    elems.append(g_table)
    elems.append(PageBreak())

    elems.append(Paragraph("Appendix B: Contact Directory", H1))
    contacts = [
        ("Vendor Relations", cfg["vendor_relations_email"], "General onboarding and account questions"),
        ("EDI Operations", cfg["edi_email"], "Transaction setup, errors, and certifications"),
        ("Routing / Logistics", cfg["routing_email"], "Routing requests, carrier issues"),
        ("Compliance Operations", cfg["compliance_email"], "Chargebacks, disputes, audits"),
        ("Quality / Safety", cfg["quality_email"], "Product testing, COA, recalls"),
        ("Insurance / COI", cfg["coi_email"], "Insurance documentation"),
        ("Sustainability", cfg["sustainability_email"], "Packaging and material substitutions"),
    ]
    c_table = Table(
        [["Team", "Email", "Use For"]] + [[t, e, Paragraph(u, BODY)] for t, e, u in contacts],
        colWidths=[1.6 * inch, 2.3 * inch, 2.3 * inch],
    )
    c_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#003366")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    elems.append(c_table)
    elems.append(PageBreak())

    elems.append(Paragraph("Appendix C: Frequently Asked Questions", H1))
    faqs = [
        ("Q: How do I begin onboarding?", f"A: Contact {cfg['vendor_relations_email']}. The onboarding workflow takes approximately {cfg['onboarding_days']} business days assuming all documentation is complete."),
        ("Q: What if my carton exceeds the maximum dimensions?", f"A: Contact the Logistics team in writing before tendering the shipment. Exceptions require documented approval. Dimensions outside the limits are subject to a per-carton chargeback."),
        ("Q: My ASN was transmitted late because of a carrier delay - is the chargeback waivable?", f"A: Late ASN chargebacks are eligible for dispute within {cfg['dispute_window_days']} days. Provide carrier-attributable documentation (BOL timestamp, dispatch records). The Compliance team reviews and responds within {cfg['dispute_response_days']} business days."),
        ("Q: Can I substitute a different pallet type?", f"A: The {cfg['pallet_type']} pallet is required. Substitutions require advance written approval from the Logistics team."),
        ("Q: How often is the chargeback schedule updated?", "A: The schedule is reviewed annually. Material changes are communicated through the vendor portal at least 60 days before the effective date."),
        ("Q: What happens if my factory fails a social compliance audit?", f"A: A failed audit triggers an immediate hold on new POs. The vendor must remediate findings and pass a re-audit within {cfg['audit_remediation_days']} days. Repeated failures may result in delisting."),
        ("Q: Do I need to stretch-wrap mixed-SKU pallets?", f"A: Yes. All pallets - single-SKU and mixed - require {cfg['stretch_wrap_passes']} full revolutions of stretch wrap."),
        ("Q: What is the difference between an inner pack and a PDQ?", "A: An inner pack is a sub-divider within a master carton (used to protect units). A PDQ is a display-ready carton that can be placed directly on the sales floor."),
        ("Q: Are recyclable shipping labels acceptable?", "A: Yes, provided they meet thermal printing durability requirements and the GS1-128 / SSCC barcode is fully scannable."),
        ("Q: How do I report a recall or safety incident?", f"A: Contact the Quality / Safety team immediately at {cfg['quality_email']} and follow the recall protocol on the vendor portal. Recalls require disclosure within 24 hours of vendor awareness."),
    ]
    for q, a in faqs:
        elems.append(Paragraph(q, H3))
        elems.append(Paragraph(a, BODY))
        elems.append(Spacer(1, 6))

    elems.append(PageBreak())

    elems.append(Paragraph("Appendix D: Document Change Log", H1))
    elems.append(Paragraph(
        f"Detailed change log for version {cfg['version']} compared to the prior published version. All changes are categorized as Material (substantive change to a requirement) or Editorial (clarifications or formatting).",
        BODY,
    ))
    for entry in cfg.get("change_log", []):
        elems.append(Paragraph(entry["heading"], H3))
        elems.append(Paragraph(entry["body"], BODY))
        elems.append(Spacer(1, 4))

    return elems


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build_pdf(out_path, cfg):
    elements = []
    elements += title_page(cfg)
    elements += revision_history(cfg)
    elements += section_introduction(cfg)
    elements += section_carton(cfg)
    elements += section_shipping(cfg)
    elements += section_labeling(cfg)
    elements += section_edi(cfg)
    elements += section_quality(cfg)
    elements += section_legal(cfg)
    elements += section_category_overlays(cfg)
    elements += section_dc_reference(cfg)
    elements += section_scenarios(cfg)
    elements += section_regulatory(cfg)
    elements += appendices(cfg)
    elements += _extended_appendices(cfg)

    doc = SimpleDocTemplate(
        out_path, pagesize=letter,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
    )

    def _footer(canvas, d):
        page_footer(canvas, d, cfg["retailer_name"], cfg["version"], cfg["effective_date"])

    doc.build(elements, onFirstPage=_footer, onLaterPages=_footer)
    return out_path


# ---------------------------------------------------------------------------
# Configurations
# ---------------------------------------------------------------------------

# Common defaults
COMMON = {
    "doc_title": "Vendor Compliance Manual",
    "stretch_wrap_passes": 4,
    "single_sku_pallet_threshold": 250,
    "barcode_x_dim_mm": 0.33,
    "barcode_quiet_zone_x": 10,
    "barcode_print_contrast": 80,
    "label_size_in": "4 x 6",
    "year_code_format": "WW",
    "po_digits": "10",
    "marking_durability_days": 180,
    "fragile_color": "red",
    "marking_ink": "indelible black or dark blue",
    "single_carton_strap": "not permitted",
    "stack_support_lbs": 400,
    "carton_corrugation": "double-wall (BC flute)",
    "ect_value": "44 lbs/in",
    "carton_min_weight_lbs": 2,
    "carton_min_length_in": 9,
    "carton_min_width_in": 6,
    "carton_min_height_in": 2,
    "edi_email": "edi-ops@example.com",
    "routing_email": "routing@example.com",
    "compliance_email": "compliance@example.com",
    "quality_email": "quality@example.com",
    "coi_email": "insurance@example.com",
    "sustainability_email": "sustainability@example.com",
}


# ----- Dollar General DI v2 (modified version of the 11/3/2025 manual) -----

DG_V2 = {
    **COMMON,
    "retailer_name": "Dollar General",
    "retailer_full_name": "Dollar General Corporation",
    "doc_title": "Direct Import Vendor Manual",
    "version": "v2.0 (12/15/2026)",
    "effective_date": "December 15, 2026",
    "vendor_relations_email": "vendor-relations@dollargeneral.com",
    "edi_email": "edi-ops@dollargeneral.com",
    "routing_email": "routing@dollargeneral.com",
    "compliance_email": "compliance-ops@dollargeneral.com",
    "quality_email": "quality-safety@dollargeneral.com",
    "coi_email": "insurance@dollargeneral.com",
    "sustainability_email": "sustainability@dollargeneral.com",
    "onboarding_days": 30,

    # Carton (CHANGED from v1: max weight 50 -> 55 lbs, height 30 -> 32 in)
    "bursting_strength_psi": 200,
    "carton_max_weight_lbs": 55,            # was 50
    "carton_max_length_in": 42,
    "carton_max_width_in": 24,
    "carton_max_height_in": 32,             # was 30
    "drop_height_light_in": 30,
    "drop_height_med_in": 24,
    "drop_height_heavy_in": 18,
    "pdq_max_depth_in": 14,
    "pdq_max_width_in": 24,
    "pdq_max_height_in": 15,
    "pdq_material": "275g coated duplex board with grey back, plus corrugated reinforcement",  # upgraded from 250g
    "banner_width_in": 4,
    "coo_text_height_pt": 12,

    # Shipping (CHANGED: pallet height 84 -> 80, added overhang spec)
    "pallet_type": "GMA 48x40 four-way wood",
    "pallet_max_height_in": 80,             # was 84
    "pallet_max_weight_lbs": 2200,
    "pallet_pattern": "interlocking column-stack",
    "pallet_overhang_in": 0.5,
    "freight_terms_prepaid": "FOB Origin, freight prepaid and added",
    "freight_terms_collect": "FOB Destination, freight collect",
    "routing_lead_time_hours": 48,
    "appt_lead_time_hours": 48,
    "delivery_window_hours_early": 1,
    "delivery_window_hours_late": 1,
    "missed_appt_notice_hours": 24,
    "aerosol_max_per_pallet": 144,

    # Labeling
    "label_min_height_in": 4,
    "label_max_height_in": 8,

    # EDI (CHANGED: ASN lead time 4 -> 2 hours pre-arrival)
    "ack_lead_time_hours": 24,
    "asn_lead_time_hours": 2,               # was 4
    "asn_accuracy_pct": 99,
    "asn_qty_tolerance_pct": 2,
    "invoice_lead_time_days": 3,
    "payment_terms": "Net 60",
    "item_setup_lead_time_days": 75,

    # Quality
    "ista_test_required": "required",
    "cpsia_lead_time_days": 60,
    "coa_required_text": "required",
    "aql_general": "ANSI/ASQ Z1.4 Level II, AQL 2.5",
    "aql_critical": "AQL 0",
    "audit_validity_months": 12,
    "audit_remediation_days": 60,
    "packaging_recyclable_pct": 80,         # was 70 in v1
    "plastic_restrictions": "prohibited",

    # Legal / Penalties (CHANGED: late delivery $250 -> $300)
    "cgl_per_occurrence_usd": 2_000_000,
    "cgl_aggregate_usd": 5_000_000,
    "umbrella_coverage_usd": 10_000_000,
    "chargeback_late_asn_pct": 5,
    "chargeback_missing_label_per_carton": 5,
    "chargeback_late_855_usd": 100,
    "chargeback_late_delivery_usd": 300,    # was 250
    "chargeback_no_show_usd": 500,
    "chargeback_unapproved_carrier_pct": 25,
    "chargeback_drop_test_usd": 1500,
    "chargeback_carton_noncompliance_usd": 25,
    "chargeback_pallet_usd": 75,
    "chargeback_item_setup_usd": 150,
    "chargeback_audit_pct": 10,
    "chargeback_hazmat_usd": 1000,
    "dispute_window_days": 60,              # was 45
    "dispute_response_days": 15,
    "scorecard_threshold": 85,

    "revision_history": [
        {"version": "v1.0", "date": "11/03/2025", "summary": "Initial publication of the Direct Import Vendor Manual."},
        {"version": "v2.0", "date": "12/15/2026", "summary": "Increased maximum carton weight (50 -> 55 lbs) and maximum carton height (30 -> 32 in). Reduced pallet maximum height (84 -> 80 in). Tightened ASN transmission lead time (4 -> 2 hours pre-arrival). Increased late-delivery chargeback ($250 -> $300). Extended dispute window (45 -> 60 days). Upgraded PDQ material requirement (250g -> 275g coated duplex). Increased recyclable packaging target (70% -> 80%)."},
    ],
    "change_log": [
        {"heading": "2.2 Carton Weight Limit", "body": "MATERIAL: Maximum allowable carton weight increased from 50 lbs to 55 lbs. Vendors with constraints from contract manufacturers may take advantage of the relaxed limit; existing tooling can remain in service."},
        {"heading": "2.2 Carton Height Limit", "body": "MATERIAL: Maximum allowable carton height increased from 30 inches to 32 inches to accommodate revised PDQ display heights."},
        {"heading": "2.6 PDQ Material Specification", "body": "MATERIAL: PDQ inner box upgraded from 250g coated duplex board to 275g. Existing artwork stays valid; only stock weight changes."},
        {"heading": "3.1 Pallet Height", "body": "MATERIAL: Maximum total pallet height reduced from 84 inches to 80 inches to align with updated truck-loading software."},
        {"heading": "5.3 ASN Transmission Lead Time", "body": "MATERIAL: ASN must now be transmitted at least 2 hours prior to physical arrival (previously 4 hours). The shorter window reflects operational changes at the DC scheduling system."},
        {"heading": "6.7 Sustainable Packaging Target", "body": "MATERIAL: Recyclable / compostable packaging content target increased from 70% to 80% by weight."},
        {"heading": "7.3 Late Delivery Chargeback", "body": "MATERIAL: Late-delivery chargeback increased from $250 to $300 per appointment."},
        {"heading": "7.4 Dispute Window", "body": "MATERIAL: Vendor dispute window extended from 45 to 60 calendar days to align with industry norms."},
        {"heading": "Editorial Revisions", "body": "EDITORIAL: Section 4 updated for clarity around GS1-128 label placement. Section 6 expanded with clearer language on COA submission timing. Appendix B contact directory refreshed."},
    ],
}


# ----- Mega-Mart v1 -----

MM_V1 = {
    **COMMON,
    "retailer_name": "Mega-Mart",
    "retailer_full_name": "Mega-Mart Stores, Inc.",
    "doc_title": "Vendor Compliance and Logistics Manual",
    "version": "v1.0 (01/15/2026)",
    "effective_date": "January 15, 2026",
    "vendor_relations_email": "vendor-relations@megamart.example",
    "edi_email": "edi@megamart.example",
    "routing_email": "transportation@megamart.example",
    "compliance_email": "compliance@megamart.example",
    "quality_email": "quality@megamart.example",
    "coi_email": "insurance@megamart.example",
    "sustainability_email": "esg@megamart.example",
    "onboarding_days": 45,

    # Carton (different baselines from DG to enable cross-retailer comparison)
    "bursting_strength_psi": 250,           # higher than DG
    "carton_max_weight_lbs": 50,
    "carton_max_length_in": 36,             # smaller than DG
    "carton_max_width_in": 24,
    "carton_max_height_in": 24,             # smaller than DG
    "drop_height_light_in": 36,             # higher drop test than DG
    "drop_height_med_in": 30,
    "drop_height_heavy_in": 24,
    "pdq_max_depth_in": 12,                 # smaller than DG
    "pdq_max_width_in": 20,
    "pdq_max_height_in": 14,
    "pdq_material": "300g SBS board with corrugated insert",   # different from DG
    "banner_width_in": 3,                   # smaller than DG
    "coo_text_height_pt": 10,

    # Shipping (different from DG)
    "pallet_type": "48x40 GMA Grade A wooden pallet",
    "pallet_max_height_in": 72,             # shorter than DG
    "pallet_max_weight_lbs": 2400,          # heavier than DG
    "pallet_pattern": "brick-stack",
    "pallet_overhang_in": 0.25,
    "freight_terms_prepaid": "FOB Origin, prepaid and add",
    "freight_terms_collect": "FOB Destination, freight collect",
    "routing_lead_time_hours": 72,          # longer than DG
    "appt_lead_time_hours": 72,
    "delivery_window_hours_early": 0,       # tighter than DG
    "delivery_window_hours_late": 0,
    "missed_appt_notice_hours": 48,
    "aerosol_max_per_pallet": 96,

    "label_min_height_in": 3,
    "label_max_height_in": 10,

    "ack_lead_time_hours": 48,              # longer than DG
    "asn_lead_time_hours": 4,
    "asn_accuracy_pct": 98,
    "asn_qty_tolerance_pct": 1,
    "invoice_lead_time_days": 5,
    "payment_terms": "Net 75",
    "item_setup_lead_time_days": 90,

    "ista_test_required": "required for e-commerce items only",
    "cpsia_lead_time_days": 90,
    "coa_required_text": "required",
    "aql_general": "ANSI/ASQ Z1.4 Level II, AQL 1.5",   # tighter than DG
    "aql_critical": "AQL 0",
    "audit_validity_months": 18,            # longer than DG
    "audit_remediation_days": 45,
    "packaging_recyclable_pct": 60,
    "plastic_restrictions": "discouraged but permitted",

    "cgl_per_occurrence_usd": 1_000_000,    # lower than DG
    "cgl_aggregate_usd": 3_000_000,
    "umbrella_coverage_usd": 5_000_000,
    "chargeback_late_asn_pct": 3,           # lower than DG
    "chargeback_missing_label_per_carton": 7,
    "chargeback_late_855_usd": 75,
    "chargeback_late_delivery_usd": 200,
    "chargeback_no_show_usd": 750,          # higher than DG
    "chargeback_unapproved_carrier_pct": 20,
    "chargeback_drop_test_usd": 1000,
    "chargeback_carton_noncompliance_usd": 35,
    "chargeback_pallet_usd": 100,
    "chargeback_item_setup_usd": 200,
    "chargeback_audit_pct": 15,
    "chargeback_hazmat_usd": 1500,
    "dispute_window_days": 30,
    "dispute_response_days": 20,
    "scorecard_threshold": 90,

    "revision_history": [
        {"version": "v1.0", "date": "01/15/2026", "summary": "Initial publication of the Mega-Mart Vendor Compliance and Logistics Manual."},
    ],
    "change_log": [
        {"heading": "Initial Release", "body": "This is the first published version of the Mega-Mart Vendor Compliance Manual. No prior version exists."},
    ],
}


# ----- Mega-Mart v2 (changes from v1) -----

MM_V2 = {
    **MM_V1,
    "version": "v2.0 (06/01/2026)",
    "effective_date": "June 1, 2026",

    # Tightened pallet specs and EDI requirements
    "pallet_max_height_in": 68,             # was 72
    "pallet_max_weight_lbs": 2200,          # was 2400
    "stretch_wrap_passes": 5,               # was 4

    "asn_lead_time_hours": 2,               # tightened from 4
    "asn_accuracy_pct": 99,                 # tightened from 98

    "chargeback_late_asn_pct": 4,           # was 3
    "chargeback_missing_label_per_carton": 10,   # was 7
    "chargeback_late_delivery_usd": 250,    # was 200
    "dispute_window_days": 30,
    "scorecard_threshold": 92,              # was 90

    "revision_history": [
        {"version": "v1.0", "date": "01/15/2026", "summary": "Initial publication."},
        {"version": "v2.0", "date": "06/01/2026", "summary": "Tightened pallet maximum height (72 -> 68 in) and weight (2400 -> 2200 lbs). Reduced ASN lead time (4 -> 2 hours). Increased ASN accuracy requirement (98% -> 99%). Increased late-ASN chargeback (3% -> 4%) and missing-label chargeback ($7 -> $10/carton). Late-delivery chargeback raised ($200 -> $250). Stretch wrap minimum revolutions raised (4 -> 5). Scorecard threshold tightened (90 -> 92)."},
    ],
    "change_log": [
        {"heading": "3.1 Pallet Maximum Height", "body": "MATERIAL: Maximum total pallet height reduced from 72 inches to 68 inches due to revised trailer-loading software at all DCs."},
        {"heading": "3.1 Pallet Maximum Weight", "body": "MATERIAL: Maximum gross pallet weight reduced from 2400 lbs to 2200 lbs to align with safe-handling thresholds."},
        {"heading": "3.1 Stretch Wrap Revolutions", "body": "MATERIAL: Minimum stretch-wrap revolutions increased from 4 to 5 to address transit-damage trends in 2025."},
        {"heading": "5.3 ASN Lead Time and Accuracy", "body": "MATERIAL: ASN must be transmitted at least 2 hours prior to arrival (was 4). Required accuracy raised from 98% to 99%."},
        {"heading": "7.3 Chargeback Schedule Updates", "body": "MATERIAL: Late-ASN penalty raised from 3% to 4% of invoice. Missing/unscannable carton-label penalty raised from $7 to $10 per carton. Late-delivery penalty raised from $200 to $250 per appointment."},
        {"heading": "7.5 Scorecard Threshold", "body": "MATERIAL: Composite scorecard threshold tightened from 90 to 92."},
        {"heading": "Editorial Revisions", "body": "EDITORIAL: Section 2 reorganized for clarity. Appendix C FAQs expanded with three new entries. No requirement changes."},
    ],
}


# ----- Mega-Mart v3 (changes from v2) -----

MM_V3 = {
    **MM_V2,
    "version": "v3.0 (12/01/2026)",
    "effective_date": "December 1, 2026",

    # Major sustainability push, revamped chargeback structure
    "packaging_recyclable_pct": 75,         # was 60
    "plastic_restrictions": "prohibited",   # was "discouraged but permitted"
    "cgl_per_occurrence_usd": 2_000_000,    # was 1M
    "cgl_aggregate_usd": 5_000_000,         # was 3M
    "umbrella_coverage_usd": 10_000_000,    # was 5M

    "carton_max_weight_lbs": 45,            # tightened from 50
    "ista_test_required": "required for all items",   # expanded
    "audit_validity_months": 12,            # tightened from 18

    "chargeback_late_asn_pct": 5,           # was 4
    "chargeback_missing_label_per_carton": 12,
    "chargeback_late_delivery_usd": 350,
    "chargeback_no_show_usd": 1000,
    "chargeback_carton_noncompliance_usd": 50,
    "scorecard_threshold": 95,

    "revision_history": [
        {"version": "v1.0", "date": "01/15/2026", "summary": "Initial publication."},
        {"version": "v2.0", "date": "06/01/2026", "summary": "Tightened pallet, ASN, and chargeback requirements."},
        {"version": "v3.0", "date": "12/01/2026", "summary": "Major sustainability update: PVC/EPS/oxo-degradable plastics now prohibited (was discouraged); recyclable content target raised (60% -> 75%). Carton max weight reduced (50 -> 45 lbs). ISTA testing now required for ALL items (was e-comm only). Insurance limits doubled (CGL per-occurrence $1M -> $2M; aggregate $3M -> $5M; umbrella $5M -> $10M). Audit validity shortened (18 -> 12 months). Chargeback schedule revised across late-ASN, missing-label, late-delivery, no-show, and carton non-compliance. Scorecard threshold raised (92 -> 95)."},
    ],
    "change_log": [
        {"heading": "6.7 Sustainable Packaging - Material Restrictions", "body": "MATERIAL: PVC, EPS, and oxo-degradable plastics are now PROHIBITED for new item launches (previously discouraged). Existing items in flight as of the effective date are subject to a 12-month transition period."},
        {"heading": "6.7 Recyclable Content Target", "body": "MATERIAL: Minimum recyclable / compostable content raised from 60% to 75% by weight."},
        {"heading": "2.2 Carton Maximum Weight", "body": "MATERIAL: Maximum allowable carton weight reduced from 50 lbs to 45 lbs to address worker-safety guidance."},
        {"heading": "6.1 ISTA Testing Scope", "body": "MATERIAL: ISTA packaging testing is now required for ALL items - previously only e-commerce-eligible items required ISTA-3A or ISTA-6 testing."},
        {"heading": "6.4 Audit Validity", "body": "MATERIAL: Social compliance audit validity reduced from 18 months to 12 months. Renewals must be initiated 90 days before expiration."},
        {"heading": "7.1 Insurance Limits", "body": "MATERIAL: Required CGL per-occurrence limit doubled from $1M to $2M. Aggregate limit raised from $3M to $5M. Umbrella coverage requirement raised from $5M to $10M."},
        {"heading": "7.3 Chargeback Schedule - Comprehensive Revision", "body": "MATERIAL: Late-ASN penalty raised from 4% to 5% of invoice. Missing-label penalty raised from $10 to $12 per carton. Late-delivery penalty raised from $250 to $350. No-show penalty raised from $750 to $1000. Carton non-compliance penalty raised from $35 to $50 per carton."},
        {"heading": "7.5 Scorecard Threshold", "body": "MATERIAL: Composite scorecard threshold raised from 92 to 95."},
        {"heading": "Editorial Revisions", "body": "EDITORIAL: Section 4 updated to reference the new GS1 Digital Link standard (informational). Appendix A glossary updated with five additional terms."},
    ],
}


def main():
    files = [
        ("DollarGeneral_DI_v2_2026.pdf", DG_V2),
        ("MegaMart_VendorCompliance_v1.pdf", MM_V1),
        ("MegaMart_VendorCompliance_v2.pdf", MM_V2),
        ("MegaMart_VendorCompliance_v3.pdf", MM_V3),
    ]
    for fname, cfg in files:
        out = os.path.join(OUT_DIR, fname)
        build_pdf(out, cfg)
        # Get the page count
        try:
            import fitz
            d = fitz.open(out)
            pages = d.page_count
            size_kb = os.path.getsize(out) / 1024
            print(f"  {fname}: {pages} pages, {size_kb:.0f} KB -> {out}")
        except Exception:
            print(f"  {fname} -> {out}")


if __name__ == "__main__":
    main()
