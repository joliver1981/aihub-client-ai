"""Pack 20 — pass-3 "reach" live drive (2026-09-02): vision, export, PDF.

Real streamed /api/chat turns as the admin user (id 13):
  V1  run_python draws a bar chart with known bars (Alpha 3, Beta 7, Gamma 5)
      and saves it as a png -> download link.
  V2  a BRAND-NEW conversation is given only the /api/files link and asked
      which bar is tallest -> read_file hands the brain the picture; the only
      way to answer "Beta" is to SEE it (the new session never saw the code).
  E1  "export vendor names + country from ERPDB to Excel" -> export_data with
      connection+sql; the delivered .xlsx is downloaded through the API and
      opened with openpyxl (row count > 0, 2 columns).
  P1  manipulate_pdf info + extract pages 1-2 of a fixture PDF -> the delivered
      PDF has exactly 2 pages (pypdf in the interpreter env).

Run:  C:\\Users\\james\\miniconda3\\envs\\aihub-agent\\python.exe pass3_reach_live.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, APP_ROOT)
sys.path.insert(0, os.path.join(APP_ROOT, "agent_service"))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from dotenv import load_dotenv
load_dotenv(os.path.join(APP_ROOT, ".env"))
try:
    import secure_config
    secure_config.load_secure_config()
except Exception:
    pass
import requests
import shared_auth

BASE = f"http://127.0.0.1:{os.getenv('AGENT_SERVICE_PORT') or int(os.getenv('HOST_PORT', '5001')) + 110}"
TOKEN = shared_auth.sign_cc_token({"user_id": 13, "role": 3, "tenant_id": os.getenv("TENANT_ID", ""),
                                   "username": "pass3-live", "name": "Pass3 Live"})
HDR = {"Authorization": f"Bearer {TOKEN}"}
FAT_PY = os.getenv("CODE_INTERPRETER_PYTHON") or sys.executable
FIXTURE = os.path.join(APP_ROOT, "test_human", "04_Planning", "fixtures", "P2_annual_SOP.pdf")
SESSION = None


def turn(msg, timeout=900, fresh=False):
    global SESSION
    if fresh:
        SESSION = None
    r = requests.post(f"{BASE}/api/chat",
                      json={"message": msg, "session_id": SESSION, "timezone": "America/New_York"},
                      headers=HDR, stream=True, timeout=(10, timeout))
    r.raise_for_status()
    tools, texts = [], []
    for raw in r.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data: "):
            continue
        try:
            ev = json.loads(raw[6:])
        except Exception:
            continue
        t = ev.get("type")
        if t == "tool":
            tools.append(ev.get("name", "").replace("mcp__aihub__", ""))
        elif t == "text":
            texts.append(ev.get("text", ""))
        elif t in ("result", "error"):
            SESSION = ev.get("session_id") or SESSION
        if t == "done":
            break
    reply = "\n".join(texts).strip()
    print("\n" + "=" * 78 + f"\nUSER> {msg}\nTOOLS> {tools}\nAGENT> {reply[:1200]}", flush=True)
    return tools, reply


def download(link_or_id):
    fid = re.search(r"/api/files/([0-9a-f-]+)", link_or_id).group(1)
    r = requests.get(f"{BASE}/api/files/{fid}", headers=HDR, timeout=60)
    r.raise_for_status()
    return r.content


def fat(code):
    return subprocess.run([FAT_PY, "-c", code], capture_output=True, text=True, timeout=120).stdout.strip()


results = []


def check(cid, ok, evidence):
    results.append((cid, bool(ok), str(evidence)[:400]))
    print(f"[{'PASS' if ok else 'FAIL'}] {cid}: {str(evidence)[:300]}", flush=True)


# V1 — make a chart with known bars
tools, reply = turn("Use run_python to draw a bar chart with three bars labeled Alpha, Beta and Gamma with "
                    "heights 3, 7 and 5, title it 'Pass 3 vision test', save it as pass3_vision.png, and give me "
                    "the link.", fresh=True)
links = re.findall(r"/api/files/[0-9a-f-]+", reply)
check("V1 run_python produced the chart png", "run_python" in tools and links, f"tools={tools} links={links}")

# V2 — a NEW conversation must SEE it to answer
if links:
    tools, reply = turn(f"Look at the image at {links[0]} and tell me which bar is the tallest and what its "
                        "label is. Answer from the picture only.", fresh=True)
    check("V2 fresh conversation reads the picture (read_file) and names the tallest bar",
          "read_file" in tools and "beta" in reply.lower(), f"tools={tools} reply={reply[:200]}")
else:
    check("V2 vision", False, "no image link from V1")

# E1 — export from ERPDB
tools, reply = turn("Export the vendor names and their countries from the ERPDB database to an Excel file "
                    "called vendors_by_country.", fresh=True)
xl = [ln for ln in re.findall(r"\[⤓[^\]]+\]\(/api/files/[0-9a-f-]+\)", reply) if ".xlsx" in ln]
ok_e = "export_data" in tools and bool(xl)
rows = cols = -1
if xl:
    data = download(xl[0])
    tmp = os.path.join(tempfile.mkdtemp(), "v.xlsx")
    open(tmp, "wb").write(data)
    out = fat(f"import openpyxl; wb=openpyxl.load_workbook(r'{tmp}'); ws=wb.active; print(ws.max_row, ws.max_column)")
    try:
        rows, cols = [int(x) for x in out.split()]
    except Exception:
        pass
check("E1 export_data (connection+sql) delivered an xlsx with rows", ok_e and rows > 1 and cols >= 2,
      f"tools={tools} link={xl[:1]} rows={rows} cols={cols}")

# P1 — PDF info + extract
tools, reply = turn(f"How many pages does {FIXTURE} have? Then extract pages 1-2 of it into a new PDF and give "
                    "me the link.", fresh=True)
pdfs = [ln for ln in re.findall(r"\[⤓[^\]]+\]\(/api/files/[0-9a-f-]+\)", reply) if ".pdf" in ln]
pages = -1
if pdfs:
    data = download(pdfs[0])
    tmp = os.path.join(tempfile.mkdtemp(), "x.pdf")
    open(tmp, "wb").write(data)
    out = fat(f"from pypdf import PdfReader; print(len(PdfReader(r'{tmp}').pages))")
    try:
        pages = int(out)
    except Exception:
        pass
check("P1 manipulate_pdf info + extract -> 2-page PDF delivered",
      "manipulate_pdf" in tools and pages == 2, f"tools={tools} link={pdfs[:1]} pages={pages}")

print("\n" + "=" * 78)
for cid, ok, ev in results:
    print(f"[{'PASS' if ok else 'FAIL'}] {cid}")
print(f"REPORT {sum(1 for _, ok, _ in results if ok)}/{len(results)} passed")
sys.exit(0 if all(ok for _, ok, _ in results) else 1)
