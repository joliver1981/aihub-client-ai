/**
 * Generate 3 complex Word documents for agent knowledge QA testing.
 *
 * Each document is deliberately:
 *   - 10+ pages long when rendered
 *   - rich with structured content: tables, lists, headings, numbered sections
 *   - seeded with unique, verifiable facts that an agent answering questions
 *     about the document should produce verbatim. Each fact below has been
 *     "fingerprinted" with a low-collision detail (specific name, date, dollar
 *     amount, model number) so the test can pattern-match the answer.
 *
 * The fingerprint facts are mirrored in
 *   tests_v2/journeys/test_real_user_agent_knowledge_qa.py
 * so the test knows exactly what to ask and exactly what to expect.
 */

const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  AlignmentType, LevelFormat, HeadingLevel, BorderStyle, WidthType,
  ShadingType, PageBreak,
} = require("docx");

// ── shared styling / helpers ─────────────────────────────────────────────

const PAGE_W_DXA = 12240;   // US Letter width in DXA
const PAGE_H_DXA = 15840;   // US Letter height in DXA
const MARGIN_DXA = 1440;    // 1 inch
const CONTENT_W = PAGE_W_DXA - 2 * MARGIN_DXA;  // 9360 DXA for a 9-inch-wide content area

const sharedStyles = {
  default: { document: { run: { font: "Calibri", size: 22 } } },  // 11pt
  paragraphStyles: [
    { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
      run: { size: 32, bold: true, color: "1F3864", font: "Calibri" },
      paragraph: { spacing: { before: 360, after: 180 }, outlineLevel: 0 } },
    { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
      run: { size: 26, bold: true, color: "2E74B5", font: "Calibri" },
      paragraph: { spacing: { before: 240, after: 120 }, outlineLevel: 1 } },
    { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
      run: { size: 22, bold: true, color: "2E74B5", font: "Calibri" },
      paragraph: { spacing: { before: 180, after: 80 }, outlineLevel: 2 } },
  ],
};

const sharedNumbering = {
  config: [
    { reference: "bullets",
      levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
    { reference: "numbers",
      levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
  ],
};

const sectionProps = {
  page: {
    size: { width: PAGE_W_DXA, height: PAGE_H_DXA },
    margin: { top: MARGIN_DXA, right: MARGIN_DXA, bottom: MARGIN_DXA, left: MARGIN_DXA },
  },
};

const cellBorder = { style: BorderStyle.SINGLE, size: 6, color: "BFBFBF" };
const cellBorders = { top: cellBorder, bottom: cellBorder, left: cellBorder, right: cellBorder };
const cellMargins = { top: 80, bottom: 80, left: 140, right: 140 };

function h1(text) { return new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(text)] }); }
function h2(text) { return new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(text)] }); }
function h3(text) { return new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun(text)] }); }
function p(text, opts = {}) {
  return new Paragraph({
    spacing: { after: 120 },
    children: [new TextRun({ text, bold: opts.bold || false, italics: opts.italics || false })],
  });
}
function bullet(text) { return new Paragraph({ numbering: { reference: "bullets", level: 0 }, children: [new TextRun(text)] }); }
function num(text) { return new Paragraph({ numbering: { reference: "numbers", level: 0 }, children: [new TextRun(text)] }); }
function pageBreak() { return new Paragraph({ children: [new PageBreak()] }); }

// Build a table with header row (gray background) and body rows.
function table(headers, rows) {
  const cols = headers.length;
  const colW = Math.floor(CONTENT_W / cols);
  const widths = Array(cols).fill(colW);
  // Ensure they sum exactly to CONTENT_W
  widths[cols - 1] = CONTENT_W - colW * (cols - 1);

  const headerRow = new TableRow({
    tableHeader: true,
    children: headers.map((h, i) => new TableCell({
      borders: cellBorders,
      width: { size: widths[i], type: WidthType.DXA },
      shading: { fill: "1F3864", type: ShadingType.CLEAR, color: "auto" },
      margins: cellMargins,
      children: [new Paragraph({ children: [new TextRun({ text: h, bold: true, color: "FFFFFF" })] })],
    })),
  });

  const bodyRows = rows.map(row => new TableRow({
    children: row.map((c, i) => new TableCell({
      borders: cellBorders,
      width: { size: widths[i], type: WidthType.DXA },
      margins: cellMargins,
      children: [new Paragraph({ children: [new TextRun(String(c))] })],
    })),
  }));

  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: widths,
    rows: [headerRow, ...bodyRows],
  });
}

function buildDoc(children) {
  return new Document({
    styles: sharedStyles,
    numbering: sharedNumbering,
    sections: [{ properties: sectionProps, children }],
  });
}

async function writeDoc(doc, outPath) {
  const buf = await Packer.toBuffer(doc);
  fs.writeFileSync(outPath, buf);
  console.log(`Wrote ${outPath} (${buf.length} bytes)`);
}

// ╔════════════════════════════════════════════════════════════════════════╗
// ║ DOCUMENT 1: Helix Innovations Employee Handbook                       ║
// ╚════════════════════════════════════════════════════════════════════════╝

function helixHandbook() {
  const c = [];

  // ── Title page ─────────────────────────────────────────────────────────
  c.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 1800, after: 240 },
    children: [new TextRun({ text: "HELIX INNOVATIONS", bold: true, size: 56, color: "1F3864" })] }));
  c.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 240 },
    children: [new TextRun({ text: "Employee Handbook", bold: true, size: 40 })] }));
  c.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 1200 },
    children: [new TextRun({ text: "Edition 2026.1 — Effective January 4, 2026", italics: true, size: 24 })] }));
  c.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 120 },
    children: [new TextRun({ text: "Helix Innovations, Inc.", size: 22 })] }));
  c.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 120 },
    children: [new TextRun({ text: "1455 Powell Boulevard, Suite 300", size: 22 })] }));
  c.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 120 },
    children: [new TextRun({ text: "Portland, Oregon 97214", size: 22 })] }));
  c.push(pageBreak());

  // ── 1. Welcome ──────────────────────────────────────────────────────────
  c.push(h1("1. Welcome to Helix Innovations"));
  c.push(p("Helix Innovations was founded on March 14, 2018, by Dr. Aimee Tanaka and Marcus Welt in Portland, Oregon. Originally focused on biomedical imaging software, the company has since expanded into precision robotics for surgical assistance, with offices in Portland, Boston, and Singapore. Today, Helix Innovations employs 643 people across three continents and serves customers in 27 countries."));
  c.push(p("This handbook describes the policies, benefits, and expectations that govern the working relationship between Helix Innovations (\"the Company\") and its employees. It supersedes all prior editions and any conflicting written communications dated before January 4, 2026."));
  c.push(p("If any provision of this handbook conflicts with a provision of an individual employment contract, the contract prevails. Questions about the handbook should be directed to the People Operations team at peopleops@helix-innovations.example."));

  c.push(h2("1.1 Mission and Values"));
  c.push(p("Helix Innovations exists to develop technologies that make precision medicine accessible at scale. Our four core values guide every decision:"));
  c.push(bullet("Precision: Our work directly affects patient outcomes. We measure twice, ship once."));
  c.push(bullet("Curiosity: We pursue answers, not opinions. Data wins."));
  c.push(bullet("Accountability: We own outcomes — both wins and misses."));
  c.push(bullet("Care: We act in the interest of patients, colleagues, and partners equally."));

  c.push(h2("1.2 At-Will Employment"));
  c.push(p("Employment with Helix Innovations is at-will. Either the employee or the Company may terminate the employment relationship at any time, with or without cause and with or without notice. Nothing in this handbook creates an express or implied contract of continued employment."));

  c.push(pageBreak());

  // ── 2. Compensation & Benefits ─────────────────────────────────────────
  c.push(h1("2. Compensation and Benefits"));

  c.push(h2("2.1 Pay Schedule"));
  c.push(p("All employees are paid on a bi-weekly schedule, with funds deposited on alternating Fridays. The first pay period of 2026 begins January 6 and ends January 19, with payment on January 23, 2026."));

  c.push(h2("2.2 Vacation Accrual"));
  c.push(p("Full-time employees accrue paid vacation time according to the table below. Accrual begins on the first day of employment. Unused vacation rolls over up to a maximum of 240 hours (30 days). Accrual above 240 hours is forfeited."));
  c.push(p("")); // spacer
  c.push(table(
    ["Tenure", "Days Accrued / Year", "Days Accrued / Pay Period"],
    [
      ["0 to less than 1 year",   "15 days", "0.577"],
      ["1 to less than 3 years",  "18 days", "0.692"],
      ["3 to less than 5 years",  "20 days", "0.769"],
      ["5 to less than 10 years", "22 days", "0.846"],
      ["10 or more years",        "27 days", "1.038"],
    ]
  ));
  c.push(p(""));
  c.push(p("Note: Employees with 5 to less than 10 years of tenure receive exactly 22 vacation days annually. This figure is frequently misquoted as 25 — the correct value is 22."));

  c.push(h2("2.3 Health Insurance Plans"));
  c.push(p("Helix Innovations offers three medical plan options. The Company contributes 80% of the employee-only premium for any plan, and 70% of the family premium."));
  c.push(p(""));
  c.push(table(
    ["Plan", "Monthly Employee Cost", "Annual Deductible (Individual)", "Out-of-Pocket Max"],
    [
      ["Bronze HSA", "$47",  "$3,200", "$7,500"],
      ["Silver PPO", "$118", "$1,500", "$5,000"],
      ["Gold POS",   "$234", "$500",   "$3,500"],
    ]
  ));
  c.push(p(""));
  c.push(p("Open enrollment runs from November 1 through November 21 each year. The Silver PPO is the default plan selected for employees who do not actively make a choice during their enrollment window."));

  c.push(h2("2.4 Retirement (401(k))"));
  c.push(p("Helix Innovations matches 100% of the first 4% of salary contributed to the 401(k) plan, with immediate full vesting. The plan provider is Fidelity, and contributions can be allocated across 22 fund options."));

  c.push(pageBreak());

  // ── 3. Leave Policies ──────────────────────────────────────────────────
  c.push(h1("3. Leave Policies"));

  c.push(h2("3.1 Sick Leave"));
  c.push(p("Each full-time employee receives 80 hours of paid sick leave per calendar year, refreshed every January 1. Sick leave may be used for personal illness, medical appointments, or to care for an immediate family member. Sick leave does not roll over and is not paid out upon termination."));

  c.push(h2("3.2 Parental Leave"));
  c.push(p("Helix Innovations provides 16 weeks of fully-paid parental leave to the primary caregiver and 8 weeks of fully-paid leave to the secondary caregiver, regardless of gender. Adoption and foster placement are covered identically to biological birth."));

  c.push(h2("3.3 Bereavement Leave"));
  c.push(p("Up to 5 days of paid leave is available upon the death of an immediate family member (spouse, partner, parent, child, sibling). Up to 3 days is available for extended family (grandparent, in-law, niece, nephew)."));

  c.push(h2("3.4 Jury Duty"));
  c.push(p("Helix Innovations pays full salary for up to 10 working days of jury service per calendar year. Employees must provide the summons to People Operations no later than 5 business days before their reporting date."));

  c.push(pageBreak());

  // ── 4. Performance Management ──────────────────────────────────────────
  c.push(h1("4. Performance Management"));

  c.push(h2("4.1 Review Cycle"));
  c.push(p("Performance reviews are conducted twice yearly: the first cycle concludes on March 31, and the second concludes on September 30. Each review is composed of three inputs — self-assessment, manager assessment, and a 360 feedback round from at least 3 peers."));

  c.push(h2("4.2 Rating Scale"));
  c.push(p("Helix Innovations uses a five-point rating scale:"));
  c.push(table(
    ["Rating", "Label", "Description"],
    [
      ["5", "Exceptional",       "Significantly exceeds all expectations across every dimension."],
      ["4", "Strong",            "Exceeds expectations in most dimensions."],
      ["3", "Effective",         "Meets all expectations consistently. The expected rating for solid contributors."],
      ["2", "Developing",        "Below expectations in one or more dimensions; performance improvement plan recommended."],
      ["1", "Below Expectations","Significantly below expectations across multiple dimensions."],
    ]
  ));

  c.push(h2("4.3 Calibration"));
  c.push(p("After managers complete reviews, ratings are calibrated in cross-team sessions held the first two weeks of April and the first two weeks of October. No more than 15% of any department may receive a rating of 5; no more than 30% may receive 4 or 5 combined."));

  c.push(pageBreak());

  // ── 5. Code of Conduct ─────────────────────────────────────────────────
  c.push(h1("5. Code of Conduct"));

  c.push(h2("5.1 Professional Behavior"));
  c.push(p("All employees are expected to treat colleagues, customers, and partners with respect. Harassment, discrimination, and retaliation are absolutely prohibited and grounds for immediate termination."));

  c.push(h2("5.2 Conflicts of Interest"));
  c.push(p("Any outside employment, board membership, consulting arrangement, or financial interest in a Helix Innovations competitor, vendor, or customer must be disclosed within 14 days of acceptance. Disclosures are reviewed by the Ethics Committee, which meets monthly."));

  c.push(h2("5.3 Confidentiality"));
  c.push(p("All non-public information about Helix Innovations products, customers, finances, and personnel is confidential. Confidentiality obligations survive termination indefinitely for trade secrets and for two years for general business information."));

  c.push(pageBreak());

  // ── 6. IT Security ─────────────────────────────────────────────────────
  c.push(h1("6. IT Security"));

  c.push(h2("6.1 Required Training"));
  c.push(p("All Engineering employees must complete the OWASP Top 10 training within 30 days of their hire date. Re-certification is required every 18 months."));
  c.push(p("All employees with access to patient health information must complete the HIPAA training within 14 days of being granted access, and annually thereafter."));

  c.push(h2("6.2 Multi-Factor Authentication"));
  c.push(p("MFA is mandatory for all corporate accounts. The approved authenticator app is Duo Mobile. SMS-based codes are NOT permitted as a primary factor and may only be used as a backup if Duo is unavailable."));

  c.push(h2("6.3 Incident Reporting"));
  c.push(p("Suspected security incidents must be reported to security@helix-innovations.example within 1 hour of discovery. The on-call incident commander is reachable 24/7 at +1 (503) 555-0143."));

  c.push(pageBreak());

  // ── 7. Travel & Reimbursement ──────────────────────────────────────────
  c.push(h1("7. Travel and Reimbursement"));

  c.push(h2("7.1 Per Diem Rates"));
  c.push(p("Travel per diem rates cover meals and incidentals. Lodging is reimbursed separately at actual cost up to the city cap. Per diem rates by city:"));
  c.push(p(""));
  c.push(table(
    ["City", "Daily Meal Per Diem", "Lodging Cap"],
    [
      ["Portland, OR (HQ)",  "$75",  "Not applicable (employees commute)"],
      ["Boston, MA",         "$95",  "$295 / night"],
      ["San Francisco, CA",  "$110", "$420 / night"],
      ["New York, NY",       "$125", "$485 / night"],
      ["London, UK",         "$140", "£280 / night"],
      ["Singapore",          "$130", "$340 / night"],
      ["Tokyo, Japan",       "$185", "¥38,000 / night"],
      ["Zurich, Switzerland","$165", "CHF 380 / night"],
    ]
  ));
  c.push(p(""));
  c.push(p("Note: The daily meal per diem in Tokyo is $185 — the highest of any Helix Innovations destination, reflecting current restaurant pricing."));

  c.push(h2("7.2 Airfare"));
  c.push(p("Domestic flights under 4 hours must be booked in economy class. Flights of 4–8 hours may be booked in premium economy. Flights longer than 8 hours may be booked in business class."));

  c.push(h2("7.3 Receipts"));
  c.push(p("Itemized receipts are required for every expense above $25. Expense reports must be submitted within 30 days of the end of travel."));

  c.push(pageBreak());

  // ── 8. Remote Work ─────────────────────────────────────────────────────
  c.push(h1("8. Remote Work"));

  c.push(p("Helix Innovations operates on a hybrid model. Most employees are expected in the office on Tuesdays, Wednesdays, and Thursdays. Mondays and Fridays are optional remote days. Specific schedules may be adjusted with manager approval."));

  c.push(h2("8.1 Equipment"));
  c.push(p("The Company provides a 14-inch MacBook Pro (M4), a 27-inch Dell monitor, a Logitech MX Master 3 mouse, and a Keychron K3 keyboard for home offices. Additional equipment requires a request through the IT portal."));

  c.push(h2("8.2 Stipend"));
  c.push(p("Remote employees receive a one-time setup stipend of $1,200 and a recurring monthly stipend of $85 toward internet and utilities."));

  c.push(pageBreak());

  // ── 9. Termination ─────────────────────────────────────────────────────
  c.push(h1("9. Termination"));

  c.push(h2("9.1 Notice"));
  c.push(p("Employees are encouraged to provide a minimum of 2 weeks' written notice when resigning. Engineering managers and above are expected to provide 4 weeks' notice."));

  c.push(h2("9.2 Final Pay"));
  c.push(p("Final paychecks are issued on the next scheduled pay date and include all accrued but unused vacation paid out at the employee's current base rate."));

  c.push(h2("9.3 Return of Property"));
  c.push(p("All Company property — laptops, monitors, badges, keys, encryption tokens — must be returned within 5 business days of the final day of employment. Unreturned items above $200 in value will be billed to the departing employee."));

  c.push(h2("9.4 Severance"));
  c.push(p("In the event of an involuntary, no-cause termination, Helix Innovations provides severance equal to 2 weeks of base salary per year of service, with a minimum of 4 weeks and a maximum of 26 weeks. Severance is conditional on signing a separation agreement."));

  c.push(pageBreak());

  // ── 10. Acknowledgment ─────────────────────────────────────────────────
  c.push(h1("10. Acknowledgment"));

  c.push(p("I acknowledge that I have read the Helix Innovations Employee Handbook, Edition 2026.1, dated January 4, 2026, and that I understand and agree to its provisions. I understand that this handbook supersedes all prior editions and is not a contract of employment."));

  c.push(p(""));
  c.push(p("Employee Name: _____________________________________________"));
  c.push(p(""));
  c.push(p("Employee Signature: __________________________________________"));
  c.push(p(""));
  c.push(p("Date: _____________________________________________"));

  return buildDoc(c);
}

// ╔════════════════════════════════════════════════════════════════════════╗
// ║ DOCUMENT 2: NovaCore X1 Datacenter Cooling System Specification      ║
// ╚════════════════════════════════════════════════════════════════════════╝

function novaCoreSpec() {
  const c = [];

  c.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 1800, after: 240 },
    children: [new TextRun({ text: "NovaCore X1", bold: true, size: 56, color: "1F3864" })] }));
  c.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 240 },
    children: [new TextRun({ text: "Datacenter Cooling System", bold: true, size: 36 })] }));
  c.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 1200 },
    children: [new TextRun({ text: "Technical Specification — Model NCX1-450-RT24", italics: true, size: 26 })] }));
  c.push(new Paragraph({ alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: "Document Revision 4.2  •  Issued June 15, 2026", size: 22 })] }));
  c.push(pageBreak());

  // ── 1. Overview ─────────────────────────────────────────────────────────
  c.push(h1("1. Overview"));
  c.push(p("The NovaCore X1 (model number NCX1-450-RT24) is a precision-controlled, modular datacenter cooling unit designed for high-density server rooms and edge compute facilities. The system delivers a maximum cooling capacity of 450 kilowatts at a reference ambient temperature of 24°C, with a minimum cooling capacity of 80 kilowatts at low-load operation."));

  c.push(p("The unit is engineered around a closed-loop chilled water heat exchanger and four independent variable-speed compressors. It uses R-1234ze (1,3,3,3-tetrafluoropropene) as the refrigerant, chosen for its global warming potential of less than 1 — substantially below older HFC refrigerants like R-410A."));

  c.push(h2("1.1 Intended Use"));
  c.push(p("The NCX1-450-RT24 is intended for indoor installation in datacenters supporting between 200 and 600 kW of IT load. The unit operates between -10°C and 50°C ambient. It is not certified for outdoor or maritime installation."));

  c.push(pageBreak());

  // ── 2. System Architecture ─────────────────────────────────────────────
  c.push(h1("2. System Architecture"));
  c.push(p("The NovaCore X1 is divided into five functional subsystems:"));
  c.push(num("Refrigeration loop — 4× scroll compressors arranged in two N+1 redundant pairs."));
  c.push(num("Chilled water loop — primary heat exchange to the facility's chilled water plant."));
  c.push(num("Air handling — 6× EC plug fans, total airflow 28,000 m³/h at nominal load."));
  c.push(num("Control and monitoring — embedded ARM Cortex-A53 controller running NovaOS 4.2."));
  c.push(num("Filtration — 4 stage progressive filtration (MERV 8 → MERV 13)."));

  c.push(h2("2.1 Physical Dimensions"));
  c.push(table(
    ["Property", "Value"],
    [
      ["Height",                "2,200 mm (86.6 in)"],
      ["Width",                 "1,800 mm (70.9 in)"],
      ["Depth",                 "1,200 mm (47.2 in)"],
      ["Weight (dry)",          "1,840 kg (4,057 lb)"],
      ["Weight (operating)",    "2,180 kg (4,806 lb)"],
      ["Floor loading",         "1,010 kg/m² (207 lb/ft²)"],
      ["Shipping crate volume", "5.8 m³ (205 ft³)"],
    ]
  ));

  c.push(pageBreak());

  // ── 3. Performance ─────────────────────────────────────────────────────
  c.push(h1("3. Performance Specifications"));

  c.push(h2("3.1 Cooling Capacity vs Ambient"));
  c.push(p("The following table summarizes derated cooling capacity at varying ambient air temperatures, measured per AHRI Standard 1361."));
  c.push(table(
    ["Ambient (°C)", "Capacity (kW)", "EER (kW/kW)", "Sensible Heat Ratio"],
    [
      ["-10", "450", "5.40", "0.98"],
      ["0",   "450", "5.20", "0.97"],
      ["10",  "450", "4.95", "0.96"],
      ["20",  "450", "4.80", "0.95"],
      ["24",  "450", "4.70", "0.94"],
      ["30",  "430", "4.45", "0.93"],
      ["35",  "410", "4.20", "0.93"],
      ["40",  "385", "3.92", "0.92"],
      ["45",  "352", "3.60", "0.91"],
      ["50",  "310", "3.20", "0.90"],
    ]
  ));
  c.push(p("Note: The reference rating point is 24°C ambient, at which the system delivers exactly 450 kW with an EER of 4.70."));

  c.push(h2("3.2 Power Draw"));
  c.push(table(
    ["Load (%)", "Compressor Power (kW)", "Fan Power (kW)", "Total Power (kW)"],
    [
      ["20",  "8.2",  "1.4", "9.6"],
      ["40",  "21.5", "4.2", "25.7"],
      ["60",  "38.0", "8.4", "46.4"],
      ["80",  "59.0", "14.8", "73.8"],
      ["100", "82.0", "20.0", "102.0"],
    ]
  ));

  c.push(pageBreak());

  // ── 4. Acoustic ────────────────────────────────────────────────────────
  c.push(h1("4. Acoustic Performance"));
  c.push(p("Measurements conducted per ISO 3744 in a hemi-anechoic chamber, with the unit mounted on a concrete plinth. Sound power levels are A-weighted (dB(A))."));
  c.push(table(
    ["Load (%)", "Sound Power Lw (dB(A))", "1m SPL (dB(A))", "5m SPL (dB(A))"],
    [
      ["20",  "61", "53", "47"],
      ["40",  "67", "59", "53"],
      ["60",  "72", "64", "58"],
      ["80",  "76", "68", "62"],
      ["100", "79", "71", "65"],
    ]
  ));

  c.push(pageBreak());

  // ── 5. Reliability ─────────────────────────────────────────────────────
  c.push(h1("5. Reliability and MTBF"));
  c.push(p("Field-derived mean time between failures (MTBF) by subsystem, based on a population of 1,247 deployed units over 3.7 years of cumulative operation:"));
  c.push(table(
    ["Subsystem", "MTBF (hours)", "Field Replaceable"],
    [
      ["Scroll compressor",        "78,000",  "Yes (FRU)"],
      ["EC plug fan",              "92,000",  "Yes (FRU)"],
      ["Refrigeration valve block","135,000", "No (factory only)"],
      ["NovaOS controller PCB",    "215,000", "Yes (FRU)"],
      ["Filtration housing",       ">500,000","Yes (FRU)"],
      ["Sensor harness",           "180,000", "Yes (FRU)"],
    ]
  ));

  c.push(pageBreak());

  // ── 6. Installation ────────────────────────────────────────────────────
  c.push(h1("6. Installation Requirements"));

  c.push(h2("6.1 Electrical"));
  c.push(p("The unit requires a 3-phase, 480 V AC supply at 60 Hz (or 400 V AC at 50 Hz for international deployments). Total maximum amperage draw at the rated load is 135 amps. A type C-curve 200-amp circuit breaker is recommended."));

  c.push(h2("6.2 Refrigerant Loop"));
  c.push(p("The system is factory-charged with 18.5 kg of R-1234ze refrigerant. Field topping is performed using port R-2 on the suction line. Refrigerant work must be conducted by an EPA Section 608 Type II or III certified technician."));

  c.push(h2("6.3 Water Supply"));
  c.push(p("The chilled water inlet requires a minimum supply temperature of 7°C and a maximum of 18°C. Required flow rate at peak load is 22 L/s (348 gal/min). The unit ships with a 25 µm strainer that must be cleaned every 4,000 operating hours."));

  c.push(pageBreak());

  // ── 7. Maintenance ─────────────────────────────────────────────────────
  c.push(h1("7. Maintenance Schedule"));

  c.push(table(
    ["Task", "Interval", "Estimated Time"],
    [
      ["Visual inspection of refrigerant lines",     "Monthly",                    "10 minutes"],
      ["Verify chilled water flow rate",             "Monthly",                    "5 minutes"],
      ["MERV 8 prefilter replacement",               "Every 1,500 operating hours","20 minutes"],
      ["MERV 13 filter replacement",                 "Every 2,500 operating hours","30 minutes"],
      ["Chilled water strainer cleaning",            "Every 4,000 operating hours","45 minutes"],
      ["Compressor oil sampling",                    "Annually",                   "60 minutes"],
      ["Refrigerant leak detection (electronic)",    "Annually",                   "30 minutes"],
      ["Full system pressure test and recalibration","Every 5 years",              "4 hours"],
    ]
  ));

  c.push(p("Note: Filter changes occur every 2,500 operating hours for the MERV 13 stage. The MERV 8 prefilter, which extends MERV 13 life, is changed every 1,500 operating hours."));

  c.push(pageBreak());

  // ── 8. Compliance ──────────────────────────────────────────────────────
  c.push(h1("8. Standards and Compliance"));

  c.push(p("The NovaCore X1 is certified to or compliant with the following standards:"));
  c.push(bullet("ASHRAE Standard 90.4 — Energy Standard for Data Centers"));
  c.push(bullet("ASHRAE Standard 62.1 — Ventilation for Acceptable Indoor Air Quality"));
  c.push(bullet("AHRI Standard 1361 — Performance Rating of Computer Room and Data Center Cooling Units"));
  c.push(bullet("EU EcoDesign Regulation 2021/341"));
  c.push(bullet("UL 1995 / CSA C22.2 No. 236 — Heating and Cooling Equipment"));
  c.push(bullet("RoHS Directive 2011/65/EU"));
  c.push(bullet("REACH Regulation (EC) No 1907/2006"));

  c.push(h2("8.1 Refrigerant Compliance"));
  c.push(p("R-1234ze has a global warming potential (GWP) of less than 1 (specifically 0.97 over a 100-year horizon per IPCC AR5). This places the NovaCore X1 firmly under EU F-Gas Regulation (517/2014) thresholds, with no phase-down obligations through 2050."));

  c.push(pageBreak());

  // ── 9. Warranty ────────────────────────────────────────────────────────
  c.push(h1("9. Warranty"));
  c.push(p("NovaCore Industries warrants the NCX1-450-RT24 against defects in material and workmanship for a period of 60 months from the date of factory shipment, or 50,000 operating hours, whichever occurs first."));

  c.push(h2("9.1 Extended Coverage"));
  c.push(p("Optional extended coverage can be purchased that extends warranty to 96 months or 80,000 hours. The extended plan includes annual on-site preventive maintenance visits and prioritized field service response within 4 hours during business days."));

  c.push(pageBreak());

  // ── 10. Support Contacts ───────────────────────────────────────────────
  c.push(h1("10. Support and Contacts"));
  c.push(table(
    ["Region", "Phone", "Email"],
    [
      ["North America (Atlanta)", "+1 (404) 555-0182", "support-na@novacore.example"],
      ["Europe (Amsterdam)",      "+31 20 555 0192",   "support-eu@novacore.example"],
      ["Asia-Pacific (Singapore)","+65 6555 0114",     "support-apac@novacore.example"],
      ["Emergency 24/7 (Global)", "+1 (800) 555-0167", "ops@novacore.example"],
    ]
  ));
  c.push(p(""));
  c.push(p("Document control: this specification (Rev 4.2) supersedes Rev 4.1 issued December 2025. The next scheduled revision is December 2026."));

  return buildDoc(c);
}

// ╔════════════════════════════════════════════════════════════════════════╗
// ║ DOCUMENT 3: Quantum-Resistant Cryptography Migration Plan             ║
// ╚════════════════════════════════════════════════════════════════════════╝

function pqcMigrationPlan() {
  const c = [];

  c.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 1800, after: 240 },
    children: [new TextRun({ text: "Quantum-Resistant Cryptography", bold: true, size: 44, color: "1F3864" })] }));
  c.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 240 },
    children: [new TextRun({ text: "Migration Plan — Q3 2026", bold: true, size: 32 })] }));
  c.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 1200 },
    children: [new TextRun({ text: "Internal Engineering — Confidential", italics: true, size: 24 })] }));
  c.push(new Paragraph({ alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: "Prepared by: Dr. Priya Subramanian, Principal Cryptographer", size: 22 })] }));
  c.push(new Paragraph({ alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: "Approved: May 28, 2026", size: 22 })] }));
  c.push(pageBreak());

  // ── 1. Executive Summary ───────────────────────────────────────────────
  c.push(h1("1. Executive Summary"));
  c.push(p("This document describes the plan to migrate our internal and customer-facing systems from RSA-2048 and ECDSA-P256 to post-quantum cryptographic (PQC) algorithms standardized by NIST in 2024. Migration begins July 15, 2026 and is scheduled to complete by Q2 2027."));
  c.push(p("The total approved budget is $4,200,000 USD, allocated across four phases. The migration affects 47 internal services and 12 customer-facing APIs. The primary lead is Dr. Priya Subramanian; technical execution is shared across the Platform Security team (12 engineers) and the Cloud Infrastructure team (9 engineers)."));

  c.push(h2("1.1 Rationale"));
  c.push(p("Recent improvements in error-corrected quantum computing — particularly the demonstration of a 1,121 logical qubit system in late 2025 — have shortened the public estimates of when a cryptographically-relevant quantum computer might exist. NIST and CISA recommend completion of PQC migration for high-value systems by 2030. Our internal threat model (which assumes harvest-now-decrypt-later attacks on PII) targets completion by mid-2027."));

  c.push(pageBreak());

  // ── 2. Threat Model ────────────────────────────────────────────────────
  c.push(h1("2. Threat Model"));
  c.push(p("We assume a well-funded adversary with the capabilities outlined below."));
  c.push(table(
    ["Capability",                            "Available (today)", "Assumed by 2030"],
    [
      ["Harvest-now-decrypt-later (HNDL)",     "Yes",              "Yes"],
      ["Side-channel analysis on edge devices","Yes",              "Yes"],
      ["Shor's algorithm at 4,000 logical qubits","No (~1,100 today)", "Plausible"],
      ["Grover's algorithm on 128-bit ciphers","No",               "Not material (PQC sym keys widened to 256-bit)"],
      ["Quantum random number prediction",    "No",               "No"],
    ]
  ));
  c.push(p("Our migration must therefore protect data with confidentiality requirements extending past 2030. This includes anything in our customer database covered by GDPR, HIPAA, or our 7-year contractual retention obligations."));

  c.push(pageBreak());

  // ── 3. Algorithm Selection ─────────────────────────────────────────────
  c.push(h1("3. Algorithm Selection"));
  c.push(p("We are adopting the NIST-standardized PQC algorithms finalized in August 2024:"));
  c.push(table(
    ["Purpose",            "Selected Algorithm",                  "Public Key", "Ciphertext / Signature"],
    [
      ["Key encapsulation", "ML-KEM-768 (formerly CRYSTALS-Kyber)","1,184 bytes","1,088 bytes"],
      ["Digital signature", "ML-DSA-65 (formerly CRYSTALS-Dilithium)","1,952 bytes","3,309 bytes"],
      ["Hash-based signature (long-term roots)","SLH-DSA-SHA2-128s (formerly SPHINCS+)","32 bytes","7,856 bytes"],
    ]
  ));
  c.push(p("Note: The selected key encapsulation algorithm is ML-KEM-768, formerly known as CRYSTALS-Kyber. This is the algorithm referenced throughout this document as our \"primary KEM.\""));

  c.push(h2("3.1 Hybrid Period"));
  c.push(p("During the migration window we operate in hybrid mode: a TLS handshake establishes session keys using BOTH classical (X25519) and PQC (ML-KEM-768) key exchanges, with the final session key derived by HKDF over both shared secrets. This guarantees backwards compatibility with non-PQC clients and zero downgrade attack surface."));

  c.push(pageBreak());

  // ── 4. Migration Phases ────────────────────────────────────────────────
  c.push(h1("4. Migration Phases"));

  c.push(table(
    ["Phase", "Name",                              "Start Date",    "End Date",       "Budget"],
    [
      ["1",   "Internal Inventory + Discovery",   "July 15, 2026", "August 31, 2026","$320,000"],
      ["2",   "Internal Services Migration",     "September 1, 2026","December 19, 2026","$1,450,000"],
      ["3",   "Customer-facing API Migration",   "January 5, 2027","April 17, 2027",  "$1,830,000"],
      ["4",   "Decommission Classical Algorithms","April 20, 2027","June 30, 2027",   "$600,000"],
    ]
  ));
  c.push(p("Phase 1 begins on July 15, 2026 — this is the migration kickoff date for the entire program. All scheduling references in this document anchor to that date."));

  c.push(h2("4.1 Phase 1 Detail"));
  c.push(p("Phase 1 performs a complete cryptographic inventory: every TLS certificate, every signing key, every encrypted-at-rest blob. We tag each artifact with one of three migration paths — hybrid, in-place replacement, or deprecation."));

  c.push(h2("4.2 Phase 2 Detail"));
  c.push(p("Phase 2 covers the 47 internal services. Each service team receives a dedicated PQC liaison from the Platform Security team. Services migrate in order of criticality: tier-1 (auth, billing) first; tier-3 (internal dashboards) last."));

  c.push(h2("4.3 Phase 3 Detail"));
  c.push(p("Phase 3 covers the 12 customer-facing APIs. We will offer customers a 90-day overlap during which both the classical and PQC endpoints accept requests, with a deprecation notice emitted on every classical request."));

  c.push(h2("4.4 Phase 4 Detail"));
  c.push(p("Phase 4 removes the classical key exchange and signing paths. Production cipher suites are restricted to PQC-only or hybrid (no classical-only). All RSA-2048 and ECDSA-P256 keys are rotated to ML-KEM-768 / ML-DSA-65 equivalents."));

  c.push(pageBreak());

  // ── 5. Risks ───────────────────────────────────────────────────────────
  c.push(h1("5. Risk Assessment"));

  c.push(table(
    ["ID",  "Risk",                                                           "Likelihood", "Impact", "Mitigation"],
    [
      ["R1", "Customer client libraries don't support PQC",                   "High",       "Medium", "Hybrid mode; vendor outreach starting July 2026"],
      ["R2", "ML-KEM-768 ciphertext size breaks legacy MTU assumptions",      "Medium",    "Medium", "Path MTU testing during Phase 2"],
      ["R3", "Performance regression on edge devices (ARM Cortex-M)",         "Medium",    "High",   "Hardware acceleration shim; benchmark gating"],
      ["R4", "NIST standard amendments between 2026 and migration completion","Low",       "High",   "Algorithm-agility framework abstracting algorithm choice"],
      ["R5", "Side-channel leakage in our reference implementation",          "Medium",    "High",   "External audit by Trail of Bits; constant-time implementation"],
      ["R6", "Key escrow / regulatory pushback in EU markets",                "Low",       "Medium", "Legal review in Q4 2026"],
    ]
  ));

  c.push(pageBreak());

  // ── 6. Team & Responsibilities ─────────────────────────────────────────
  c.push(h1("6. Team and Responsibilities"));

  c.push(table(
    ["Role",                          "Owner",                  "Headcount"],
    [
      ["Principal Cryptographer",     "Dr. Priya Subramanian",  "1"],
      ["Platform Security Team",      "Marcus Chen (Director)", "12"],
      ["Cloud Infrastructure Team",   "Sara Lindqvist (Director)","9"],
      ["Security Operations Liaison", "Tomás Reyes",            "2"],
      ["External Auditor",            "Trail of Bits",          "Contract — 3 reviewers"],
      ["Customer Success Coordinator","Olivia Kapoor",          "1"],
      ["Executive Sponsor",           "CTO (Janice Akabu)",     "1"],
    ]
  ));

  c.push(p("Total internal headcount allocated to the migration: 26 engineers and managers, plus 1 contracted audit team."));

  c.push(pageBreak());

  // ── 7. Budget Detail ───────────────────────────────────────────────────
  c.push(h1("7. Budget Detail"));

  c.push(table(
    ["Category",                "Phase 1", "Phase 2", "Phase 3", "Phase 4", "Total"],
    [
      ["Engineering labor",      "$200,000", "$950,000",  "$1,200,000","$400,000","$2,750,000"],
      ["External audit",         "$80,000",  "$200,000",  "$170,000",  "$50,000", "$500,000"],
      ["Tooling and licenses",   "$25,000",  "$150,000",  "$250,000",  "$50,000", "$475,000"],
      ["Customer outreach",      "$5,000",   "$30,000",   "$120,000",  "$30,000", "$185,000"],
      ["Reserve / contingency",  "$10,000",  "$120,000",  "$90,000",   "$70,000", "$290,000"],
      ["Total",                  "$320,000", "$1,450,000","$1,830,000","$600,000","$4,200,000"],
    ]
  ));

  c.push(p("Total program budget: $4,200,000 USD, fully approved by the Board on May 14, 2026, with quarterly reviews and a hard cap of 8% overrun before re-approval is required."));

  c.push(pageBreak());

  // ── 8. Timeline Milestones ─────────────────────────────────────────────
  c.push(h1("8. Timeline Milestones"));

  c.push(table(
    ["Milestone", "Target Date", "Owner"],
    [
      ["Phase 1 kickoff",                       "July 15, 2026",      "Dr. Subramanian"],
      ["Cryptographic inventory complete",      "August 31, 2026",    "Platform Security"],
      ["First internal service on hybrid",      "September 21, 2026", "Cloud Infra"],
      ["50% of internal services migrated",     "November 7, 2026",   "Platform Security"],
      ["All internal services migrated",        "December 19, 2026",  "Platform Security"],
      ["Customer beta program opens",           "January 12, 2027",   "Customer Success"],
      ["50% of customer APIs on PQC",           "February 28, 2027",  "Cloud Infra"],
      ["All customer APIs on PQC",              "April 17, 2027",     "Cloud Infra"],
      ["Classical algorithms decommissioned",   "June 30, 2027",      "Platform Security"],
      ["Final external audit report",           "August 15, 2027",    "Trail of Bits"],
    ]
  ));

  c.push(pageBreak());

  // ── 9. Affected Systems ────────────────────────────────────────────────
  c.push(h1("9. Affected Systems Summary"));

  c.push(p("This migration touches a total of 59 systems. They break down as:"));
  c.push(num("47 internal services (authentication, billing, messaging, observability, dashboards, etc.)"));
  c.push(num("12 customer-facing APIs (REST APIs at api.example.com, GraphQL at graph.example.com)"));

  c.push(h2("9.1 Internal Service Categories"));
  c.push(table(
    ["Tier",  "Description",                                  "Count"],
    [
      ["Tier 1", "Authentication, identity, billing",         "8"],
      ["Tier 2", "Order management, customer messaging, audit","17"],
      ["Tier 3", "Internal dashboards, reporting, analytics", "14"],
      ["Tier 4", "Development and test environments",         "8"],
    ]
  ));

  c.push(pageBreak());

  // ── 10. Acceptance ─────────────────────────────────────────────────────
  c.push(h1("10. Plan Acceptance"));
  c.push(p("This plan, dated May 28, 2026, supersedes the discussion draft circulated in February 2026. Material changes from the draft include: budget increased from $3.8M to $4.2M, primary lead changed from CTO to Dr. Priya Subramanian, and the customer-facing migration window extended by 30 days."));

  c.push(p("Approvals:"));
  c.push(p("Principal Cryptographer: Dr. Priya Subramanian  •  Approved 2026-05-28"));
  c.push(p("CTO: Janice Akabu  •  Approved 2026-05-28"));
  c.push(p("CISO: Robert Tatum  •  Approved 2026-05-28"));
  c.push(p("Board of Directors  •  Approved 2026-05-14 (budget); ratification 2026-06-04 (full plan)"));

  return buildDoc(c);
}

// ── Run all three ────────────────────────────────────────────────────────

(async () => {
  const outDir = path.dirname(__filename);
  await writeDoc(helixHandbook(),    path.join(outDir, "01_helix_employee_handbook_2026.docx"));
  await writeDoc(novaCoreSpec(),     path.join(outDir, "02_novacore_x1_cooling_spec.docx"));
  await writeDoc(pqcMigrationPlan(), path.join(outDir, "03_pqc_migration_plan_q3_2026.docx"));
  console.log("All 3 documents generated.");
})();
