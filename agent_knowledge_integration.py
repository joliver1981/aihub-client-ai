import logging
import os
import re
import requests
import json
import threading
import queue
from langchain.tools import tool
import config as cfg
from CommonUtils import get_db_connection, get_db_connection_string, get_app_path
from agent_knowledge_routes import process_document_as_knowledge
from TextChunker_LLM import TextChunker
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


def _format_chunk_for_ai(meta: dict, chunk_text: str, filename: str, page) -> str:
    """
    Format a retrieved chunk for inclusion in the AI's prompt context.

    When section context is available (smart chunking + INCLUDE_SECTION_HEADER produced
    document_identifier / section_breadcrumb / section_summary in metadata), surface it
    in the header so the AI knows which document, which section, and what the section
    is broadly about — this lets it link chunks from the same section across pages
    or compare chunks from the same section across different documents.

    Falls back to the legacy `--- filename (page N) ---` header when section context
    is absent (smart chunking disabled or older indexed chunks).
    """
    doc_identifier = meta.get('document_identifier')
    breadcrumb = meta.get('section_breadcrumb')
    section_summary = meta.get('section_summary')

    if doc_identifier or breadcrumb:
        header_bits = []
        if doc_identifier:
            header_bits.append(doc_identifier)
        else:
            header_bits.append(filename)
        if breadcrumb:
            header_bits.append(breadcrumb)
        header_bits.append(f"page {page}")
        header = " — ".join(header_bits)
        body = f"--- {header} ---\n"
        if section_summary:
            body += f"[Section: {section_summary}]\n"
        body += f"{chunk_text}\n"
        return body

    # Legacy / smart-chunking-off path
    return f"--- {filename} (page {page}) ---\n{chunk_text}\n"


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


def _sample_document_text(pages_text: List[str], total_chars: int, n_points: int) -> str:
    """
    Stratified sampling: pick ~n_points evenly-spaced excerpts across the document
    so buried provisions (e.g. an HVAC clause on page 17 of a 30-page lease) make it
    into the sample. Each excerpt's character budget is total_chars / n_points.

    Falls back to "first N chars" behavior if the document is small enough that
    stratified sampling would just produce overlapping excerpts.
    """
    if not pages_text:
        return ""

    full_text = "\n\n".join(p for p in pages_text if p and p.strip())
    if not full_text:
        return ""

    # If the doc is shorter than the budget, return all of it.
    if len(full_text) <= total_chars:
        return full_text[:total_chars]

    # If only a few sample points are requested or the doc is small relative to budget,
    # stratified sampling adds little value — fall back to head-of-doc.
    if n_points <= 1 or len(full_text) <= total_chars * 1.5:
        return full_text[:total_chars]

    per_sample_chars = max(200, total_chars // n_points)
    step = max(1, (len(full_text) - per_sample_chars) // (n_points - 1)) if n_points > 1 else 1

    excerpts = []
    seen_positions = set()
    for i in range(n_points):
        start = min(i * step, len(full_text) - per_sample_chars)
        # Snap to the nearest paragraph break for cleaner excerpts
        if start > 0:
            nearest_break = full_text.rfind("\n\n", max(0, start - 200), start + 50)
            if nearest_break != -1:
                start = nearest_break + 2
        if start in seen_positions:
            continue
        seen_positions.add(start)
        excerpts.append(full_text[start:start + per_sample_chars])

    sample = "\n\n[...]\n\n".join(excerpts)
    # Hard cap — guard against per_sample math overshooting
    if len(sample) > total_chars:
        sample = sample[:total_chars]
    return sample


def generate_knowledge_summary(document_id: str, filename: str, document_type: str, pages_text: List[str]) -> str:
    """
    Generate a structured summary of a knowledge document for aggregate queries.
    Uses a cheap LLM call to extract key facts into a compact index card.

    Sampling strategy is configurable via cfg.KNOWLEDGE_SUMMARY_SAMPLING:
      - 'stratified' (default): take ~N evenly-spaced excerpts so buried provisions
        (HVAC clauses, default-cure terms, etc.) make it into the summary
      - 'first_n' (legacy): take the first ~5000 chars only

    Returns:
        Structured summary string (~200-500 chars)
    """
    if not cfg.KNOWLEDGE_ENABLE_SUMMARIES:
        return ""

    try:
        sample_total_chars = int(getattr(cfg, 'KNOWLEDGE_SUMMARY_SAMPLE_TOTAL_CHARS', 5000))
        sampling_mode = getattr(cfg, 'KNOWLEDGE_SUMMARY_SAMPLING', 'stratified')

        if sampling_mode == 'first_n':
            # Legacy behavior: first ~5000 chars
            sample_text = ""
            for page in pages_text:
                sample_text += page + "\n\n"
                if len(sample_text) > sample_total_chars:
                    break
            sample_text = sample_text[:sample_total_chars]
        else:
            # Stratified — picks N excerpts evenly across the doc
            n_points = int(getattr(cfg, 'KNOWLEDGE_SUMMARY_SAMPLE_POINTS', 5))
            sample_text = _sample_document_text(pages_text, sample_total_chars, n_points)
        
        from api_keys_config import create_anthropic_client
        client, anthropic_config = create_anthropic_client()
        
        summary_system = (
            "You are a document indexer. The text below is a STRATIFIED SAMPLE — excerpts from "
            "the start, middle, and end of the document, separated by [...] markers. Treat each "
            "excerpt as evidence of what the document contains. "
            "Create a structured summary in 2-4 lines covering: document subject/parties, "
            "key entities and dates, and the SPECIFIC TOPICS or PROVISIONS the document addresses "
            "(e.g. for a lease: HVAC, assignment, default, renewal, term). "
            "Prefer naming concrete provisions/topics over generic phrases like 'standard terms'. "
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


_PAGE_MARKER_RE = re.compile(r'={5}\s*PAGE\s+(\d+)\s*={5}')


def _build_document_with_page_markers(pages):
    """
    Concatenate page texts into a single document string with explicit page markers.

    Args:
        pages: list of (page_id, page_number, full_text, filename, doc_type) rows

    Returns:
        Tuple of (flattened_text, page_id_by_number) where page_id_by_number maps
        page_number -> page_id for later metadata lookups.
    """
    parts = []
    page_id_by_number = {}
    for page_id, page_number, full_text, _filename, _doc_type in pages:
        if not full_text or not full_text.strip():
            continue
        page_id_by_number[page_number] = page_id
        parts.append(f"\n\n===== PAGE {page_number} =====\n\n{full_text.strip()}")
    return ''.join(parts).lstrip('\n'), page_id_by_number


def _derive_page_from_chunk(chunk_text: str, running_page):
    """
    Derive the page number a chunk belongs to from any "===== PAGE N =====" marker
    inside the chunk text. If none is present, the chunk falls entirely within the
    previous chunk's page (carry forward).

    Returns:
        Tuple of (resolved_page_number, cleaned_chunk_text) where the cleaned text
        has all page markers stripped.
    """
    matches = list(_PAGE_MARKER_RE.finditer(chunk_text))
    if matches:
        # First marker in the chunk = the page where this chunk's first content lives.
        # (If a chunk straddles a page boundary we tag it to the earlier page; the
        # _next_ chunk will start with its own marker and update running_page.)
        running_page = int(matches[0].group(1))
    cleaned = _PAGE_MARKER_RE.sub('', chunk_text)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    return running_page, cleaned


# ===== Phase 1: Embedding-cap enforcer =====
#
# text-embedding-3-small has a hard 8192-token input limit and embedding quality
# drops well before that — leading retrieval systems (Anthropic Contextual
# Retrieval, OpenAI cookbook, LangChain RAG defaults) keep individual chunks
# in the 256–1024 token range so each vector represents a tight, focused idea.
#
# The upstream chunker (TextChunker_LLM with VECTOR_USE_SMART_CHUNKING=True) is
# allowed to produce LARGER blocks because it preserves table integrity and
# section structure — those are inputs the cap enforcer here splits into
# embedding-sized pieces just before they hit the vector store. The split is
# deliberately layered so the chunker's table-detection logic still sees
# whole tables (Phase 2 will add LLM-driven row-aware splitting here; Phase 1
# uses a paragraph/line/char fallback that is safe but coarse).

_TIKTOKEN_ENCODER = None


def _get_token_encoder():
    """Lazy-load the tiktoken encoder once per process. Returns None if tiktoken
    is not installed — callers fall back to a char-count heuristic."""
    global _TIKTOKEN_ENCODER
    if _TIKTOKEN_ENCODER is not None:
        return _TIKTOKEN_ENCODER
    try:
        import tiktoken
        enc_name = getattr(cfg, 'VECTOR_EMBEDDING_TOKEN_ENCODING', 'cl100k_base')
        _TIKTOKEN_ENCODER = tiktoken.get_encoding(enc_name)
        return _TIKTOKEN_ENCODER
    except Exception as e:
        logging.warning(f"tiktoken unavailable, falling back to char heuristic: {e}")
        _TIKTOKEN_ENCODER = False  # sentinel so we don't retry every call
        return None


def _count_tokens(text: str) -> int:
    """Token count for text using the embedding model's encoder. Falls back to
    chars/4 when tiktoken is unavailable — a conservative under-estimate that
    rarely matters because Phase 1 still splits oversize chunks aggressively."""
    enc = _get_token_encoder()
    if enc is None or enc is False:
        return max(1, len(text) // 4)
    try:
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def _split_text_under_token_cap(text: str, max_tokens: int) -> list:
    """
    Split text into pieces each ≤ max_tokens. Greedy by paragraph → line →
    sentence → hard char-slice. Phase 2 will override this with LLM-aware
    row-packing when the chunk contains a detected table; Phase 1 keeps the
    split deterministic and table-agnostic.
    """
    if _count_tokens(text) <= max_tokens:
        return [text]

    pieces = []

    def _pack(units, joiner):
        """Greedily concatenate `units` (each already token-safe-ish), joining
        with `joiner`, into output pieces that each fit under max_tokens."""
        buf = ''
        for u in units:
            if not u:
                continue
            candidate = u if not buf else buf + joiner + u
            if _count_tokens(candidate) <= max_tokens:
                buf = candidate
            else:
                if buf:
                    pieces.append(buf)
                # The single unit itself may already be too large — recurse one level.
                if _count_tokens(u) > max_tokens:
                    buf = ''
                    # Recurse below the current granularity.
                    for sub in _split_one_unit(u, max_tokens):
                        if not buf:
                            buf = sub
                        elif _count_tokens(buf + joiner + sub) <= max_tokens:
                            buf = buf + joiner + sub
                        else:
                            pieces.append(buf)
                            buf = sub
                else:
                    buf = u
        if buf:
            pieces.append(buf)

    def _split_one_unit(u, cap):
        # Try by line, then sentence, then hard char-slice.
        lines = u.split('\n')
        if len(lines) > 1:
            sub = []
            _pack_inner(lines, '\n', cap, sub)
            return sub
        sentences = re.split(r'(?<=[.!?])\s+', u)
        if len(sentences) > 1:
            sub = []
            _pack_inner(sentences, ' ', cap, sub)
            return sub
        # Hard char-slice: estimate chars per token from this very text.
        n_tokens = max(_count_tokens(u), 1)
        chars_per_token = max(1, len(u) // n_tokens)
        slice_size = max(256, cap * chars_per_token - 64)  # small margin
        return [u[i:i + slice_size] for i in range(0, len(u), slice_size)]

    def _pack_inner(units, joiner, cap, out_list):
        buf = ''
        for u in units:
            candidate = u if not buf else buf + joiner + u
            if _count_tokens(candidate) <= cap:
                buf = candidate
            else:
                if buf:
                    out_list.append(buf)
                if _count_tokens(u) > cap:
                    for sub in _split_one_unit(u, cap):
                        out_list.append(sub)
                    buf = ''
                else:
                    buf = u
        if buf:
            out_list.append(buf)

    # Outer pass: by paragraph (double newline).
    paragraphs = re.split(r'\n{2,}', text)
    _pack(paragraphs, '\n\n')
    return pieces if pieces else [text]


# ===== Phase 2: LLM-driven table detection + header-repeat row split =====
#
# When the upstream smart chunker keeps a large table together (correctly — splitting
# inside a table destroys the rows' meaning), we don't want to fall back to a coarse
# paragraph-split that orphans rows from their header. Instead we ask a small LLM
# (Haiku or ANTHROPIC_MINI) for the table's literal header text and row delimiter,
# then deterministically pack rows under the token cap while repeating the header
# at the top of every piece. This keeps each embedded vector semantically equivalent
# to "header + a slice of body rows" — what an analyst would actually paste into a
# question — so vector retrieval can score row-level matches without the LLM having
# to guess column names.

def _llm_detect_table_structure(chunk_text: str) -> Optional[Dict]:
    """
    Ask Haiku/ANTHROPIC_MINI whether `chunk_text` contains a tabular block.
    Returns a dict {header_text, row_delimiter, approx_row_count} when the chunk
    contains a splittable table and the LLM's `header_text` literally appears in
    the chunk. Returns None on any failure or sanity-check miss — callers fall
    back to the paragraph splitter.
    """
    if not chunk_text or len(chunk_text) < 500:
        return None  # too small to be a table worth splitting

    # Cap LLM input to keep token usage bounded; 30K chars covers virtually any
    # single oversize chunk our chunker emits (chunks are bounded by the smart
    # chunker's window, so this is mostly a safety belt).
    sample = chunk_text[:30000]

    system = (
        "You analyze a text chunk extracted from a document and detect whether "
        "it contains tabular data (rows of structured records like invoice "
        "line-items, financial-statement rows, an employee roster, etc.). "
        "You reply with strict JSON only — no prose, no markdown fences."
    )
    prompt = (
        "Analyze the following CHUNK and decide whether it contains a table "
        "that can be safely split into row-aligned pieces.\n\n"
        f"CHUNK:\n\"\"\"\n{sample}\n\"\"\"\n\n"
        "Reply with this exact JSON shape:\n"
        "{\n"
        '  "is_table": true|false,\n'
        '  "header_text": "<exact verbatim text of the header row(s) — must appear in the CHUNK literally, character-for-character>",\n'
        '  "row_delimiter": "newline" | "double_newline",\n'
        '  "approx_row_count": <integer estimate>,\n'
        '  "splittable": true|false\n'
        "}\n\n"
        "Rules:\n"
        "- is_table=true only if the chunk has ≥5 rows of similarly-structured records.\n"
        "- header_text MUST be a substring of the CHUNK (verbatim, no paraphrasing, no trimming).\n"
        "- splittable=true only if rows can be cleanly separated by the indicated delimiter without breaking cell values.\n"
        "- If unsure, set is_table=false."
    )

    resp = _haiku_call_with_fallback(prompt, system, max_tokens=512, temp=0.0)
    if not resp:
        return None

    try:
        s = resp.strip()
        if s.startswith('```'):
            s = re.sub(r'^```(?:json)?\s*', '', s, flags=re.IGNORECASE)
            s = re.sub(r'\s*```\s*$', '', s)
        data = json.loads(s)
    except Exception as e:
        logging.debug(f"Table detector returned non-JSON: {e}; resp={resp[:200]}")
        return None

    if not (data.get('is_table') and data.get('splittable')):
        return None

    header = (data.get('header_text') or '').strip()
    if not header:
        return None

    # Critical sanity check: header MUST appear verbatim in the chunk. If the LLM
    # paraphrased it, our locate-by-string logic below would fail silently — bail
    # so the caller falls back to the paragraph splitter.
    if header not in chunk_text:
        logging.debug("Table detector header_text not found verbatim in chunk; falling back.")
        return None

    delim = data.get('row_delimiter', 'newline')
    if delim not in ('newline', 'double_newline'):
        delim = 'newline'

    return {
        'header_text': header,
        'row_delimiter': delim,
        'approx_row_count': int(data.get('approx_row_count') or 0),
    }


def _row_pack_with_header(chunk_text: str, table_meta: Dict, max_tokens: int) -> Optional[list]:
    """
    Split chunk_text into pieces ≤ max_tokens, repeating the table header at
    the top of every piece. Returns the list of pieces, or None if the split
    couldn't produce ≥2 valid pieces (caller falls back to paragraph splitter).
    """
    header = table_meta['header_text']
    header_idx = chunk_text.find(header)
    if header_idx < 0:
        return None  # double-check; should have been caught by detector

    pre = chunk_text[:header_idx].rstrip()
    body_start = header_idx + len(header)
    body = chunk_text[body_start:].lstrip('\n')
    if not body:
        return None

    if table_meta.get('row_delimiter') == 'double_newline':
        rows = re.split(r'\n{2,}', body)
        row_sep = '\n\n'
    else:
        rows = body.split('\n')
        row_sep = '\n'
    rows = [r for r in rows if r.strip()]
    if len(rows) < 2:
        return None

    # First piece gets any pre-table context (section heading, intro line). Subsequent
    # pieces start with just the header — this matches how an analyst would paste
    # consecutive slices of a long table into different questions.
    first_prefix = (pre + '\n\n' + header) if pre else header
    cont_prefix = header

    pieces = []
    current = first_prefix
    using_first = True

    def _piece_with_row(prefix: str, accumulated_body: str, next_row: str) -> str:
        sep = row_sep if accumulated_body else '\n'
        return prefix + sep + (accumulated_body + row_sep + next_row if accumulated_body else next_row)

    body_buf = ''  # accumulated rows for the current piece
    for r in rows:
        prefix = first_prefix if using_first else cont_prefix
        candidate = _piece_with_row(prefix, body_buf, r)
        if _count_tokens(candidate) <= max_tokens:
            body_buf = (body_buf + row_sep + r) if body_buf else r
            continue

        # Flush current piece (if it has any rows) and start a new one with this row.
        if body_buf:
            pieces.append(prefix + ('\n' if not body_buf.startswith('\n') else '') + body_buf)
        using_first = False
        prefix = cont_prefix
        # Check if a single row + header already exceeds cap → fall back.
        candidate_single = prefix + '\n' + r
        if _count_tokens(candidate_single) > max_tokens:
            return None
        body_buf = r

    # Flush trailing piece.
    if body_buf:
        prefix = first_prefix if using_first else cont_prefix
        pieces.append(prefix + '\n' + body_buf)

    if len(pieces) < 2:
        return None

    # Final sanity check — every piece must be ≤ cap.
    for p in pieces:
        if _count_tokens(p) > max_tokens:
            return None

    return pieces


def _split_with_table_awareness(chunk_text: str, max_tokens: int,
                                 detection_cache: dict, cache_key: str,
                                 document_id: Optional[str] = None,
                                 header_cache_by_doc: Optional[dict] = None) -> tuple:
    """
    Try LLM-driven table-aware split first; fall back to paragraph splitter on
    any failure.

    Returns a 2-tuple (pieces, split_kind) where split_kind ∈
    {'detected', 'inherited', 'paragraph'} so the caller can log how each
    chunk was resolved.

    `detection_cache` is a per-call dict keyed by `cache_key` so we don't
    re-ask the LLM about the same chunk text twice.

    `header_cache_by_doc` (optional, Phase 2.5 — header inheritance) is a
    cross-chunk dict keyed by `document_id`. When this chunk *does not* have
    an in-chunk header but a previous chunk from the same document did, we
    inject the cached header at the top of this chunk and row-pack as if
    the header had always been there. This handles the production case where
    a long table's column header is printed only on page 1; pages 2-N have
    rows but no header, and their chunks would otherwise embed as bare row
    text losing the column-name context.
    """
    # Quick gate — only call the LLM when the chunk is meaningfully over the cap
    # AND has multiple newlines (i.e. looks like it might be tabular). Avoids
    # paying the LLM tax on prose blocks.
    if chunk_text.count('\n') < 10:
        return _split_text_under_token_cap(chunk_text, max_tokens), 'paragraph'

    if cache_key in detection_cache:
        table_meta = detection_cache[cache_key]
    else:
        table_meta = _llm_detect_table_structure(chunk_text)
        detection_cache[cache_key] = table_meta

    if table_meta:
        # Header detected IN this chunk. Cache it for any subsequent chunks
        # from the same document that may be header-less continuation pages.
        if header_cache_by_doc is not None and document_id:
            header_cache_by_doc[document_id] = {
                'header_text': table_meta['header_text'],
                'row_delimiter': table_meta.get('row_delimiter', 'newline'),
                'fresh_count': 0,  # incremented each time it's used downstream
            }
        pieces = _row_pack_with_header(chunk_text, table_meta, max_tokens)
        if pieces and len(pieces) >= 2:
            return pieces, 'detected'

    # ── Phase 2.5: header inheritance ──
    # No header in this chunk, but did the previous chunk(s) of this document
    # establish one? If so, inject it and try row-packing as a continuation.
    # Sanity gate: only inherit when this chunk has the high-newline-density
    # shape of a table continuation (≥20 newlines). Prose blocks have far
    # fewer newlines so this rejection keeps non-table chunks out of the path.
    if (header_cache_by_doc is not None
            and document_id
            and document_id in header_cache_by_doc
            and chunk_text.count('\n') >= 20):
        cached = header_cache_by_doc[document_id]
        injected_text = cached['header_text'] + '\n' + chunk_text
        synth_meta = {
            'header_text': cached['header_text'],
            'row_delimiter': cached['row_delimiter'],
        }
        pieces = _row_pack_with_header(injected_text, synth_meta, max_tokens)
        if pieces and len(pieces) >= 2:
            cached['fresh_count'] = cached.get('fresh_count', 0) + 1
            return pieces, 'inherited'

    # Fall back to the deterministic paragraph/line/char splitter.
    return _split_text_under_token_cap(chunk_text, max_tokens), 'paragraph'


def _enforce_embedding_token_cap(documents: list, metadatas: list, ids: list):
    """
    Post-chunking pass: ensure every embedded document fits under
    cfg.VECTOR_EMBEDDING_MAX_TOKENS. Any oversize chunk is split into multiple
    pieces; metadata is duplicated for each piece (with an added 'cap_split_index'
    and 'cap_split_of' so retrieval can reassemble them if needed). Returns the
    new (documents, metadatas, ids) tuple. IDs of split pieces get a `_s{N}`
    suffix so they remain unique.

    This is the *only* place a chunk's size relative to the embedding model is
    enforced. Phase 2 will add an LLM-driven table-aware splitter that this
    helper delegates to when the chunk contains tabular data — the contract
    here (input list of docs, output list of docs that all fit under the cap)
    stays the same.
    """
    cap = int(getattr(cfg, 'VECTOR_EMBEDDING_MAX_TOKENS', 1024))
    if cap <= 0:
        return documents, metadatas, ids

    out_docs, out_metas, out_ids = [], [], []
    split_count = 0
    oversized_chunks = 0
    table_split_chunks = 0
    inherited_split_chunks = 0
    # Per-call cache: skip redundant LLM table-detection calls when the same
    # oversize chunk text is somehow presented twice. Keyed by chunk_id since
    # ids are unique within a single index call.
    detection_cache = {}
    # Phase 2.5: per-document header cache. When chunk N from a document gets
    # its table header detected, we remember it so chunks N+1, N+2, ... from
    # the same document can borrow it as continuation pieces.
    header_cache_by_doc: Dict[str, Dict] = {}

    for doc_text, meta, doc_id in zip(documents, metadatas, ids):
        tok = _count_tokens(doc_text)
        if tok <= cap:
            out_docs.append(doc_text)
            out_metas.append(meta)
            out_ids.append(doc_id)
            continue

        oversized_chunks += 1
        document_id = str(meta.get('document_id') or '') or None
        pieces, split_kind = _split_with_table_awareness(
            doc_text, cap, detection_cache, doc_id,
            document_id=document_id,
            header_cache_by_doc=header_cache_by_doc,
        )
        if split_kind == 'detected':
            table_split_chunks += 1
        elif split_kind == 'inherited':
            inherited_split_chunks += 1
        if len(pieces) <= 1:
            # Splitter couldn't reduce it — keep as-is and let the vector API
            # surface the failure. (This is rare; would mean a single sentence
            # or unbreakable run exceeds 1024 tokens.)
            logging.warning(
                f"Embedding-cap enforcer could not split chunk {doc_id} "
                f"({tok} tokens > cap {cap}); passing through as-is."
            )
            out_docs.append(doc_text)
            out_metas.append(meta)
            out_ids.append(doc_id)
            continue

        split_count += len(pieces) - 1
        for piece_idx, piece in enumerate(pieces):
            piece_meta = dict(meta)
            piece_meta['cap_split_index'] = piece_idx
            piece_meta['cap_split_of'] = len(pieces)
            out_docs.append(piece)
            out_metas.append(piece_meta)
            out_ids.append(f"{doc_id}_s{piece_idx}")

    if oversized_chunks:
        paragraph_fallback = oversized_chunks - table_split_chunks - inherited_split_chunks
        logging.info(
            f"Embedding-cap enforcer: {oversized_chunks} oversize chunk(s) split into "
            f"{oversized_chunks + split_count} pieces (cap={cap} tokens, "
            f"table-aware-detected={table_split_chunks}, "
            f"table-aware-inherited-header={inherited_split_chunks}, "
            f"paragraph-fallback={paragraph_fallback}). "
            f"Final document count: {len(out_docs)}."
        )

    return out_docs, out_metas, out_ids


def _build_embed_text(chunk_text: str, chunk_metadata: dict) -> str:
    """
    Build the string that gets embedded into the vector store. When section context
    is available (smart chunking + INCLUDE_SECTION_HEADER), prepend a compact header
    so chunks from the same section embed similarly across documents and pages.

    When no section context is present (smart chunking off, or LLM didn't supply it),
    embed just the chunk text — preserving today's behavior.
    """
    doc_id_text = chunk_metadata.get('document_identifier')
    breadcrumb = chunk_metadata.get('section_breadcrumb')
    if doc_id_text or breadcrumb:
        prefix_parts = []
        if doc_id_text:
            prefix_parts.append(f"[{doc_id_text}]")
        if breadcrumb:
            prefix_parts.append(f"[Section: {breadcrumb}]")
        return ' '.join(prefix_parts) + '\n' + chunk_text
    return chunk_text


def index_knowledge_document(document_id: str, agent_id: int, user_id: str = None):
    """
    Index a knowledge document into the agent_knowledge vector collection.

    Smart-chunking-aware: feeds the WHOLE document (with page markers) to TextChunker
    in one pass so the chunking LLM can see section structure that spans pages and
    assign consistent section_breadcrumb / document_identifier metadata to each chunk.

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

        filename = pages[0][3] if pages else 'unknown'
        doc_type = pages[0][4] if pages else 'unknown'

        # Generate and store structured summary (for aggregate queries) — non-fatal
        try:
            pages_text = [row[2] for row in pages if row[2]]
            summary = generate_knowledge_summary(document_id, filename, doc_type, pages_text)
            if summary:
                store_knowledge_summary(document_id, summary)
        except Exception as sum_err:
            logging.warning(f"Summary generation failed (non-fatal): {sum_err}")

        # Build a single document text with page markers so the chunking LLM can see
        # the full section structure (Article 7 spanning pages 13–14, etc.).
        flattened_text, page_id_by_number = _build_document_with_page_markers(pages)
        if not flattened_text.strip():
            logging.info(f"Document {document_id} has no non-empty page text — nothing to index")
            return False

        # Determine the page number of the first non-empty page (used as fallback for
        # the very first chunk if it somehow doesn't contain its page marker).
        first_page_number = next(
            (pn for (_pid, pn, txt, _f, _d) in pages if txt and txt.strip()),
            pages[0][1] if pages else 1
        )

        # Run smart-chunking ONCE for the whole document. TextChunker honors
        # VECTOR_USE_SMART_CHUNKING + VECTOR_SMART_CHUNK_INCLUDE_SECTION_HEADER and
        # falls back to standard chunking on validation failure.
        chunker = TextChunker(
            chunk_size=cfg.VECTOR_CHUNK_SIZE if hasattr(cfg, 'VECTOR_CHUNK_SIZE') else 1000,
            chunk_overlap=cfg.VECTOR_CHUNK_OVERLAP if hasattr(cfg, 'VECTOR_CHUNK_OVERLAP') else 100,
        )
        # 'SHARED' sentinel = visible to every user with access to the agent.
        # Used by compliance docs and any other code that passes user_id=None
        # to indicate "this is general agent knowledge, not user-specific".
        # Search-side counterpart: search_knowledge_vectors / fanout match
        # vectors where user_id == <chatting user> OR user_id == 'SHARED'.
        meta_user_id = str(user_id) if user_id else 'SHARED'
        base_metadata = {
            'document_id': document_id,
            'filename': filename,
            'document_type': doc_type or 'unknown',
            'agent_id': str(agent_id),
            'user_id': meta_user_id,
            'is_knowledge': 'true',
        }
        chunks = chunker.chunk_text(flattened_text, base_metadata)

        if not chunks:
            logging.info(f"Chunker returned no chunks for {document_id}")
            return False

        documents = []
        metadatas = []
        ids = []
        running_page = first_page_number

        for i, chunk in enumerate(chunks):
            running_page, cleaned_text = _derive_page_from_chunk(chunk['text'], running_page)
            if not cleaned_text:
                continue

            chunk_meta = chunk.get('metadata', {}).copy()
            chunk_meta.update({
                'document_id': document_id,
                'filename': filename,
                'document_type': doc_type or 'unknown',
                'agent_id': str(agent_id),
                'user_id': meta_user_id,  # 'SHARED' or actual user id (see base_metadata)
                'is_knowledge': 'true',
                'page_number': running_page,
                'page_id': page_id_by_number.get(running_page, ''),
                'chunk_index': i,
            })

            # Build embed text — prepend [doc_identifier] [Section: ...] when section
            # context is present. Falls back to bare chunk text otherwise.
            embed_text = _build_embed_text(cleaned_text, chunk_meta)

            documents.append(embed_text)
            metadatas.append(chunk_meta)
            ids.append(f"kb_{document_id}_c{i}")

        # Phase 1: enforce embedding-model token cap. Any chunk that exceeds
        # cfg.VECTOR_EMBEDDING_MAX_TOKENS (default 1024) is split into smaller
        # pieces here so the downstream embedding call never sees a chunk above
        # the model's 8192-token hard limit and so vector retrieval can return
        # focused snippets instead of giant blocks.
        if documents:
            documents, metadatas, ids = _enforce_embedding_token_cap(documents, metadatas, ids)

        if documents:
            ok = vector_engine.index(documents=documents, metadatas=metadatas, ids=ids)
            if ok:
                logging.info(
                    f"Indexed {len(documents)} chunks for knowledge document {document_id} "
                    f"(agent={agent_id}, user={user_id}, smart={getattr(cfg, 'VECTOR_USE_SMART_CHUNKING', False)})"
                )
                return True
            else:
                logging.error(
                    f"FAILED to index {len(documents)} chunks for knowledge document {document_id} "
                    f"(agent={agent_id}, user={user_id}). Vector store call returned False — "
                    f"check Vector API logs (port HOST_PORT+30) for the underlying cause "
                    f"(common: chunk text exceeds embedding model's 8192-token limit)."
                )
                return False

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


def search_knowledge_vectors(query: str, agent_id: int, user_id: str = None,
                              top_k: int = None,
                              forced_document_id: Optional[str] = None) -> List[Dict]:
    """
    Search the knowledge vector collection for chunks relevant to the query.
    Filters by agent_id and user_id for isolation.

    forced_document_id (optional) — when supplied, the search is hard-filtered
    to chunks from that one document. Used by the LLM document detector
    (BUG-NEEDLE-WRONG-DOC-AFTER-CLARIFY fix) to prevent cross-document
    contamination when the user explicitly named the file they want.

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

        # Build filters for user isolation — BOTH agent_id AND user_id required.
        # If user_id is missing, refuse to search rather than leaking cross-user data.
        if not user_id:
            logging.warning(f"search_knowledge_vectors called without user_id for agent {agent_id} — returning empty to prevent data leakage")
            return []

        # Match BOTH this user's own vectors AND any shared vectors
        # (compliance docs and other "general agent knowledge" indexed with
        # the 'SHARED' sentinel — see index_knowledge_document for the
        # indexing-side counterpart).
        and_clauses = [
            {'agent_id': str(agent_id)},
            {'$or': [
                {'user_id': str(user_id)},
                {'user_id': 'SHARED'},
            ]},
        ]
        if forced_document_id:
            and_clauses.append({'document_id': str(forced_document_id)})
            _skr_trace(
                f"Vector search hard-filtered to document_id={forced_document_id} "
                f"(LLM doc-detector match)"
            )

        filters = {'$and': and_clauses}

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
    Use a cheap LLM call to classify the query into one of three retrieval shapes:
    NEEDLE, FANOUT, or AGGREGATE. No keyword heuristics — pure LLM intelligence.

    Returns:
        'NEEDLE', 'FANOUT', or 'AGGREGATE'
    """
    system_prompt = (
        "Classify this knowledge base query into exactly ONE category. "
        "Respond with ONLY the single word NEEDLE, FANOUT, or AGGREGATE.\n\n"

        "NEEDLE: Finding a specific piece of information that lives in ONE document or a few. "
        "The user wants the location of a fact, clause, or provision. "
        "Examples: 'which lease allows hazardous materials', 'does any lease mention solar panels', "
        "'find the termination clause', 'what was Q1 revenue', 'who is the CEO', "
        "'what is the renewal term of the Acme lease'.\n\n"

        "FANOUT: Comparing, listing, or extracting the SAME field/provision across MANY (or all) documents. "
        "The answer requires touching every document specifically for the same thing. "
        "Examples: 'compare HVAC requirements across all leases', "
        "'for each contract, what is the renewal term', "
        "'list the termination notice period for every vendor agreement', "
        "'which leases assign maintenance to tenant vs landlord — give me the breakdown', "
        "'show me the rent amount for each lease', 'extract the governing law from each contract'.\n\n"

        "AGGREGATE: High-level summarizing or counting that does NOT require detail-level extraction. "
        "The answer is meta — about what kinds of documents exist or general topic coverage. "
        "Examples: 'what topics do these documents cover', 'give me an overview of our knowledge base', "
        "'what types of contracts do we have', 'how many documents mention X' (mere count), "
        "'summarize what's in this collection'.\n\n"

        "DECISION RULES:\n"
        "- Asking 'does any lease have X' or 'which lease has X' → NEEDLE.\n"
        "- Asking 'for each / across all / compare X / list X for every doc' → FANOUT.\n"
        "- Asking 'overview / what topics / summarize / what kinds' → AGGREGATE.\n"
        "- If the query asks for a SPECIFIC detail and explicitly says 'across all' or 'for each' — that is FANOUT, not AGGREGATE."
    )
    user_msg = f"Documents: {doc_count} documents, {total_chars:,} total characters.\nQuery: {query}"

    def _normalize_route(raw: str) -> str:
        upper = (raw or '').strip().upper()
        if 'FANOUT' in upper or 'FAN-OUT' in upper or 'FAN OUT' in upper:
            return 'FANOUT'
        if 'AGGREGATE' in upper:
            return 'AGGREGATE'
        return 'NEEDLE'

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
            result = response.content[0].text
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
                result = response['content'][0]['text']
            elif isinstance(response, dict) and 'error' in response:
                raise ValueError(f"Proxy error: {response['error']}")
            else:
                raise ValueError(f"Unexpected proxy response: {str(response)[:200]}")

        route = _normalize_route(result)

        # Honor the FANOUT kill switch — if the user has disabled it, downgrade to AGGREGATE
        # (the next-most-coverage-oriented option). This lets ops disable fan-out cost
        # without changing the router's classification.
        if route == 'FANOUT' and not getattr(cfg, 'KNOWLEDGE_FANOUT_ENABLED', True):
            _skr_trace("Router said FANOUT but KNOWLEDGE_FANOUT_ENABLED=False — downgrading to AGGREGATE")
            route = 'AGGREGATE'

        _skr_trace(f"Router LLM response: '{result.strip()}' -> {route}")
        logging.info(f"Knowledge query routed as {route}: '{query[:80]}...' ({doc_count} docs, {total_chars:,} chars)")
        return route

    except Exception as e:
        _skr_trace(f"Router FAILED: {e} — defaulting to NEEDLE")
        logging.warning(f"Query routing failed, defaulting to NEEDLE: {e}")
        return 'NEEDLE'


def _haiku_call_with_fallback(prompt: str, system: str, max_tokens: int = 512, temp: float = 0.0) -> Optional[str]:
    """
    Call Haiku via claudeQuickPrompt, falling back to cfg.ANTHROPIC_MINI on failure.
    Returns the response text on success, or None if both attempts failed (or returned empty).

    Used by FANOUT per-document extraction and the document re-ranker. Designed to degrade
    gracefully — never raises — so a per-doc failure inside fan-out marks just that one doc
    as extraction-unavailable rather than crashing the whole query.
    """
    haiku_model = getattr(cfg, 'KNOWLEDGE_HAIKU_MODEL', None)
    fallback_model = getattr(cfg, 'ANTHROPIC_MINI', None)

    try:
        from claudeQuickPrompt import claudeQuickPrompt
    except ImportError as e:
        logging.warning(f"claudeQuickPrompt unavailable: {e}")
        return None

    # Attempt 1: Haiku
    if haiku_model:
        try:
            resp = claudeQuickPrompt(prompt, system=system, temp=temp, model=haiku_model)
            if resp and resp.strip():
                return resp
        except Exception as e:
            logging.warning(f"Haiku call failed ({haiku_model}): {e} — falling back to {fallback_model}")

    # Attempt 2: ANTHROPIC_MINI fallback
    if fallback_model and fallback_model != haiku_model:
        try:
            resp = claudeQuickPrompt(prompt, system=system, temp=temp, model=fallback_model)
            if resp and resp.strip():
                return resp
        except Exception as e:
            logging.warning(f"Fallback ANTHROPIC_MINI call also failed ({fallback_model}): {e}")

    return None


# ===== BUG-NEEDLE-WRONG-DOC-AFTER-CLARIFY fix: LLM document detector =====
#
# When an agent has multiple similar-template documents (e.g., several FedEx
# invoices), vector retrieval routinely returns chunks from the wrong document
# even after the user explicitly names the file they want. Vector similarity
# doesn't honour filenames.
#
# This detector asks a small LLM (Haiku / ANTHROPIC_MINI) — using the recent
# conversation history — to decide whether the user's current question is
# referring to ONE specific document with HIGH confidence. If so, the caller
# applies a hard `document_id` filter to the vector search; if not, today's
# behaviour is preserved (no boost, no implicit decay state).
#
# This deliberately uses the LLM rather than regex / fuzzy-match heuristics:
# the LLM trivially handles "I mean the Continental invoice" → high confidence,
# "what's the fuel surcharge?" (right after a Continental question) → still
# high confidence, "and the others?" → no confidence, etc. — without us
# building a state machine for follow-up resolution.

def _format_doc_list_for_detector(available_documents: List[Dict]) -> str:
    """Compact, deterministic rendering of the agent's knowledge document list
    for the LLM detector prompt. Each line has the document_id (the value the
    LLM should return) plus the user-visible filename + optional title."""
    lines = []
    for d in available_documents or []:
        doc_id = str(d.get('document_id') or '')
        if not doc_id:
            continue
        filename = d.get('filename') or 'unknown'
        title = d.get('document_identifier') or d.get('description') or ''
        line = f"  - doc_id={doc_id!r}, filename={filename!r}"
        if title:
            line += f", title={title!r}"
        lines.append(line)
    return "\n".join(lines)


def _format_chat_history_for_detector(chat_history, max_turns: int = 8,
                                       max_chars_per_turn: int = 600) -> str:
    """Compact recent chat history for the detector prompt. Accepts either:
       - LangChain message objects (have `.type` / `.content`)
       - plain dicts ({'role': ..., 'content': ...})
       - tuples of (human_msg, ai_msg)
    Returns a string like:
       [USER]: ...
       [AGENT]: ...
       [USER]: ...
    Trimmed to last `max_turns` turns and `max_chars_per_turn` per message."""
    if not chat_history:
        return "(no prior turns)"

    lines = []
    flat: List[tuple] = []  # (role, content)

    for item in chat_history:
        # LangChain message object
        if hasattr(item, 'content'):
            role = 'AGENT' if getattr(item, 'type', '') in ('ai', 'assistant') else 'USER'
            content = getattr(item, 'content', '') or ''
            flat.append((role, content))
            continue
        # Plain dict {role, content}
        if isinstance(item, dict):
            role_in = (item.get('role') or item.get('type') or 'user').lower()
            role = 'AGENT' if role_in in ('ai', 'assistant', 'agent') else 'USER'
            content = item.get('content') or item.get('text') or ''
            flat.append((role, content))
            continue
        # Tuple/list (human, ai)
        if isinstance(item, (list, tuple)) and len(item) == 2:
            flat.append(('USER', str(item[0])))
            flat.append(('AGENT', str(item[1])))
            continue

    # Take the last max_turns entries (already in chronological order)
    flat = flat[-max_turns:]
    for role, content in flat:
        content_str = str(content)
        if len(content_str) > max_chars_per_turn:
            content_str = content_str[:max_chars_per_turn] + '…'
        lines.append(f"[{role}]: {content_str}")

    return "\n".join(lines) if lines else "(no prior turns)"


def _llm_detect_named_document(
    chat_history,
    latest_question: str,
    available_documents: List[Dict],
    original_user_input: Optional[str] = None,
) -> Optional[str]:
    """
    Ask a small LLM whether the user's current turn refers — with HIGH
    confidence — to one specific knowledge document, considering the recent
    conversation so follow-ups inherit the document reference.

    `original_user_input` (when provided) is the LITERAL user message that
    triggered the current agent turn. `latest_question` is the agent's
    paraphrased tool-query argument. The detector sees both: when the agent
    strips a document reference from the tool query, the original user
    message often still names the document (e.g. "I mean the Continental
    invoice, what's the grand total?"). Passing both is what lets the
    detector survive the agent's query-rewriting step.

    Returns the matched `document_id` (as str) when the LLM is HIGH-confidence,
    else None. The caller treats a returned id as a hard filter on the vector
    search — no soft boost, no decay. Lower-confidence cases return None and
    the retriever runs as it does today.
    """
    if not available_documents or len(available_documents) < 2:
        # No disambiguation needed when there's 0 or 1 document.
        return None

    if not getattr(cfg, 'KNOWLEDGE_LLM_DOC_FILTER_ENABLED', True):
        return None

    doc_list = _format_doc_list_for_detector(available_documents)
    if not doc_list.strip():
        return None

    history_block = _format_chat_history_for_detector(chat_history)

    # Compose the question section: when we have the literal user input
    # alongside the tool query, surface both — the user message is the
    # authoritative signal, the tool query is a hint at what the agent thinks
    # the user is asking. Truncate each to 2000 chars to bound prompt size.
    def _trim(s, n=2000):
        s = str(s or '')
        return s if len(s) <= n else s[:n] + '…'

    if original_user_input and original_user_input.strip() and original_user_input != latest_question:
        question_block = (
            "USER'S LITERAL CURRENT MESSAGE (authoritative — what the user actually typed):\n"
            f"{_trim(original_user_input)}\n\n"
            "AGENT'S TOOL-QUERY PARAPHRASE (advisory only — the agent may have "
            "stripped or rephrased the user's wording, including any document "
            "name they mentioned):\n"
            f"{_trim(latest_question)}\n"
        )
    else:
        question_block = (
            "CURRENT QUESTION:\n"
            f"{_trim(latest_question)}\n"
        )

    system = (
        "You decide whether a user's CURRENT turn is asking about ONE specific "
        "document from a known list. Consider the recent conversation — if "
        "the user named a document a few turns ago and is now asking a "
        "follow-up, the document reference still applies. When both the "
        "user's literal message AND the agent's paraphrased tool query are "
        "shown, treat the literal user message as authoritative — the agent "
        "may have dropped a document reference from the paraphrase. Reply "
        "with strict JSON only — no markdown, no prose."
    )
    prompt = (
        "AVAILABLE DOCUMENTS (the user's knowledge base):\n"
        f"{doc_list}\n\n"
        "RECENT CONVERSATION:\n"
        f"{history_block}\n\n"
        f"{question_block}\n"
        "Decide: is the user asking about exactly ONE document from the "
        "list above, with HIGH confidence?\n"
        "- HIGH confidence means either:\n"
        "  (a) the user named the document (filename, title, or unambiguous "
        "company name) in the CURRENT message, OR\n"
        "  (b) the user named one in a recent turn and the CURRENT message "
        "is clearly a follow-up about the same document (e.g. \"and the "
        "grand total?\" after asking about Continental).\n"
        "- If the user is asking across multiple documents, or you cannot "
        "tell which document is meant, or the question is generic, return "
        "no match — do not guess.\n\n"
        "Reply with this exact JSON shape:\n"
        "{\n"
        '  "doc_id": "<the document_id value from the list above, or null>",\n'
        '  "confidence": "high" | "none",\n'
        '  "reason": "<one short sentence>"\n'
        "}\n"
    )

    resp = _haiku_call_with_fallback(prompt, system, max_tokens=256, temp=0.0)
    if not resp:
        return None

    try:
        s = resp.strip()
        if s.startswith('```'):
            s = re.sub(r'^```(?:json)?\s*', '', s, flags=re.IGNORECASE)
            s = re.sub(r'\s*```\s*$', '', s)
        data = json.loads(s)
    except Exception as e:
        logging.debug(f"Doc detector returned non-JSON: {e}; resp={resp[:200]}")
        return None

    if data.get('confidence') != 'high':
        _skr_trace(
            f"LLM doc-detector: NO high-confidence match for "
            f"'{latest_question[:80]}' (reason={data.get('reason', '')!r})"
        )
        return None

    doc_id = data.get('doc_id')
    if not doc_id or str(doc_id).lower() in ('null', 'none', ''):
        return None

    doc_id = str(doc_id)

    # Sanity check: detector MUST return a doc_id that's actually in the list.
    valid_ids = {str(d.get('document_id') or '') for d in available_documents}
    valid_ids.discard('')
    if doc_id not in valid_ids:
        logging.debug(
            f"LLM doc-detector returned doc_id={doc_id!r} not in available_documents; ignoring"
        )
        return None

    _skr_trace(
        f"LLM doc-detector matched: doc_id={doc_id} "
        f"reason={data.get('reason', '')!r}"
    )
    logging.info(
        f"Knowledge retrieval will hard-filter on document_id={doc_id} "
        f"(LLM doc-detector matched user's question — see SKR trace)"
    )
    return doc_id


def _fanout_extract_for_doc(query: str, doc_id: str, document_identifier: str,
                            chunks_text: List[str]) -> Optional[str]:
    """
    Per-document extraction step in FANOUT. Asks Haiku to pull the answer to `query`
    out of the chunks retrieved for this single document. Returns either:
      - a short extracted finding (1-3 sentences),
      - the literal string 'NULL' meaning "no relevant content in this doc",
      - or None if both Haiku and the fallback model failed.
    """
    if not chunks_text:
        return 'NULL'

    chunks_block = "\n\n---\n\n".join(chunks_text)
    doc_label = document_identifier or f"Document {doc_id}"

    system = (
        "You are an extraction assistant. Given a user question and a few text chunks "
        "from ONE document, extract the answer to the question if it appears in those chunks. "
        "Be concise (1-3 sentences). Quote specific terms when relevant. "
        "If the chunks do NOT contain information that answers the question, respond with the single word: NULL"
    )
    prompt = (
        f"Question: {query}\n\n"
        f"Document: {doc_label}\n\n"
        f"Chunks from this document:\n{chunks_block}\n\n"
        f"Extract the answer to the question, or respond NULL if not present."
    )

    response = _haiku_call_with_fallback(prompt, system=system, max_tokens=400, temp=0.0)
    if response is None:
        return None
    return response.strip()


def fanout_knowledge_retrieval(query: str, agent_id: int, user_id: str = None,
                               documents: list = None) -> str:
    """
    FANOUT retrieval — per-document map-reduce for cross-document comparison queries
    like "compare HVAC across all leases" or "for each contract, what's the renewal term".

    For each agent knowledge document:
      1. Filtered vector search (top_k chunks) scoped to that doc only
      2. Skip if best chunk's similarity is below KNOWLEDGE_FANOUT_SKIP_SIMILARITY_THRESHOLD
      3. Concurrent Haiku extraction of the answer for that doc
    Then aggregate per-doc findings into a single context bundle for the agent.

    Returns the formatted bundle as a single string.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not documents:
        return "[FANOUT: no knowledge documents available for this agent]"

    # Safety cap
    max_docs = getattr(cfg, 'KNOWLEDGE_FANOUT_MAX_DOCS', 1500)
    if len(documents) > max_docs:
        _skr_trace(f"FANOUT: truncating from {len(documents)} to {max_docs} docs (cap)")
        logging.warning(f"FANOUT truncated {len(documents)} -> {max_docs} docs")
        documents = documents[:max_docs]

    per_doc_top_k = getattr(cfg, 'KNOWLEDGE_FANOUT_PER_DOC_TOP_K', 2)
    skip_threshold = getattr(cfg, 'KNOWLEDGE_FANOUT_SKIP_SIMILARITY_THRESHOLD', 0.4)
    parallel = getattr(cfg, 'KNOWLEDGE_FANOUT_PARALLEL', 20)

    vector_engine = _get_knowledge_vector_engine()
    if not vector_engine:
        return "[FANOUT: knowledge vector engine unavailable]"

    # ---- Stage 1: per-document retrieval (sequential — vector API is fast) ----
    normalized_query = _normalize_search_query(query)
    per_doc_chunks = []  # list of (doc_id, document_identifier, [chunk_texts])
    skipped_count = 0

    for doc in documents:
        doc_id = doc.get('document_id')
        if not doc_id:
            continue
        try:
            # Match user's own vectors AND shared (compliance) vectors —
            # parallel to search_knowledge_vectors. When user_id is None,
            # don't filter by user at all (admin / system caller).
            if user_id is None:
                _filters = {
                    '$and': [
                        {'document_id': str(doc_id)},
                        {'agent_id': str(agent_id)},
                    ]
                }
            else:
                _filters = {
                    '$and': [
                        {'document_id': str(doc_id)},
                        {'agent_id': str(agent_id)},
                        {'$or': [
                            {'user_id': str(user_id)},
                            {'user_id': 'SHARED'},
                        ]},
                    ]
                }
            results = vector_engine.search(
                query=normalized_query,
                filters=_filters,
                limit=per_doc_top_k,
            )
        except Exception as e:
            logging.warning(f"FANOUT per-doc search failed for {doc_id}: {e}")
            skipped_count += 1
            continue

        if not results:
            skipped_count += 1
            continue

        # Knowledge vector client returns 'score' which is actually the raw distance.
        # Convert to similarity = max(0, 1 - distance) to compare against threshold.
        best_distance = min((r.get('score', 1.0) for r in results), default=1.0)
        best_similarity = max(0.0, 1.0 - best_distance)
        if best_similarity < skip_threshold:
            skipped_count += 1
            continue

        # Pick a document_identifier — prefer one already on a chunk (set by smart chunking),
        # else fall back to filename.
        document_identifier = None
        for r in results:
            ident = (r.get('metadata') or {}).get('document_identifier')
            if ident:
                document_identifier = ident
                break
        if not document_identifier:
            document_identifier = doc.get('filename') or f"Document {doc_id}"

        chunk_texts = [r.get('text') or r.get('document') or '' for r in results]
        chunk_texts = [c for c in chunk_texts if c.strip()]
        if not chunk_texts:
            skipped_count += 1
            continue

        per_doc_chunks.append((str(doc_id), document_identifier, chunk_texts))

    _skr_trace(
        f"FANOUT stage 1: {len(per_doc_chunks)} docs to extract from, "
        f"{skipped_count} skipped (out of {len(documents)} total)"
    )

    if not per_doc_chunks:
        return (
            f"[FANOUT: no documents had content above similarity threshold "
            f"({skip_threshold}) for query '{query}'. "
            f"All {len(documents)} agent documents were checked.]"
        )

    # ---- Stage 2: parallel Haiku extraction ----
    findings = {}  # doc_id -> {document_identifier, finding}
    extraction_failures = 0

    def _extract(item):
        doc_id, document_identifier, chunk_texts = item
        finding = _fanout_extract_for_doc(query, doc_id, document_identifier, chunk_texts)
        return doc_id, document_identifier, finding

    with ThreadPoolExecutor(max_workers=max(1, parallel)) as executor:
        future_map = {executor.submit(_extract, item): item for item in per_doc_chunks}
        for future in as_completed(future_map):
            try:
                doc_id, document_identifier, finding = future.result()
                if finding is None:
                    extraction_failures += 1
                    findings[doc_id] = {
                        'document_identifier': document_identifier,
                        'finding': '[extraction unavailable — model error]',
                    }
                elif finding.strip().upper() == 'NULL':
                    # Skip docs with no relevant content
                    continue
                else:
                    findings[doc_id] = {
                        'document_identifier': document_identifier,
                        'finding': finding,
                    }
            except Exception as e:
                extraction_failures += 1
                logging.warning(f"FANOUT extraction worker error: {e}")

    _skr_trace(
        f"FANOUT stage 2: {len(findings)} findings, "
        f"{extraction_failures} extraction failures"
    )

    # ---- Stage 3: reduce / format ----
    if not findings:
        return (
            f"[FANOUT: searched {len(per_doc_chunks)} documents for '{query}' but "
            f"none returned a relevant finding. {skipped_count} additional docs were "
            f"below similarity threshold and not searched.]"
        )

    parts = [
        f"[FANOUT search across agent knowledge for '{query}': {len(findings)} documents "
        f"returned a finding; {skipped_count} were below similarity threshold; "
        f"{extraction_failures} had extraction errors.]\n"
    ]

    # Sort findings deterministically by document_identifier so the output is stable
    sorted_findings = sorted(
        findings.items(),
        key=lambda kv: (kv[1]['document_identifier'] or '', kv[0])
    )

    for doc_id, info in sorted_findings:
        parts.append(f"=== {info['document_identifier']} ===")
        parts.append(info['finding'])

        # Append knowledge reference info so downstream tools can resolve to a knowledge_id
        if documents:
            for d in documents:
                if str(d.get('document_id')) == doc_id:
                    parts.append(
                        f"[Knowledge Reference Info: document_id={doc_id} | "
                        f"knowledge_id={d.get('knowledge_id')} | agent_id={agent_id}]"
                    )
                    break
        parts.append("")  # blank separator

    return "\n".join(parts)


# ===== Phase 3: Parent-child retrieval =====
#
# The vector store holds small chunks (≤1024 tokens after Phase 1/2 caps) so
# similarity matching is precise. But once we've found the right chunks, the
# LLM almost always benefits from seeing the *page* the chunks live on — the
# row above an invoice line, the header of a table, the paragraph framing a
# clause. Parent-child retrieval groups the matched chunks by (document_id,
# page_number), fetches the parent page text once, and returns that to the
# LLM annotated with "matched N chunk(s) on this page" so the model knows
# which content earned the page its slot in the bundle.

def _parent_child_format(results: list, agent_id: int, user_id: Optional[str],
                          query: str, documents: list = None) -> Optional[str]:
    """
    Group matched chunks by (document_id, page_number), fetch each parent page's
    full text from DocumentPages, and return a formatted bundle. Returns the
    formatted string, or None if disabled / DB unavailable / no parent pages
    could be fetched (callers fall back to the per-chunk loop).
    """
    if not results:
        return None

    # Bucket matched chunks by parent page, preserving discovery order so the
    # highest-similarity page lands at the top of the bundle.
    parent_order = []                 # list of (doc_id_str, page_int)
    parent_buckets: Dict[tuple, dict] = {}

    for r in results:
        if not isinstance(r, dict):
            continue
        meta = r.get('metadata', {}) or {}
        text = r.get('document') or r.get('text') or ''
        doc_id = str(meta.get('document_id') or '')
        page_raw = meta.get('page_number')
        try:
            page = int(page_raw) if page_raw is not None else None
        except (TypeError, ValueError):
            page = None
        if not doc_id or page is None:
            continue
        key = (doc_id, page)
        if key not in parent_buckets:
            parent_buckets[key] = {'meta': meta, 'matched_chunks': []}
            parent_order.append(key)
        if text.strip():
            parent_buckets[key]['matched_chunks'].append(text)

    if not parent_buckets:
        return None

    per_page_cap = int(getattr(cfg, 'KNOWLEDGE_PARENT_PAGE_CHAR_CAP', 12000))
    total_cap = int(getattr(cfg, 'KNOWLEDGE_PARENT_TOTAL_CHAR_CAP', 80000))

    conn = get_db_connection()
    if not conn:
        return None

    formatted_blocks = []
    total_chars = 0
    pages_fetched = 0
    pages_skipped_cap = 0

    try:
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        for (doc_id, page) in parent_order:
            entry = parent_buckets[(doc_id, page)]
            meta = entry['meta']
            cursor.execute(
                """
                SELECT dp.full_text, d.filename
                FROM DocumentPages dp
                JOIN Documents d ON dp.document_id = d.document_id
                WHERE dp.document_id = ? AND dp.page_number = ?
                """,
                doc_id, page,
            )
            row = cursor.fetchone()
            if not row or not row[0]:
                continue
            page_text = row[0]
            filename = row[1] or meta.get('filename', 'unknown')

            # Per-page truncation safety belt.
            if len(page_text) > per_page_cap:
                page_text = page_text[:per_page_cap] + (
                    f"\n... [page truncated at {per_page_cap:,} chars to fit context]"
                )

            # Total-context safety belt: stop adding pages once we've used the
            # bundle's char budget. Earlier (higher-similarity) pages are
            # preferred — this is why we walk parent_order in discovery order.
            if total_chars + len(page_text) > total_cap:
                pages_skipped_cap = len(parent_order) - parent_order.index((doc_id, page))
                formatted_blocks.append(
                    f"\n[... {pages_skipped_cap} additional parent page(s) omitted "
                    f"to stay under {total_cap:,}-char context cap.]"
                )
                break

            # Header — match the convention in _format_chunk_for_ai so the LLM
            # sees consistent labelling between NEEDLE and the fallback paths.
            doc_identifier = meta.get('document_identifier')
            breadcrumb = meta.get('section_breadcrumb')
            section_summary = meta.get('section_summary')
            header_bits = [doc_identifier or filename]
            if breadcrumb:
                header_bits.append(breadcrumb)
            header_bits.append(f"page {page}")
            header_line = " — ".join(str(b) for b in header_bits if b)

            block_lines = [f"--- {header_line} ---"]
            if section_summary:
                block_lines.append(f"[Section: {section_summary}]")
            matched_n = len(entry['matched_chunks'])
            if matched_n:
                block_lines.append(
                    f"[Matched {matched_n} chunk(s) on this page for query — full page text follows]"
                )
            block_lines.append(page_text)

            # Knowledge Reference Info — once per parent page (not per chunk).
            if documents:
                for doc in documents:
                    if str(doc.get('document_id')) == doc_id:
                        block_lines.append(
                            f"[Knowledge Reference Info: document_id={doc_id} | "
                            f"page_number={page} | knowledge_id={doc.get('knowledge_id')} | "
                            f"agent_id={agent_id}]"
                        )
                        break

            formatted_blocks.append("\n".join(block_lines))
            total_chars += len(page_text)
            pages_fetched += 1
    except Exception as e:
        _skr_trace(f"NEEDLE parent-child fetch error: {e}")
        logging.warning(f"Parent-child retrieval DB error (falling back to chunks): {e}")
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if pages_fetched == 0:
        return None

    _skr_trace(
        f"NEEDLE parent-child: returned {pages_fetched} parent page(s) from "
        f"{len(results)} chunk matches ({pages_skipped_cap} skipped to cap)."
    )
    return "\n\n".join(formatted_blocks)


def smart_knowledge_retrieval(query: str, agent_id: int, user_id: str = None,
                               document_contents: dict = None, documents: list = None,
                               load_contents=None, doc_count_hint: int = None,
                               total_chars_hint: int = None,
                               chat_history=None,
                               latest_user_input: Optional[str] = None) -> str:
    """
    Smart retrieval path for when brute force won't fit in context.
    Routes between NEEDLE (vector search) and AGGREGATE (summary scan) strategies.

    document_contents may be None if the caller chose not to bulk-load text upfront
    (the typical case for large agents). When a fallback path needs the full text,
    load_contents() is called to lazily load it.

    chat_history (optional) is the recent conversation in any of the supported
    shapes. latest_user_input (optional) is the literal user message that
    triggered this turn — distinct from `query`, which is the agent's
    paraphrased tool argument. Together they let the LLM document detector
    see the same signal a human reading the conversation would see. Closes
    BUG-NEEDLE-WRONG-DOC-AFTER-CLARIFY. The detector only fires when there
    are ≥2 knowledge documents on the agent.
    """
    # ── BUG-NEEDLE-WRONG-DOC-AFTER-CLARIFY: LLM document detector ──
    # Before routing to NEEDLE/AGGREGATE/FANOUT, ask a small LLM whether the
    # user's current question (with chat-history context) points at ONE
    # specific knowledge document with HIGH confidence. If yes, hard-filter
    # the rest of this retrieval to that document. If no, behave as before.
    forced_document_id: Optional[str] = None
    if documents and len(documents) >= 2:
        try:
            forced_document_id = _llm_detect_named_document(
                chat_history=chat_history,
                latest_question=query,
                available_documents=documents,
                original_user_input=latest_user_input,
            )
        except Exception as e:
            # Best-effort: any detector failure falls back to today's retrieval.
            _skr_trace(f"LLM doc-detector raised, ignoring: {e}")
            logging.warning(f"LLM doc-detector raised (continuing without filter): {e}")
            forced_document_id = None

    if forced_document_id:
        # Narrow `documents` to just the matched doc so AGGREGATE/FANOUT paths
        # that iterate the list also stay scoped. NEEDLE's vector search picks
        # up the filter via `forced_document_id` directly below.
        documents = [d for d in documents if str(d.get('document_id')) == forced_document_id]
    # Lazy-load wrapper: returns document_contents, loading once if needed.
    def _ensure_contents():
        nonlocal document_contents
        if document_contents is None and load_contents is not None:
            try:
                document_contents = load_contents() or {}
                _skr_trace(f"Lazy-loaded document_contents: {len(document_contents)} docs")
            except Exception as e:
                logging.warning(f"Lazy load of document_contents failed: {e}")
                document_contents = {}
        return document_contents or {}

    # Stats for the router — prefer hints (cheap) over loading text.
    if document_contents is not None:
        total_chars = sum(
            len(page_text)
            for content in document_contents.values()
            for page_text in content['pages'].values()
        )
        doc_count = len(document_contents)
    else:
        total_chars = total_chars_hint if total_chars_hint is not None else 0
        doc_count = doc_count_hint if doc_count_hint is not None else (len(documents) if documents else 0)

    # Route the query
    route = route_knowledge_query(query, doc_count, total_chars)
    _skr_trace(f"Query routed as {route}: '{query[:80]}'")

    # FANOUT: per-document map-reduce for cross-document comparison queries.
    # Falls through to NEEDLE if FANOUT is disabled or finds nothing usable.
    if route == 'FANOUT':
        if not getattr(cfg, 'KNOWLEDGE_FANOUT_ENABLED', True):
            _skr_trace("FANOUT disabled by config — downgrading to NEEDLE for this query")
            route = 'NEEDLE'
        elif not documents:
            _skr_trace("FANOUT: no documents list provided — downgrading to NEEDLE")
            route = 'NEEDLE'
        else:
            _skr_trace(f"FANOUT: dispatching across {len(documents)} agent documents")
            try:
                bundle = fanout_knowledge_retrieval(
                    query=query,
                    agent_id=agent_id,
                    user_id=user_id,
                    documents=documents,
                )
                if bundle and not bundle.startswith("[FANOUT: no documents had content"):
                    return bundle
                # If FANOUT produced nothing usable, fall through to NEEDLE for a best-effort
                _skr_trace("FANOUT produced no findings — falling through to NEEDLE")
                route = 'NEEDLE'
            except Exception as e:
                _skr_trace(f"FANOUT error: {e} — falling through to NEEDLE")
                logging.warning(f"FANOUT retrieval failed, falling through to NEEDLE: {e}")
                route = 'NEEDLE'

    if route == 'NEEDLE':
        # Vector search for specific information. When the LLM doc detector
        # matched a specific document above, hard-filter on it so retrieval
        # cannot return chunks from any other document.
        results = search_knowledge_vectors(
            query, agent_id, user_id,
            forced_document_id=forced_document_id,
        )
        _skr_trace(
            f"Vector search returned {len(results)} results"
            + (f" (filtered to doc {forced_document_id})" if forced_document_id else "")
        )

        if results:
            # Format vector search results, surfacing section context when present
            response_parts = [f"[Knowledge search: found {len(results)} relevant chunks for '{query}']\n"]

            # ── BUG-LARGE-PDF-DEGRADATION fix #2: include each contributing
            # document's pre-computed knowledge_summary in the bundle.
            # The retriever often pulls line-item chunks but misses the
            # summary/totals chunks on cover/header pages. The summary is
            # already generated at upload time (see generate_knowledge_summary)
            # and stored in Documents.document_metadata.knowledge_summary
            # — we just weren't using it in the NEEDLE path. Now we collect
            # the unique document_ids from the vector matches and prepend
            # their summaries once each, gated by a config flag so it can
            # be turned off if it causes regressions.
            include_summaries = getattr(
                cfg, 'KNOWLEDGE_INCLUDE_SUMMARY_IN_NEEDLE', True,
            )
            if include_summaries:
                doc_ids_seen = []
                for r in results:
                    if isinstance(r, dict):
                        doc_id = r.get('metadata', {}).get('document_id', '')
                        if doc_id and doc_id not in doc_ids_seen:
                            doc_ids_seen.append(doc_id)
                if doc_ids_seen:
                    try:
                        all_summaries = get_all_knowledge_summaries(agent_id, user_id)
                        summary_by_id = {
                            s['document_id']: s for s in all_summaries
                            if s.get('summary')
                        }
                        # Only include summaries for documents that
                        # actually contributed chunks to this query.
                        included = [
                            summary_by_id[d] for d in doc_ids_seen
                            if d in summary_by_id
                        ]
                        if included:
                            response_parts.append(
                                "[Per-document summaries — quick reference "
                                "for header/total/identifier facts that "
                                "live on cover/summary pages:]"
                            )
                            for s in included:
                                response_parts.append(
                                    f"📄 {s['filename']} ({s['document_type']})\n"
                                    f"{s['summary']}\n"
                                )
                            response_parts.append("[End summaries — retrieved chunks follow:]\n")
                            _skr_trace(
                                f"NEEDLE: prepended {len(included)} document "
                                f"summaries from {len(doc_ids_seen)} contributing docs"
                            )
                    except Exception as e:
                        # Best-effort: never let summary lookup break NEEDLE
                        _skr_trace(f"NEEDLE: summary lookup failed: {e}")
                        logging.warning(
                            f"NEEDLE summary lookup failed (continuing without): {e}"
                        )

            # Phase 3: parent-child retrieval. Group matched chunks by their
            # parent page and return the full page text — gives the LLM the
            # surrounding context (rows above/below, table header, paragraph
            # framing) that the small embedding chunks lose. Falls back to the
            # per-chunk format if disabled, the DB is unreachable, or no parent
            # pages were resolvable from the chunk metadata.
            use_parent_child = getattr(cfg, 'KNOWLEDGE_PARENT_CHILD_RETRIEVAL', True)
            parent_bundle = None
            if use_parent_child:
                parent_bundle = _parent_child_format(
                    results, agent_id, user_id, query, documents=documents
                )

            if parent_bundle:
                response_parts.append(parent_bundle)
                return "\n".join(response_parts)

            # Fallback: per-chunk formatting (legacy behaviour).
            for r in results:
                if isinstance(r, dict):
                    meta = r.get('metadata', {})
                    text = r.get('document', r.get('text', ''))
                    filename = meta.get('filename', 'unknown')
                    page = meta.get('page_number', '?')
                    doc_id = meta.get('document_id', '')
                    response_parts.append(_format_chunk_for_ai(meta, text, filename, page))

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
            return _format_knowledge_response(_ensure_contents(), apply_caps=True)

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
            # No summaries — use wider vector search to get representative chunks from many docs.
            # Honour the doc-detector hard filter if it matched a specific document.
            _skr_trace("AGGREGATE without summaries: using wide vector search (top-30)")
            wide_results = search_knowledge_vectors(
                query, agent_id, user_id, top_k=30,
                forced_document_id=forced_document_id,
            )
            
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
                            response_parts.append(_format_chunk_for_ai(meta, text, filename, page))
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
                return _format_knowledge_response(_ensure_contents(), apply_caps=True)


def _count_agent_knowledge_pages(agent_id, user_id=None) -> int:
    """
    Cheap pre-flight COUNT of total pages across all of an agent's active knowledge documents.
    Used to decide between brute-force-all and smart retrieval routing without bulk-loading text.
    Mirrors the visibility rules used by get_agent_knowledge_documents.
    """
    try:
        import pyodbc
        conn = pyodbc.connect(
            f"DRIVER={{SQL Server}};SERVER={cfg.DATABASE_SERVER};DATABASE={cfg.DATABASE_NAME};UID={cfg.DATABASE_UID};PWD={cfg.DATABASE_PWD}"
        )
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        scoped_user = 'USER' if user_id is None else str(user_id)
        cursor.execute("""
            SELECT COUNT(*)
            FROM DocumentPages dp
            JOIN AgentKnowledge ak ON dp.document_id = ak.document_id
            WHERE ak.agent_id = ? AND ak.is_active = 1
              AND (
                  ISNULL(ak.added_by, 'USER') = 'USER'
                  OR
                  ISNULL(ak.added_by, 'USER') = ?
              )
        """, agent_id, scoped_user)
        row = cursor.fetchone()
        conn.close()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception as e:
        logging.warning(f"Page count preflight failed for agent {agent_id}: {e}")
        return 0


def _load_agent_knowledge_contents(document_ids, documents):
    """
    Bulk-load full page text for the given document IDs. Returns the document_contents dict
    in the shape used by _format_knowledge_response and smart_knowledge_retrieval:
        { doc_id: { 'filename': str, 'document_type': str, 'pages': { page_num: text } } }

    Each page's text is appended with a Knowledge Reference Info footer so the agent can use
    add/update knowledge tools against the right rows.
    """
    if not document_ids:
        return {}
    try:
        import pyodbc
        conn = pyodbc.connect(
            f"DRIVER={{SQL Server}};SERVER={cfg.DATABASE_SERVER};DATABASE={cfg.DATABASE_NAME};UID={cfg.DATABASE_UID};PWD={cfg.DATABASE_PWD}"
        )
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        placeholders = ','.join(['?'] * len(document_ids))
        cursor.execute(f"""
            SELECT d.document_id, d.filename, d.document_type, dp.page_number, dp.full_text
            FROM Documents d
            JOIN DocumentPages dp ON d.document_id = dp.document_id
            WHERE d.document_id IN ({placeholders})
            ORDER BY d.filename, dp.page_number
        """, document_ids)

        document_contents = {}
        for doc_id, filename, doc_type, page_num, full_text in cursor.fetchall():
            if doc_id not in document_contents:
                document_contents[doc_id] = {
                    'filename': filename,
                    'document_type': doc_type,
                    'pages': {}
                }
            document_contents[doc_id]['pages'][page_num] = full_text

            # Append knowledge reference footer
            for document in documents:
                if document.get('document_id') == doc_id:
                    knowledge_id = document.get('knowledge_id')
                    a_id = document.get('agent_id')
                    footer = (
                        f'\n\n[Knowledge Reference Info: document_id={doc_id} | '
                        f'page_number={str(page_num)} | knowledge_id={knowledge_id} | '
                        f'agent_id={a_id}]'
                    )
                    document_contents[doc_id]['pages'][page_num] += footer
                    break
        conn.close()
        return document_contents
    except Exception as e:
        logging.error(f"Failed to load agent knowledge contents: {e}")
        return {}


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

    def __init__(self, agent_id, user_id=None, get_chat_history=None,
                  get_latest_user_input=None):
        """Initialize knowledge tool with agent ID.

        Args:
            agent_id: the agent these tools serve
            user_id: scopes user-specific knowledge
            get_chat_history: optional callable returning the latest chat history
                              (LangChain message list). When provided, the tool
                              passes it through to `smart_knowledge_retrieval`
                              so the LLM document detector can decide whether
                              the current question is a follow-up about a
                              previously-named document.
            get_latest_user_input: optional callable returning the literal
                              current user message (NOT the agent's paraphrased
                              tool-query argument). When the calling LangChain
                              agent rewrites the user's question into a search
                              term (a common pattern), the disambiguation cue
                              ("I mean the Continental invoice") often gets
                              stripped. This callback hands the detector the
                              raw user message so it has every signal needed.
                              Without it, the detector only sees the tool's
                              `query` and recent chat_history.
        """
        self.agent_id = agent_id
        self.user_id = user_id
        self.get_chat_history = get_chat_history
        self.get_latest_user_input = get_latest_user_input
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

                document_ids = [doc['document_id'] for doc in documents]

                # Pre-flight: count total pages without bulk-loading any text
                total_pages = _count_agent_knowledge_pages(self.agent_id, self.user_id)
                threshold = cfg.KNOWLEDGE_BRUTE_FORCE_PAGE_THRESHOLD

                _skr_trace(
                    f"Knowledge routing: {total_pages} pages, threshold={threshold}, "
                    f"smart={cfg.KNOWLEDGE_ENABLE_SMART_RETRIEVAL}"
                )

                if total_pages <= threshold:
                    # Brute force: small enough to send everything — load and dump uncapped
                    _skr_trace(f"PATH: BRUTE FORCE ({total_pages} pages <= {threshold})")
                    logging.info(f"Knowledge brute force: {total_pages} pages ≤ {threshold} threshold")
                    document_contents = _load_agent_knowledge_contents(document_ids, documents)
                    if not document_contents:
                        return "No content found in the agent's knowledge documents."
                    return _format_knowledge_response(document_contents, apply_caps=False)
                elif cfg.KNOWLEDGE_ENABLE_SMART_RETRIEVAL:
                    # Smart retrieval — defer text loading to fallback paths via lazy loader
                    _skr_trace(f"PATH: SMART RETRIEVAL ({total_pages} pages > {threshold})")
                    logging.info(f"Knowledge smart retrieval: {total_pages} pages > {threshold} threshold")
                    # Snapshot chat history AND the literal user input so the
                    # LLM doc detector sees the actual user message — not just
                    # the agent's paraphrased tool query (which often strips
                    # the document name).
                    chat_history_snapshot = None
                    latest_user_input_snapshot = None
                    if self.get_chat_history is not None:
                        try:
                            chat_history_snapshot = self.get_chat_history()
                        except Exception as e:
                            logging.debug(f"get_chat_history callback raised, ignoring: {e}")
                    if self.get_latest_user_input is not None:
                        try:
                            latest_user_input_snapshot = self.get_latest_user_input()
                        except Exception as e:
                            logging.debug(f"get_latest_user_input callback raised, ignoring: {e}")
                    return smart_knowledge_retrieval(
                        query=query,
                        agent_id=self.agent_id,
                        user_id=self.user_id,
                        documents=documents,
                        load_contents=lambda: _load_agent_knowledge_contents(document_ids, documents),
                        doc_count_hint=len(documents),
                        chat_history=chat_history_snapshot,
                        latest_user_input=latest_user_input_snapshot,
                    )
                else:
                    # Smart retrieval disabled — load and use capped brute force
                    logging.info(f"Knowledge capped response: {total_pages} pages (smart retrieval disabled)")
                    document_contents = _load_agent_knowledge_contents(document_ids, documents)
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
                documents = get_agent_knowledge_documents(self.agent_id, self.user_id)
                if not documents:
                    return "No knowledge documents available for this agent."

                document_ids = [doc['document_id'] for doc in documents]

                # Pre-flight: count total pages without bulk-loading text
                total_pages = _count_agent_knowledge_pages(self.agent_id, self.user_id)
                threshold = cfg.KNOWLEDGE_BRUTE_FORCE_PAGE_THRESHOLD

                if total_pages <= threshold:
                    logging.info(f"User knowledge brute force: {total_pages} pages ≤ {threshold} threshold")
                    document_contents = _load_agent_knowledge_contents(document_ids, documents)
                    if not document_contents:
                        return "No content found in the agent's knowledge documents."
                    return _format_knowledge_response(document_contents, apply_caps=False)
                elif cfg.KNOWLEDGE_ENABLE_SMART_RETRIEVAL:
                    logging.info(f"User knowledge smart retrieval: {total_pages} pages > {threshold} threshold")
                    chat_history_snapshot = None
                    latest_user_input_snapshot = None
                    if self.get_chat_history is not None:
                        try:
                            chat_history_snapshot = self.get_chat_history()
                        except Exception as e:
                            logging.debug(f"get_chat_history callback raised, ignoring: {e}")
                    if self.get_latest_user_input is not None:
                        try:
                            latest_user_input_snapshot = self.get_latest_user_input()
                        except Exception as e:
                            logging.debug(f"get_latest_user_input callback raised, ignoring: {e}")
                    return smart_knowledge_retrieval(
                        query="retrieve user document content",
                        agent_id=self.agent_id,
                        user_id=self.user_id,
                        documents=documents,
                        load_contents=lambda: _load_agent_knowledge_contents(document_ids, documents),
                        doc_count_hint=len(documents),
                        chat_history=chat_history_snapshot,
                        latest_user_input=latest_user_input_snapshot,
                    )
                else:
                    logging.info(f"User knowledge capped response: {total_pages} pages (smart retrieval disabled)")
                    document_contents = _load_agent_knowledge_contents(document_ids, documents)
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