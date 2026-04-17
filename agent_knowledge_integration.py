import logging
import os
import requests
import json
import threading
import queue
from langchain.tools import tool
import config as cfg
from CommonUtils import get_db_connection, get_db_connection_string, get_app_path
from agent_knowledge_routes import process_document_as_knowledge
from typing import Optional, List, Dict


def _skr_trace(msg):
    """Write SKR trace to a dedicated file for debugging routing decisions.
    Controlled by cfg.KNOWLEDGE_ENABLE_TRACE (default False)."""
    try:
        if not getattr(cfg, 'KNOWLEDGE_ENABLE_TRACE', False):
            return
        import datetime
        trace_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', 'skr_trace.txt')
        with open(trace_path, 'a', encoding='utf-8') as f:
            f.write(f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [SKR] {msg}\n")
    except:
        pass


# ===== Background indexing queue (single worker, unbounded, always processes) =====
_indexing_queue = queue.Queue()  # Unbounded — every document gets indexed, no exceptions
_indexing_worker_started = False
_indexing_worker_lock = threading.Lock()


def _indexing_worker():
    """
    Single background worker that processes indexing jobs sequentially.
    Uses ChromaDBStore (same as existing document vector pipeline) for stability.
    """
    while True:
        try:
            job = _indexing_queue.get(block=True)
            if job is None:
                break
            action, doc_id, agent_id, user_id = job
            try:
                if action == 'index':
                    index_knowledge_document(
                        document_id=doc_id,
                        agent_id=agent_id,
                        user_id=user_id
                    )
                elif action == 'delete':
                    _do_vector_delete(doc_id)
            except Exception as e:
                logging.warning(f"Knowledge vector {action} failed for {doc_id} (non-fatal): {e}")
            finally:
                _indexing_queue.task_done()
        except Exception as e:
            logging.error(f"Indexing worker error: {e}")


def _do_vector_delete(document_id: str):
    """Remove vectors for a document — only called from the worker thread."""
    try:
        vector_engine = _get_knowledge_vector_engine()
        if not vector_engine:
            return
        vector_engine.delete(document_id)
    except Exception as e:
        logging.error(f"Error removing knowledge vectors for {document_id}: {e}")


def _ensure_worker_started():
    """Start the background worker thread if not already running."""
    global _indexing_worker_started
    if _indexing_worker_started:
        return
    with _indexing_worker_lock:
        if _indexing_worker_started:
            return
        t = threading.Thread(target=_indexing_worker, daemon=True, name="knowledge-indexer")
        t.start()
        _indexing_worker_started = True
        logging.info("Knowledge indexing background worker started")


def queue_knowledge_indexing(document_id: str, agent_id, user_id=None):
    """
    Queue a document for background vector indexing.
    Unbounded queue — every document will be indexed, it just waits its turn.
    The document is already saved in the DB before this is called.
    """
    _ensure_worker_started()
    _indexing_queue.put(('index', document_id, agent_id, user_id))
    pending = _indexing_queue.qsize()
    logging.info(f"Queued knowledge indexing for doc {document_id} ({pending} pending)")


def queue_knowledge_vector_delete(document_id: str):
    """
    Queue removal of vectors for a deleted knowledge document.
    Routed through the same single worker to avoid concurrent ChromaDB access.
    """
    _ensure_worker_started()
    _indexing_queue.put(('delete', document_id, None, None))
    logging.info(f"Queued vector deletion for doc {document_id}")


def _format_knowledge_response(document_contents, apply_caps=True):
    """Format document contents into a response string. When apply_caps=True, enforces size limits to prevent LLM overflow."""
    MAX_TOTAL_CHARS = 400_000  # ~100K tokens — safe for Claude's context window
    MAX_CHARS_PER_PAGE = 50_000  # ~12.5K tokens per page
    
    response_parts = []
    total_chars = 0
    truncated_notice = False
    
    for doc_id, content in document_contents.items():
        filename = content['filename']
        doc_type = content['document_type']
        
        doc_header = f"Document: {filename}"
        if doc_type:
            doc_header += f" (Type: {doc_type})"
        
        response_parts.append(doc_header)
        total_chars += len(doc_header)
        
        for page_num in sorted(content['pages'].keys()):
            if apply_caps and total_chars >= MAX_TOTAL_CHARS:
                if not truncated_notice:
                    response_parts.append(
                        f"\n[... remaining pages omitted — document context capped at ~{MAX_TOTAL_CHARS:,} chars "
                        f"to fit within model limits. Ask about specific sections for more detail.]"
                    )
                    truncated_notice = True
                break
            
            page_text = content['pages'][page_num]
            
            if apply_caps and len(page_text) > MAX_CHARS_PER_PAGE:
                page_text = page_text[:MAX_CHARS_PER_PAGE] + f"\n... [page {page_num} truncated at {MAX_CHARS_PER_PAGE:,} chars]"
            
            response_parts.append(f"Page {page_num}:\n{page_text}\n")
            total_chars += len(page_text)
        
        if truncated_notice:
            break
    
    return "\n\n".join(response_parts)


# ===== Knowledge Vector Store (separate collection from system documents) =====

_knowledge_vector_engine = None
_knowledge_vector_lock = threading.Lock()


def _get_knowledge_vector_engine():
    """Get or initialize the knowledge-specific vector engine (lazy singleton)."""
    global _knowledge_vector_engine
    if _knowledge_vector_engine is not None:
        return _knowledge_vector_engine
    
    with _knowledge_vector_lock:
        if _knowledge_vector_engine is not None:
            return _knowledge_vector_engine
        
        try:
            # Use the existing Vector API service (runs in aihubvector2 env, port 5031)
            # which has working ChromaDB/hnswlib + OpenAI embeddings.
            # Knowledge endpoints: /knowledge/index, /knowledge/search, /knowledge/delete
            host_port = int(os.getenv('HOST_PORT', 5001))
            vector_api_port = host_port + 30  # Same convention as wsgi_vector_api.py
            vector_api_url = f"http://127.0.0.1:{vector_api_port}"
            
            class _KnowledgeVectorClient:
                """HTTP client to Vector API's knowledge endpoints."""
                def __init__(self, base_url):
                    self.base_url = base_url
                    
                def index(self, documents, metadatas, ids):
                    """Index chunks via POST /knowledge/index."""
                    r = requests.post(
                        f"{self.base_url}/knowledge/index",
                        json={'documents': documents, 'metadatas': metadatas, 'ids': ids},
                        timeout=120
                    )
                    if r.status_code == 200:
                        data = r.json()
                        logging.info(f"Knowledge indexed: {data.get('count', 0)} chunks, total={data.get('collection_total', '?')}")
                        return True
                    else:
                        logging.warning(f"Knowledge index failed: {r.status_code} - {r.text[:300]}")
                        return False
                    
                def search(self, query, filters=None, limit=10):
                    """Search via POST /knowledge/search. Returns normalized list of dicts."""
                    r = requests.post(
                        f"{self.base_url}/knowledge/search",
                        json={'query': query, 'filters': filters, 'limit': limit},
                        timeout=30
                    )
                    if r.status_code == 200:
                        data = r.json()
                        # Normalize ChromaDB-format results to list of dicts
                        results_raw = data.get('results', {})
                        docs = results_raw.get('documents', [[]])[0] if results_raw.get('documents') else []
                        metas = results_raw.get('metadatas', [[]])[0] if results_raw.get('metadatas') else []
                        dists = results_raw.get('distances', [[]])[0] if results_raw.get('distances') else []
                        return [
                            {'text': docs[i], 'metadata': metas[i] if i < len(metas) else {}, 'score': dists[i] if i < len(dists) else 0}
                            for i in range(len(docs))
                        ]
                    return []
                    
                def delete(self, document_id):
                    """Delete by document_id via POST /knowledge/delete."""
                    r = requests.post(
                        f"{self.base_url}/knowledge/delete",
                        json={'document_id': document_id},
                        timeout=30
                    )
                    if r.status_code == 200:
                        data = r.json()
                        logging.info(f"Knowledge delete: {data.get('deleted', 0)} chunks removed for {document_id}")
                        return True
                    logging.warning(f"Knowledge delete failed: {r.status_code} - {r.text[:200]}")
                    return False
            
            client = _KnowledgeVectorClient(vector_api_url)
            
            # Health check
            r = requests.get(f"{vector_api_url}/health", timeout=5)
            if r.status_code != 200:
                raise ConnectionError(f"Vector API not available at {vector_api_url}")
            
            _knowledge_vector_engine = client
            logging.info(f"Knowledge vector engine initialized via Vector API at {vector_api_url}")
            return _knowledge_vector_engine
        except Exception as e:
            logging.error(f"Failed to initialize knowledge vector engine: {e}")
            return None


def generate_knowledge_summary(document_id: str, filename: str, document_type: str, pages_text: List[str]) -> str:
    """
    Generate a structured summary of a knowledge document for aggregate queries.
    Uses a cheap LLM call to extract key facts into a compact index card.
    
    Returns:
        Structured summary string (~200-500 chars)
    """
    if not cfg.KNOWLEDGE_ENABLE_SUMMARIES:
        return ""
    
    try:
        # Take first ~5000 chars for summarization (enough to understand the doc)
        sample_text = ""
        for page in pages_text:
            sample_text += page + "\n\n"
            if len(sample_text) > 5000:
                break
        sample_text = sample_text[:5000]
        
        from api_keys_config import create_anthropic_client
        client, anthropic_config = create_anthropic_client()
        
        summary_system = (
            "You are a document indexer. Create a structured summary of this document in 2-4 lines. "
            "Include: document title/subject, key entities (people, companies, dates), "
            "key facts/numbers, and the main topics covered. "
            "Format: one line per category, pipe-separated values. Keep under 500 characters total. "
            "Do NOT include any preamble or explanation — just the structured summary."
        )
        summary_user = f"Document: {filename} (type: {document_type})\n\n{sample_text}"
        
        if client is not None:
            response = client.messages.create(
                model=cfg.ANTHROPIC_MODEL,
                max_tokens=512,
                system=summary_system,
                messages=[{"role": "user", "content": summary_user}]
            )
            summary = response.content[0].text.strip()
        else:
            # Use proxy client
            from CommonUtils import AnthropicProxyClient
            proxy = AnthropicProxyClient()
            proxy._set_tracking_params('knowledge_summary')
            response = proxy.messages_create(
                model=cfg.ANTHROPIC_MODEL,
                max_tokens=512,
                system=summary_system,
                messages=[{"role": "user", "content": summary_user}]
            )
            if isinstance(response, dict) and 'content' in response:
                summary = response['content'][0]['text'].strip()
            else:
                raise ValueError(f"Proxy summary error: {str(response)[:200]}")
        
        # Enforce max chars
        if len(summary) > cfg.KNOWLEDGE_SUMMARY_MAX_CHARS:
            summary = summary[:cfg.KNOWLEDGE_SUMMARY_MAX_CHARS]
        
        logging.info(f"Generated knowledge summary for {filename}: {len(summary)} chars")
        return summary
        
    except Exception as e:
        logging.warning(f"Failed to generate knowledge summary for {filename}: {e}")
        return ""


def store_knowledge_summary(document_id: str, summary: str):
    """Store the structured summary in the database."""
    if not summary:
        return
    try:
        conn = get_db_connection()
        if not conn:
            return
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Store in Documents table metadata column (JSON)
        cursor.execute("""
            UPDATE Documents 
            SET document_metadata = JSON_MODIFY(
                COALESCE(document_metadata, '{}'), 
                '$.knowledge_summary', 
                ?
            )
            WHERE document_id = ?
        """, summary, document_id)
        conn.commit()
        conn.close()
        logging.info(f"Stored knowledge summary for document {document_id}")
    except Exception as e:
        logging.warning(f"Failed to store knowledge summary: {e}")


def get_all_knowledge_summaries(agent_id: int, user_id: str = None) -> List[Dict]:
    """
    Retrieve all knowledge summaries for an agent/user combination.
    Used for aggregate queries where the LLM needs to scan all documents.
    
    Returns:
        List of dicts with 'document_id', 'filename', 'summary' keys
    """
    try:
        conn = get_db_connection()
        if not conn:
            return []
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        query = """
            SELECT d.document_id, d.filename, d.document_type, d.document_metadata
            FROM Documents d
            JOIN AgentKnowledge ak ON d.document_id = ak.document_id
            WHERE ak.agent_id = ? AND ak.is_active = 1
        """
        params = [agent_id]
        
        if user_id:
            query += " AND ak.added_by = ?"
            params.append(str(user_id))
        
        cursor.execute(query, params)
        
        summaries = []
        for doc_id, filename, doc_type, metadata_json in cursor.fetchall():
            summary = ""
            if metadata_json:
                try:
                    meta = json.loads(metadata_json)
                    summary = meta.get('knowledge_summary', '')
                except (json.JSONDecodeError, TypeError):
                    pass
            
            summaries.append({
                'document_id': doc_id,
                'filename': filename,
                'document_type': doc_type or 'unknown',
                'summary': summary
            })
        
        conn.close()
        return summaries
        
    except Exception as e:
        logging.error(f"Error getting knowledge summaries: {e}")
        return []


def index_knowledge_document(document_id: str, agent_id: int, user_id: str = None):
    """
    Index a knowledge document's pages into the agent_knowledge vector collection.
    Also generates and stores a structured summary for aggregate queries.
    Called after a document is processed and linked to an agent.
    
    Args:
        document_id: The document ID in the Documents table
        agent_id: The agent this document belongs to
        user_id: The user who uploaded it (for isolation)
    """
    try:
        vector_engine = _get_knowledge_vector_engine()
        if not vector_engine:
            logging.warning("Knowledge vector engine not available — skipping indexing")
            return False
        
        # Fetch pages from SQL
        conn = get_db_connection()
        if not conn:
            logging.warning("DB connection unavailable for knowledge indexing")
            return False
        
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        cursor.execute("""
            SELECT dp.page_id, dp.page_number, dp.full_text, d.filename, d.document_type
            FROM DocumentPages dp
            JOIN Documents d ON dp.document_id = d.document_id
            WHERE dp.document_id = ?
            ORDER BY dp.page_number
        """, document_id)
        
        pages = cursor.fetchall()
        conn.close()
        
        if not pages:
            logging.info(f"No pages found for document {document_id} — skipping vector indexing")
            return False
        
        # Generate and store structured summary (for aggregate queries)
        try:
            pages_text = [row[2] for row in pages if row[2]]  # full_text column
            filename = pages[0][3] if pages else 'unknown'
            doc_type = pages[0][4] if pages else 'unknown'
            summary = generate_knowledge_summary(document_id, filename, doc_type, pages_text)
            if summary:
                store_knowledge_summary(document_id, summary)
        except Exception as sum_err:
            logging.warning(f"Summary generation failed (non-fatal): {sum_err}")
        
        # Prepare documents for vector storage
        documents = []
        metadatas = []
        ids = []
        
        for page_id, page_number, full_text, filename, doc_type in pages:
            if not full_text or not full_text.strip():
                continue
            
            # Chunk large pages
            chunk_size = cfg.VECTOR_CHUNK_SIZE if hasattr(cfg, 'VECTOR_CHUNK_SIZE') else 512
            text = full_text.strip()
            
            if len(text) <= chunk_size * 4:  # Small enough for a single chunk
                documents.append(text)
                metadatas.append({
                    'document_id': document_id,
                    'page_id': page_id,
                    'page_number': page_number,
                    'filename': filename,
                    'document_type': doc_type or 'unknown',
                    'agent_id': str(agent_id),
                    'user_id': str(user_id) if user_id else '',
                    'is_knowledge': 'true',
                })
                ids.append(f"kb_{page_id}")
            else:
                # Split into chunks at paragraph/line boundaries
                chunks = _split_text_into_chunks(text, chunk_size)
                for i, chunk in enumerate(chunks):
                    documents.append(chunk)
                    metadatas.append({
                        'document_id': document_id,
                        'page_id': page_id,
                        'page_number': page_number,
                        'chunk_index': i,
                        'filename': filename,
                        'document_type': doc_type or 'unknown',
                        'agent_id': str(agent_id),
                        'user_id': str(user_id) if user_id else '',
                        'is_knowledge': 'true',
                    })
                    ids.append(f"kb_{page_id}_c{i}")
        
        if documents:
            vector_engine.index(documents=documents, metadatas=metadatas, ids=ids)
            logging.info(f"Indexed {len(documents)} chunks for knowledge document {document_id} (agent={agent_id}, user={user_id})")
            return True
        
        return False
        
    except Exception as e:
        logging.error(f"Error indexing knowledge document {document_id}: {e}")
        return False


def _normalize_search_query(query: str, max_chars: int = 200) -> str:
    """
    Normalize a vector search query to prevent embedding degradation from
    redundant/repeated terms (agents sometimes repeat search terms).
    Deduplicates phrases and caps length.
    """
    # Deduplicate words while preserving order
    words = query.split()
    seen = set()
    deduped = []
    for w in words:
        w_lower = w.lower().strip('.,!?')
        if w_lower not in seen:
            seen.add(w_lower)
            deduped.append(w)
    clean = ' '.join(deduped)
    # Cap at max_chars (embedding quality degrades with very long inputs)
    return clean[:max_chars]


def search_knowledge_vectors(query: str, agent_id: int, user_id: str = None, top_k: int = None) -> List[Dict]:
    """
    Search the knowledge vector collection for chunks relevant to the query.
    Filters by agent_id and user_id for isolation.
    
    Returns:
        List of dicts with 'text', 'metadata', 'score' keys
    """
    try:
        vector_engine = _get_knowledge_vector_engine()
        if not vector_engine:
            return []
        
        if top_k is None:
            top_k = cfg.KNOWLEDGE_VECTOR_TOP_K
        
        # Normalize query — deduplicate repeated terms before embedding
        query = _normalize_search_query(query)
        
        # Build filters for user isolation — BOTH agent_id AND user_id required
        # This function is ONLY used for user-specific knowledge searches.
        # If user_id is missing, refuse to search rather than leaking cross-user data.
        if not user_id:
            logging.warning(f"search_knowledge_vectors called without user_id for agent {agent_id} — returning empty to prevent data leakage")
            return []
        
        filters = {
            '$and': [
                {'agent_id': str(agent_id)},
                {'user_id': str(user_id)}
            ]
        }
        
        raw_results = vector_engine.search(
            query=query,
            filters=filters,
            limit=top_k
        )
        
        # Normalize ChromaDB results into list of dicts
        results = []
        if raw_results and isinstance(raw_results, dict) and 'documents' in raw_results:
            docs = raw_results.get('documents', [[]])[0] if raw_results.get('documents') else []
            metas = raw_results.get('metadatas', [[]])[0] if raw_results.get('metadatas') else []
            dists = raw_results.get('distances', [[]])[0] if raw_results.get('distances') else []
            for i, doc in enumerate(docs):
                results.append({
                    'text': doc,
                    'metadata': metas[i] if i < len(metas) else {},
                    'score': dists[i] if i < len(dists) else 0
                })
        elif isinstance(raw_results, list):
            results = raw_results
        
        return results
        
    except Exception as e:
        logging.error(f"Error searching knowledge vectors: {e}")
        return []


def remove_knowledge_document_vectors(document_id: str):
    """Remove all vector chunks for a knowledge document (called on delete).
    Routes through the worker queue to avoid concurrent ChromaDB access."""
    try:
        queue_knowledge_vector_delete(document_id)
    except Exception as e:
        logging.error(f"Error queuing knowledge vector removal for {document_id}: {e}")


def route_knowledge_query(query: str, doc_count: int, total_chars: int) -> str:
    """
    Use a cheap LLM call to classify the query as NEEDLE or AGGREGATE.
    No keyword heuristics — pure LLM intelligence.
    
    Returns:
        'NEEDLE' or 'AGGREGATE'
    """
    system_prompt = (
        "Classify this knowledge base query into exactly one category. Respond with ONLY the word NEEDLE or AGGREGATE.\n\n"
        "NEEDLE: Finding specific information that likely exists in ONE document or a few. "
        "Use when: looking for a specific clause, fact, person, date, or provision. "
        "Examples: 'which lease allows hazardous materials', 'does any lease mention solar panels', "
        "'find the termination clause', 'what was Q1 revenue', 'who is the CEO'.\n\n"
        "AGGREGATE: Counting, comparing, or summarizing ACROSS MANY documents. "
        "Use when: the answer requires looking at all documents together. "
        "Examples: 'how many leases assign HVAC to tenant vs landlord', "
        "'what is the range of rents across all leases', 'list all tenants', "
        "'what percentage have X', 'compare all contracts', 'breakdown by category'.\n\n"
        "Key rule: If the user asks 'does any lease have X' or 'which lease has X' — that is NEEDLE. "
        "If the user asks 'how many leases have X' or 'what is the range/average/count' — that is AGGREGATE."
    )
    user_msg = f"Documents: {doc_count} documents, {total_chars:,} total characters.\nQuery: {query}"
    
    try:
        # Try direct Anthropic client first
        from api_keys_config import create_anthropic_client
        client, anthropic_config = create_anthropic_client()
        
        if client is not None:
            response = client.messages.create(
                model=cfg.ANTHROPIC_MODEL,
                max_tokens=20,
                system=system_prompt,
                messages=[{"role": "user", "content": user_msg}]
            )
            result = response.content[0].text.strip().upper()
        else:
            # Use proxy client (standard path when BYOK is not configured)
            from CommonUtils import AnthropicProxyClient
            proxy = AnthropicProxyClient()
            proxy._set_tracking_params('knowledge_router')
            response = proxy.messages_create(
                model=cfg.ANTHROPIC_MODEL,
                max_tokens=20,
                system=system_prompt,
                messages=[{"role": "user", "content": user_msg}]
            )
            # Proxy returns JSON dict, not SDK object
            if isinstance(response, dict) and 'content' in response:
                result = response['content'][0]['text'].strip().upper()
            elif isinstance(response, dict) and 'error' in response:
                raise ValueError(f"Proxy error: {response['error']}")
            else:
                raise ValueError(f"Unexpected proxy response: {str(response)[:200]}")
        
        route = 'AGGREGATE' if 'AGGREGATE' in result else 'NEEDLE'
        _skr_trace(f"Router LLM response: '{result}' -> {route}")
        logging.info(f"Knowledge query routed as {route}: '{query[:80]}...' ({doc_count} docs, {total_chars:,} chars)")
        return route
        
    except Exception as e:
        _skr_trace(f"Router FAILED: {e} — defaulting to NEEDLE")
        logging.warning(f"Query routing failed, defaulting to NEEDLE: {e}")
        return 'NEEDLE'


def smart_knowledge_retrieval(query: str, agent_id: int, user_id: str = None, 
                               document_contents: dict = None, documents: list = None) -> str:
    """
    Smart retrieval path for when brute force won't fit in context.
    Routes between NEEDLE (vector search) and AGGREGATE (summary scan) strategies.
    """
    # Calculate stats
    total_chars = sum(
        len(page_text)
        for content in document_contents.values()
        for page_text in content['pages'].values()
    ) if document_contents else 0
    
    doc_count = len(document_contents) if document_contents else 0
    
    # Route the query
    route = route_knowledge_query(query, doc_count, total_chars)
    _skr_trace(f"Query routed as {route}: '{query[:80]}'")
    
    if route == 'NEEDLE':
        # Vector search for specific information
        results = search_knowledge_vectors(query, agent_id, user_id)
        _skr_trace(f"Vector search returned {len(results)} results")
        
        if results:
            # Format vector search results
            response_parts = [f"[Knowledge search: found {len(results)} relevant chunks for '{query}']\n"]
            for r in results:
                if isinstance(r, dict):
                    meta = r.get('metadata', {})
                    text = r.get('document', r.get('text', ''))
                    filename = meta.get('filename', 'unknown')
                    page = meta.get('page_number', '?')
                    doc_id = meta.get('document_id', '')
                    response_parts.append(f"--- {filename} (page {page}) ---\n{text}\n")
                    
                    # Add knowledge reference for tools
                    if documents:
                        for doc in documents:
                            if doc.get('document_id') == doc_id:
                                response_parts.append(
                                    f"[Knowledge Reference Info: document_id={doc_id} | "
                                    f"page_number={page} | knowledge_id={doc.get('knowledge_id')} | "
                                    f"agent_id={agent_id}]"
                                )
                                break
            
            return "\n".join(response_parts)
        else:
            # Fallback to capped brute force if vector search returns nothing
            _skr_trace("FALLBACK: Vector search empty -> CAPPED BRUTE FORCE")
            logging.info("Vector search returned no results, falling back to capped brute force")
            return _format_knowledge_response(document_contents, apply_caps=True)
    
    else:  # AGGREGATE
        # Load all summaries for aggregate scan
        summaries = get_all_knowledge_summaries(agent_id, user_id)
        
        if summaries and any(s.get('summary') for s in summaries):
            # Build summary context for the LLM
            response_parts = [
                f"[Knowledge overview: {len(summaries)} documents. "
                f"Showing structured summaries for aggregate analysis. "
                f"Ask about a specific document for full text.]\n"
            ]
            
            for s in summaries:
                summary = s.get('summary', '')
                if summary:
                    response_parts.append(
                        f"📄 {s['filename']} ({s['document_type']})\n{summary}\n"
                    )
                else:
                    response_parts.append(
                        f"📄 {s['filename']} ({s['document_type']}) — [no summary available]\n"
                    )
            
            return "\n".join(response_parts)
        else:
            # No summaries — use wider vector search to get representative chunks from many docs
            _skr_trace("AGGREGATE without summaries: using wide vector search (top-30)")
            wide_results = search_knowledge_vectors(query, agent_id, user_id, top_k=30)
            
            if wide_results:
                # Group by document to show breadth
                seen_docs = {}
                response_parts = [
                    f"[Aggregate knowledge search: found {len(wide_results)} relevant chunks "
                    f"across {doc_count} documents for '{query}']\n"
                ]
                for r in wide_results:
                    if isinstance(r, dict):
                        meta = r.get('metadata', {})
                        text = r.get('text', '')
                        filename = meta.get('filename', 'unknown')
                        doc_id = meta.get('document_id', '')
                        page = meta.get('page_number', '?')
                        
                        # Include up to 2 chunks per doc for diversity
                        doc_count_for_this = seen_docs.get(doc_id, 0)
                        if doc_count_for_this < 2:
                            response_parts.append(f"--- {filename} (page {page}) ---\n{text}\n")
                            seen_docs[doc_id] = doc_count_for_this + 1
                            
                            if documents:
                                for doc in documents:
                                    if doc.get('document_id') == doc_id:
                                        response_parts.append(
                                            f"[Knowledge Reference Info: document_id={doc_id} | "
                                            f"page_number={page} | knowledge_id={doc.get('knowledge_id')} | "
                                            f"agent_id={agent_id}]"
                                        )
                                        break
                
                _skr_trace(f"AGGREGATE wide search: {len(seen_docs)} unique docs in results")
                return "\n".join(response_parts)
            else:
                # Final fallback
                _skr_trace("FALLBACK: No summaries + no vector results -> CAPPED BRUTE FORCE")
                logging.info("No summaries available for aggregate query, falling back to capped brute force")
                return _format_knowledge_response(document_contents, apply_caps=True)


def _split_text_into_chunks(text: str, chunk_size: int = 512) -> List[str]:
    """
    Split text into chunks using the same TextChunker used by the document vector pipeline.
    Uses LLM-powered smart chunking when VECTOR_USE_SMART_CHUNKING is enabled,
    otherwise falls back to RecursiveCharacterTextSplitter.
    """
    try:
        from TextChunker_LLM import TextChunker
        # Use smaller chunk size for knowledge docs — better embedding precision
        knowledge_chunk_size = min(chunk_size * 2, 1500)
        chunker = TextChunker(
            chunk_size=knowledge_chunk_size,
            chunk_overlap=100,
        )
        chunk_results = chunker.chunk_text(text, metadata={})
        # chunk_text returns list of dicts with 'text' key
        chunks = [c['text'] for c in chunk_results if c.get('text', '').strip()]
        return chunks if chunks else [text]
    except Exception as e:
        logging.warning(f"TextChunker failed, falling back to simple split: {e}")
        # Simple fallback — split at double newlines
        max_chars = chunk_size * 3
        chunks = []
        current = []
        current_size = 0
        for para in text.split('\n\n'):
            para = para.strip()
            if not para:
                continue
            if current_size + len(para) > max_chars and current:
                chunks.append('\n\n'.join(current))
                current = []
                current_size = 0
            current.append(para)
            current_size += len(para)
        if current:
            chunks.append('\n\n'.join(current))
        return chunks if chunks else [text]


class KnowledgeTool:
    """Class to create knowledge retrieval tools for agents"""
    
    def __init__(self, agent_id, user_id=None):
        """Initialize knowledge tool with agent ID"""
        self.agent_id = agent_id
        self.user_id = user_id
        self.doc_api_base_url = self._get_doc_api_url()
        logging.info(f"Initialized knowledge tool for agent {agent_id}")
        
    def _get_doc_api_url(self):
        """Get document API base URL"""
        # Try to import from CommonUtils
        try:
            from CommonUtils import get_document_api_base_url
            return get_document_api_base_url()
        except ImportError:
            # Fallback method if function not available
            import socket
            
            def get_local_ip():
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
                s.close()
                return ip
                
            # Get the protocol from environment or default to http
            protocol = os.getenv('PROTOCOL', 'http')
            
            # Get host from environment
            host = os.getenv('HOST', 'localhost')
            if host == "0.0.0.0":
                host = get_local_ip()
            
            # Calculate document API port by adding 10 to the current port
            try:
                current_port = int(os.getenv('HOST_PORT', '3001'))
                document_port = current_port + 10
            except ValueError:
                # Fallback to default port if HOST_PORT is not a valid integer
                document_port = 3011
            
            # Construct the base URL
            base_url = f"{protocol}://{host}:{document_port}"
            
            return base_url
        
    def get_knowledge_management_tool(self):
        """Create a knowledge management tool for the agent"""

        @tool
        def manage_agent_knowledge(
            action: str,
            content: str,
            description: str,
            filename: Optional[str] = None,
            knowledge_id: Optional[int] = None,
            document_id: Optional[str] = None,
            page_number: Optional[int] = None
        ) -> str:
            """
            Allows an agent to add or update their own knowledge base / memory. Use this tool whenever a user asks you to "remember," "remind," or "always" do something, including both factual knowledge and user instructions or preferences that affect agent behavior.
            
            This tool enables agents to:
            - Add new knowledge from text content or conversation insights
            - Add/persist "memory" when users ask you to remember something
            - Update existing knowledge (The required ids and page numbers can be obtained from 'Knowledge Reference Info' at the bottom of each knowledge page)
            - Create knowledge documents from important information discovered during conversations

            IMPORTANT:
            ----------
            The text for the entire page must be provided when updating knowledge, not just excerpts from each page. The is important because the entire page will be overwritten with the content provided.
            
            **When to use:**  
            - Any time a user asks you to "remember," "remind," "always," or otherwise persist a fact, instruction, or preference for future reference.

            **If unsure:**  
            - If you are unsure whether a user’s request should be stored as knowledge, confirm with the user before proceeding.

            Parameters:
            -----------
            action : str
                The action to perform - 'add' for new knowledge or 'update' for existing
            content : str
                For 'add': The text content to save as knowledge
                For 'update': The new text content that will overwrite the existing knowledge text
            description : str
                Description of what this knowledge contains
            filename : str, optional
                For 'add': Name for the knowledge file (without extension). If not provided, auto-generates based on timestamp
            knowledge_id : int, optional
                For 'update': The ID of the knowledge entry to update (required for update action)
            document_id : int, optional
                For 'update': The ID of the document to update (required for update action)
            page_number : int, optional
                For 'update': The page number of the knowledge entry to update (required for update action)
            
            Returns:
            --------
            str
                Success/failure message with details
                
            Examples:
            ---------
            # Add new knowledge
            manage_agent_knowledge(
                action="add",
                content="Company policy states that all invoices must be approved within 48 hours...",
                description="Company invoice approval policy and procedures",
                filename="invoice_approval_policy"
            )
            
            # Update existing knowledge description
            manage_agent_knowledge(
                action="update", 
                content="Company policy states that all invoices must be approved within 24 hours with no exceptions.",
                description="Updated: Company invoice approval policy including 2025 changes",
                knowledge_id=123,
                document_id="abd-12344-defg-98765"
                page_number=2
            )
            """
            import tempfile
            from datetime import datetime
            import pyodbc
            
            try:
                agent_id = self.agent_id
                # Get connection string
                conn_str = get_db_connection_string()
                conn = pyodbc.connect(conn_str)
                cursor = conn.cursor()
                cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
                
                if action.lower() == "add":
                    # Validate inputs
                    if not content or not content.strip():
                        return "Error: Content cannot be empty when adding knowledge"
                    
                    # Generate filename if not provided
                    if not filename:
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        filename = f"agent_knowledge_{timestamp}"
                    
                    # Ensure filename is safe and add .txt extension
                    safe_filename = "".join(c for c in filename if c.isalnum() or c in ('_', '-'))
                    safe_filename = f"{safe_filename}.txt"
                    
                    # Create temporary file with the content
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as tmp_file:
                        tmp_file.write(content)
                        tmp_file_path = tmp_file.name
                    
                    try:
                        # Process file and add as knowledge
                        result = process_document_as_knowledge(
                            file_path=tmp_file_path,
                            agent_id=agent_id,
                            description=description
                        )

                        # Copy the temp file to archive location
                        import shutil
                        if result['status'] == 'success':
                            # Create a temporary archive path
                            archive_folder = get_app_path('agent_knowledge_' + str(agent_id))
                            os.makedirs(archive_folder, exist_ok=True)
                            archive_path = os.path.join(archive_folder, safe_filename)
                            shutil.copy2(tmp_file_path, archive_path)
                            return f"Successfully added knowledge: '{safe_filename}' (Knowledge ID: {result['knowledge_id']}, Document ID: {result['document_id']})"
                        else:
                            # Create a temporary archive path (for failed updates)
                            archive_folder = get_app_path('failed_agent_knowledge_' + str(agent_id))
                            os.makedirs(archive_folder, exist_ok=True)
                            archive_path = os.path.join(archive_folder, safe_filename)
                            shutil.copy2(tmp_file_path, archive_path)
                            return "Error: Failed to process knowledge document"
                    finally:
                        # Clean up temp file
                        if os.path.exists(tmp_file_path):
                            os.unlink(tmp_file_path)
                
                elif action.lower() == "update":
                    # Validate inputs
                    if not knowledge_id:
                        return "Error: knowledge_id is required for update action"
                    
                    if not description or not description.strip():
                        return "Error: Description cannot be empty"
                    
                    # Verify the knowledge entry exists and belongs to this agent
                    cursor.execute("""
                        SELECT agent_id, document_id 
                        FROM AgentKnowledge 
                        WHERE knowledge_id = ? AND is_active = 1
                    """, (knowledge_id,))
                    
                    result = cursor.fetchone()
                    if not result:
                        return f"Error: Knowledge entry {knowledge_id} not found or is inactive"
                    
                    stored_agent_id, doc_id = result
                    
                    # Check if this knowledge belongs to the current agent
                    if stored_agent_id != agent_id:
                        return f"Error: Knowledge entry {knowledge_id} does not belong to current agent"
                    
                    # Update the description (NOT NECESSARY)
                    # cursor.execute("""
                    #     UPDATE AgentKnowledge 
                    #     SET description = ?
                    #     WHERE knowledge_id = ?
                    # """, (description, knowledge_id))

                    # Update main content
                    cursor.execute("""
                    update DocumentPages
                    set full_text = ?
                    where document_id = ? and page_number = ?
                    """, (content, document_id, page_number))
                    
                    conn.commit()
                    
                    return f"Successfully updated knowledge entry {knowledge_id} description"
                
                else:
                    return f"Error: Invalid action '{action}'. Use 'add' or 'update'"
                    
            except Exception as e:
                logging.error(f"Error in manage_agent_knowledge: {str(e)}")
                return f"Error managing agent knowledge: {str(e)}"
            finally:
                if 'conn' in locals():
                    conn.close()

        return manage_agent_knowledge
        
        
    def get_knowledge_tool(self):
        """Create a knowledge search tool for the agent"""
        
        @tool
        def search_agent_knowledge(query: str) -> str:
            """
            Search through documents uploaded to this agent's knowledge base.
            Use this tool when you need to find specific information from documents that were provided to you.
            
            Args:
                query: The search query or question to look for in the agent's knowledge documents
                
            Returns:
                Relevant information from the agent's knowledge documents
            """
            try:
                # Get agent's knowledge documents (filtered by user for isolation)
                documents = get_agent_knowledge_documents(self.agent_id, self.user_id)
                if not documents:
                    return "No knowledge documents available for this agent."
                    
                # Get document IDs
                document_ids = [doc['document_id'] for doc in documents]
                
                # Connect to database to get the full text
                import pyodbc
                conn = pyodbc.connect(
                    f"DRIVER={{SQL Server}};SERVER={cfg.DATABASE_SERVER};DATABASE={cfg.DATABASE_NAME};UID={cfg.DATABASE_UID};PWD={cfg.DATABASE_PWD}"
                )
                cursor = conn.cursor()
                
                # Set tenant context
                cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
                
                # Create placeholders for SQL query
                placeholders = ','.join(['?'] * len(document_ids))
                
                # Get document text content
                sql_query = f"""
                    SELECT d.document_id, d.filename, d.document_type, dp.page_number, dp.full_text
                    FROM Documents d
                    JOIN DocumentPages dp ON d.document_id = dp.document_id
                    WHERE d.document_id IN ({placeholders})
                    ORDER BY d.filename, dp.page_number
                """
                
                cursor.execute(sql_query, document_ids)
                
                # Organize document content
                document_contents = {}
                for doc_id, filename, doc_type, page_num, full_text in cursor.fetchall():
                    if doc_id not in document_contents:
                        document_contents[doc_id] = {
                            'filename': filename,
                            'document_type': doc_type,
                            'pages': {}
                        }
                    
                    document_contents[doc_id]['pages'][page_num] = full_text

                    # Append agent_id and knowledge_id footer to be used for adding/updating knowledge
                    knowledge_referece_text = ''
                    for document in documents:
                        if document['document_id'] == doc_id:
                            temp_knowledge_id = document['knowledge_id']
                            temp_agent_id = document['agent_id']
                            knowledge_referece_text = f'\n\n[Knowledge Reference Info: document_id={doc_id} | page_number={str(page_num)} | knowledge_id={temp_knowledge_id} | agent_id={temp_agent_id}]'
                            document_contents[doc_id]['pages'][page_num] += knowledge_referece_text
                
                # Check if we found any content
                if not document_contents:
                    return "No content found in the agent's knowledge documents."
                
                # Calculate total text size for brute force vs smart retrieval decision
                total_chars = sum(
                    len(page_text) 
                    for content in document_contents.values() 
                    for page_text in content['pages'].values()
                )
                
                _skr_trace(f"Knowledge routing: {total_chars:,} chars, threshold={cfg.KNOWLEDGE_BRUTE_FORCE_MAX_CHARS:,}, smart={cfg.KNOWLEDGE_ENABLE_SMART_RETRIEVAL}")
                if total_chars <= cfg.KNOWLEDGE_BRUTE_FORCE_MAX_CHARS:
                    # Brute force: all text fits in context — dump everything
                    _skr_trace(f"PATH: BRUTE FORCE (total_chars {total_chars:,} <= threshold)")
                    logging.info(f"Knowledge brute force: {total_chars:,} chars ≤ {cfg.KNOWLEDGE_BRUTE_FORCE_MAX_CHARS:,} threshold")
                    return _format_knowledge_response(document_contents, apply_caps=False)
                elif cfg.KNOWLEDGE_ENABLE_SMART_RETRIEVAL:
                    # Smart retrieval: route between vector search and summary scan
                    _skr_trace(f"PATH: SMART RETRIEVAL (total_chars {total_chars:,} > threshold)")
                    logging.info(f"Knowledge smart retrieval: {total_chars:,} chars > {cfg.KNOWLEDGE_BRUTE_FORCE_MAX_CHARS:,} threshold")
                    return smart_knowledge_retrieval(
                        query=query,
                        agent_id=self.agent_id,
                        user_id=self.user_id,
                        document_contents=document_contents,
                        documents=documents
                    )
                else:
                    # Smart retrieval disabled — use capped brute force
                    logging.info(f"Knowledge capped response: {total_chars:,} chars (smart retrieval disabled)")
                    return _format_knowledge_response(document_contents, apply_caps=True)
                
            except Exception as e:
                logging.error(f"Error retrieving agent knowledge: {str(e)}")
                return f"Error accessing knowledge documents: {str(e)}"
            
        return search_agent_knowledge
    
    def get_user_knowledge_tool(self):
        """Create a knowledge search tool for the agent"""
        
        @tool
        def get_user_specific_knowledge() -> str:
            """
            Retrieves text content from user-uploaded documents such as PDFs,
            resumes, Word documents, and other non-Excel files. Use this tool
            when the user asks about information from their uploaded documents
            that are NOT Excel spreadsheets (.xlsx/.xls files).

            Returns:
                The full text content from all user-specific knowledge documents
            """
            try:
                # Get agent's knowledge documents
                documents = get_agent_knowledge_documents(self.agent_id, self.user_id)
                if not documents:
                    return "No knowledge documents available for this agent."
                    
                # Get document IDs
                document_ids = [doc['document_id'] for doc in documents]
                
                # Connect to database to get the full text
                import pyodbc
                conn = pyodbc.connect(
                    f"DRIVER={{SQL Server}};SERVER={cfg.DATABASE_SERVER};DATABASE={cfg.DATABASE_NAME};UID={cfg.DATABASE_UID};PWD={cfg.DATABASE_PWD}"
                )
                cursor = conn.cursor()
                
                # Set tenant context
                cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
                
                # Create placeholders for SQL query
                placeholders = ','.join(['?'] * len(document_ids))
                
                # Get document text content
                sql_query = f"""
                    SELECT d.document_id, d.filename, d.document_type, dp.page_number, dp.full_text
                    FROM Documents d
                    JOIN DocumentPages dp ON d.document_id = dp.document_id
                    WHERE d.document_id IN ({placeholders})
                    ORDER BY d.filename, dp.page_number
                """
                
                cursor.execute(sql_query, document_ids)
                
                # Organize document content
                document_contents = {}
                for doc_id, filename, doc_type, page_num, full_text in cursor.fetchall():
                    if doc_id not in document_contents:
                        document_contents[doc_id] = {
                            'filename': filename,
                            'document_type': doc_type,
                            'pages': {}
                        }
                    
                    document_contents[doc_id]['pages'][page_num] = full_text

                    # Append agent_id and knowledge_id footer to be used for adding/updating knowledge
                    knowledge_referece_text = ''
                    for document in documents:
                        if document['document_id'] == doc_id:
                            temp_knowledge_id = document['knowledge_id']
                            temp_agent_id = document['agent_id']
                            knowledge_referece_text = f'\n\n[Knowledge Reference Info: document_id={doc_id} | page_number={str(page_num)} | knowledge_id={temp_knowledge_id} | agent_id={temp_agent_id}]'
                            document_contents[doc_id]['pages'][page_num] += knowledge_referece_text
                
                # Check if we found any content
                if not document_contents:
                    return "No content found in the agent's knowledge documents."
                
                # Calculate total text size for brute force vs smart retrieval decision
                total_chars = sum(
                    len(page_text) 
                    for content in document_contents.values() 
                    for page_text in content['pages'].values()
                )
                
                if total_chars <= cfg.KNOWLEDGE_BRUTE_FORCE_MAX_CHARS:
                    # Brute force: all text fits in context — dump everything
                    logging.info(f"User knowledge brute force: {total_chars:,} chars ≤ {cfg.KNOWLEDGE_BRUTE_FORCE_MAX_CHARS:,} threshold")
                    return _format_knowledge_response(document_contents, apply_caps=False)
                elif cfg.KNOWLEDGE_ENABLE_SMART_RETRIEVAL:
                    # Smart retrieval: route between vector search and summary scan
                    logging.info(f"User knowledge smart retrieval: {total_chars:,} chars > {cfg.KNOWLEDGE_BRUTE_FORCE_MAX_CHARS:,} threshold")
                    return smart_knowledge_retrieval(
                        query="retrieve user document content",  # User tool doesn't receive a query param
                        agent_id=self.agent_id,
                        user_id=self.user_id,
                        document_contents=document_contents,
                        documents=documents
                    )
                else:
                    # Smart retrieval disabled — use capped brute force
                    logging.info(f"User knowledge capped response: {total_chars:,} chars (smart retrieval disabled)")
                    return _format_knowledge_response(document_contents, apply_caps=True)
                
            except Exception as e:
                logging.error(f"Error retrieving user-specific knowledge: {str(e)}")
                return f"Error accessing user-specific knowledge documents: {str(e)}"
            
        return get_user_specific_knowledge

def get_agent_knowledge_documents(agent_id, user_id=None):
    """Get knowledge documents for an agent"""
    try:
        import pyodbc
            
        conn = pyodbc.connect(
            f"DRIVER={{SQL Server}};SERVER={cfg.DATABASE_SERVER};DATABASE={cfg.DATABASE_NAME};UID={cfg.DATABASE_UID};PWD={cfg.DATABASE_PWD}"
        )
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        if user_id is None:
            user_id = 'USER'
        else:
            user_id = str(user_id)
        
        # Get knowledge items
        cursor.execute("""
            SELECT ak.knowledge_id, ak.agent_id, ak.document_id, ak.description,
                   d.filename, d.document_type, d.original_path, d.document_metadata
            FROM AgentKnowledge ak
            JOIN Documents d ON ak.document_id = d.document_id
            WHERE ak.agent_id = ? AND ak.is_active = 1
            AND (
				ISNULL(ak.added_by, 'USER') = 'USER'
					OR
				ISNULL(ak.added_by, 'USER') = ?
				)
        """, agent_id, str(user_id))

        # Format results
        documents = []
        for row in cursor.fetchall():
            documents.append({
                'knowledge_id': row[0],
                'agent_id': row[1],
                'document_id': row[2],
                'description': row[3],
                'filename': row[4],
                'document_type': row[5],
                'original_path': row[6],
                'document_metadata': row[7]
            })
        
        cursor.close()
        conn.close()

        logging.info(f"Agent {agent_id}: Loaded {len(documents)} knowledge documents"
                     + (f" for user {user_id}" if user_id and user_id != 'USER' else " (agent-level)"))
        for doc in documents:
            logging.debug(f"  Knowledge doc: id={doc['document_id']}, file={doc['filename']}, "
                          f"desc={'(empty)' if not doc['description'] else doc['description'][:50]}")

        return documents
    except Exception as e:
        logging.error(f"Error getting agent knowledge documents for agent_id={agent_id}, user_id={user_id}: {str(e)}", exc_info=True)
        return []