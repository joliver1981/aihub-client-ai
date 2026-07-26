"""Pack 13 automated runner — drives the LIVE app end-to-end.

Phases:
  1. upload  — repository ingestion via the real Document Processor job pipeline
               (SQL-inserted DCT13 job + /api/scheduler/execute_document_job trigger;
               the background worker is the live app_doc_job_q service)
  2. ingest  — Phase A assertions straight against DocumentPages (SQL oracle)
  3. agents  — two dedicated agents: R (repository search tools, NO knowledge) and
               K (knowledge attachments, no repo tools) so phases can't cheat
  4. ask     — Phase B questions -> agent R; Phase C -> agent K (incl. delete-then-ask)
  5. grade   — deterministic verdicts (grading.py)
  6. report  — TEST_RUN_<date>.md next to this file

Flags: --skip-upload  --skip-knowledge  --teardown  --only <ids,comma-sep>
Interpreter: aihub2.1 (main-app env). Main app + vector API + doc job queue must be running.
"""
import argparse
import datetime as _dt
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)

import battery as B
from grading import grade_answer

import requests
import pyodbc
import config as cfg  # noqa: F401  (loads .env so API_KEY/tenant context resolve)
from CommonUtils import get_db_connection_string

BASE = os.getenv('DCT13_BASE_URL', 'http://127.0.0.1:5001')
API_KEY = os.getenv('AIHUB_API_KEY') or os.getenv('API_KEY') or 'DB27D555-03A8-446E-9C23-8DAAA95EAD21'
FIX = os.path.join(HERE, 'fixtures')
STATE_PATH = os.path.join(HERE, 'state.json')
BLANK_MAX = 20
JOB_NAME = 'DCT13 Lease Corpus Ingest'
CHAT_TIMEOUT = int(os.getenv('DCT13_CHAT_TIMEOUT', 300))
INDEX_WAIT = int(os.getenv('DCT13_INDEX_WAIT', 90))
# Real-world ingest cost of this corpus: ~264 pages with per-page field extraction
# through the throttled proxy — the 79-page lease alone runs 10-30 minutes.
UPLOAD_TIMEOUT = int(os.getenv('DCT13_UPLOAD_TIMEOUT', 5400))


# ---------------------------------------------------------------------------
# State + SQL
# ---------------------------------------------------------------------------

def load_state():
    if os.path.exists(STATE_PATH):
        try:
            return json.load(open(STATE_PATH, encoding='utf-8'))
        except Exception:
            pass
    return {}


def save_state(st):
    json.dump(st, open(STATE_PATH, 'w', encoding='utf-8'), indent=1)


def _sql():
    conn = pyodbc.connect(get_db_connection_string(), timeout=30)
    cur = conn.cursor()
    key = os.getenv('API_KEY')
    if key:
        try:
            cur.execute("EXEC tenant.sp_setTenantContext ?", key)
        except Exception:
            pass
    return conn, cur


# ---------------------------------------------------------------------------
# HTTP plumbing (per the live app's actual API surface)
# ---------------------------------------------------------------------------

def make_session():
    s = requests.Session()
    s.headers.update({"X-API-Key": API_KEY, "Authorization": f"Bearer {API_KEY}"})
    r = s.get(f"{BASE}/get/workflows", timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f'main app not ready at {BASE} (GET /get/workflows -> {r.status_code})')
    return s


def ensure_doc_job():
    """Find-or-create the DCT13 DocumentJobs row (SQL — the save route is session-gated)."""
    conn, cur = _sql()
    try:
        cur.execute("SELECT JobID FROM DocumentJobs WHERE JobName = ?", [JOB_NAME])
        row = cur.fetchone()
        if row:
            return int(row[0])
        archive = os.path.join(FIX, '_archived')
        os.makedirs(archive, exist_ok=True)
        cur.execute(
            """
            INSERT INTO DocumentJobs (
                JobName, Description, CreatedBy, CreatedAt, IsActive, InputDirectory,
                ArchiveDirectory, FilePattern, ProcessSubdirectories, DefaultDocumentType,
                ForceAIExtraction, UseBatchProcessing, BatchSize, NotifyOnCompletion, NotificationEmail
            ) VALUES (?, ?, ?, getutcdate(), 1, ?, ?, ?, 0, ?, 0, 0, 3, 0, '')
            """,
            [JOB_NAME, 'Pack 13 document competency corpus (auto-created by run_battery.py)',
             'DCT13', FIX, archive, 'DCT13_*.pdf', B.DOCUMENT_TYPE],
        )
        conn.commit()
        cur.execute("SELECT JobID FROM DocumentJobs WHERE JobName = ?", [JOB_NAME])
        return int(cur.fetchone()[0])
    finally:
        conn.close()


def run_doc_job(sess, job_id):
    r = sess.post(f"{BASE}/api/scheduler/execute_document_job/{job_id}",
                  json={'api_key': API_KEY}, timeout=30)
    if r.status_code == 409:
        print('  (job already has a queued execution — reusing it)')
        return
    if r.status_code != 200:
        raise RuntimeError(f'execute_document_job -> {r.status_code}: {r.text[:200]}')


def repository_docs_present(names):
    """Return the subset of fixture names that have a repository row with >=1 stored page."""
    conn, cur = _sql()
    try:
        present = set()
        for name in names:
            cur.execute(
                """
                SELECT TOP 1 d.document_id FROM Documents d
                JOIN DocumentPages dp ON dp.document_id = d.document_id
                WHERE d.filename LIKE ? AND d.is_knowledge_document = 0
                """,
                [f'%{name}'],
            )
            if cur.fetchone():
                present.add(name)
        return present
    finally:
        conn.close()


def _job_execution_state(job_id):
    conn, cur = _sql()
    try:
        cur.execute(
            "SELECT TOP 1 ExecutionID, Status, ErrorMessage FROM DocumentJobExecutions "
            "WHERE JobID = ? ORDER BY ExecutionID DESC", [job_id])
        row = cur.fetchone()
        return (row[0], row[1], row[2]) if row else (None, None, None)
    finally:
        conn.close()


def wait_for_processing(names, job_id=None, timeout_s=UPLOAD_TIMEOUT):
    deadline = time.time() + timeout_s
    last = -1
    while time.time() < deadline:
        present = repository_docs_present(names)
        if len(present) != last:
            print(f'  processed {len(present)}/{len(names)}', flush=True)
            last = len(present)
        if len(present) == len(names):
            return
        if job_id:
            _, status, err = _job_execution_state(job_id)
            if status and status.upper() in ('FAILED', 'ERROR', 'CANCELLED'):
                raise RuntimeError(f'document job execution ended {status}: {err}')
        time.sleep(20)
    missing = sorted(set(names) - repository_docs_present(names))
    raise RuntimeError(f'processing timeout after {timeout_s}s; missing: {missing}')


def create_agent(sess, name, objective, core_tools):
    r = sess.post(f"{BASE}/add/agent", json={
        'agent_id': 0,
        'agent_description': name,
        'agent_objective': objective,
        'agent_enabled': True,
        'core_tool_names': core_tools,
        'tool_names': [],
    }, timeout=60)
    r.raise_for_status()
    data = r.json()
    agent_id = data.get('agent_id') or data.get('id') or data.get('message')
    return int(str(agent_id))


def ensure_agents(sess, st):
    if 'agent_repo' not in st:
        st['agent_repo'] = create_agent(
            sess, 'DCT13 Repo Search Agent',
            'You answer questions about documents stored in the document repository using your '
            'document search tools. Always search before answering. Quote the specific lease '
            'language when you can. If the documents do not address something, say so plainly '
            'instead of guessing.',
            ['document_super_search', 'search_documents_meaning', 'search_documents'],
        )
        print(f"  created repo agent {st['agent_repo']}")
    if 'agent_knowledge' not in st:
        st['agent_knowledge'] = create_agent(
            sess, 'DCT13 Knowledge Lease Agent',
            'You answer questions using your attached knowledge documents (commercial leases and '
            'amendments). Be precise about which store/lease each fact comes from. If your '
            'knowledge does not contain the answer, say so plainly instead of guessing.',
            [],
        )
        print(f"  created knowledge agent {st['agent_knowledge']}")
    save_state(st)
    return st['agent_repo'], st['agent_knowledge']


def attach_knowledge(sess, agent_id, path, st):
    name = os.path.basename(path)
    kids = st.setdefault('knowledge_ids', {})
    if name in kids:
        return kids[name]
    if not os.path.exists(path):
        # The doc job may have archived processed fixtures out of fixtures/.
        archived = os.path.join(os.path.dirname(path), '_archived', name)
        if os.path.exists(archived):
            path = archived
        else:
            raise RuntimeError(f'fixture missing (re-run make_fixtures.py): {name}')
    with open(path, 'rb') as fh:
        r = sess.post(f"{BASE}/add/agent_knowledge",
                      data={'agent_id': str(agent_id), 'description': f'Pack13 {name}'},
                      files={'file': (name, fh, 'application/pdf')}, timeout=600)
    if r.status_code not in (200, 201):
        raise RuntimeError(f'add/agent_knowledge {name} -> {r.status_code}: {r.text[:200]}')
    data = r.json()
    kid = data.get('knowledge_id')
    print(f'  attached {name} (knowledge_id={kid}, pages={data.get("page_count")})')
    kids[name] = kid
    save_state(st)
    return kid


def delete_knowledge(sess, fixture_name, st):
    kid = st.get('knowledge_ids', {}).get(fixture_name)
    if not kid:
        raise RuntimeError(f'no knowledge_id recorded for {fixture_name}')
    r = sess.post(f"{BASE}/delete/agent_knowledge/{kid}", timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f'delete/agent_knowledge/{kid} -> {r.status_code}: {r.text[:200]}')
    print(f'  deleted knowledge {fixture_name} (knowledge_id={kid})')


def chat(sess, agent_id, question):
    r = sess.post(f"{BASE}/api/agents/{agent_id}/chat",
                  json={'prompt': question, 'history': '[]'}, timeout=CHAT_TIMEOUT)
    if r.status_code != 200:
        return f'[HTTP {r.status_code}] {r.text[:300]}'
    data = r.json()
    return str(data.get('response') or data.get('answer') or '')


def teardown(sess, st):
    for name, kid in list(st.get('knowledge_ids', {}).items()):
        try:
            sess.post(f"{BASE}/delete/agent_knowledge/{kid}", timeout=60)
            print(f'  knowledge deleted: {name}')
        except Exception as e:
            print(f'  knowledge delete failed {name}: {e}')
    for key in ('agent_repo', 'agent_knowledge'):
        if key in st:
            try:
                sess.post(f"{BASE}/delete/agent", json={'agent_id': st[key]}, timeout=60)
                print(f'  agent deleted: {st[key]}')
            except Exception as e:
                print(f'  agent delete failed: {e}')
    conn, cur = _sql()
    try:
        cur.execute("UPDATE DocumentJobs SET IsActive = 0 WHERE JobName = ?", [JOB_NAME])
        conn.commit()
    finally:
        conn.close()
    if os.path.exists(STATE_PATH):
        os.remove(STATE_PATH)
    print('  teardown complete (repository DCT13_* documents are left in place — see README)')


# ---------------------------------------------------------------------------
# Phase A — SQL oracle
# ---------------------------------------------------------------------------

def ingestion_check(item):
    conn, cur = _sql()
    try:
        cur.execute(
            """
            SELECT d.document_id, COUNT(dp.page_id),
                   SUM(CASE WHEN dp.full_text IS NULL OR LEN(LTRIM(RTRIM(dp.full_text))) <= ? THEN 1 ELSE 0 END)
            FROM Documents d JOIN DocumentPages dp ON dp.document_id = d.document_id
            WHERE d.filename LIKE ? AND d.is_knowledge_document = 0
            GROUP BY d.document_id
            """,
            [BLANK_MAX, f"%{item['fixture']}"],
        )
        rows = cur.fetchall()
        if not rows:
            return 'FAIL', 'no repository document found (upload/processing missing?)'
        doc_id, pages, blank = rows[0]
        problems = []
        if pages != item['pages']:
            problems.append(f'expected {item["pages"]} pages, stored {pages}')
        if (blank or 0) > item['max_blank']:
            problems.append(f'{blank} blank-stored pages (max {item["max_blank"]})')
        missing = []
        for phrase in item['must_contain']:
            cur.execute(
                "SELECT COUNT(*) FROM DocumentPages WHERE document_id = ? AND full_text LIKE ?",
                [doc_id, f'%{phrase}%'],
            )
            if cur.fetchone()[0] == 0:
                missing.append(phrase)
        if missing:
            problems.append(f'phrases not stored: {missing}')
        if problems:
            return 'FAIL', '; '.join(problems)
        return 'PASS', f'{pages} pages, 0 blank, phrases present (doc {doc_id})'
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--skip-upload', action='store_true')
    ap.add_argument('--skip-knowledge', action='store_true')
    ap.add_argument('--teardown', action='store_true')
    ap.add_argument('--only', default='')
    args = ap.parse_args()

    only = {x.strip() for x in args.only.split(',') if x.strip()}
    results = []
    started = _dt.datetime.now()
    st = load_state()
    sess = make_session()

    if args.teardown:
        teardown(sess, st)
        return 0

    upload_names = sorted({i['fixture'] for i in B.INGESTION} | set(B.EXTRA_UPLOADS))

    if not args.skip_upload:
        already = repository_docs_present(upload_names)
        todo = sorted(set(upload_names) - already)
        if not todo:
            print(f'[upload] all {len(upload_names)} fixtures already in repository — skipping job run')
        else:
            job_id = ensure_doc_job()
            print(f'[upload] doc job {job_id}: {len(todo)} fixtures to process (of {len(upload_names)})')
            _, status, _ = _job_execution_state(job_id)
            if status and status.upper() in ('RUNNING', 'QUEUED'):
                print(f'  execution already {status} — waiting for it instead of re-triggering')
            else:
                run_doc_job(sess, job_id)
            wait_for_processing(upload_names, job_id=job_id)

    print('[phase A] ingestion integrity (SQL oracle)')
    for item in B.INGESTION:
        if only and item['id'] not in only:
            continue
        verdict, detail = ingestion_check(item)
        results.append(dict(id=item['id'], phase='A', q=item['fixture'], verdict=verdict, detail=detail, answer=''))
        print(f"  {item['id']}: {verdict} — {detail}")

    agent_repo, agent_knowledge = ensure_agents(sess, st)

    if not args.skip_knowledge:
        fresh = [n for n in B.KNOWLEDGE_DOCS if n not in st.get('knowledge_ids', {})]
        print(f'[knowledge] attaching {len(fresh)} docs to agent {agent_knowledge}')
        for name in B.KNOWLEDGE_DOCS:
            attach_knowledge(sess, agent_knowledge, os.path.join(FIX, name), st)
        if fresh:
            print(f'  waiting {INDEX_WAIT}s for vector indexing to settle')
            time.sleep(INDEX_WAIT)

    print('[phase B/C] questions')
    for item in B.ALL_QUESTIONS:
        if only and item['id'] not in only:
            continue
        agent_id = agent_repo if item['mode'] == 'repo' else agent_knowledge
        if item['mode'] == 'knowledge_after_delete':
            if item['delete_doc'] in st.get('knowledge_ids', {}):
                print(f"  {item['id']}: deleting knowledge doc {item['delete_doc']} first")
                delete_knowledge(sess, item['delete_doc'], st)
                st['knowledge_ids'].pop(item['delete_doc'], None)
                save_state(st)
                time.sleep(5)
            else:
                print(f"  {item['id']}: {item['delete_doc']} already deleted — asking directly")
        answer = chat(sess, agent_id, item['q'])
        verdict, detail = grade_answer(item, answer, fanout_key=B.FANOUT_HVAC_KEY)
        results.append(dict(id=item['id'], phase=item['mode'], q=item['q'], verdict=verdict,
                            detail=detail, answer=(answer or '')[:6000]))
        print(f"  {item['id']}: {verdict} — {detail}")

    write_report(results, started, partial=bool(only))
    fails = sum(1 for r in results if r['verdict'] == 'FAIL')
    return 1 if fails else 0


def write_report(results, started, partial=False):
    date = started.strftime('%Y-%m-%d')
    suffix = '_partial' if partial else ''
    path = os.path.join(HERE, f'TEST_RUN_{date}{suffix}.md')
    counts = {}
    for r in results:
        counts[r['verdict']] = counts.get(r['verdict'], 0) + 1
    lines = [
        f'# Pack 13 run — {started.strftime("%Y-%m-%d %H:%M")}',
        '',
        '**Totals:** ' + ' · '.join(f'{k} {v}' for k, v in sorted(counts.items())),
        '',
        '| id | verdict | detail |',
        '|---|---|---|',
    ]
    for r in results:
        lines.append(f"| {r['id']} | {r['verdict']} | {r['detail'].replace('|', '/')} |")
    lines.append('')
    lines.append('## Answers (Phase B/C)')
    for r in results:
        if r['phase'] != 'A':
            lines.append(f"\n### {r['id']} — {r['verdict']}\n**Q:** {r['q']}\n\n**A:** {r['answer']}\n")
    open(path, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    print(f'\nreport: {path}')


if __name__ == '__main__':
    sys.exit(main())
