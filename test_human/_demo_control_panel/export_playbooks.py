"""Export every playbook docx referenced in registry.json to a web page (playbooks/*.html)
the control panel serves in a browser tab. Uses Word COM "filtered HTML" so the playbooks'
colored callout boxes, tables and fonts survive, then normalizes to UTF-8 and injects a
responsive wrapper. Re-run whenever a playbook docx is rebuilt.
"""
import json
import os
import re
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
PLAY = os.path.join(HERE, "playbooks")
WD_FILTERED_HTML = 10


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
    import win32com.client
    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    try:
        for d in docs:
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
