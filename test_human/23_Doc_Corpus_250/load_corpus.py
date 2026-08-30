"""Load the pack-23 corpus into a dedicated agent's knowledge base.

    python load_corpus.py --limit 12        # smoke run first, ALWAYS
    python load_corpus.py                   # the full 255 -- hours, see below
    python load_corpus.py --status
    python load_corpus.py --teardown

Unlike pack 13's loader, this does NOT shortcut extraction by writing DocumentPages from
text files. It posts each file to the real `/document/process` endpoint so PDF extraction,
DOCX/XLSX parsing and OCR on the scanned images all run on the live path -- that path is
most of what is under test, and 26 of these documents have no text layer at all.

COST CONTROL. Two of that endpoint's defaults are AI calls per document and are switched
OFF here:
    extract_fields=false        skips per-document AI field extraction
    detect_document_type=false  skips the AI document-type classifier
Neither contributes anything to a knowledge corpus that is queried by retrieval, and with
them on, ingest cost scales with document count instead of being ~free. Pass
--with-ai-extraction if you specifically want to test those paths.

Resumable: progress is written to load_state.json after every document, so a run that is
interrupted (or that hits the 503 admission gate) can be restarted and picks up where it
stopped. Idempotent on the SKY- filename prefix.
"""
import argparse
import json
import os
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)

import requests                                        # noqa: E402
import pyodbc                                          # noqa: E402
import config as cfg                                   # noqa: E402
from CommonUtils import get_db_connection_string       # noqa: E402

DOC_API = os.getenv("DOC_API_BASE_URL", "http://127.0.0.1:5011")
APP_API = os.getenv("PACK23_BASE_URL", "http://127.0.0.1:5001")
# Local dev tenant key, same fallback pack 13's loader uses. Not a production secret.
DEV_KEY = "DB27D555-03A8-446E-9C23-8DAAA95EAD21"
API_KEY = os.getenv("AIHUB_API_KEY") or os.getenv("API_KEY") or DEV_KEY
os.environ.setdefault("API_KEY", API_KEY)      # sp_setTenantContext reads this
STATE = os.path.join(HERE, "load_state.json")
AGENT_NAME = "Pack23 Doc Corpus 250 Agent"
PREFIX = "SKY-"


def _headers():
    return {"X-API-Key": API_KEY, "Authorization": f"Bearer {API_KEY}"}


def _sql():
    conn = pyodbc.connect(get_db_connection_string(), timeout=30)
    cur = conn.cursor()
    cur.execute("EXEC tenant.sp_setTenantContext ?", os.getenv("API_KEY"))
    return conn, cur


def load_state():
    return json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {"done": []}


def save_state(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), indent=1)


def ensure_agent(st):
    if st.get("agent_id"):
        return st["agent_id"]
    r = requests.post(f"{APP_API}/add/agent", headers=_headers(), json={
        "agent_id": 0,
        "agent_description": AGENT_NAME,
        "agent_objective": (
            "You answer questions using your attached knowledge documents: a retailer's "
            "facilities and legal file room of store leases, amendments, vendor service "
            "agreements, reconciliations and reports. Be precise about store IDs. When a "
            "lease has been amended, the amendment controls. Distinguish store leases from "
            "vendor service agreements and equipment leases. If your knowledge does not "
            "contain an answer, say so plainly rather than inferring one."),
        "agent_enabled": True,
        "core_tool_names": [],
        "tool_names": [],
    }, timeout=60)
    r.raise_for_status()
    body = r.json()
    st["agent_id"] = int(str(body.get("agent_id") or body.get("message")))
    save_state(st)
    print(f"created agent {st['agent_id']}")
    return st["agent_id"]


def process_one(path, with_ai, retries=6):
    """POST to the live ingest endpoint, honouring the 503 admission gate."""
    form = {
        "filePath": path,
        "is_knowledge_document": "true",
        "extract_fields": "true" if with_ai else "false",
        "detect_document_type": "true" if with_ai else "false",
        "use_batch_processing": "true",
    }
    delay = 5
    for attempt in range(retries):
        r = requests.post(f"{DOC_API}/document/process", headers=_headers(), data=form,
                          timeout=cfg.DOC_API_REQUESTS_TIMEOUT)
        if r.status_code == 503:
            # The doc API's admission gate answers fast-busy instead of queueing. Honour
            # its Retry-After rather than hammering it.
            wait = int(r.headers.get("Retry-After") or delay)
            print(f"    503 busy, retrying in {wait}s "
                  f"(attempt {attempt + 1}/{retries})")
            time.sleep(wait)
            delay = min(delay * 2, 60)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"gave up after {retries} busy responses: {os.path.basename(path)}")


def _tune_indexing(with_summaries):
    """Silence the per-document summary LLM call unless it is explicitly wanted.

    index_knowledge_document() calls generate_knowledge_summary() once per document -- a
    real Anthropic call each time, 255 of them for a full load. This dev tree already runs
    with DOC_SEARCH_ENABLE_SUMMARIES=False, so those summaries are not consulted at search
    time and the spend buys nothing for a retrieval test. Pack 13's loader stubbed the same
    call for the same reason. In-process only; the server is untouched.
    """
    import agent_knowledge_integration as aki
    if not with_summaries:
        aki.generate_knowledge_summary = lambda *a, **k: None


def link_and_index(cur, conn, agent_id, filename, category):
    """Attach the freshly-processed document to the agent and build its vectors."""
    import agent_knowledge_integration as aki
    cur.execute("SELECT TOP 1 document_id FROM Documents WHERE filename = ? "
                "ORDER BY processed_at DESC", [filename])
    row = cur.fetchone()
    if not row:
        return None, "document row not found after processing"
    doc_id = row[0]
    cur.execute("SELECT COUNT(*) FROM AgentKnowledge WHERE agent_id = ? AND document_id = ?",
                [agent_id, doc_id])
    if not cur.fetchone()[0]:
        cur.execute(
            "INSERT INTO AgentKnowledge (agent_id, document_id, description, added_date, "
            "is_active) VALUES (?, ?, ?, getutcdate(), 1)",
            [agent_id, doc_id, f"pack23 [{category}] {filename}"])
        conn.commit()
    ok = aki.index_knowledge_document(doc_id, agent_id, user_id=None)
    return doc_id, None if ok else "vector indexing returned False"


def do_load(a, st):
    gt = json.load(open(os.path.join(a.out, "ground_truth.json"), encoding="utf-8"))
    recs = gt["documents"]
    if a.tier:
        recs = [r for r in recs if r["tier"] in a.tier]
    todo = [r for r in recs if r["filename"] not in set(st["done"])]
    if a.limit:
        todo = todo[: a.limit]

    agent_id = ensure_agent(st)
    _tune_indexing(a.with_summaries)
    docs_dir = os.path.join(a.out, "docs")
    pages = sum(r["pages"] for r in todo)
    print(f"loading {len(todo)} documents / {pages} pages into agent {agent_id}")
    print(f"  AI field extraction + type detection: "
          f"{'ON (expensive)' if a.with_ai_extraction else 'OFF'}")
    print(f"  per-document knowledge summaries:     "
          f"{'ON (one LLM call each)' if a.with_summaries else 'OFF'}")
    print(f"  measured on a 5-document smoke run: ~84s/doc "
          f"-> roughly {len(todo) * 84 / 3600:.1f}h for this batch")

    conn, cur = _sql()
    t0, errors = time.time(), []
    try:
        for i, r in enumerate(todo, 1):
            path = os.path.join(docs_dir, r["filename"])
            try:
                res = process_one(path, a.with_ai_extraction)
                if str(res.get("status", "")).lower() == "error":
                    raise RuntimeError(res.get("message", "unknown error"))
                doc_id, warn = link_and_index(cur, conn, agent_id, r["filename"], r["category"])
                if warn:
                    errors.append((r["filename"], warn))
                    print(f"  [{i}/{len(todo)}] WARN {r['filename']}: {warn}")
                else:
                    st["done"].append(r["filename"])
                    save_state(st)
            except Exception as e:
                errors.append((r["filename"], str(e)[:160]))
                print(f"  [{i}/{len(todo)}] FAIL {r['filename']}: {str(e)[:160]}")
                continue
            if i % 10 == 0 or i == len(todo):
                el = time.time() - t0
                print(f"  [{i}/{len(todo)}] {el / 60:.1f} min elapsed, "
                      f"{el / i:.1f}s/doc, eta {(len(todo) - i) * el / i / 60:.0f} min")
    finally:
        conn.close()

    print(f"\nloaded {len(st['done'])} of {len(gt['documents'])} corpus documents")
    if errors:
        print(f"{len(errors)} document(s) had problems:")
        for f, e in errors[:15]:
            print(f"  {f}: {e}")
        print("Re-running picks up exactly these -- successful documents are not reprocessed.")


def do_status(a, st):
    gt = json.load(open(os.path.join(a.out, "ground_truth.json"), encoding="utf-8"))
    agent_id = st.get("agent_id")
    print(f"agent {agent_id} · state file records {len(st['done'])} of "
          f"{len(gt['documents'])} loaded")
    if not agent_id:
        return
    conn, cur = _sql()
    try:
        cur.execute(
            """SELECT COUNT(DISTINCT d.document_id), COUNT(dp.page_id)
               FROM Documents d
               JOIN AgentKnowledge ak ON ak.document_id = d.document_id
                    AND ak.agent_id = ? AND ak.is_active = 1
               LEFT JOIN DocumentPages dp ON dp.document_id = d.document_id
               WHERE d.filename LIKE ?""", [agent_id, PREFIX + "%"])
        docs, pgs = cur.fetchone()
        print(f"in the database: {docs} documents / {pgs} pages")
        print(f"ground truth expects: {gt['counts']['documents']} documents / "
              f"{gt['counts']['pages']} pages")
        if pgs and pgs < gt["counts"]["pages"] * 0.97:
            print("  ^ pages are SHORT of ground truth -- extraction dropped content. "
                  "Investigate before running any question, or every recall number is "
                  "measuring the wrong thing.")
        if docs and pgs and pgs <= 999:
            print(f"  ^ {pgs} pages is UNDER the 999-page brute-force threshold: production "
                  f"would read every page and could not miss. Load the full corpus before "
                  f"drawing conclusions about retrieval.")
    finally:
        conn.close()


def do_teardown(a, st):
    import agent_knowledge_integration as aki
    conn, cur = _sql()
    try:
        cur.execute("SELECT document_id FROM Documents WHERE filename LIKE ?", [PREFIX + "%"])
        ids = [r[0] for r in cur.fetchall()]
        for d in ids:
            aki.remove_knowledge_document_vectors(d)
        cur.execute("DELETE FROM AgentKnowledge WHERE document_id IN "
                    "(SELECT document_id FROM Documents WHERE filename LIKE ?)", [PREFIX + "%"])
        cur.execute("DELETE FROM DocumentPages WHERE document_id IN "
                    "(SELECT document_id FROM Documents WHERE filename LIKE ?)", [PREFIX + "%"])
        cur.execute("DELETE FROM Documents WHERE filename LIKE ?", [PREFIX + "%"])
        conn.commit()
        print(f"teardown: removed {len(ids)} documents (vector purges queued)")
    finally:
        conn.close()
    if st.get("agent_id"):
        requests.post(f"{APP_API}/delete/agent", headers=_headers(),
                      json={"agent_id": st["agent_id"]}, timeout=60)
        print(f"teardown: deleted agent {st['agent_id']}")
    if os.path.exists(STATE):
        os.remove(STATE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=r"C:\temp\doc_corpus_250")
    ap.add_argument("--limit", type=int, help="load only N documents (smoke run)")
    ap.add_argument("--tier", type=int, nargs="*", help="restrict to these tiers")
    ap.add_argument("--with-ai-extraction", action="store_true",
                    help="enable per-document AI field extraction and type detection "
                         "(costs money per document; off by default)")
    ap.add_argument("--with-summaries", action="store_true",
                    help="enable the per-document knowledge summary LLM call (one Anthropic "
                         "call per document; off by default because this tree runs with "
                         "DOC_SEARCH_ENABLE_SUMMARIES=False and never reads them)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--teardown", action="store_true")
    a = ap.parse_args()
    st = load_state()
    if a.teardown:
        do_teardown(a, st)
    elif a.status:
        do_status(a, st)
    else:
        do_load(a, st)
        do_status(a, st)


if __name__ == "__main__":
    main()
