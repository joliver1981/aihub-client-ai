import logging
import json
import config as cfg
from CommonUtils import get_db_connection
import os

logger = logging.getLogger()

def get_document_search_content(page_id, document_type, full_text):
    """
    Get the appropriate content for search based on settings.
    
    Args:
        page_id: Page ID  
        document_type: Type of document
        full_text: Original full text content
        
    Returns:
        String content to use for search
    """
    try:
        # Check if summaries are enabled globally
        if not cfg.DOC_SEARCH_ENABLE_SUMMARIES:
            logger.debug(f"Summaries disabled globally for search, using full text for page {page_id}")
            print(f"Summaries disabled globally for search, using full text for page {page_id}")
            return full_text
        
        # Check if this document type should use summaries
        use_summaries = cfg.DOC_SEARCH_USE_SUMMARIES_BY_DOCTYPE.get(document_type.lower(), False)
        if not use_summaries:
            logger.debug(f"Document type '{document_type}' configured to use full text for page {page_id}")
            print(f"Document type '{document_type}' configured to use full text for page {page_id}")
            return full_text
        
        # Try to get summary content
        summary_content = get_page_summary_content(page_id)
        if summary_content:
            logger.debug(f"Using summary content for page {page_id} (document type: {document_type})")
            print(f"Using summary content for page {page_id} (document type: {document_type})")
            return summary_content
        else:
            logger.debug(f"No summary found for page {page_id}, falling back to full text")
            print(f"No summary found for page {page_id}, falling back to full text")
            return full_text
            
    except Exception as e:
        logger.error(f"Error getting search content for page {page_id}: {str(e)}")
        return full_text  # Always fallback to full text on error

def get_page_summary_content(page_id):
    """
    Get the summary and key points for a page, formatted for search.
    
    Args:
        page_id: Page ID
        
    Returns:
        Formatted string with summary and key points, or None if not found
    """
    try:
        conn = get_db_connection()
        if not conn:
            return None
            
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get the preferred summary type for this page
        cursor.execute("""
            SELECT summary_text, key_points 
            FROM DocumentPageSummaries 
            WHERE page_id = ? AND summary_type = ?
            ORDER BY created_at DESC
        """, (page_id, cfg.DOC_SEARCH_DEFAULT_SUMMARY_TYPE))
        
        row = cursor.fetchone()
        if not row:
            # Fallback to any available summary
            cursor.execute("""
                SELECT summary_text, key_points 
                FROM DocumentPageSummaries 
                WHERE page_id = ?
                ORDER BY created_at DESC
            """, (page_id,))
            row = cursor.fetchone()
        
        if row:
            summary_text = row[0] or ""
            key_points_json = row[1]
            
            # Build the search content
            search_content = summary_text
            
            # Add key points if available
            if key_points_json:
                try:
                    key_points = json.loads(key_points_json)
                    if isinstance(key_points, list) and key_points:
                        key_points_text = format_key_points_for_search(key_points)
                        search_content += f"\n\n{key_points_text}"
                except (json.JSONDecodeError, TypeError):
                    # If key points can't be parsed, just use the summary
                    pass
            
            conn.close()
            return search_content.strip()
        
        conn.close()
        return None
        
    except Exception as e:
        logger.error(f"Error getting summary content for page {page_id}: {str(e)}")
        return None
    

def format_key_points_for_search(key_points, format_style="sentences"):
    """
    Format key points for search in a clear, readable way.
    
    Args:
        key_points: List of key points
        format_style: "bullets", "numbers", or "sentences"
        
    Returns:
        Formatted string
    """
    if not key_points or not isinstance(key_points, list):
        return ""
    
    if format_style == "bullets":
        # Use bullet points with clear separation
        formatted = "Key Points:\n" + "\n".join([f"• {point.strip()}" for point in key_points if point.strip()])
    
    elif format_style == "numbers": 
        # Use numbered list
        formatted = "Key Points:\n" + "\n".join([f"{i+1}. {point.strip()}" for i, point in enumerate(key_points) if point.strip()])
    
    elif format_style == "sentences":
        # Convert to sentences with proper punctuation
        formatted_points = []
        for point in key_points:
            point = point.strip()
            if point:
                # Add period if not already there
                if not point.endswith(('.', '!', '?')):
                    point += '.'
                formatted_points.append(point)
        formatted = "Key Points: " + " ".join(formatted_points)
    
    else:
        # Default to bullets
        formatted = "Key Points:\n" + "\n".join([f"• {point.strip()}" for point in key_points if point.strip()])
    
    return formatted