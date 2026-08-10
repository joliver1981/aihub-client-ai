"""Export every playbook docx referenced in registry.json to a web page (playbooks/*.html)
the control panel serves in a browser tab. Uses Word COM "filtered HTML" so the playbooks'
colored callout boxes, tables and fonts survive, then normalizes to UTF-8 and injects a
responsive wrapper. Re-run whenever a playbook docx is rebuilt.
"""
import html as _html
import json
import os
import re
import shutil

from _web_stem import web_stem

HERE = os.path.dirname(os.path.abspath(__file__))
PLAY = os.path.join(HERE, "playbooks")
WD_FILTERED_HTML = 10


# ---------------------------------------------------------------------------
# Markdown scenarios (competency pack) -> styled HTML. Self-contained mini
# renderer (no markdown lib in the env): handles headings, bold, inline code,
# fenced code, tables, blockquotes, lists, rules, and passes inline HTML
# (the <span> callouts) through. Enough for the SCENARIO.md structure.
# ---------------------------------------------------------------------------

MD_STYLE = """
<style>
 :root{--ink:#0f1826;--text:#28323f;--muted:#5a6a7c;--line:#e2e8f1;
       --accent:#2563eb;--accent-soft:#eef3fd;--good:#1c8a57;--good-soft:#e8f5ee;
       --watch:#a96908;--console:#0c1320;--console-ink:#dce7f6;
       --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
       --mono:ui-monospace,"Cascadia Code",Menlo,Consolas,monospace;}
 body{max-width:860px;margin:34px auto;padding:0 26px 70px;background:#f6f8fc;
      color:var(--text);font-family:var(--sans);line-height:1.62;font-size:16px;}
 h1{font-size:30px;letter-spacing:-.02em;color:var(--ink);margin:.2em 0 .5em;}
 h2{font-size:22px;color:var(--ink);margin:1.4em 0 .4em;border-bottom:1px solid var(--line);padding-bottom:6px;}
 h3{font-size:16.5px;color:var(--ink);margin:1.2em 0 .3em;}
 p{margin:.6em 0;} strong{color:var(--ink);}
 code{font-family:var(--mono);font-size:.86em;background:var(--accent-soft);
      padding:.06em .38em;border-radius:5px;color:#1d4fbe;}
 pre{background:var(--console);border-radius:11px;padding:14px 16px;overflow-x:auto;
     margin:.7em 0;} pre code{background:none;color:var(--console-ink);padding:0;
     font-size:13.5px;line-height:1.55;white-space:pre-wrap;word-break:break-word;}
 *{box-sizing:border-box;} html,body{overflow-x:hidden;max-width:100%;}
 blockquote{margin:.7em 0;padding:.4em 16px;border-left:3px solid var(--accent);
            background:#fff;border-radius:0 8px 8px 0;}
 blockquote pre{margin:.4em 0;}
 table{border-collapse:collapse;width:100%;margin:.8em 0;font-size:14px;}
 th,td{border:1px solid var(--line);padding:7px 11px;text-align:left;}
 th{background:var(--accent-soft);color:var(--ink);}
 ul,ol{padding-left:1.4em;} li{margin:.25em 0;}
 hr{border:0;border-top:1px solid var(--line);margin:1.4em 0;}
 span[style]{font-weight:600;}
 a{color:var(--accent);}
</style>
"""


def _inline(s):
    # code spans first (protect from other rules), then bold, then leave any
    # inline HTML (<span…>) untouched.
    parts = re.split(r"(`[^`]+`)", s)
    out = []
    for p in parts:
        if p.startswith("`") and p.endswith("`") and len(p) >= 2:
            out.append("<code>" + _html.escape(p[1:-1]) + "</code>")
        else:
            # bold
            p = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", p)
            out.append(p)
    return "".join(out)


def render_markdown(md):
    lines = md.replace("\r\n", "\n").split("\n")
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        # fenced code
        if line.strip().startswith("```"):
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(_html.escape(lines[i])); i += 1
            i += 1
            out.append("<pre><code>" + "\n".join(buf) + "</code></pre>")
            continue
        # heading
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
            i += 1
            continue
        # horizontal rule
        if re.match(r"^\s*---+\s*$", line):
            out.append("<hr>"); i += 1; continue
        # table (header row + separator)
        if "|" in line and i + 1 < n and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]) and "-" in lines[i + 1]:
            def cells(r):
                return [c.strip() for c in r.strip().strip("|").split("|")]
            head = cells(line)
            i += 2
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(cells(lines[i])); i += 1
            th = "".join(f"<th>{_inline(c)}</th>" for c in head)
            body = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows)
            out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>")
            continue
        # blockquote (may wrap a fenced block)
        if line.lstrip().startswith(">"):
            buf = []
            while i < n and lines[i].lstrip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i])); i += 1
            out.append("<blockquote>" + render_markdown("\n".join(buf)) + "</blockquote>")
            continue
        # list
        if re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < n and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append("<li>" + _inline(re.sub(r"^\s*[-*]\s+", "", lines[i])) + "</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        # blank
        if not line.strip():
            i += 1; continue
        # paragraph (gather until blank / block start)
        para = [line]
        i += 1
        while i < n and lines[i].strip() and not re.match(r"^(#{1,6}\s|```|\s*[-*]\s|>|\s*---+\s*$)", lines[i]) and "|" not in lines[i]:
            para.append(lines[i]); i += 1
        out.append("<p>" + _inline(" ".join(para)) + "</p>")
    return "\n".join(out)


def export_markdown(md_path):
    stem = web_stem(md_path)
    out_html = os.path.join(PLAY, stem + ".html")
    with open(md_path, encoding="utf-8") as fh:
        md = fh.read()
    title = stem.replace("_", " ")
    m = re.search(r"^#\s+(.*)$", md, re.M)
    if m:
        title = m.group(1).strip()
    body = render_markdown(md)
    doc = ('<!doctype html><html><head><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width, initial-scale=1">'
           f"<title>{_html.escape(title)}</title>{MD_STYLE}</head><body>"
           f"{body}</body></html>")
    with open(out_html, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return out_html


def docs_from_registry():
    with open(os.path.join(HERE, "registry.json"), encoding="utf-8") as fh:
        reg = json.load(fh)
    seen = []
    for d in reg.get("demos", []):
        doc = d.get("doc")
        if doc and doc not in seen:
            seen.append(doc)
    return seen


def export_one(word, docx_path):
    stem = os.path.splitext(os.path.basename(docx_path))[0]
    out_html = os.path.join(PLAY, stem + ".html")
    doc = word.Documents.Open(docx_path, False, True)  # no convert dialog, read-only
    try:
        for toc in doc.TablesOfContents:
            toc.Update()
        doc.SaveAs2(out_html, FileFormat=WD_FILTERED_HTML, Encoding=65001)
    finally:
        doc.Close(0)

    with open(out_html, "rb") as fh:
        raw = fh.read()
    m = re.search(rb"charset=([A-Za-z0-9\-]+)", raw[:2000])
    enc = (m.group(1).decode("ascii") if m else "utf-8").lower()
    try:
        text = raw.decode("utf-8" if enc in ("utf-8", "unicode") else enc)
    except (UnicodeDecodeError, LookupError):
        text = raw.decode("cp1252", errors="replace")
    text = re.sub(r"charset=[A-Za-z0-9\-]+", "charset=utf-8", text, count=1)

    inject = (
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<style>\n"
        " body { max-width: 980px; margin: 24px auto !important; padding: 0 34px 60px;\n"
        "        background: #fff; }\n"
        " table { max-width: 100%; }\n"
        " img { max-width: 100%; height: auto; }\n"
        " @media print { body { max-width: none; margin: 0 !important; } }\n"
        "</style>\n")
    if "</head>" in text:
        text = text.replace("</head>", inject + "</head>", 1)
    else:
        text = inject + text

    with open(out_html, "w", encoding="utf-8") as fh:
        fh.write(text)

    # Word may emit an assets folder (images etc.) next to the html — keep it in playbooks/.
    aux_src = os.path.splitext(docx_path)[0] + "_files"
    aux_dst = os.path.join(PLAY, stem + "_files")
    if os.path.isdir(aux_src) and not os.path.isdir(aux_dst):
        shutil.move(aux_src, aux_dst)
    return out_html


def main():
    os.makedirs(PLAY, exist_ok=True)
    docs = docs_from_registry()
    if not docs:
        print("no demo docs found in registry.json")
        return
    md_docs = [d for d in docs if d.lower().endswith((".md", ".markdown"))]
    docx_docs = [d for d in docs if d.lower().endswith((".docx", ".doc"))]

    # Markdown scenarios (competency pack) — no Word needed.
    for d in md_docs:
        if not os.path.isfile(d):
            print(f"SKIP (md missing): {d}")
            continue
        out = export_markdown(d)
        print(f"rendered {os.path.basename(os.path.dirname(d))}/{os.path.basename(d)} "
              f"-> playbooks/{os.path.basename(out)} ({os.path.getsize(out):,} bytes)")

    # Word playbooks (.docx masters) — only spin up Word if any exist.
    if not docx_docs:
        return
    import win32com.client
    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    try:
        for d in docx_docs:
            if not os.path.isfile(d):
                print(f"SKIP (docx missing): {d}")
                continue
            out = export_one(word, d)
            print(f"exported {os.path.basename(d)} -> playbooks/{os.path.basename(out)} "
                  f"({os.path.getsize(out):,} bytes)")
    finally:
        word.Quit()


if __name__ == "__main__":
    main()
