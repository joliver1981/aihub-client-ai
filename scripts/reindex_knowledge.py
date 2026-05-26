"""
Reindex every active knowledge document through the new chunking pipeline.

Run this once after deploying Phases 0-3 (vector_engine.index() return-value
fix + 1024-token cap + LLM table-aware split + parent-child retrieval). It
re-queues every active row in AgentKnowledge through queue_knowledge_indexing,
which uses the same code path as a fresh upload so chunks are rebuilt under
the new cap and existing-but-broken indexings are repaired.

Usage:
    & "$env:USERPROFILE\miniconda3\envs\aihub2.1\python.exe" scripts\reindex_knowledge.py
    # optional flags:
    & "...\python.exe" scripts\reindex_knowledge.py --dry-run
    & "...\python.exe" scripts\reindex_knowledge.py --agent-id 42
    & "...\python.exe" scripts\reindex_knowledge.py --limit 10

The script queues jobs to the in-process indexing worker, so it must be run
from the same machine as the main app (it shares the worker thread). Run it
once after a restart on the new code — completed jobs will write
"Indexed N chunks…" log lines on success or "FAILED to index N chunks…" on
failure (Phase 0 fix), so tail the app log to track progress.
"""

import argparse
import logging
import os
import sys
import time

# Make sibling modules importable when invoked as `python scripts/reindex_knowledge.py`.
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, '.env'))

# Lazy import after env load so config.py sees the env vars.
from CommonUtils import get_db_connection  # noqa: E402
from agent_knowledge_integration import (  # noqa: E402
    queue_knowledge_indexing,
    _indexing_queue,
)


def fetch_active_knowledge(agent_id=None, limit=None):
    """Return list of (document_id, agent_id, added_by) for every active row
    in AgentKnowledge, optionally scoped to one agent or capped at `limit`."""
    conn = get_db_connection()
    if not conn:
        raise RuntimeError("DB connection unavailable")
    try:
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        where = ["ak.is_active = 1"]
        params = []
        if agent_id is not None:
            where.append("ak.agent_id = ?")
            params.append(agent_id)
        sql = (
            "SELECT ak.document_id, ak.agent_id, ak.added_by "
            "FROM AgentKnowledge ak "
            "WHERE " + " AND ".join(where) + " "
            "ORDER BY ak.added_date ASC"
        )
        if limit:
            sql = sql.replace("SELECT ", f"SELECT TOP {int(limit)} ")
        cursor.execute(sql, *params)
        return [(row[0], row[1], row[2]) for row in cursor.fetchall()]
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true',
                        help='List documents that would be re-queued, but do nothing.')
    parser.add_argument('--agent-id', type=int, default=None,
                        help='Only reindex documents for this agent_id.')
    parser.add_argument('--limit', type=int, default=None,
                        help='Maximum number of documents to re-queue.')
    parser.add_argument('--throttle-ms', type=int, default=50,
                        help='Sleep this many milliseconds between enqueues '
                             '(default 50). Stops the queue from spiking before '
                             'the worker drains it.')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
    )

    rows = fetch_active_knowledge(agent_id=args.agent_id, limit=args.limit)
    if not rows:
        print("No active knowledge documents to reindex.")
        return 0

    print(f"Found {len(rows)} active knowledge document(s) to reindex.")
    if args.dry_run:
        for doc_id, agent_id, added_by in rows:
            print(f"  [dry-run] document_id={doc_id} agent={agent_id} user={added_by or 'SHARED'}")
        return 0

    queued = 0
    for doc_id, agent_id, added_by in rows:
        try:
            queue_knowledge_indexing(
                document_id=str(doc_id),
                agent_id=agent_id,
                user_id=str(added_by) if added_by else None,
            )
            queued += 1
            if args.throttle_ms:
                time.sleep(args.throttle_ms / 1000.0)
        except Exception as e:
            logging.error(f"Failed to queue document {doc_id}: {e}")

    print(f"Queued {queued}/{len(rows)} documents for reindexing.")
    print("Worker thread is running in this process. Waiting for queue to drain "
          "(each document logs 'Indexed N chunks…' on success, 'FAILED to index "
          "N chunks…' on failure). Press Ctrl+C to abort and leave remaining "
          "jobs un-processed.")

    # Poll the queue until empty, printing progress every 10s.
    last = time.time()
    started_at = time.time()
    while True:
        pending = _indexing_queue.qsize()
        if pending == 0 and _indexing_queue.unfinished_tasks == 0:
            break
        if time.time() - last >= 10:
            print(f"  [progress] {pending} pending, "
                  f"{_indexing_queue.unfinished_tasks} in-flight or queued, "
                  f"{int(time.time() - started_at)}s elapsed")
            last = time.time()
        time.sleep(1)

    print(f"All {queued} document(s) processed in "
          f"{int(time.time() - started_at)}s.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
