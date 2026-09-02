"""
The Agent — exports and PDF manipulation (pass 3 of the CC-gap plan,
2026-09-02): the "reach" tools. Both run through the SAME code-interpreter
lane as run_python (code_tools.execute_python): sandboxed interpreter, policy
files, the aihub_runtime SDK token, and produced files delivered as the chat's
download links. No new dependency — pandas/openpyxl/pypdf/reportlab already
live in the interpreter environment.

export_data — "give me this as Excel / CSV / JSON / PDF".
  Two sources, exactly one per call:
    rows_json          small data already in the conversation (a tool result,
                       the user's own numbers) — the model transcribes it.
    connection + sql   data from a database: the SQL runs server-side through
                       aihub_runtime.query, uncapped (well, AGENT_EXPORT_MAX_ROWS),
                       and the rows NEVER pass through the model. Preferred for
                       anything that comes from a connection.
  The SQL is gated SELECT-only client-side (fail-closed) before it runs.

manipulate_pdf — info / extract pages / split / rotate / merge with pypdf.
  Inputs resolve through document_tools._resolve_read_path, i.e. the same
  owner-scoped, role-scoped rule read_file applies (server paths for
  Developer+, delivered links and chat attachments for everyone).
"""

import json
import os
import re
from typing import Any, Optional

from claude_agent_sdk import tool

from agent_config import logger
from platform_tools import CURRENT_USER, _text
from code_tools import execute_python

FORMATS = ("xlsx", "csv", "json", "pdf")
PDF_OPS = ("info", "extract", "split", "rotate", "merge")
MAX_ROWS = int(os.getenv("AGENT_EXPORT_MAX_ROWS", "100000"))
PDF_TABLE_MAX_ROWS = 2000
SPLIT_ZIP_THRESHOLD = 20
_TIMEOUT = int(os.getenv("AGENT_EXPORT_TIMEOUT", "300"))

_SQL_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|exec|execute|merge|truncate|grant|"
    r"revoke|into|sp_\w+|xp_\w+)\b", re.I)


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------

def safe_name(name: str, ext: str, default: str = "export") -> str:
    """A filename The Agent can hand to the sandbox: no path parts, no odd
    characters, the right extension, bounded length."""
    base = re.sub(r"[\\/]+", "_", str(name or "").strip())   # slashes are characters, not paths
    base = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", base)          # drop any extension
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_", base)
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[._]{2,}", "_", base).strip(" ._-") or default
    return f"{base[:80]}.{ext}"


def sql_is_select_only(sql: str) -> tuple:
    """(ok, why). A fail-closed FORMAT check, not a parser: one statement that
    starts with SELECT or WITH, no statement separators, no write/DDL/exec
    keywords anywhere (the platform's automations runtime executes what it is
    given, so this is the only gate on an export)."""
    s = re.sub(r"/\*.*?\*/", " ", str(sql or ""), flags=re.S)
    s = re.sub(r"--[^\n]*", " ", s)
    s = s.strip().rstrip(";").strip()
    if not s:
        return False, "the SQL is empty"
    if ";" in s:
        return False, "only ONE statement is allowed (no ';' separators)"
    if not re.match(r"^(select|with)\b", s, re.I):
        return False, "the export SQL must be a single SELECT (or WITH … SELECT)"
    m = _SQL_FORBIDDEN.search(s)
    if m:
        return False, f"'{m.group(0)}' is not allowed in an export query (read-only)"
    return True, ""


def parse_rows_json(text: str) -> tuple:
    """(columns, rows, err) from rows_json: a list of objects, or
    {"columns": [...], "rows": [[...], ...]}. rows come back as lists in
    column order."""
    try:
        data = json.loads(str(text or ""))
    except Exception as e:
        return None, None, f"rows_json is not valid JSON ({e})"
    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        cols = [str(c) for c in (data.get("columns") or [])]
        rows = data["rows"]
        if not cols:
            return None, None, "rows_json needs \"columns\" when rows are lists"
        out = []
        for r in rows:
            if isinstance(r, dict):
                out.append([r.get(c) for c in cols])
            elif isinstance(r, (list, tuple)):
                out.append(list(r)[:len(cols)] + [None] * max(0, len(cols) - len(r)))
            else:
                return None, None, "each row must be a list or an object"
        return cols, out, None
    if isinstance(data, list):
        if not data:
            return None, None, "rows_json is an empty list — nothing to export"
        if all(isinstance(r, dict) for r in data):
            cols: list = []
            for r in data:
                for k in r.keys():
                    if k not in cols:
                        cols.append(str(k))
            return cols, [[r.get(c) for c in cols] for r in data], None
        return None, None, ("rows_json must be a list of objects, or "
                            "{\"columns\": [...], \"rows\": [[...]]}")
    return None, None, "rows_json must be a JSON list or object"


def parse_pages(spec: str) -> tuple:
    """'1-3,7,10-12' -> ([1,2,3,7,10,11,12], err). 1-based, order kept, deduped.
    Empty spec -> ([], None) meaning 'all pages'."""
    s = str(spec or "").strip()
    if not s:
        return [], None
    out: list = []
    for part in s.split(","):
        p = part.strip()
        if not p:
            continue
        m = re.match(r"^(\d+)\s*-\s*(\d+)$", p)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a < 1 or b < a:
                return None, f"bad page range '{p}'"
            if b - a > 5000:
                return None, f"page range '{p}' is too large"
            rng = range(a, b + 1)
        elif re.match(r"^\d+$", p):
            if int(p) < 1:
                return None, "pages are numbered from 1"
            rng = [int(p)]
        else:
            return None, f"can't read page spec '{p}' (use e.g. 1-3,7)"
        for n in rng:
            if n not in out:
                out.append(n)
    return out, None


def compose_export_code(fmt: str, filename: str, *, rows_json_text: Optional[str] = None,
                        columns: Optional[list] = None, connection: Optional[str] = None,
                        sql: Optional[str] = None, sheet_name: str = "Sheet1",
                        title: str = "") -> str:
    """The sandbox script for one export. Data literals are embedded via
    json.dumps twice (a Python string literal holding JSON) so nothing the
    model typed can break out of the script."""
    lines = ["import json, sys", "import pandas as pd", ""]
    if rows_json_text is not None:
        lines += [f"_cols = json.loads({json.dumps(json.dumps(columns or []))})",
                  f"_rows = json.loads({json.dumps(rows_json_text)})",
                  "df = pd.DataFrame(_rows, columns=_cols)",
                  "truncated = False"]
    else:
        lines += ["import aihub_runtime as aihub",
                  f"_rows = aihub.query({json.dumps(str(connection))}, {json.dumps(str(sql))})",
                  "_rows = list(_rows or [])",
                  f"truncated = len(_rows) > {MAX_ROWS}",
                  f"_rows = _rows[:{MAX_ROWS}]",
                  "df = pd.DataFrame(_rows)"]
    lines += ["n = len(df)",
              "print(f'ROWS={n}')",
              "print(f'COLS={len(df.columns)}')",
              "print(f'TRUNCATED={int(truncated)}')",
              "if n == 0:",
              "    sys.exit(0)",
              f"fn = {json.dumps(filename)}"]
    sheet = json.dumps(str(sheet_name or "Sheet1")[:31])
    if fmt == "xlsx":
        lines += [f"with pd.ExcelWriter(fn, engine='openpyxl') as xw:",
                  f"    df.to_excel(xw, index=False, sheet_name={sheet})",
                  f"    ws = xw.sheets[{sheet}]",
                  "    ws.freeze_panes = 'A2'",
                  "    for i, col in enumerate(df.columns, start=1):",
                  "        width = max([len(str(col))] + [len(str(v)) for v in df[col].head(500)])",
                  "        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(60, max(8, width + 2))"]
    elif fmt == "csv":
        lines += ["df.to_csv(fn, index=False)"]
    elif fmt == "json":
        lines += ["df.to_json(fn, orient='records', indent=1, date_format='iso', force_ascii=False)"]
    elif fmt == "pdf":
        lines += ["from reportlab.lib import colors",
                  "from reportlab.lib.pagesizes import letter, landscape",
                  "from reportlab.lib.styles import getSampleStyleSheet",
                  "from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer",
                  f"dfp = df.head({PDF_TABLE_MAX_ROWS})",
                  "data = [[str(c) for c in dfp.columns]] + [[('' if v is None else str(v))[:60] for v in row] for row in dfp.itertuples(index=False)]",
                  "size = landscape(letter) if len(dfp.columns) > 6 else letter",
                  f"doc = SimpleDocTemplate(fn, pagesize=size, title={json.dumps(str(title or ''))})",
                  "styles = getSampleStyleSheet()",
                  "elems = []",
                  f"if {json.dumps(str(title or ''))}:",
                  f"    elems += [Paragraph({json.dumps(str(title or ''))}, styles['Title']), Spacer(1, 8)]",
                  "t = Table(data, repeatRows=1)",
                  "t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#DDE6F0')),",
                  "                       ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#9AA4B2')),",
                  "                       ('FONTSIZE', (0, 0), (-1, -1), 7.5),",
                  "                       ('VALIGN', (0, 0), (-1, -1), 'TOP')]))",
                  "elems.append(t)",
                  "doc.build(elems)",
                  f"if n > {PDF_TABLE_MAX_ROWS}:",
                  f"    print('PDF_ROWS_SHOWN={PDF_TABLE_MAX_ROWS}')"]
    lines += ["print('FILE=' + fn)"]
    return "\n".join(lines) + "\n"


def compose_pdf_code(op: str, inputs: list, pages: list, degrees: int, out_name: str,
                     stem: str) -> str:
    """The sandbox script for one PDF operation over already-staged inputs."""
    lines = ["import os, sys, zipfile",
             "from pypdf import PdfReader, PdfWriter",
             f"inputs = {json.dumps(inputs)}",
             f"sel = {json.dumps(pages)}",
             f"deg = {int(degrees)}",
             f"out_name = {json.dumps(out_name)}",
             f"stem = {json.dumps(stem)}",
             "r = PdfReader(inputs[0])",
             "n = len(r.pages)",
             "print(f'PAGES={n}')",
             "bad = [p for p in sel if p < 1 or p > n]",
             "if bad:",
             "    print(f'ERROR=page(s) {bad} do not exist (the document has {n} pages)')",
             "    sys.exit(2)",
             "idx = [p - 1 for p in sel] if sel else list(range(n))"]
    if op == "info":
        lines += ["m = r.metadata or {}",
                  "for k in ('/Title', '/Author', '/Subject', '/Creator', '/Producer', '/CreationDate', '/ModDate'):",
                  "    v = m.get(k)",
                  "    if v:",
                  "        print(f'META {k[1:]}={str(v)[:120]}')",
                  "print('ENCRYPTED=' + str(bool(r.is_encrypted)))",
                  "sz = [r.pages[0].mediabox.width, r.pages[0].mediabox.height]",
                  "print(f'PAGE_SIZE={float(sz[0]):.0f}x{float(sz[1]):.0f}pt')"]
    elif op == "extract":
        lines += ["w = PdfWriter()",
                  "for i in idx:",
                  "    w.add_page(r.pages[i])",
                  "with open(out_name, 'wb') as fh:",
                  "    w.write(fh)",
                  "print(f'OUT={out_name}')",
                  "print(f'PAGES_OUT={len(idx)}')"]
    elif op == "rotate":
        lines += ["w = PdfWriter()",
                  "chosen = set(idx)",
                  "for i, page in enumerate(r.pages):",
                  "    if i in chosen:",
                  "        page.rotate(deg)",
                  "    w.add_page(page)",
                  "with open(out_name, 'wb') as fh:",
                  "    w.write(fh)",
                  "print(f'OUT={out_name}')",
                  "print(f'ROTATED={len(chosen)}')",
                  "print(f'DEGREES={deg}')"]
    elif op == "split":
        lines += ["names = []",
                  "for i in idx:",
                  "    w = PdfWriter()",
                  "    w.add_page(r.pages[i])",
                  "    nm = f'{stem}_page_{i + 1}.pdf'",
                  "    with open(nm, 'wb') as fh:",
                  "        w.write(fh)",
                  "    names.append(nm)",
                  f"if len(names) > {SPLIT_ZIP_THRESHOLD}:",
                  "    zn = f'{stem}_pages.zip'",
                  "    with zipfile.ZipFile(zn, 'w', zipfile.ZIP_DEFLATED) as z:",
                  "        for nm in names:",
                  "            z.write(nm)",
                  "    for nm in names:",
                  "        os.remove(nm)",
                  "    print(f'OUT={zn}')",
                  "    print(f'PAGES_OUT={len(names)}')",
                  "    print('ZIPPED=1')",
                  "else:",
                  "    for nm in names:",
                  "        print(f'OUT={nm}')",
                  "    print(f'PAGES_OUT={len(names)}')"]
    elif op == "merge":
        lines += ["w = PdfWriter()",
                  "total = 0",
                  "for f in inputs:",
                  "    rr = PdfReader(f)",
                  "    for page in rr.pages:",
                  "        w.add_page(page)",
                  "        total += 1",
                  "with open(out_name, 'wb') as fh:",
                  "    w.write(fh)",
                  "print(f'OUT={out_name}')",
                  "print(f'PAGES_OUT={total}')",
                  "print(f'FILES={len(inputs)}')"]
    return "\n".join(lines) + "\n"


def _parse_kv(output: str) -> dict:
    """Pull KEY=value markers out of the sandbox stdout."""
    out: dict = {}
    for line in (output or "").splitlines():
        m = re.match(r"^([A-Z_]+)=(.*)$", line.strip())
        if m:
            out.setdefault(m.group(1), m.group(2).strip())
    return out


def _link_for(links: list, filename: str) -> Optional[str]:
    for ln in links or []:
        if f"⤓ {filename} (" in ln or f"[⤓ {filename}]" in ln:
            return ln
    return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool(
    "export_data",
    "Produce a downloadable FILE — Excel (.xlsx), CSV, JSON or a PDF table — "
    "for 'give me this as a spreadsheet', 'export the list to Excel', 'send me "
    "a CSV of…'. Two sources (exactly one): connection+sql runs a single SELECT "
    "server-side and writes EVERY row (no probe cap; the rows never pass "
    "through you) — use this for anything from a database; rows_json is for "
    "small data already in the conversation (a list of objects, or "
    "{\"columns\":[...],\"rows\":[[...]]}) copied EXACTLY from a tool result or "
    "the user's message. Returns a download link — include it VERBATIM. Do "
    "not retype hundreds of rows: query them.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "File name without extension"},
            "format": {"type": "string", "enum": ["xlsx", "csv", "json", "pdf"]},
            "rows_json": {"type": "string",
                          "description": "JSON: list of objects, or {columns, rows}"},
            "connection": {"type": "string", "description": "Connection name or id"},
            "sql": {"type": "string", "description": "ONE SELECT statement"},
            "sheet_name": {"type": "string", "description": "xlsx sheet name"},
            "title": {"type": "string", "description": "PDF title"},
        },
        "required": ["name", "format"],
        "additionalProperties": False,
    },
)
async def export_data(args: dict[str, Any]) -> dict[str, Any]:
    user = CURRENT_USER.get() or {}
    uid = int(user.get("user_id") or 0)
    fmt = str(args.get("format") or "").lower().strip()
    if fmt not in FORMATS:
        return _text(f"format must be one of {', '.join(FORMATS)}.", is_error=True)
    filename = safe_name(args.get("name"), fmt)
    rows_json = args.get("rows_json")
    conn = str(args.get("connection") or "").strip()
    sql = str(args.get("sql") or "").strip()
    has_rows = rows_json is not None and str(rows_json).strip() != ""
    has_sql = bool(conn or sql)
    if has_rows == has_sql:
        return _text("Give exactly ONE source: rows_json (data already in the "
                     "conversation) OR connection + sql (query the database).",
                     is_error=True)
    columns = None
    rows_text = None
    if has_rows:
        columns, rows, err = parse_rows_json(rows_json)
        if err:
            return _text(f"Nothing exported — {err}.", is_error=True)
        rows_text = json.dumps(rows, default=str)
    else:
        if not conn or not sql:
            return _text("Both connection and sql are required for a query export.",
                         is_error=True)
        ok, why = sql_is_select_only(sql)
        if not ok:
            return _text(f"Nothing exported — {why}.", is_error=True)
        # Resolve the connection NAME the runtime expects (ids are accepted by
        # the discovery seam, names by aihub_runtime).
        try:
            from platform_tools import _connections_index
            idx = await _connections_index()
            hit = next((c for c in idx if str(c.get("id")) == conn
                        or str(c.get("name") or "").lower() == conn.lower()), None)
            if not hit:
                names = ", ".join(str(c.get("name")) for c in idx if c.get("name"))
                return _text(f"No connection named '{conn}'. Known: {names or '(none)'}",
                             is_error=True)
            conn = str(hit.get("name") or conn)
        except Exception as e:
            logger.warning(f"export_data: connection lookup failed: {e}")
    code = compose_export_code(fmt, filename, rows_json_text=rows_text, columns=columns,
                               connection=conn or None, sql=sql or None,
                               sheet_name=str(args.get("sheet_name") or "Sheet1"),
                               title=str(args.get("title") or ""))
    r = await execute_python(uid, code, stage_uploads=False, timeout=_TIMEOUT,
                             lane="export_data")
    if not r["configured"] or r["timed_out"]:
        return _text(r["error"], is_error=True)
    kv = _parse_kv(r["output"])
    if not r["ok"]:
        tail = (r["output"] or "").strip()[-1500:]
        return _text(f"Export FAILED — nothing was produced.\n{tail}", is_error=True)
    rows_n = int(kv.get("ROWS") or 0)
    if rows_n == 0:
        return _text("The query returned 0 rows — nothing was exported. (Usually a "
                     "filter value that does not exist; verify with a probe first.)")
    link = _link_for(r["links"], filename) or (r["links"][0] if r["links"] else None)
    if not link:
        return _text("The export script finished but no file was delivered — report "
                     "this as UNVERIFIED.", is_error=True)
    src = ("from the conversation" if has_rows
           else f"from connection '{conn}' (query ran server-side)")
    extra = ""
    if kv.get("TRUNCATED") == "1":
        extra += f" NOTE: the result exceeded {MAX_ROWS:,} rows and was cut at that cap."
    if kv.get("PDF_ROWS_SHOWN"):
        extra += (f" NOTE: the PDF table shows the first {kv['PDF_ROWS_SHOWN']} rows; "
                  "use xlsx/csv for the full set.")
    return _text(f"Exported {rows_n:,} row(s) × {kv.get('COLS', '?')} column(s) {src} to "
                 f"{filename}. Include this link VERBATIM in your reply:\n{link}{extra}")


@tool(
    "manipulate_pdf",
    "Work on a PDF file: operation='info' (page count, metadata), 'extract' "
    "(pages='1-3,7' into a new PDF), 'split' (one PDF per page — zipped when "
    "more than 20), 'rotate' (degrees=90|180|270, optional pages), or 'merge' "
    "(paths=[...] several PDFs into one, in order). Inputs are a server path, "
    "an /api/files link delivered in this chat, or a chat attachment — the "
    "same rule as read_file. Results come back as download links — include "
    "them VERBATIM. Never modifies the original file.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The PDF (all ops except merge)"},
            "paths": {"type": "array", "items": {"type": "string"},
                      "description": "PDFs to merge, in order (merge only)"},
            "operation": {"type": "string", "enum": ["info", "extract", "split", "rotate", "merge"]},
            "pages": {"type": "string", "description": "e.g. '1-3,7' (extract/split/rotate)"},
            "degrees": {"type": "integer", "description": "90, 180 or 270 (rotate)"},
            "output_name": {"type": "string", "description": "Optional output file name"},
        },
        "required": ["operation"],
        "additionalProperties": False,
    },
)
async def manipulate_pdf(args: dict[str, Any]) -> dict[str, Any]:
    from document_tools import _resolve_read_path
    user = CURRENT_USER.get() or {}
    uid = int(user.get("user_id") or 0)
    op = str(args.get("operation") or "").lower().strip()
    if op not in PDF_OPS:
        return _text(f"operation must be one of {', '.join(PDF_OPS)}.", is_error=True)
    refs = [str(p) for p in (args.get("paths") or []) if str(p).strip()] if op == "merge" \
        else ([str(args.get("path") or "")] if str(args.get("path") or "").strip() else [])
    if op == "merge" and len(refs) < 2:
        return _text("merge needs at least two PDFs in `paths`.", is_error=True)
    if op != "merge" and not refs:
        return _text("Give me the PDF's path (or /api/files link / attachment).",
                     is_error=True)
    pages, perr = parse_pages(args.get("pages") or "")
    if perr:
        return _text(f"Nothing done — {perr}.", is_error=True)
    degrees = int(args.get("degrees") or 90) if op == "rotate" else 0
    if op == "rotate" and degrees not in (90, 180, 270):
        return _text("degrees must be 90, 180 or 270.", is_error=True)
    if op == "extract" and not pages:
        return _text("extract needs `pages` (e.g. '1-3,7').", is_error=True)
    staged: list = []
    for i, ref in enumerate(refs):
        src, err = _resolve_read_path(ref)
        if err:
            return _text(f"Nothing done — {err}", is_error=True)
        if not src.lower().endswith(".pdf"):
            return _text(f"'{os.path.basename(src)}' is not a PDF.", is_error=True)
        staged.append((src, f"in_{i}.pdf"))
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.splitext(os.path.basename(staged[0][0]))[0])[:60] or "document"
    default_out = {"extract": f"{stem}_pages_{(args.get('pages') or '').replace(',', '_').replace(' ', '')}.pdf",
                   "rotate": f"{stem}_rotated_{degrees}.pdf",
                   "merge": f"{stem}_merged.pdf", "split": "", "info": ""}[op]
    out_name = safe_name(args.get("output_name"), "pdf", default=stem) if args.get("output_name") else default_out
    code = compose_pdf_code(op, [n for _s, n in staged], pages, degrees, out_name, stem)
    r = await execute_python(uid, code, trusted_files=staged, stage_uploads=False,
                             timeout=_TIMEOUT, lane="manipulate_pdf")
    if not r["configured"] or r["timed_out"]:
        return _text(r["error"], is_error=True)
    kv = _parse_kv(r["output"])
    if kv.get("ERROR"):
        return _text(f"Nothing done — {kv['ERROR']}.", is_error=True)
    if not r["ok"]:
        tail = (r["output"] or "").strip()[-1500:]
        return _text(f"PDF operation FAILED — nothing was produced.\n{tail}", is_error=True)
    name0 = os.path.basename(staged[0][0])
    if op == "info":
        meta = [ln[5:] for ln in (r["output"] or "").splitlines() if ln.startswith("META ")]
        return _text(f"'{name0}': {kv.get('PAGES', '?')} page(s), page size "
                     f"{kv.get('PAGE_SIZE', '?')}, encrypted={kv.get('ENCRYPTED', '?')}"
                     + (("\n" + "\n".join("- " + m for m in meta)) if meta else ""))
    if not r["links"]:
        return _text("The operation finished but no file was delivered — report this "
                     "as UNVERIFIED.", is_error=True)
    what = {"extract": f"extracted {kv.get('PAGES_OUT', '?')} page(s) of {kv.get('PAGES', '?')}",
            "rotate": f"rotated {kv.get('ROTATED', '?')} page(s) by {degrees}°",
            "split": f"split into {kv.get('PAGES_OUT', '?')} single-page PDF(s)"
                     + (" (zipped)" if kv.get("ZIPPED") == "1" else ""),
            "merge": f"merged {kv.get('FILES', '?')} PDF(s) into {kv.get('PAGES_OUT', '?')} pages"}[op]
    return _text(f"'{name0}': {what}. Include these link(s) VERBATIM in your reply:\n"
                 + "\n".join(r["links"]))


EXPORT_TOOLS = [export_data, manipulate_pdf]
