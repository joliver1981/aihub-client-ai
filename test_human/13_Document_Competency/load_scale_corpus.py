"""Load the SCALE corpus into a dedicated agent's knowledge, mirroring live shapes exactly.

- Documents / DocumentPages / AgentKnowledge rows: same columns the knowledge upload path
  writes (is_knowledge_document=1, page_id='{doc}_p{n}', added_by omitted = visible to all).
- Vectors: by calling the REAL agent_knowledge_integration.index_knowledge_document per doc
  (in-process), so chunking, metadata, ids, and embedding flow through the live code path.
  Smart chunking + summary generation are stubbed off IN THIS PROCESS ONLY (deterministic
  standard chunking; the harness caveat is documented) — the server-side vector API and
  embedding calls are the real ones.

Idempotent: SCALE13_ filename prefix; re-runs skip existing documents.
Teardown: --teardown removes vectors (via the live delete queue), SQL rows, and the agent.
Usage:  python load_scale_corpus.py [--teardown]     (env aihub2.1; vector API must be up)
"""
import argparse
import json
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)

import requests
import pyodbc
import config as cfg
from CommonUtils import get_db_connection_string

BASE = os.getenv('DCT13_BASE_URL', 'http://127.0.0.1:5001')
API_KEY = os.getenv('AIHUB_API_KEY') or os.getenv('API_KEY') or 'DB27D555-03A8-446E-9C23-8DAAA95EAD21'
STATE = os.path.join(HERE, 'scale_state.json')
AGENT_NAME = 'DCT13 Scale Lease Agent'


def _sql():
    conn = pyodbc.connect(get_db_connection_string(), timeout=30)
    cur = conn.cursor()
    cur.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
    return conn, cur


def _headers():
    return {"X-API-Key": API_KEY, "Authorization": f"Bearer {API_KEY}"}


def load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE, encoding='utf-8'))
    return {}


def save_state(st):
    json.dump(st, open(STATE, 'w', encoding='utf-8'), indent=1)


def ensure_agent(st):
    if st.get('agent_id'):
        return st['agent_id']
    r = requests.post(f"{BASE}/add/agent", headers=_headers(), json={
        'agent_id': 0,
        'agent_description': AGENT_NAME,
        'agent_objective': ('You answer questions using your attached knowledge documents '
                            '(commercial leases). Be precise about store IDs. If your knowledge '
                            'does not contain an answer, say so plainly.'),
        'agent_enabled': True,
        'core_tool_names': [],
        'tool_names': [],
    }, timeout=60)
    r.raise_for_status()
    st['agent_id'] = int(str(r.json().get('agent_id') or r.json().get('message')))
    save_state(st)
    print(f"created scale agent {st['agent_id']}")
    return st['agent_id']


def load_corpus(agent_id):
    import agent_knowledge_integration as aki
    # In-process only: deterministic standard chunking, no per-doc summary LLM calls.
    cfg.VECTOR_USE_SMART_CHUNKING = False
    aki.generate_knowledge_summary = lambda *a, **k: None

    corpus_dir = os.path.join(HERE, 'scale_corpus')
    files = sorted(f for f in os.listdir(corpus_dir) if f.startswith('SCALE13_'))
    conn, cur = _sql()
    loaded = skipped = 0
    try:
        for fname in files:
            cur.execute("SELECT document_id FROM Documents WHERE filename = ?", [fname])
            row = cur.fetchone()
            if row:
                skipped += 1
                continue
            pages = open(os.path.join(corpus_dir, fname), encoding='utf-8').read().split('\f')
            doc_id = str(uuid.uuid4())
            cur.execute(
                """INSERT INTO Documents (document_id, filename, original_path, document_type,
                                          page_count, processed_at, is_knowledge_document)
                   VALUES (?, ?, ?, ?, ?, getutcdate(), 1)""",
                [doc_id, fname, f'synthetic://pack13-scale/{fname}', 'lease_agreement', len(pages)])
            for n, text in enumerate(pages, 1):
                pid = f"{doc_id}_p{n}"
                cur.execute(
                    "INSERT INTO DocumentPages (page_id, document_id, page_number, full_text, vector_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [pid, doc_id, n, f"[Page {n}]\n{text}", pid])
            cur.execute(
                "INSERT INTO AgentKnowledge (agent_id, document_id, description, added_date, is_active) "
                "VALUES (?, ?, ?, getutcdate(), 1)",
                [agent_id, doc_id, f'Pack13 scale corpus {fname}'])
            conn.commit()
            ok = aki.index_knowledge_document(doc_id, agent_id, user_id=None)  # SHARED vectors
            if not ok:
                print(f"  WARNING: vector indexing returned False for {fname}")
            loaded += 1
            if loaded % 20 == 0:
                print(f"  loaded {loaded}/{len(files)}")
    finally:
        conn.close()
    print(f"corpus load complete: {loaded} loaded, {skipped} already present, {len(files)} total")


def verify(agent_id):
    conn, cur = _sql()
    try:
        cur.execute(
            """SELECT COUNT(DISTINCT d.document_id), COUNT(dp.page_id)
               FROM Documents d
               JOIN AgentKnowledge ak ON ak.document_id = d.document_id AND ak.agent_id = ? AND ak.is_active = 1
               JOIN DocumentPages dp ON dp.document_id = d.document_id
               WHERE d.filename LIKE 'SCALE13_%'""", [agent_id])
        docs, pages = cur.fetchone()
        print(f"verify: {docs} scale docs / {pages} pages active for agent {agent_id}")
    finally:
        conn.close()
    import agent_knowledge_integration as aki
    hits = aki.search_knowledge_vectors('climate control systems maintenance', int(agent_id),
                                        user_id='admin', top_k=5)
    print(f"verify: probe vector search returned {len(hits)} hits "
          f"(e.g. {[(h.get('metadata') or {}).get('filename', '?')[:40] for h in hits[:3]]})")


def teardown(st):
    import agent_knowledge_integration as aki
    conn, cur = _sql()
    try:
        cur.execute("SELECT document_id FROM Documents WHERE filename LIKE 'SCALE13_%'")
        doc_ids = [r[0] for r in cur.fetchall()]
        for doc_id in doc_ids:
            aki.remove_knowledge_document_vectors(doc_id)
        cur.execute("DELETE FROM AgentKnowledge WHERE document_id IN "
                    "(SELECT document_id FROM Documents WHERE filename LIKE 'SCALE13_%')")
        cur.execute("DELETE FROM DocumentPages WHERE document_id IN "
                    "(SELECT document_id FROM Documents WHERE filename LIKE 'SCALE13_%')")
        cur.execute("DELETE FROM Documents WHERE filename LIKE 'SCALE13_%'")
        conn.commit()
        print(f"teardown: removed {len(doc_ids)} scale documents (vector purges queued)")
    finally:
        conn.close()
    if st.get('agent_id'):
        requests.post(f"{BASE}/delete/agent", headers=_headers(),
                      json={'agent_id': st['agent_id']}, timeout=60)
        print(f"teardown: deleted agent {st['agent_id']}")
    if os.path.exists(STATE):
        os.remove(STATE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--teardown', action='store_true')
    args = ap.parse_args()
    st = load_state()
    if args.teardown:
        teardown(st)
        return
    agent_id = ensure_agent(st)
    load_corpus(agent_id)
    verify(agent_id)


if __name__ == '__main__':
    main()
