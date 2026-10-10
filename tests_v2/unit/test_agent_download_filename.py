"""The Agent's chat downloads keep the file's real name (2026-10-10).

Clicking a file link in the chat fetches /api/files/<id> with the auth header
and saves the blob. The saved name used to come from the link text only, and
only when the text started with the tools' "⤓" arrow. When The Agent reworded
a link ("[Report.xlsx (20.8 KB)](/api/files/...)") the file saved as a bare
"download" with no extension. The name now comes from the server's
Content-Disposition first, then from the link text with or without the arrow.

Runs the page's own downloadName function in node (skips without node).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "agent_service" / "static" / "index.html").read_text(encoding="utf-8")


def _function_source():
    m = re.search(r"const downloadName = \(resp, linkText\) => \{.*?\n    \};", HTML, re.S)
    assert m, "downloadName not found in index.html"
    return m.group(0)


def test_the_click_handler_uses_it():
    block = HTML[HTML.index("a[href^=\"/api/files/\"]"):]
    block = block[:block.index("Safety net")]
    assert "dl.download = downloadName(r, orig);" in block
    assert '"download").trim()' not in block          # the old text-only fallback is gone


CASES = [
    # (Content-Disposition header, link text, expected name)
    ('attachment; filename="Azure_Spend_Report_Jan-Sep_2026.xlsx"',
     "Azure_Spend_Report_Jan-Sep_2026.xlsx (20.8 KB)", "Azure_Spend_Report_Jan-Sep_2026.xlsx"),
    ("attachment; filename*=utf-8''R%C3%A9sum%C3%A9%202026.pdf", "anything", "Résumé 2026.pdf"),
    ("", "⤓ Invoice_Register.xlsx (7.6 KB)", "Invoice_Register.xlsx"),
    ("", "Azure_Spend_Report_Jan-Sep_2026.xlsx (20.8 KB)", "Azure_Spend_Report_Jan-Sep_2026.xlsx"),
    ("", "Q3 (draft) summary.docx (1.2 MB)", "Q3 (draft) summary.docx"),
    ("", "", "download"),
]


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
@pytest.mark.parametrize("header,text,expected", CASES)
def test_download_name(header, text, expected):
    script = (_function_source()
              + "\nconst resp = {headers: {get: (k) => (k === 'Content-Disposition' ? "
              + json.dumps(header) + " : null)}};"
              + "\nprocess.stdout.write(downloadName(resp, " + json.dumps(text) + "));")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout == expected
