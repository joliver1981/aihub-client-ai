"""
Builder-specific document search routes.

These routes are dedicated to the Builder Agent and do NOT modify
any existing platform document routes. They provide search capabilities
that the builder agent needs to answer document-related queries.
"""

import os
import json
import logging
from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

builder_document_bp = Blueprint('builder_documents', __name__, url_prefix='/api/builder/documents')


def api_key_or_session_required_lazy(min_role=2):
    """Lazy wrapper for auth decorator — imports at call time to avoid circular imports."""
    from functools import wraps
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from role_decorators import api_key_or_session_required
            # Apply the real decorator at call time
            decorated = api_key_or_session_required(min_role=min_role)(f)
            return decorated(*args, **kwargs)
        return wrapper
    return decorator


def get_db_connection():
    """Get database connection using the standard AppUtils pattern."""
    from AppUtils import get_db_connection as _get_conn
    return _get_conn()


def get_db_connection_string():
    """Get connection string for DocUtils using the standard AppUtils pattern."""
    from AppUtils import get_db_connection_string as _get_conn_str
    return _get_conn_str()


@builder_document_bp.route('/search', methods=['POST'])
@api_key_or_session_required_lazy()
def builder_search_documents():
    """
    Builder-agent-specific document search endpoint.
    
    Performs content-based search across documents using DocUtils.document_search.
    Returns structured results suitable for the builder agent to present.
    
    Request body:
        {
            "query": "inventory stock levels",        # Required: search query
            "document_type": "lease_agreement",       # Optional: filter by type
            "max_results": 20                         # Optional: max results (default 20)
        }
    
    Returns:
        {
            "results": [...],
            "total_results": N,
            "query": "..."
        }
    """
    
    try:
        data = request.json or {}
        query = data.get('query', data.get('search', ''))
        document_type = data.get('document_type', '')
        max_results = int(data.get('max_results', 20))
        
        if not query:
            return jsonify({
                'error': 'Missing required field: query',
                'results': [],
                'total_results': 0
            }), 400
        
        conn_str = get_db_connection_string()
        
        # Try DocUtils content search first
        content_results = []
        try:
            from DocUtils import document_search
            search_json = document_search(
                conn_str,
                document_type=document_type,
                search_query=query,
                field_filters=None,
                include_metadata=True,
                max_results=max_results,
                user_question=query,
                check_completeness=False
            )
            search_data = json.loads(search_json) if isinstance(search_json, str) else search_json
            content_results = search_data.get('results', [])
            
            if content_results:
                return jsonify({
                    'results': content_results,
                    'total_results': len(content_results),
                    'query': query,
                    'search_type': 'content'
                })
            else:
                logger.info(f"DocUtils content search returned 0 results for '{query}', trying filename search")
            
        except ImportError:
            logger.warning("DocUtils not available, falling back to filename search")
        except Exception as e:
            logger.warning(f"DocUtils search failed: {e}, falling back to filename search")
        
        # Fallback: filename + document_type search via SQL
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        sql = """
            SELECT TOP (?)
                d.document_id,
                d.filename,
                d.document_type,
                d.page_count,
                d.processed_at,
                d.reference_number,
                d.original_path
            FROM Documents d
            WHERE d.is_knowledge_document = 0
              AND (d.filename LIKE ? OR d.document_type LIKE ?)
            ORDER BY d.processed_at DESC
        """
        like_pattern = f'%{query}%'
        cursor.execute(sql, (max_results, like_pattern, like_pattern))
        
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
        results = []
        for row in rows:
            doc = {}
            for i, col in enumerate(columns):
                val = row[i]
                if val is not None and not isinstance(val, (str, int, float, bool)):
                    val = str(val)
                doc[col] = val
            results.append(doc)
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'results': results,
            'total_results': len(results),
            'query': query,
            'search_type': 'filename'
        })
        
    except Exception as e:
        logger.error(f"Builder document search error: {e}", exc_info=True)
        return jsonify({
            'error': str(e),
            'results': [],
            'total_results': 0
        }), 500


@builder_document_bp.route('/types', methods=['GET'])
@api_key_or_session_required_lazy()
def builder_list_document_types():
    """List all document types in the system (for filter options)."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        cursor.execute("""
            SELECT document_type, COUNT(*) as doc_count
            FROM Documents
            WHERE is_knowledge_document = 0 AND document_type IS NOT NULL
            GROUP BY document_type
            ORDER BY doc_count DESC
        """)
        
        types = []
        for row in cursor.fetchall():
            types.append({
                'document_type': row[0],
                'count': row[1]
            })
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'document_types': types,
            'total_types': len(types)
        })
        
    except Exception as e:
        logger.error(f"Builder document types error: {e}", exc_info=True)
        return jsonify({'error': str(e), 'document_types': []}), 500
