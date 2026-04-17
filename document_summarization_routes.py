# Flask API Routes for Document Summarization
from flask import Blueprint, jsonify, request
from flask_cors import cross_origin
import os
import logging
from LLMDocumentSummarizer import DocumentPageSummarizer
from CommonUtils import get_db_connection, AnthropicProxyClient

import config as cfg
from api_keys_config import create_anthropic_client

# Set up logging
from logging.handlers import WatchedFileHandler
logger = logging.getLogger("DocumentAPI")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=os.getenv('DOC_API_LOG', 'doc_api_log.txt'), encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)


doc_summ_bp = Blueprint('doc_summ', __name__)


# Initialize the summarizer
def get_summarizer():
    """Get or create a DocumentPageSummarizer instance.

    Uses create_anthropic_client() which respects BYOK priority:
    user key > system direct > proxy.
    """
    if not hasattr(get_summarizer, '_instance'):
        client, config = create_anthropic_client()
        if config['use_direct_api']:
            logger.info(f"Initializing DocumentPageSummarizer with direct Anthropic (source: {config['source']})")
            get_summarizer._instance = DocumentPageSummarizer(
                anthropic_client=client,
                anthropic_config=config,
                logger=logging.getLogger("DocumentSummarizer")
            )
        else:
            logger.info("Initializing DocumentPageSummarizer with Anthropic proxy")
            proxy_client = AnthropicProxyClient()
            get_summarizer._instance = DocumentPageSummarizer(
                anthropic_proxy_client=proxy_client,
                anthropic_config=config,
                logger=logging.getLogger("DocumentSummarizer")
            )
    return get_summarizer._instance

@doc_summ_bp.route('/api/documents/<document_id>/summarize', methods=['POST'])
@cross_origin()
def summarize_document_pages(document_id):
    """
    Generate summaries for all pages of a document.
    
    Request body:
    {
        "summary_types": ["standard", "brief", "detailed"],  // Optional, defaults to ["standard"]
        "custom_instructions": "Focus on financial information",  // Optional
        "overwrite_existing": false  // Optional, defaults to false
    }
    """
    try:
        data = request.json or {}
        summary_types = data.get('summary_types', ['standard'])
        custom_instructions = data.get('custom_instructions')
        overwrite_existing = data.get('overwrite_existing', False)
        
        # Validate summary types
        valid_types = ['standard', 'brief', 'detailed', 'bullet_points', 'executive']
        for summary_type in summary_types:
            if summary_type not in valid_types:
                return jsonify({
                    "status": "error",
                    "message": f"Invalid summary type: {summary_type}. Valid types: {valid_types}"
                }), 400
        
        # Get document information
        conn = get_db_connection()
        if not conn:
            return jsonify({
                "status": "error",
                "message": "Database connection failed"
            }), 500
        
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Check if document exists and get document type
        cursor.execute("""
            SELECT document_type, filename 
            FROM Documents 
            WHERE document_id = ?
        """, (document_id,))
        
        doc_row = cursor.fetchone()
        if not doc_row:
            return jsonify({
                "status": "error",
                "message": f"Document {document_id} not found"
            }), 404
        
        document_type = doc_row[0]
        filename = doc_row[1]
        
        # Get all pages for this document
        cursor.execute("""
            SELECT page_id, page_number, full_text
            FROM DocumentPages 
            WHERE document_id = ?
            ORDER BY page_number
        """, (document_id,))
        
        pages = cursor.fetchall()
        if not pages:
            return jsonify({
                "status": "error",
                "message": f"No pages found for document {document_id}"
            }), 404
        
        conn.close()

        # Get custom instructions from config if available (and none were provided)
        if not custom_instructions:
            custom_instructions = cfg.DOC_SUMMARY_SPECIAL_INSTRUCTIONS.get(document_type, '')
        
        # Initialize summarizer
        summarizer = get_summarizer()
        
        # Process each page
        results = []
        total_pages = len(pages)
        successful_summaries = 0
        failed_summaries = 0

        if overwrite_existing:
            print("Purging existing document summaries...")
            _ = delete_summaries_by_document(document_id=document_id)
        
        for page_id, page_number, full_text in pages:
            page_result = {
                "page_id": page_id,
                "page_number": page_number,
                "summaries": {},
                "status": "success"
            }
            
            # Check if summaries already exist (unless overwriting)
            if not overwrite_existing:
                existing_summaries = {}
                for summary_type in summary_types:
                    existing = summarizer.get_page_summary(page_id, summary_type)
                    if existing:
                        existing_summaries[summary_type] = existing
                
                if existing_summaries:
                    page_result["summaries"] = existing_summaries
                    page_result["status"] = "existing_summaries_found"
                    results.append(page_result)
                    successful_summaries += len(existing_summaries)
                    continue
            
            # Generate summaries for each type
            for summary_type in summary_types:
                try:
                    summary_data = summarizer.summarize_page(
                        page_content=full_text,
                        document_type=document_type,
                        summary_type=summary_type,
                        custom_instructions=custom_instructions
                    )
                    
                    # Save to database
                    if summarizer.save_page_summary(
                        page_id=page_id,
                        document_id=document_id,
                        page_number=page_number,
                        summary_data=summary_data
                    ):
                        page_result["summaries"][summary_type] = summary_data
                        successful_summaries += 1
                    else:
                        failed_summaries += 1
                        page_result["summaries"][summary_type] = {
                            "error": "Failed to save summary to database"
                        }
                        
                except Exception as e:
                    failed_summaries += 1
                    page_result["summaries"][summary_type] = {
                        "error": f"Failed to generate summary: {str(e)}"
                    }
            
            results.append(page_result)
        
        return jsonify({
            "status": "success",
            "message": f"Processed {total_pages} pages",
            "document_id": document_id,
            "document_type": document_type,
            "filename": filename,
            "summary_statistics": {
                "total_pages": total_pages,
                "successful_summaries": successful_summaries,
                "failed_summaries": failed_summaries,
                "summary_types_requested": summary_types
            },
            "results": results
        })
        
    except Exception as e:
        logger.error(f"Error summarizing document {document_id}: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500


@doc_summ_bp.route('/api/pages/<page_id>/summarize', methods=['POST'])
@cross_origin()
def summarize_single_page(page_id):
    """
    Generate summaries for a single page.
    
    Request body:
    {
        "summary_types": ["standard", "brief"],  // Optional, defaults to ["standard"]
        "custom_instructions": "Focus on financial information",  // Optional
        "overwrite_existing": false  // Optional, defaults to false
    }
    """
    try:
        data = request.json or {}
        summary_types = data.get('summary_types', ['standard'])
        custom_instructions = data.get('custom_instructions')
        overwrite_existing = data.get('overwrite_existing', False)
        
        # Validate summary types
        valid_types = ['standard', 'brief', 'detailed', 'bullet_points', 'executive']
        for summary_type in summary_types:
            if summary_type not in valid_types:
                return jsonify({
                    "status": "error",
                    "message": f"Invalid summary type: {summary_type}. Valid types: {valid_types}"
                }), 400
        
        # Get page information
        conn = get_db_connection()
        if not conn:
            return jsonify({
                "status": "error",
                "message": "Database connection failed"
            }), 500
        
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get page and document information
        cursor.execute("""
            SELECT dp.page_id, dp.document_id, dp.page_number, dp.full_text, d.document_type, d.filename
            FROM DocumentPages dp
            JOIN Documents d ON dp.document_id = d.document_id
            WHERE dp.page_id = ?
        """, (page_id,))
        
        page_row = cursor.fetchone()
        if not page_row:
            return jsonify({
                "status": "error",
                "message": f"Page {page_id} not found"
            }), 404
        
        page_id, document_id, page_number, full_text, document_type, filename = page_row
        conn.close()

        # Get custom instructions from config if available (and none were provided)
        if not custom_instructions:
            custom_instructions = cfg.DOC_SUMMARY_SPECIAL_INSTRUCTIONS.get(document_type, '')
        
        # Initialize summarizer
        summarizer = get_summarizer()
        
        # Check for existing summaries if not overwriting
        existing_summaries = {}
        if not overwrite_existing:
            for summary_type in summary_types:
                existing = summarizer.get_page_summary(page_id, summary_type)
                if existing:
                    existing_summaries[summary_type] = existing
        else:
            print("Purging existing summaries for page...")
            _ = delete_summaries_by_page(page_id=page_id)
        
        # Generate new summaries
        new_summaries = {}
        errors = {}
        
        for summary_type in summary_types:
            # Skip if exists and not overwriting
            if summary_type in existing_summaries and not overwrite_existing:
                continue
            
            try:
                summary_data = summarizer.summarize_page(
                    page_content=full_text,
                    document_type=document_type,
                    summary_type=summary_type,
                    custom_instructions=custom_instructions
                )
                
                # Save to database
                if summarizer.save_page_summary(
                    page_id=page_id,
                    document_id=document_id,
                    page_number=page_number,
                    summary_data=summary_data
                ):
                    new_summaries[summary_type] = summary_data
                else:
                    errors[summary_type] = "Failed to save summary to database"
                    
            except Exception as e:
                errors[summary_type] = f"Failed to generate summary: {str(e)}"
        
        # Combine existing and new summaries
        all_summaries = {**existing_summaries, **new_summaries}
        
        return jsonify({
            "status": "success",
            "page_id": page_id,
            "document_id": document_id,
            "page_number": page_number,
            "document_type": document_type,
            "filename": filename,
            "summaries": all_summaries,
            "processing_info": {
                "existing_summaries": list(existing_summaries.keys()) if existing_summaries else [],
                "new_summaries": list(new_summaries.keys()) if new_summaries else [],
                "errors": errors if errors else None
            }
        })
        
    except Exception as e:
        logger.error(f"Error summarizing page {page_id}: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500

def delete_summaries_by_document(document_id):
    """Delete a specific summary by ID."""
    try:
        conn = get_db_connection()
        if not conn:
            print("Database connection failed")
            return False
        
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Check if summaries exist
        cursor.execute("""
            SELECT page_id, summary_type 
            FROM DocumentPageSummaries 
            WHERE document_id = ?
        """, (document_id,))
        
        summary_row = cursor.fetchone()
        if not summary_row:
            print(f"Summary for document {document_id} not found")
            return True
        
        # Delete the summaries
        cursor.execute("""
            DELETE FROM DocumentPageSummaries 
            WHERE document_id = ?
        """, (document_id,))
        
        conn.commit()
        
        return True
        
    except Exception as e:
        logger.error(f"Error deleting summary for document {document_id}: {str(e)}")
        print(f"Error deleting summary for document {document_id}: {str(e)}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def delete_summaries_by_page(page_id):
    """Delete a specific summary by ID."""
    try:
        conn = get_db_connection()
        if not conn:
            print("Database connection failed")
            return False
        
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Check if summaries exist
        cursor.execute("""
            SELECT page_id, summary_type 
            FROM DocumentPageSummaries 
            WHERE page_id = ?
        """, (page_id,))
        
        summary_row = cursor.fetchone()
        if not summary_row:
            print(f"Summary for document page {page_id} not found")
            return True
        
        # Delete the summaries
        cursor.execute("""
            DELETE FROM DocumentPageSummaries 
            WHERE page_id = ?
        """, (page_id,))
        
        conn.commit()
        
        return True
        
    except Exception as e:
        logger.error(f"Error deleting summary for document page {page_id}: {str(e)}")
        print(f"Error deleting summary for document page {page_id}: {str(e)}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()

@doc_summ_bp.route('/api/documents/<document_id>/summaries', methods=['GET'])
@cross_origin()
def get_document_summaries(document_id):
    """
    Retrieve all summaries for a document.
    
    Query parameters:
    - summary_type: Filter by summary type (optional)
    - page_number: Filter by specific page number (optional)
    """
    try:
        summary_type = request.args.get('summary_type')
        page_number = request.args.get('page_number')
        
        # Initialize summarizer
        summarizer = get_summarizer()
        
        # Get document summaries
        summaries = summarizer.get_document_summaries(document_id, summary_type)
        
        # Filter by page number if specified
        if page_number is not None:
            try:
                page_num = int(page_number)
                summaries = [s for s in summaries if s['page_number'] == page_num]
            except ValueError:
                return jsonify({
                    "status": "error",
                    "message": "Invalid page_number parameter. Must be an integer."
                }), 400
        
        # Group summaries by page for easier consumption
        pages_with_summaries = {}
        for summary in summaries:
            page_num = summary['page_number']
            if page_num not in pages_with_summaries:
                pages_with_summaries[page_num] = {
                    "page_number": page_num,
                    "page_id": summary['page_id'],
                    "summaries": {}
                }
            
            pages_with_summaries[page_num]["summaries"][summary['summary_type']] = summary
        
        # Convert to list and sort by page number
        pages_list = sorted(pages_with_summaries.values(), key=lambda x: x['page_number'])
        
        return jsonify({
            "status": "success",
            "document_id": document_id,
            "total_pages_with_summaries": len(pages_list),
            "filter_applied": {
                "summary_type": summary_type,
                "page_number": page_number
            },
            "pages": pages_list
        })
        
    except Exception as e:
        logger.error(f"Error retrieving summaries for document {document_id}: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500


@doc_summ_bp.route('/api/pages/<page_id>/summaries', methods=['GET'])
@cross_origin()
def get_page_summaries(page_id):
    """
    Retrieve all summaries for a specific page.
    
    Query parameters:
    - summary_type: Filter by summary type (optional)
    """
    try:
        summary_type = request.args.get('summary_type')
        
        # Initialize summarizer
        summarizer = get_summarizer()
        
        # Get page summaries
        if summary_type:
            summary = summarizer.get_page_summary(page_id, summary_type)
            summaries = [summary] if summary else []
        else:
            # Get all summary types for this page
            conn = get_db_connection()
            if not conn:
                return jsonify({
                    "status": "error",
                    "message": "Database connection failed"
                }), 500
            
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            cursor.execute("""
                SELECT DISTINCT summary_type 
                FROM DocumentPageSummaries 
                WHERE page_id = ?
            """, (page_id,))
            
            summary_types = [row[0] for row in cursor.fetchall()]
            conn.close()
            
            summaries = []
            for st in summary_types:
                summary = summarizer.get_page_summary(page_id, st)
                if summary:
                    summaries.append(summary)
        
        # Organize summaries by type
        summaries_by_type = {}
        for summary in summaries:
            summaries_by_type[summary['summary_type']] = summary
        
        return jsonify({
            "status": "success",
            "page_id": page_id,
            "summaries_found": len(summaries),
            "filter_applied": {
                "summary_type": summary_type
            },
            "summaries": summaries_by_type
        })
        
    except Exception as e:
        logger.error(f"Error retrieving summaries for page {page_id}: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500


@doc_summ_bp.route('/api/summaries/<int:summary_id>', methods=['DELETE'])
@cross_origin()
def delete_summary(summary_id):
    """Delete a specific summary by ID."""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({
                "status": "error",
                "message": "Database connection failed"
            }), 500
        
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Check if summary exists
        cursor.execute("""
            SELECT page_id, summary_type 
            FROM DocumentPageSummaries 
            WHERE summary_id = ?
        """, (summary_id,))
        
        summary_row = cursor.fetchone()
        if not summary_row:
            return jsonify({
                "status": "error",
                "message": f"Summary {summary_id} not found"
            }), 404
        
        page_id, summary_type = summary_row
        
        # Delete the summary
        cursor.execute("""
            DELETE FROM DocumentPageSummaries 
            WHERE summary_id = ?
        """, (summary_id,))
        
        conn.commit()
        
        return jsonify({
            "status": "success",
            "message": f"Summary {summary_id} deleted successfully",
            "deleted_summary": {
                "summary_id": summary_id,
                "page_id": page_id,
                "summary_type": summary_type
            }
        })
        
    except Exception as e:
        logger.error(f"Error deleting summary {summary_id}: {str(e)}")
        if conn:
            conn.rollback()
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500
    finally:
        if conn:
            conn.close()


@doc_summ_bp.route('/api/summaries/batch-regenerate', methods=['POST'])
@cross_origin()
def batch_regenerate_summaries():
    """
    Batch regenerate summaries for multiple documents or pages.
    
    Request body:
    {
        "document_ids": ["doc1", "doc2"],  // Optional
        "page_ids": ["page1", "page2"],   // Optional
        "summary_types": ["standard", "brief"],  // Optional, defaults to ["standard"]
        "custom_instructions": "Focus on financial information",  // Optional
        "overwrite_existing": true  // Optional, defaults to false
    }
    """
    try:
        data = request.json or {}
        document_ids = data.get('document_ids', [])
        page_ids = data.get('page_ids', [])
        summary_types = data.get('summary_types', ['standard'])
        custom_instructions = data.get('custom_instructions')
        overwrite_existing = data.get('overwrite_existing', False)
        
        if not document_ids and not page_ids:
            return jsonify({
                "status": "error",
                "message": "Must specify either document_ids or page_ids"
            }), 400
        
        # Validate summary types
        valid_types = ['standard', 'brief', 'detailed', 'bullet_points', 'executive']
        for summary_type in summary_types:
            if summary_type not in valid_types:
                return jsonify({
                    "status": "error",
                    "message": f"Invalid summary type: {summary_type}. Valid types: {valid_types}"
                }), 400
        
        # Collect all pages to process
        all_pages = []
        
        # Get pages from document_ids
        if document_ids:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            for document_id in document_ids:
                cursor.execute("""
                    SELECT dp.page_id, dp.document_id, dp.page_number, dp.full_text, d.document_type
                    FROM DocumentPages dp
                    JOIN Documents d ON dp.document_id = d.document_id
                    WHERE dp.document_id = ?
                    ORDER BY dp.page_number
                """, (document_id,))
                
                pages = cursor.fetchall()
                all_pages.extend(pages)
            
            conn.close()
        
        # Get specific pages from page_ids
        if page_ids:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
            
            for page_id in page_ids:
                cursor.execute("""
                    SELECT dp.page_id, dp.document_id, dp.page_number, dp.full_text, d.document_type
                    FROM DocumentPages dp
                    JOIN Documents d ON dp.document_id = d.document_id
                    WHERE dp.page_id = ?
                """, (page_id,))
                
                page = cursor.fetchone()
                if page:
                    all_pages.append(page)
            
            conn.close()
        
        if not all_pages:
            return jsonify({
                "status": "error",
                "message": "No pages found for the specified documents/pages"
            }), 404
        
        # Initialize summarizer
        summarizer = get_summarizer()
        
        # Process all pages
        results = []
        total_summaries_generated = 0
        total_errors = 0
        
        for page_id, document_id, page_number, full_text, document_type in all_pages:
            page_result = {
                "page_id": page_id,
                "document_id": document_id,
                "page_number": page_number,
                "summaries": {},
                "errors": {}
            }
            
            for summary_type in summary_types:
                try:
                    # Check if exists and skip if not overwriting
                    if not overwrite_existing:
                        existing = summarizer.get_page_summary(page_id, summary_type)
                        if existing:
                            page_result["summaries"][summary_type] = "skipped_existing"
                            continue
                    
                    # Generate new summary
                    summary_data = summarizer.summarize_page(
                        page_content=full_text,
                        document_type=document_type,
                        summary_type=summary_type,
                        custom_instructions=custom_instructions
                    )
                    
                    # Save to database
                    if summarizer.save_page_summary(
                        page_id=page_id,
                        document_id=document_id,
                        page_number=page_number,
                        summary_data=summary_data
                    ):
                        page_result["summaries"][summary_type] = "generated"
                        total_summaries_generated += 1
                    else:
                        page_result["errors"][summary_type] = "Failed to save to database"
                        total_errors += 1
                        
                except Exception as e:
                    page_result["errors"][summary_type] = str(e)
                    total_errors += 1
            
            results.append(page_result)
        
        return jsonify({
            "status": "success",
            "message": f"Batch processing completed",
            "statistics": {
                "total_pages_processed": len(all_pages),
                "total_summaries_generated": total_summaries_generated,
                "total_errors": total_errors,
                "summary_types": summary_types
            },
            "results": results
        })
        
    except Exception as e:
        logger.error(f"Error in batch regenerate summaries: {str(e)}")
        return jsonify({
            "status": "error",
            "message": f"Server error: {str(e)}"
        }), 500
