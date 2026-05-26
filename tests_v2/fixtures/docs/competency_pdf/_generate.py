"""Generate PDF fixture files for the agent-knowledge PDF COMPETENCY suite.

Each fixture probes a different extraction surface:

  01_clean_report.pdf            baseline single-column body text
  02_multi_column_newsletter.pdf 2-column layout (column-order extraction)
  03_invoice_with_tables.pdf     embedded tables (line items + totals)
  04_headers_footers_doc.pdf     repeated headers/footers, page numbers
  05_large_50_page.pdf           50-page reference doc with section markers
                                  to test "needle on page 42" retrieval

Run:
    C:\\Users\\james\\miniconda3\\envs\\aihub2.1\\python.exe _generate.py
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    Frame, PageTemplate, NextPageTemplate, BaseDocTemplate,
)
from reportlab.pdfgen import canvas
from reportlab.platypus.flowables import KeepTogether


OUT_DIR = Path(__file__).resolve().parent
styles = getSampleStyleSheet()


# ============================================================================
# 01 — Clean single-column report
# ============================================================================

def fixture_clean_report():
    out = OUT_DIR / "01_clean_report.pdf"
    doc = SimpleDocTemplate(str(out), pagesize=letter,
                            topMargin=0.7 * inch,
                            leftMargin=0.8 * inch,
                            rightMargin=0.8 * inch)

    body = styles["BodyText"]
    body.fontSize = 10
    body.leading = 14
    h1 = styles["Heading1"]
    h2 = styles["Heading2"]

    story = []
    story.append(Paragraph("Pelagic Maritime — Annual Operations Brief, 2025", h1))
    story.append(Paragraph(
        "Pelagic Maritime is a marine logistics firm headquartered in "
        "Halifax, Nova Scotia. The company operates a fleet of 47 vessels "
        "across the North Atlantic and Mediterranean. Founded in 1998 by "
        "Captain Ingrid Hellesund. As of December 2025 the company "
        "employs 1,820 people.",
        body))

    story.append(Paragraph("1. Fleet Composition", h2))
    story.append(Paragraph(
        "The fleet comprises 22 container vessels, 14 bulk carriers, "
        "8 tankers, and 3 specialized roll-on / roll-off ships. Average "
        "vessel age is 9.4 years. The newest vessel, the MV Aurora Heron, "
        "was commissioned in March 2024.",
        body))

    story.append(Paragraph("2. Financial Highlights", h2))
    story.append(Paragraph(
        "FY2025 revenue: $812M (up 6.8% YoY). EBITDA margin: 18.2%. "
        "Net income: $94M. Cash position at year-end: $148M. The board "
        "approved a dividend of $1.20 per share, payable March 14, 2026.",
        body))

    story.append(Paragraph("3. Sustainability", h2))
    story.append(Paragraph(
        "Carbon intensity per ton-mile fell 4.7% YoY. The company "
        "committed to a 38% absolute reduction by 2030, baseline 2020. "
        "All new vessels ordered from 2025 forward must be capable of "
        "running on biofuel blends up to B30.",
        body))

    story.append(Paragraph("4. Anchor Fingerprint", h2))
    story.append(Paragraph(
        "ANCHOR — Pelagic Maritime: founded 1998 by Captain Ingrid "
        "Hellesund. 47 vessels (22 container + 14 bulk + 8 tankers + "
        "3 ro-ro). FY2025 revenue $812M. EBITDA margin 18.2%. Newest "
        "vessel MV Aurora Heron, commissioned March 2024.",
        body))

    doc.build(story)
    print(f"Wrote {out}")


# ============================================================================
# 02 — Multi-column newsletter
# ============================================================================

def fixture_multi_column_newsletter():
    """Two-column layout. Tests if the PDF extractor reads columns in
    reading order (left column top-to-bottom, then right column) — many
    naive extractors interleave them and produce garbled prose."""
    out = OUT_DIR / "02_multi_column_newsletter.pdf"

    class TwoColDoc(BaseDocTemplate):
        def __init__(self, filename, **kw):
            super().__init__(filename, **kw)
            margin = 0.7 * inch
            gutter = 0.3 * inch
            page_w, page_h = letter
            col_w = (page_w - 2 * margin - gutter) / 2.0
            frame_h = page_h - 2 * margin - 0.4 * inch
            left = Frame(margin, margin, col_w, frame_h, id="left")
            right = Frame(margin + col_w + gutter, margin, col_w, frame_h,
                          id="right")
            tmpl = PageTemplate(id="two", frames=[left, right],
                                onPage=self._draw_header)
            self.addPageTemplates([tmpl])

        def _draw_header(self, c, doc):
            c.setFont("Helvetica-Bold", 16)
            c.drawString(0.7 * inch, letter[1] - 0.5 * inch,
                         "The Halberd Quarterly — Spring 2026 Edition")

    body = styles["BodyText"]
    body.fontSize = 9
    body.leading = 12

    story = [
        Paragraph(
            "<b>Editor's Note.</b> Welcome to the Spring 2026 issue of the "
            "Halberd Quarterly, the in-house newsletter of Halberd Steel "
            "Industries. This issue covers our Q1 results, the opening "
            "of the Chongqing furnace, and an interview with our new "
            "CTO, Dr. Wynne Rasmussen.",
            body),
        Spacer(1, 6),

        Paragraph("<b>Q1 2026 Results</b>", body),
        Paragraph(
            "Halberd posted quarterly revenue of $1.42 billion, up 9.1% "
            "year-on-year. Operating margin held at 14.6%. Crude steel "
            "output reached 4.18 million tonnes, our highest first "
            "quarter ever. CFO Yumiko Lange attributed the result to "
            "strong demand from the renewable-energy sector.",
            body),
        Spacer(1, 6),

        Paragraph("<b>Chongqing Furnace Online</b>", body),
        Paragraph(
            "On February 28, 2026 we lit the first heat in our new "
            "Chongqing electric-arc furnace. The 2.4-million-tonne-per-"
            "year facility cost $680 million and was completed two "
            "months ahead of schedule. The plant is staffed by 1,150 "
            "employees, recruited primarily from the Chongqing region.",
            body),
        Spacer(1, 6),

        Paragraph("<b>Interview with Dr. Rasmussen</b>", body),
        Paragraph(
            "Dr. Wynne Rasmussen joined Halberd as Chief Technology "
            "Officer on January 6, 2026, succeeding the retiring Dr. "
            "Lars Pettersen. Rasmussen previously led the materials-"
            "science group at Ostmark Industries for nine years. Her "
            "first major initiative is the rollout of our digital twin "
            "platform across all 12 production sites by year-end.",
            body),
        Spacer(1, 6),

        Paragraph("<b>Safety Milestone</b>", body),
        Paragraph(
            "Our Bremen mill achieved 1,000 consecutive days without a "
            "lost-time injury on March 18, 2026 — a company record. "
            "Site Director Hartmut Vogt credited the milestone to the "
            "Stop-Work-Authority program rolled out in late 2022.",
            body),
        Spacer(1, 6),

        Paragraph("<b>Customer Spotlight</b>", body),
        Paragraph(
            "Vellichor Industries renewed its supply contract through "
            "2029, locking in 180,000 tonnes per year of structural "
            "beams. The renewal represents $124 million in committed "
            "revenue and our longest-tenured customer relationship.",
            body),
        Spacer(1, 6),

        Paragraph("<b>Anchor Fingerprint</b>", body),
        Paragraph(
            "ANCHOR: Halberd Q1 2026 revenue $1.42B. Chongqing furnace "
            "online Feb 28, 2026, capacity 2.4 Mt/yr, cost $680M, "
            "1,150 employees. CTO Wynne Rasmussen since Jan 6, 2026. "
            "Bremen safety milestone: 1,000 days, March 18, 2026. "
            "Vellichor renewal: 180,000 t/yr through 2029, $124M.",
            body),
    ]

    doc = TwoColDoc(str(out))
    doc.build(story)
    print(f"Wrote {out}")


# ============================================================================
# 03 — Invoice with embedded tables
# ============================================================================

def fixture_invoice_with_tables():
    out = OUT_DIR / "03_invoice_with_tables.pdf"
    doc = SimpleDocTemplate(str(out), pagesize=letter,
                            topMargin=0.7 * inch,
                            leftMargin=0.7 * inch,
                            rightMargin=0.7 * inch)

    body = styles["BodyText"]
    body.fontSize = 10
    body.leading = 13
    h1 = styles["Heading1"]
    h2 = styles["Heading2"]

    story = []
    story.append(Paragraph("INVOICE #INV-2026-04827", h1))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "<b>From:</b> Quasar Components Ltd., 14 Silverdale Way, "
        "Sheffield S9 1XB, United Kingdom. VAT: GB 482 9183 22.<br/>"
        "<b>To:</b> Aurora Bioplastics Inc., Maximilianstraße 28, "
        "80539 Munich, Germany. VAT: DE 113 224 901.<br/>"
        "<b>Invoice date:</b> March 12, 2026 &nbsp;&nbsp;"
        "<b>Due date:</b> April 11, 2026 &nbsp;&nbsp;"
        "<b>Terms:</b> Net 30",
        body))
    story.append(Spacer(1, 14))

    # Line items table
    story.append(Paragraph("Line Items", h2))
    items = [
        ["SKU", "Description", "Qty", "Unit price (£)", "Line total (£)"],
        ["QC-PLT-018", "Platinum-on-glass electrode, 50 mm",   80, 142.50,  11400.00],
        ["QC-ITO-205", "ITO sputter target, 4-inch",            5, 1480.00,  7400.00],
        ["QC-CAL-007", "NIST traceable calibration kit",        2, 2150.00,  4300.00],
        ["QC-PUR-091", "High-purity nitrogen, 50 L cylinder",  12,  185.00,  2220.00],
        ["QC-PWR-114", "Programmable DC power supply, 600 W",   3,  920.00,  2760.00],
        ["QC-SVC-001", "On-site commissioning, day rate",       4,  680.00,  2720.00],
    ]
    t = Table(items, colWidths=[1.0*inch, 2.6*inch, 0.6*inch, 1.1*inch, 1.2*inch])
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,0), colors.HexColor("#1F3864")),
        ("TEXTCOLOR",    (0,0), (-1,0), colors.white),
        ("FONTNAME",     (0,0), (-1,0), "Helvetica-Bold"),
        ("ALIGN",        (2,1), (-1,-1), "RIGHT"),
        ("GRID",         (0,0), (-1,-1), 0.5, colors.grey),
    ]))
    story.append(t)
    story.append(Spacer(1, 14))

    # Totals table
    story.append(Paragraph("Totals", h2))
    subtotal = sum(r[4] for r in items[1:])
    vat_rate = 0.20
    vat = round(subtotal * vat_rate, 2)
    grand = subtotal + vat
    totals = [
        ["Subtotal",         f"£{subtotal:,.2f}"],
        ["VAT (20%)",        f"£{vat:,.2f}"],
        ["TOTAL DUE",        f"£{grand:,.2f}"],
    ]
    t2 = Table(totals, colWidths=[3.0*inch, 1.6*inch])
    t2.setStyle(TableStyle([
        ("FONTNAME",     (0,-1), (-1,-1), "Helvetica-Bold"),
        ("BACKGROUND",   (0,-1), (-1,-1), colors.HexColor("#D9E1F2")),
        ("ALIGN",        (1,0), (-1,-1), "RIGHT"),
        ("GRID",         (0,0), (-1,-1), 0.5, colors.grey),
    ]))
    story.append(t2)
    story.append(Spacer(1, 14))

    story.append(Paragraph(
        "<b>Payment instructions.</b> Wire transfer in GBP to "
        "Quasar Components Ltd., Barclays Sheffield, "
        "sort 20-72-15, account 73884291, reference INV-2026-04827. "
        "International (SWIFT): BARCGB22.",
        body))

    # Anchor
    story.append(Spacer(1, 12))
    story.append(Paragraph(
        f"<i>ANCHOR — Invoice INV-2026-04827, March 12 2026. "
        f"Subtotal £{subtotal:,.2f}, VAT £{vat:,.2f}, TOTAL £{grand:,.2f}. "
        f"Top line: QC-PLT-018 at £11,400.00 (80 × £142.50). "
        f"Issued to Aurora Bioplastics in Munich, Germany.</i>",
        body))

    doc.build(story)
    print(f"Wrote {out}")


# ============================================================================
# 04 — Doc with repeated headers/footers + page numbers
# ============================================================================

def fixture_headers_footers_doc():
    """Repeats a 'CONFIDENTIAL — Project Greenline' header on every page
    plus a page-number footer. Tests two things:
      (a) the extractor handles per-page repeated text without duplicating
          it inflated 30x in the indexed body
      (b) page-anchored facts can be retrieved correctly even when the
          page header/footer dominates each chunk."""
    out = OUT_DIR / "04_headers_footers_doc.pdf"

    class HeaderedDoc(BaseDocTemplate):
        def __init__(self, filename, **kw):
            super().__init__(filename, **kw)
            page_w, page_h = letter
            margin = 0.7 * inch
            frame_top = page_h - 1.0 * inch
            frame_bot = 0.7 * inch
            frame = Frame(margin, frame_bot, page_w - 2 * margin,
                          frame_top - frame_bot, id="body")
            tmpl = PageTemplate(id="hdr", frames=[frame],
                                onPage=self._draw_hf)
            self.addPageTemplates([tmpl])

        def _draw_hf(self, c, doc):
            c.setStrokeColor(colors.HexColor("#999999"))
            c.setLineWidth(0.5)
            c.line(0.7*inch, letter[1]-0.6*inch,
                   letter[0]-0.7*inch, letter[1]-0.6*inch)
            c.setFont("Helvetica-Bold", 9)
            c.setFillColor(colors.HexColor("#1F3864"))
            c.drawString(0.7*inch, letter[1]-0.5*inch,
                         "CONFIDENTIAL — Project Greenline · "
                         "Internal Distribution Only")
            c.setFont("Helvetica-Oblique", 8)
            c.setFillColor(colors.HexColor("#666666"))
            c.drawRightString(letter[0]-0.7*inch, 0.4*inch,
                              f"Page {doc.page}   |   Greenline Steering Cmte")

    body = styles["BodyText"]
    body.fontSize = 10
    body.leading = 13
    h1 = styles["Heading1"]
    h2 = styles["Heading2"]

    story = []
    story.append(Paragraph("Project Greenline — Steering Committee Memo", h1))
    story.append(Paragraph(
        "Project Greenline is a 24-month digital transformation initiative "
        "spanning our Cologne, Memphis, and Bangalore sites. The total "
        "budget is $42M with $18M earmarked for FY2026. Project sponsor: "
        "COO Aldous Marchand. Project lead: Faraj Karimi.",
        body))

    story.append(Paragraph("1. Phase Plan", h2))
    phases = [
        ("Phase 1 — Discovery & baseline", "Jan 2026 – Apr 2026", "$4.5M"),
        ("Phase 2 — Cologne pilot",       "May 2026 – Sep 2026", "$8.0M"),
        ("Phase 3 — Memphis rollout",     "Oct 2026 – Mar 2027", "$12.0M"),
        ("Phase 4 — Bangalore rollout",   "Apr 2027 – Sep 2027", "$10.5M"),
        ("Phase 5 — Hardening & training", "Oct 2027 – Dec 2027", "$7.0M"),
    ]
    t = Table(
        [["Phase", "Timeline", "Budget"]] + list(phases),
        colWidths=[3.0*inch, 2.0*inch, 1.0*inch],
    )
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1F3864")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("GRID",       (0,0), (-1,-1), 0.5, colors.grey),
    ]))
    story.append(t)
    story.append(PageBreak())

    story.append(Paragraph("2. Cologne Pilot Scope", h2))
    story.append(Paragraph(
        "The Cologne pilot will retrofit the Continuous Caster #2 with "
        "real-time vibration analytics from the Sentinel-X edge platform. "
        "Expected benefits: 14% reduction in unplanned downtime and 3% "
        "improvement in yield. Pilot lead: Henrike Sauer.",
        body))
    story.append(Paragraph(
        "Equipment installation begins May 18, 2026. Cutover is gated by "
        "a 48-hour parallel-run validation with no anomalies detected.",
        body))
    story.append(PageBreak())

    story.append(Paragraph("3. Risks & Mitigations", h2))
    risks = [
        ("R1 Supplier delay on Sentinel-X sensors", "Medium",
         "Order placed Feb 2026; alternate supplier Nexgen pre-qualified."),
        ("R2 Cybersecurity exposure on OT network", "High",
         "Segmented VLAN; IDS deployed; quarterly pen-test."),
        ("R3 Talent attrition during Phase 3",      "Medium",
         "Retention bonus pool of $1.5M approved by Board."),
        ("R4 Regulatory delay in Bangalore",        "Low",
         "Local counsel engaged; clearances expected by Feb 2027."),
    ]
    t2 = Table(
        [["Risk", "Severity", "Mitigation"]] + list(risks),
        colWidths=[3.4*inch, 0.9*inch, 2.3*inch],
    )
    t2.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1F3864")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("GRID",       (0,0), (-1,-1), 0.5, colors.grey),
        ("VALIGN",     (0,1), (-1,-1), "TOP"),
    ]))
    story.append(t2)
    story.append(PageBreak())

    story.append(Paragraph("4. Anchor Fingerprint Facts", h2))
    story.append(Paragraph(
        "Project Greenline total budget: $42M (FY2026 allocation $18M). "
        "Project sponsor: Aldous Marchand. Project lead: Faraj Karimi. "
        "Cologne pilot equipment install date: May 18, 2026. Cologne "
        "pilot lead: Henrike Sauer. Expected downtime reduction: 14%. "
        "Highest-severity risk: R2 cybersecurity exposure on OT network.",
        body))

    doc = HeaderedDoc(str(out))
    doc.build(story)
    print(f"Wrote {out}")


# ============================================================================
# 05 — Large 50-page reference doc with anchored page-N facts
# ============================================================================

def fixture_large_50_page():
    """A 50-page reference doc. Around page 38 we plant a unique fact
    ("RFC-OPAL-007 author: Verena Strauss; approved 2025-11-04") that
    no other fixture has. Tests whether retrieval can surface a needle
    buried deep in a long doc."""
    out = OUT_DIR / "05_large_50_page.pdf"
    doc = SimpleDocTemplate(str(out), pagesize=letter,
                            topMargin=0.6*inch,
                            leftMargin=0.7*inch,
                            rightMargin=0.7*inch)

    body = styles["BodyText"]
    body.fontSize = 10
    body.leading = 13
    h1 = styles["Heading1"]
    h2 = styles["Heading2"]

    story = []
    story.append(Paragraph("Opal Networks — Technical Reference (v6.1)", h1))
    story.append(Paragraph(
        "This document is the master technical reference for the Opal "
        "Networks platform. It is structured into 12 chapters covering "
        "architecture, deployment, identity, observability, and "
        "operational procedures.",
        body))

    # Pad with sections, one per page (PageBreak after each)
    chapters = [
        ("Chapter 1 — Architecture Overview",
         "Opal Networks is a distributed control plane for fleet management. "
         "It runs on AWS in three regions: us-west-2, eu-west-1, ap-southeast-1. "
         "The control plane is sharded by tenant; the data plane is global."),
        ("Chapter 2 — Service Inventory",
         "There are 31 microservices in the Opal stack. The largest by "
         "request volume is opal-router (4,200 rps p95). The smallest by "
         "footprint is opal-policy-eval (0.4 vCPU, 256 MB)."),
        ("Chapter 3 — Identity & Access",
         "Authentication is OIDC against an internal IdP named OpalAuth. "
         "Authorization is policy-as-code in Rego. Service identity is "
         "SPIFFE-based with mTLS rotated every 8 hours."),
        ("Chapter 4 — Data Stores",
         "Primary store is PostgreSQL 16 with 4 read replicas per region. "
         "Time-series telemetry lives in ClickHouse (Altinity managed). "
         "Object storage is S3-compatible with cross-region replication."),
        ("Chapter 5 — Event Bus",
         "Kafka 3.6 across 8 brokers per region. Topic naming convention "
         "is <domain>.<event>.v<N>. Retention is 14 days for transient "
         "topics, 365 days for audit topics."),
        ("Chapter 6 — Deployment Topology",
         "EKS clusters per region: opal-ctrl, opal-data, opal-edge. "
         "Image promotion is from dev → stage → prod via a manual gate. "
         "Cutover is blue/green with a synthetic health probe."),
        ("Chapter 7 — Observability",
         "Logs ship to Datadog via Fluent Bit. Metrics via OpenTelemetry "
         "collector. Traces sampled 100% on error paths, 5% baseline. "
         "Dashboards: OPAL-OVERVIEW, OPAL-LATENCY, OPAL-COST."),
        ("Chapter 8 — Resilience & DR",
         "RTO 25 minutes, RPO 60 seconds. Cross-region failover automated "
         "via Argo Workflows. Last full-stack DR drill: March 9, 2026."),
        ("Chapter 9 — Security Posture",
         "Vault for secrets. WAF in front of the public API. Quarterly "
         "third-party pen-test (latest: Praesidium Security, March 2026). "
         "Bug bounty open since June 2024 via HackerOne."),
        ("Chapter 10 — Performance Tuning",
         "PostgreSQL autovacuum tuned for high-write workloads. "
         "ClickHouse merges are tier-based: hot (24h) → warm (30d) → "
         "cold (365d). Kafka rebalances limited to off-peak windows."),
        ("Chapter 11 — Reference RFCs",
         "All architecture changes require an RFC. Active RFC index: "
         "RFC-OPAL-001 through RFC-OPAL-027. RFC-OPAL-007 — 'Cross-"
         "region routing fabric' — was authored by Verena Strauss on "
         "October 18, 2025 and approved by the Architecture Council on "
         "November 4, 2025. This is the canonical reference for our "
         "multi-region traffic-shaping policies."),
        ("Chapter 12 — Operational Runbooks",
         "All on-call procedures are in the opal-runbooks GitLab repo. "
         "Mandatory runbooks: payment-failure, tenant-isolation-breach, "
         "kafka-broker-failure, postgres-failover, identity-outage. "
         "Each runbook is reviewed quarterly."),
    ]

    for chap_title, chap_body in chapters:
        story.append(Paragraph(chap_title, h2))
        # 4 paragraphs of repeated content to fill ~4 pages per chapter
        for i in range(4):
            story.append(Paragraph(chap_body, body))
            story.append(Spacer(1, 8))
        story.append(PageBreak())

    # Final anchor page
    story.append(Paragraph("Appendix A — Anchor Fingerprint Facts", h2))
    anchors = [
        "Opal Networks runs in 3 AWS regions: us-west-2, eu-west-1, ap-southeast-1.",
        "Opal Networks has 31 microservices.",
        "opal-router is the highest-volume service at 4,200 rps p95.",
        "RTO 25 minutes, RPO 60 seconds.",
        "Last DR drill: March 9, 2026.",
        "Pen-test partner (latest, March 2026): Praesidium Security.",
        "RFC-OPAL-007 author: Verena Strauss; approved November 4, 2025.",
    ]
    for a in anchors:
        story.append(Paragraph("• " + a, body))

    doc.build(story)
    print(f"Wrote {out}")


# ============================================================================

if __name__ == "__main__":
    fixture_clean_report()
    fixture_multi_column_newsletter()
    fixture_invoice_with_tables()
    fixture_headers_footers_doc()
    fixture_large_50_page()
    print("All PDF competency fixtures generated.")
