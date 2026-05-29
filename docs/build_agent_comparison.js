// Build the AI Hub Agent vs ChatGPT stakeholder analysis (.docx)
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, LevelFormat, TableOfContents, HeadingLevel,
  BorderStyle, WidthType, ShadingType, VerticalAlign, PageNumber, PageBreak,
} = require("docx");

const CONTENT_W = 9360; // US Letter, 1" margins
const NAVY = "1F3864";
const BLUE = "2E75B6";
const HDR_FILL = "1F3864";
const ROW_ALT = "EEF3FA";
const OK = "1E7B34";
const WARN = "B26A00";
const BAD = "B11F1F";

const border = { style: BorderStyle.SINGLE, size: 1, color: "BFCAD9" };
const borders = { top: border, bottom: border, left: border, right: border };
const cellMargins = { top: 70, bottom: 70, left: 110, right: 110 };

function h1(text) {
  return new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(text)] });
}
function h2(text) {
  return new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(text)] });
}
function p(text, opts = {}) {
  return new Paragraph({
    spacing: { after: 120, line: 276 },
    children: [new TextRun({ text, ...opts })],
  });
}
function runs(children, opts = {}) {
  return new Paragraph({ spacing: { after: 120, line: 276 }, children, ...opts });
}
function bullet(text, level = 0, boldLead = null) {
  const children = [];
  if (boldLead) {
    children.push(new TextRun({ text: boldLead, bold: true }));
    children.push(new TextRun({ text: text }));
  } else {
    children.push(new TextRun(text));
  }
  return new Paragraph({ numbering: { reference: "bullets", level }, spacing: { after: 60 }, children });
}

// Generic table builder.
// cols: array of widths summing to CONTENT_W
// header: array of strings
// rows: array of arrays; each cell is string OR {text, color, bold}
function buildTable(cols, header, rows) {
  const headerRow = new TableRow({
    tableHeader: true,
    children: header.map((txt, i) =>
      new TableCell({
        borders, width: { size: cols[i], type: WidthType.DXA }, margins: cellMargins,
        shading: { fill: HDR_FILL, type: ShadingType.CLEAR },
        verticalAlign: VerticalAlign.CENTER,
        children: [new Paragraph({ children: [new TextRun({ text: txt, bold: true, color: "FFFFFF", size: 19 })] })],
      })
    ),
  });
  const bodyRows = rows.map((cells, r) =>
    new TableRow({
      children: cells.map((cell, i) => {
        const obj = typeof cell === "string" ? { text: cell } : cell;
        return new TableCell({
          borders, width: { size: cols[i], type: WidthType.DXA }, margins: cellMargins,
          shading: { fill: r % 2 ? ROW_ALT : "FFFFFF", type: ShadingType.CLEAR },
          verticalAlign: VerticalAlign.CENTER,
          children: [new Paragraph({ children: [new TextRun({
            text: obj.text, color: obj.color || "222222", bold: !!obj.bold, size: 18,
          })] })],
        });
      }),
    })
  );
  return new Table({ width: { size: CONTENT_W, type: WidthType.DXA }, columnWidths: cols, rows: [headerRow, ...bodyRows] });
}

function rule() {
  return new Paragraph({
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: BLUE, space: 1 } },
    spacing: { after: 160 }, children: [new TextRun("")],
  });
}

const children = [];

// ---- Title block ----
children.push(new Paragraph({ spacing: { before: 2200, after: 0 }, alignment: AlignmentType.CENTER,
  children: [new TextRun({ text: "AI Hub Agents vs. ChatGPT", bold: true, size: 52, color: NAVY })] }));
children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 80 },
  children: [new TextRun({ text: "Capability, Artifact & Security Analysis", size: 30, color: BLUE })] }));
children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 40 },
  children: [new TextRun({ text: "General Agent  •  Command Center (Ops Room)  •  ChatGPT", size: 22, color: "555555" })] }));
children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 1600 },
  children: [new TextRun({ text: "Prepared for platform strategy review", italics: true, size: 22 })] }));
children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 40 },
  children: [new TextRun({ text: "Date: 29 May 2026", size: 20, color: "555555" })] }));
children.push(new Paragraph({ alignment: AlignmentType.CENTER,
  children: [new TextRun({ text: "Status: Analysis only — no code changes made", size: 20, color: "555555" })] }));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ---- TOC ----
children.push(h1("Contents"));
children.push(new TableOfContents("Table of Contents", { hyperlink: true, headingStyleRange: "1-2" }));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ---- Executive Summary ----
children.push(h1("1. Executive Summary"));
children.push(p("Users increasingly expect every agent on the platform to do what ChatGPT does: accept uploaded files, reason over them, and produce polished, downloadable artifacts (Word, Excel, PDF, charts). This document compares our two agents — the General Agent and the Command Center (CC) — against ChatGPT on those consumer-facing capabilities, and assesses what it would take to make CC the primary agent for all users."));
children.push(p("Headline finding: our differentiator is not matching ChatGPT feature-for-feature — it is combining those features with live connections to the customer’s own data sources and their custom-built agents. ChatGPT structurally cannot reach a customer’s warehouse, documents, or internal services; we can. The strategic prize is “ChatGPT-class capability × the customer’s data × user-built agents.”"));
children.push(runs([
  new TextRun({ text: "On capabilities, the Command Center is already the stronger platform.", bold: true }),
  new TextRun(" CC already does streaming, image generation (DALL·E 3), live web search (Tavily), PDF/chart/map artifacts, and cross-session memory — every one of which the General Agent lacks. CC’s orchestrator design (it delegates to all other agents) is also the natural home for the “pull from my data → chart it → build a deck” workflow."),
]));
children.push(runs([
  new TextRun({ text: "The blocker is security, and it is a genuine project — not a checkbox.", bold: true, color: BAD }),
  new TextRun(" CC currently has no enforced per-user security. It is gated to developers only, and that gate is the sole protection today. Its runtime chat endpoint accepts the caller’s identity from the request body without verification, sets no database tenant context, and enforces no per-agent data ACLs. Opening CC to all users without first building that security layer would expose cross-tenant data."),
]));
children.push(p("Recommendation in brief: adopt CC as the long-term primary agent, but treat the core security hardening (~1 week) as a hard gate on any general rollout, with full per-user parity (~2–3 further weeks) as fast-follow. Two capability gaps — a real sandboxed code interpreter and native vision — remain in both agents and are worth a separate decision."));
children.push(rule());

// ---- Scope & Method ----
children.push(h1("2. Scope & Method"));
children.push(p("This analysis is based on a direct read of the codebase (no code was changed). Key sources examined include GeneralAgent.py and core_tools.yaml; the chat file pipeline (chat_file_manager.py, active_chat_context.py, link_sanitizer.py); the Command Center service (command_center_service/graph/nodes.py, cc_graph.py, routes/chat.py, routes/ops.py, routes/upload.py, cc_config.py); the Ops Room frontend; and the project’s own competency reports under tests_v2/artifacts/competency/."));
children.push(p("“ChatGPT” here means the current consumer/Plus experience (GPT-class model with code interpreter, vision, file upload, image generation, and artifact download). The comparison focuses on the features users explicitly ask for, plus the security model required to expose them safely."));
children.push(rule());

// ---- Three-way comparison ----
children.push(h1("3. Three-Way Capability Comparison"));
children.push(p("The table below summarizes where each agent stands. “Yes”, “Partial” and “No” are colour-coded for quick scanning."));

const cmpCols = [3000, 1500, 2200, 2660];
const Y = (t) => ({ text: t, color: OK, bold: true });
const Pp = (t) => ({ text: t, color: WARN, bold: true });
const N = (t) => ({ text: t, color: BAD, bold: true });
children.push(buildTable(cmpCols,
  ["Capability", "ChatGPT", "General Agent", "Command Center"],
  [
    ["Streaming responses", Y("Yes"), N("No (off)"), Y("Yes (SSE blocks)")],
    ["Vision (image input)", Y("Yes"), N("No"), Pp("Partial — caption-to-text")],
    ["Image generation", Y("Yes"), N("No"), Y("Yes (DALL·E 3)")],
    ["Web search", Y("Yes"), Y("Yes (Tavily)"), Y("Yes (Tavily)")],
    ["Create CSV / XLSX / DOCX", Y("Yes"), Y("Yes"), Y("Yes")],
    ["Create PDF from scratch", Y("Yes"), N("No (edit only)"), Y("Yes")],
    ["Charts / maps / KPIs", Y("Yes (img)"), N("No"), Y("Yes (rich blocks)")],
    ["File upload / ingestion", Y("Yes"), Y("Yes (RAG/OCR)"), Y("Yes (50MB)")],
    ["Code interpreter", Y("Sandboxed"), Pp("Unsandboxed exec()"), Pp("Via Builder only")],
    ["Cross-session memory", Y("Yes"), N("No"), Y("Yes (3 layers)")],
    ["Multi-agent orchestration", N("No"), N("No"), Y("Yes")],
    ["Ops dashboard / live ticker", N("No"), N("No"), Y("Yes")],
    ["Connect to user data sources", N("No"), Y("Yes"), Y("Yes")],
    ["Per-user security enforced", Y("Yes"), Y("Yes"), N("No — see §6")],
  ]
));
children.push(new Paragraph({ spacing: { before: 80, after: 0 },
  children: [new TextRun({ text: "Takeaway: CC matches or beats ChatGPT on most capability rows and beats the General Agent on nearly all of them — except the final, decisive row.", italics: true, size: 19 })] }));
children.push(rule());

// ---- General Agent ----
children.push(h1("4. General Agent — Deep Dive"));
children.push(p("A LangChain tool-calling agent running on GPT (Azure / OpenAI / bring-your-own-key), part of the main Flask app. It carries an unusually deep enterprise toolset and a mature, enforced security model."));
children.push(h2("Strengths"));
children.push(bullet("five file-creation tools auto-attached to every agent (create_csv, create_excel, create_word_doc, create_text_file, manipulate_pdf); deterministic and reliable — in some respects more dependable than ChatGPT, which regenerates code each time.", 0, "Reliable artifact creation: "));
children.push(bullet("query_database, the document “universe” search tools, NLQ-agent delegation, and connectors (Stripe, HubSpot, Slack, etc.).", 0, "Deep data reach: "));
children.push(bullet("Windows service control, SQL Agent job orchestration, SFTP reporting, email/SMS/voice alerts — none of which ChatGPT can do.", 0, "Operational reach: "));
children.push(bullet("login/role decorators, per-agent document-type ACLs, and SQL Server row-level security via tenant context on every query.", 0, "Enforced security: "));
children.push(bullet("strips hallucinated download links the model invents — a real, observed failure mode.", 0, "link_sanitizer.py: "));
children.push(h2("Gaps vs. ChatGPT"));
children.push(bullet("No streaming (responses arrive all at once after up to a 590s wait), no vision, no image generation."));
children.push(bullet("No PPTX (a PowerPoint icon exists in the UI but no tool backs it), no from-scratch PDF, no chart/graph image output."));
children.push(bullet("File ingestion is forced through RAG/OCR-to-text — there is no “attach a short file and reason over its full text in context” fast path that users expect for small documents."));
children.push(bullet("run_python_code is a bare in-process exec() with no sandbox, resource limits, or file output — functionally far weaker than ChatGPT’s interpreter and a security liability. It is opt-in (user-selectable), not default."));
children.push(rule());

// ---- Command Center ----
children.push(h1("5. Command Center (CC) — Deep Dive"));
children.push(p("CC is not a larger General Agent — it is a different architecture. It is a standalone LangGraph orchestrator microservice with a streaming (SSE) chat endpoint, whose role is to classify intent and delegate to every other agent on the platform while producing rich visual output itself. The General Agent is a worker; CC is the conductor."));
children.push(h2("What CC already does well (ahead of the General Agent)"));
children.push(bullet("Block-level SSE streaming with live progress events (“thinking”, “scanning”, “processing”)."));
children.push(bullet("Image generation via DALL·E 3 (generate_image), live web search via Tavily (search_web), and interactive maps (generate_map)."));
children.push(bullet("Multi-format artifacts — Excel, CSV, Word, and PDF from scratch — plus native rich blocks for charts, tables, KPIs and maps rendered directly in the Ops Room UI."));
children.push(bullet("File upload with drag-and-drop, 50 MB limit, scoped per (user, tenant)."));
children.push(bullet("Cross-session memory in three layers: user preferences (DB), learned routes, and session insights."));
children.push(bullet("A genuine Ops Room dashboard: live KPI strip, dominant map, activity ticker, and a trace inspector — capabilities ChatGPT has no equivalent for."));
children.push(h2("CC’s remaining capability caveats"));
children.push(bullet("Vision is a pre-processing trick: an uploaded image is captioned to text by a vision model, then the text is fed to the LLM. Good for “read this receipt,” weak for “reason about this chart.” Still ahead of the General Agent’s zero."));
children.push(bullet("No native code interpreter — code runs via the Builder Agent sandbox, same underlying gap as the General Agent."));
children.push(bullet("Streaming is block-level, not token-by-token (a minor UX difference, by design, to support structured blocks)."));
children.push(bullet("Checkpointing is in-memory (per-process) with file-based sessions — fine for a developer tool, but worth pressure-testing for all-user scale and persistence."));
children.push(rule());

// ---- Security ----
children.push(h1("6. The Security Reality (Honest Assessment)"));
children.push(runs([
  new TextRun({ text: "This is the crux of the “make CC the main agent” decision. ", bold: true }),
  new TextRun("CC is gated to developers ("),
  new TextRun({ text: "@developer_required()", italics: true }),
  new TextRun(") precisely because it has no per-user security underneath. That gate is the only thing protecting the platform today. The moment CC opens to all users, the gate comes off — and what’s underneath trusts the caller far more than the General Agent ever does."),
]));
children.push(h2("Gaps that must be closed before an all-user rollout"));
children.push(buildTable([2400, 3480, 3480],
  ["Control", "General Agent (today)", "Command Center (today)"],
  [
    ["Per-request auth", { text: "Enforced on /chat/general", color: OK }, { text: "None on /api/chat runtime endpoint", color: BAD }],
    ["User identity", { text: "Verified session / API key", color: OK }, { text: "Taken from request body, unverified (forgery)", color: BAD }],
    ["Tenant row-level security", { text: "sp_setTenantContext on every query", color: OK }, { text: "Never set — no DB tenant filter", color: BAD }],
    ["Document-type ACLs", { text: "Checked before query", color: OK }, { text: "Not checked before delegation", color: BAD }],
    ["Session ownership", { text: "Implicit per-user", color: OK }, { text: "Check exists but off by default", color: WARN }],
    ["Role gating inside agent", { text: "Decorators throughout", color: OK }, { text: "Entry-gate only; none inside graph", color: BAD }],
    ["Artifact / upload scoping", { text: "Per-conversation", color: OK }, { text: "Per (user, tenant) — recently fixed", color: OK }],
    ["Ops endpoints", { text: "N/A", color: "555555" }, { text: "Auth recently added", color: OK }],
  ]
));
children.push(new Paragraph({ spacing: { before: 100 }, children: [
  new TextRun({ text: "Why this is hard: ", bold: true }),
  new TextRun("we would be retrofitting a zero-trust identity model into an architecture built to trust a body-supplied context and to delegate using a single internal (“god-mode”) API key. The riskiest piece is the delegation layer: every downstream call must carry and enforce verified identity plus tenant, or CC becomes a confused-deputy that launders any user’s request into full-platform access."),
] }));
children.push(h2("Effort estimate"));
children.push(bullet("Core close — real auth on /api/chat, validated/signed user context, enable session-ownership enforcement, tenant context on delegated calls. Load-bearing; getting it wrong ships a data breach.", 0, "~1 week: "));
children.push(bullet("Full per-user parity — document-type ACLs, role gating inside the graph, agent-visibility filtering, audit logging.", 0, "~2–3 further weeks: "));
children.push(bullet("Production-grade hardening — cryptographic payload validation, comprehensive audit trail, cross-tenant isolation verification.", 0, "Beyond that: "));
children.push(rule());

// ---- Recommendation ----
children.push(h1("7. Recommendation & Roadmap"));
children.push(runs([
  new TextRun({ text: "Direction — yes, CC is the right long-term primary agent. ", bold: true, color: NAVY }),
  new TextRun("It already embodies the product thesis and has the features we would otherwise spend months porting into the General Agent. We should not invest heavily in bringing the General Agent to ChatGPT parity when CC is most of the way there."),
]));
children.push(runs([
  new TextRun({ text: "Sequencing — security first, rollout second (non-negotiable). ", bold: true, color: BAD }),
  new TextRun("Do not flip CC to all-users before the core security close lands. Treat the ~1-week core close as a hard gate, with the 2–3 week parity work as fast-follow."),
]));
children.push(h2("Suggested phased plan"));
children.push(bullet("CC security core close — auth, verified identity, tenant context, session-ownership enforcement. Gate for any general availability.", 0, "Phase 1 (block rollout): "));
children.push(bullet("Document-type ACLs, in-graph role gating, agent-visibility filtering, audit logging; scale/persistence test of checkpointing.", 0, "Phase 2 (parity): "));
children.push(bullet("Limited rollout of CC as primary to a pilot tenant; monitor via the Ops Room trace inspector.", 0, "Phase 3 (pilot): "));
children.push(bullet("Sandboxed code interpreter (host it in the Executor service) and native vision — the two gaps shared by both agents and the biggest remaining distance to ChatGPT.", 0, "Phase 4 (capability): "));
children.push(p("These two capability items — a real sandboxed interpreter and native multimodal vision — are worth a separate go/no-go decision regardless of which agent becomes primary, because they unlock the “data-analysis magic” neither agent has today."));
children.push(rule());

// ---- Known bugs ----
children.push(h1("8. Known Issues to Fix Regardless of Direction"));
children.push(p("Surfaced by the platform’s own competency reports and reproduction scripts:"));
children.push(bullet("Large CSV generation (1,000+ rows) produced no artifact at all (timeout / no-stream) — exactly where users push hardest."));
children.push(bullet("Excel metadata extraction mis-detects multi-row titles/subtitles, picking the wrong header row (reproduce_excel_failure.py)."));
children.push(bullet("Agents over-commit to oversized requests instead of capping or warning (competency test T03)."));
children.push(bullet("CC vision, image generation, and web search all depend on environment keys/flags — confirm configuration before relying on them in production."));
children.push(rule());

// ---- Appendix ----
children.push(h1("Appendix A. Key Files Referenced"));
children.push(buildTable([3600, 5760],
  ["Area", "Files"],
  [
    ["General Agent", "GeneralAgent.py; core_tools.yaml"],
    ["Chat file pipeline", "chat_file_manager.py; active_chat_context.py; link_sanitizer.py"],
    ["CC core", "command_center_service/graph/nodes.py; graph/cc_graph.py; cc_config.py"],
    ["CC routes", "routes/chat.py; routes/ops.py; routes/upload.py; routes/artifacts.py"],
    ["CC frontend", "command_center_service/static/ops_room.html; static/js/ops-room.js"],
    ["Evidence", "tests_v2/artifacts/competency/*; test_human/_scripts/reproduce_excel_failure.py"],
  ]
));

// ---- Document assembly ----
const doc = new Document({
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 30, bold: true, color: NAVY, font: "Arial" },
        paragraph: { spacing: { before: 280, after: 160 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, color: BLUE, font: "Arial" },
        paragraph: { spacing: { before: 200, after: 100 }, outlineLevel: 1 } },
    ],
  },
  numbering: {
    config: [
      { reference: "bullets", levels: [
        { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 540, hanging: 280 } } } },
        { level: 1, format: LevelFormat.BULLET, text: "◦", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 1080, hanging: 280 } } } },
      ] },
    ],
  },
  sections: [{
    properties: { page: {
      size: { width: 12240, height: 15840 },
      margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
    } },
    headers: { default: new Header({ children: [new Paragraph({
      alignment: AlignmentType.RIGHT,
      border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "BFCAD9", space: 4 } },
      children: [new TextRun({ text: "AI Hub Agents vs. ChatGPT — Strategy Analysis", size: 16, color: "888888" })],
    })] }) },
    footers: { default: new Footer({ children: [new Paragraph({
      alignment: AlignmentType.CENTER,
      children: [
        new TextRun({ text: "Confidential — internal  •  Page ", size: 16, color: "888888" }),
        new TextRun({ children: [PageNumber.CURRENT], size: 16, color: "888888" }),
        new TextRun({ text: " of ", size: 16, color: "888888" }),
        new TextRun({ children: [PageNumber.TOTAL_PAGES], size: 16, color: "888888" }),
      ],
    })] }) },
    children,
  }],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync("C:/src/aihub-client-ai-dev/docs/AI_Hub_Agents_vs_ChatGPT_Analysis.docx", buf);
  console.log("WROTE docs/AI_Hub_Agents_vs_ChatGPT_Analysis.docx (" + buf.length + " bytes)");
});
