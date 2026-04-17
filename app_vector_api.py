from flask import Flask, request, jsonify
import os
import json
import logging
from logging.handlers import WatchedFileHandler
from typing import Dict, Any, List, Optional

import config as cfg
from LLMDocumentVectorEngine import LLMDocumentVectorEngine, ChromaDBStore
from CommonUtils import rotate_logs_on_startup, get_log_path


rotate_logs_on_startup(os.getenv('DOC_VECTOR_API_LOG', get_log_path('doc_vector_api_log.txt')))

# Configure logging
logger = logging.getLogger("DocVectorAPI")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=os.getenv('DOC_VECTOR_API_LOG', get_log_path('doc_vector_api_log.txt')), encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)

# Initialize Flask app
app = Flask(__name__)

# Global vector engine instance
vector_engine = None

# Configuration from environment variables
DEFAULT_VECTOR_DB_PATH = os.getenv('VECTOR_DB_PATH', './chroma_db')
DEFAULT_COLLECTION_NAME = os.getenv('VECTOR_COLLECTION_NAME', 'documents')
API_KEY = os.getenv('VECTOR_API_KEY', '')  # For basic API security


def get_vector_engine():
    """Initialize and return the vector engine singleton"""
    global vector_engine
    if vector_engine is None:
        logger.info("Initializing vector engine...")
        vector_engine = LLMDocumentVectorEngine(vector_store_class=ChromaDBStore)
        vector_engine.initialize(
            path=DEFAULT_VECTOR_DB_PATH,
            collection_name=DEFAULT_COLLECTION_NAME
        )
    return vector_engine


@app.before_request
def authenticate():
    """Simple API key authentication middleware"""
    if API_KEY:  # Only check if API_KEY is configured
        # Skip auth for health check endpoint
        if request.path == '/health':
            return
            
        # Check if API key is provided and valid (header only, never query string)
        request_api_key = request.headers.get('X-API-Key')
        if not request_api_key or request_api_key != API_KEY:
            return jsonify({'error': 'Unauthorized. Invalid or missing API key.'}), 401


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok',
        'message': 'Vector API is operational'
    })


@app.route('/stats', methods=['GET'])
def get_stats():
    """Get vector database statistics"""
    try:
        engine = get_vector_engine()
        stats = engine.get_stats()
        return jsonify({
            'status': 'success',
            'data': stats
        })
    except Exception as e:
        logger.error(f"Error getting stats: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/documents', methods=['POST'])
def add_document():
    """Add a single document to the vector store"""
    try:
        data = request.json
        if not data:
            return jsonify({
                'status': 'error',
                'message': 'No data provided'
            }), 400
            
        # Validate required fields
        required_fields = ['text', 'metadata', 'id']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'status': 'error',
                    'message': f'Missing required field: {field}'
                }), 400
        
        # Add the document
        engine = get_vector_engine()
        success = engine.add_document(
            document_text=data['text'],
            metadata=data['metadata'],
            doc_id=data['id']
        )
        
        if success:
            return jsonify({
                'status': 'success',
                'message': f'Document {data["id"]} added successfully'
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to add document'
            }), 500
            
    except Exception as e:
        logger.error(f"Error adding document: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/documents/batch', methods=['POST'])
def add_documents_batch():
    """Add multiple documents to the vector store in a batch"""
    try:
        data = request.json
        if not data or not isinstance(data, list):
            return jsonify({
                'status': 'error',
                'message': 'Invalid data. Expected a list of documents.'
            }), 400
            
        # Validate each document in the batch
        for i, doc in enumerate(data):
            required_fields = ['text', 'metadata', 'id']
            for field in required_fields:
                if field not in doc:
                    return jsonify({
                        'status': 'error',
                        'message': f'Document at index {i} is missing required field: {field}'
                    }), 400
        
        # Add the batch
        engine = get_vector_engine()
        success = engine.add_documents(data)
        
        if success:
            return jsonify({
                'status': 'success',
                'message': f'Added {len(data)} documents successfully'
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to add document batch'
            }), 500
            
    except Exception as e:
        logger.error(f"Error adding document batch: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/documents/<doc_id>', methods=['GET'])
def get_document(doc_id):
    """Get a document by ID"""
    try:
        print(f'Call to /documents/{doc_id}...')
        engine = get_vector_engine()
        document = engine.get_document(doc_id)
        
        if document:
            return jsonify({
                'status': 'success',
                'data': document
            })
        else:
            return jsonify({
                'status': 'error',
                'message': f'Document not found: {doc_id}'
            }), 404
            
    except Exception as e:
        logger.error(f"Error getting document: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/documents/<doc_id>', methods=['PUT'])
def update_document(doc_id):
    """Update a document by ID"""
    try:
        data = request.json
        if not data:
            return jsonify({
                'status': 'error',
                'message': 'No data provided'
            }), 400
            
        # Validate required fields
        required_fields = ['text', 'metadata']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'status': 'error',
                    'message': f'Missing required field: {field}'
                }), 400
        
        # Update the document
        engine = get_vector_engine()
        success = engine.update_document(
            doc_id=doc_id,
            document_text=data['text'],
            metadata=data['metadata']
        )
        
        if success:
            return jsonify({
                'status': 'success',
                'message': f'Document {doc_id} updated successfully'
            })
        else:
            return jsonify({
                'status': 'error',
                'message': f'Document not found or update failed: {doc_id}'
            }), 404
            
    except Exception as e:
        logger.error(f"Error updating document: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/documents/<doc_id>', methods=['DELETE'])
def delete_document(doc_id):
    """Delete a document by ID"""
    try:
        engine = get_vector_engine()
        success = engine.delete_document(doc_id)
        
        if success:
            return jsonify({
                'status': 'success',
                'message': f'Document {doc_id} deleted successfully'
            })
        else:
            return jsonify({
                'status': 'error',
                'message': f'Document not found or deletion failed: {doc_id}'
            }), 404
            
    except Exception as e:
        logger.error(f"Error deleting document: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/search', methods=['POST'])
def search_documents():
    """Search for documents similar to the query"""
    try:
        print(f'Call to /search...')
        data = request.json
        if not data or 'query' not in data:
            return jsonify({
                'status': 'error',
                'message': 'No query provided'
            }), 400
            
        # Get search parameters
        query = data['query']
        filters = data.get('filters')
        limit = int(data.get('limit', 10))
        min_score = float(data.get('min_score', 0.0))
        
        # Perform the search
        print('Starting engine...')
        engine = get_vector_engine()

        print('Searching...')
        results = engine.search(
            query=query,
            filters=filters,
            limit=limit,
            min_score=min_score
        )

        # print(86 * '$')
        # print(86 * '$')
        # print('Total Results before AI formatting:', len(results))
        # print('FORMATTED RESULTS FOR AI (PREVIEW)')
        # ai_results = format_search_results_for_ai(results)
        # print(ai_results)
        # print(86 * '$')
        # print(86 * '$')
        
        return jsonify({
            'status': 'success',
            'count': len(results),
            'results': results
        })
            
    except Exception as e:
        logger.error(f"Error searching documents: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500
    

@app.route('/search_for_ai', methods=['POST'])
def search_documents_for_ai():
    """Search for documents similar to the query"""
    try:
        print(f'Call to /search_for_ai...')
        data = request.json
        if not data or 'query' not in data:
            return jsonify({
                'status': 'error',
                'message': 'No query provided'
            }), 400
            
        # Get search parameters
        query = data['query']
        filters = data.get('filters')
        limit = int(cfg.VECTOR_SEARCH_RESULTS_RESULT_LIMIT_FOR_AI)
        min_score = float(cfg.VECTOR_SEARCH_RESULTS_MIN_SCORE_FOR_AI)
        
        # Perform the search
        print('Starting engine...')
        engine = get_vector_engine()

        print('Searching...')
        results = engine.search(
            query=query,
            filters=filters,
            limit=limit,
            min_score=min_score
        )

        # print('Formatting results for AI ingestion...')
        # ai_results = format_search_results_for_ai(results)

        # print(86 * '$')
        # print(86 * '$')
        # print('FORMATTED RESULTS FOR AI (PREVIEW):')
        # print(ai_results)
        # print(86 * '$')
        # print(86 * '$')
        
        return jsonify({
            'status': 'success',
            'results': results
        })
            
    except Exception as e:
        logger.error(f"Error searching documents for AI: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500
    

def format_search_results_for_ai(search_results: List[Dict[str, Any]]) -> str:
    """
    Simple function to format search results for AI consumption.
    Takes your existing search results and returns a clean string.
    
    Args:
        search_results: List of search result dictionaries from your vector search
        max_length: Maximum character length for the formatted output
        
    Returns:
        Clean formatted string ready to insert into AI prompts
    """
    
    if not search_results:
        return "No relevant documents found."
    
    formatted_parts = []
    current_length = 0
    max_length = int(cfg.VECTOR_SEARCH_RESULTS_CHAR_LIMIT_FOR_AI)

    for i, result in enumerate(search_results, 1):
        try:
            # Get the chunk text (most relevant part)
            if 'matched_chunk' in result.get('metadata', {}):
                text = result['metadata']['matched_chunk']
            elif 'text' in result:
                text = result['text']
            elif 'document' in result:
                text = result['document']
            else:
                continue  # Skip if no text found
            
            # Get metadata for source reference
            metadata = result.get('metadata', {})
            filename = metadata.get('filename', 'Unknown Document')
            page_num = metadata.get('page_number', '?')
            doc_type = metadata.get('document_type', 'document')
            relevance = result.get('relevance_score', 0.0)
            
            # Clean filename 
            clean_filename = filename.split('/')[-1].split('\\')[-1]
            if '.' in clean_filename:
                clean_filename = '.'.join(clean_filename.split('.')[:-1])
            
            # Format this result
            result_text = f"[Source {i}: {clean_filename} - Page {page_num}] ({doc_type}) (Relevance: {relevance:.2f})\n{text.strip()}\n"
            
            # Check length constraint
            if current_length + len(result_text) > max_length:
                # Try to fit truncated version
                remaining_space = max_length - current_length - 50
                if remaining_space > 100:
                    truncated_text = text[:remaining_space] + "..."
                    result_text = f"[Source {i}: {clean_filename} - Page {page_num}] ({doc_type}) (Relevance: {relevance:.2f})\n{truncated_text}\n"
                    formatted_parts.append(result_text)
                break
            
            formatted_parts.append(result_text)
            current_length += len(result_text)
            
        except Exception as e:
            print(f"Error formatting result {i}: {str(e)}")
            continue
    
    if not formatted_parts:
        return "No relevant document content available."
    
    print('Total Results after AI formatting:', len(formatted_parts))
    
    return "\n".join(formatted_parts)


# ============================================================
# Knowledge Vector Endpoints
# Separate ChromaDB collection for agent knowledge documents
# ============================================================

# Knowledge vector engine singleton (separate from document vectors)
_knowledge_engine = None

KNOWLEDGE_DB_PATH = os.getenv('KNOWLEDGE_VECTOR_DB_PATH', './data/chroma_knowledge')
KNOWLEDGE_COLLECTION = getattr(cfg, 'KNOWLEDGE_VECTOR_COLLECTION', 'agent_knowledge')


def get_knowledge_engine():
    """Initialize and return the knowledge vector engine singleton"""
    global _knowledge_engine
    if _knowledge_engine is None:
        logger.info(f"Initializing knowledge vector engine: path={KNOWLEDGE_DB_PATH}, collection={KNOWLEDGE_COLLECTION}")
        _knowledge_engine = ChromaDBStore()
        _knowledge_engine.initialize(
            path=KNOWLEDGE_DB_PATH,
            collection_name=KNOWLEDGE_COLLECTION,
        )
        logger.info(f"Knowledge vector engine ready. Collection count: {_knowledge_engine.collection.count()}")
    return _knowledge_engine


@app.route('/knowledge/index', methods=['POST'])
def knowledge_index():
    """
    Index knowledge document chunks into the knowledge vector collection.
    
    Expects JSON:
    {
        "documents": ["chunk1 text", "chunk2 text", ...],
        "metadatas": [{"document_id": "...", "agent_id": "...", ...}, ...],
        "ids": ["kb_page1", "kb_page1_c0", ...]
    }
    """
    try:
        data = request.json
        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400

        documents = data.get('documents', [])
        metadatas = data.get('metadatas', [])
        ids = data.get('ids', [])

        if not documents or not ids:
            return jsonify({'status': 'error', 'message': 'documents and ids are required'}), 400

        if len(documents) != len(ids) or len(documents) != len(metadatas):
            return jsonify({'status': 'error', 'message': 'documents, metadatas, and ids must have equal length'}), 400

        engine = get_knowledge_engine()

        # Add in batches to avoid oversized requests
        BATCH = 100
        total_added = 0
        for i in range(0, len(documents), BATCH):
            batch_docs = documents[i:i+BATCH]
            batch_metas = metadatas[i:i+BATCH]
            batch_ids = ids[i:i+BATCH]
            engine.collection.add(
                documents=batch_docs,
                metadatas=batch_metas,
                ids=batch_ids
            )
            total_added += len(batch_docs)
            logger.info(f"Knowledge index batch: added {len(batch_docs)} chunks (total {total_added})")

        return jsonify({
            'status': 'success',
            'message': f'Indexed {total_added} chunks',
            'count': total_added,
            'collection_total': engine.collection.count()
        })

    except Exception as e:
        logger.error(f"Error indexing knowledge: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/knowledge/search', methods=['POST'])
def knowledge_search():
    """
    Search knowledge vectors.
    
    Expects JSON:
    {
        "query": "search text",
        "filters": {"agent_id": "292", ...},  // optional ChromaDB where clause
        "limit": 10  // optional, default 10
    }
    """
    try:
        data = request.json
        if not data or 'query' not in data:
            return jsonify({'status': 'error', 'message': 'query is required'}), 400

        query = data['query']
        filters = data.get('filters')
        limit = int(data.get('limit', 10))

        engine = get_knowledge_engine()
        count = engine.collection.count()

        if count == 0:
            return jsonify({
                'status': 'success',
                'count': 0,
                'results': {'documents': [[]], 'metadatas': [[]], 'distances': [[]]}
            })

        actual_limit = min(limit, count)
        results = engine.collection.query(
            query_texts=[query],
            n_results=actual_limit,
            where=filters,
            include=["documents", "metadatas", "distances"]
        )

        result_count = len(results.get('documents', [[]])[0]) if results.get('documents') else 0

        return jsonify({
            'status': 'success',
            'count': result_count,
            'results': results
        })

    except Exception as e:
        logger.error(f"Error searching knowledge: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/knowledge/delete', methods=['POST'])
def knowledge_delete():
    """
    Delete knowledge vectors by document_id.
    
    Expects JSON:
    {
        "document_id": "abc-123"
    }
    """
    try:
        data = request.json
        if not data or 'document_id' not in data:
            return jsonify({'status': 'error', 'message': 'document_id is required'}), 400

        document_id = data['document_id']
        engine = get_knowledge_engine()

        before_count = engine.collection.count()
        engine.collection.delete(where={'document_id': document_id})
        after_count = engine.collection.count()
        deleted = before_count - after_count

        logger.info(f"Knowledge delete: document_id={document_id}, removed {deleted} chunks")

        return jsonify({
            'status': 'success',
            'message': f'Deleted {deleted} chunks for document {document_id}',
            'deleted': deleted,
            'collection_total': after_count
        })

    except Exception as e:
        logger.error(f"Error deleting knowledge vectors: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/knowledge/stats', methods=['GET'])
def knowledge_stats():
    """Get knowledge vector collection stats."""
    try:
        engine = get_knowledge_engine()
        return jsonify({
            'status': 'success',
            'collection': KNOWLEDGE_COLLECTION,
            'count': engine.collection.count(),
            'path': KNOWLEDGE_DB_PATH
        })
    except Exception as e:
        logger.error(f"Error getting knowledge stats: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.teardown_appcontext
def close_vector_engine(error):
    """Close vector engine connections when the application shuts down"""
    global vector_engine, _knowledge_engine
    if vector_engine is not None:
        vector_engine.close()
        vector_engine = None
        logger.info("Vector engine connections closed")
    if _knowledge_engine is not None:
        _knowledge_engine.close()
        _knowledge_engine = None
        logger.info("Knowledge vector engine connections closed")


# if __name__ == '__main__':
#     # Get port from environment variable or use default
#     port = int(os.getenv('HOST_PORT')) + 30
    
#     # Run the Flask app
#     app.run(host='0.0.0.0', port=port)

