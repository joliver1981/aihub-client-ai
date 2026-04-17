import json
from typing import Dict, List, Optional, Any, Union, Tuple
import pyodbc
from math import ceil
import os
from collections import defaultdict
from AppUtils import get_db_connection_string, azureQuickPrompt, get_db_connection, azureMiniQuickPrompt
import config as cfg
import system_prompts as sysp
from datetime import datetime
import time
from DocSummaryUtils import get_document_search_content
from CommonUtils import build_filter_conditions, get_base_url


def get_document_types():
    """
    Execute a query to extract all available document types, returning a JSON structure.
    
    Returns:
        str: JSON string with document types
    """
    try:
        # Establish connection to the SQL Server database
        # You may need to adjust these connection parameters for your environment
        conn = get_db_connection()
        
        # Create a cursor
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Execute the query
        query = """
        SELECT distinct d.document_type
        FROM [dbo].[Documents] d
        ORDER BY d.document_type
        """

        print('Running query...')
        cursor.execute(query)
        
        # Use defaultdict to group fields by document type
        document_fields = defaultdict(list)
        
        # Process the results
        for row in cursor.fetchall():
            document_type = row[0]
            document_fields['document_types'].append(document_type)
        
        # Close the connection
        conn.close()
        
        # Convert to regular dict for JSON serialization
        result = dict(document_fields)
        
        # Return as JSON string
        return json.dumps(result, indent=2)
        
    except Exception as e:
        print(str(e))
        return json.dumps({"error": str(e)})
    

def get_document_fields(document_types=None):
    """
    Execute a query to extract all document types and their fields,
    returning a JSON structure where fields are grouped by document type.
    
    Args:
        document_types (list or None): List of document types to filter by, or None for all types
    
    Returns:
        str: JSON string with document types and their associated fields
    """
    try:
        # Establish connection to the SQL Server database
        conn = get_db_connection()
        
        # Create a cursor
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Execute the query based on whether document types are provided
        if document_types is None:
            query = """
            SELECT distinct d.document_type, f.field_name
            FROM [dbo].[Documents] d
            JOIN [dbo].[DocumentPages] p on p.document_id = d.document_id
            JOIN [dbo].[DocumentFields] f on f.page_id = p.page_id
            ORDER BY d.document_type, f.field_name
            """
            
            print('Running query for all types...')
            cursor.execute(query)
        else:
            # Convert single string to list if needed
            if isinstance(document_types, str):
                document_types = [document_types]
                
            # Create parameterized query with the right number of placeholders
            placeholders = ','.join(['?' for _ in document_types])
            query = f"""
            SELECT distinct d.document_type, f.field_name
            FROM [dbo].[Documents] d
            JOIN [dbo].[DocumentPages] p on p.document_id = d.document_id
            JOIN [dbo].[DocumentFields] f on f.page_id = p.page_id
            WHERE d.document_type IN ({placeholders})
            ORDER BY d.document_type, f.field_name
            """
            
            print(f'Running query for specific types: {", ".join(document_types)}')
            cursor.execute(query, document_types)
        
        # Use defaultdict to group fields by document type
        document_fields = defaultdict(list)
        
        # Process the results
        for row in cursor.fetchall():
            document_type = row[0]
            field_name = row[1]
            document_fields[document_type].append(field_name)
        
        # Close the connection
        conn.close()
        
        # Convert to regular dict for JSON serialization
        result = dict(document_fields)
        
        # Return as JSON string
        return json.dumps(result, indent=2)
        
    except Exception as e:
        print(str(e))
        return json.dumps({"error": str(e)})


def suggest_fields_for_value(search_value, document_type=None):
    """
    Find all fields that contain a specific value and return document types and field names as JSON.
    
    Args:
        conn_string (str): Database connection string
        search_value (str): The value to search for across all fields
        document_type (str, optional): Limit suggestions to a specific document type
        
    Returns:
        str: A JSON string containing document types and fields where the value is found
    """
    
    # Format search value for LIKE query
    search_pattern = f"%{search_value}%"
    
    try:
        # Connect to database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Build query based on whether document_type is specified
        query = """
        SELECT DISTINCT d.document_type, f.field_name
        FROM [dbo].[Documents] d
        JOIN [dbo].[DocumentPages] p ON p.document_id = d.document_id
        JOIN [dbo].[DocumentFields] f ON f.page_id = p.page_id
        WHERE f.field_value LIKE ?
        """
        
        params = [search_pattern]
        
        # Add document type filter if specified
        if document_type:
            query += " AND d.document_type = ?"
            params.append(document_type)
            
        query += " ORDER BY d.document_type, f.field_name"
        
        # Execute query
        cursor.execute(query, params)
        
        # Organize results by document type
        field_suggestions = {}
        
        for row in cursor.fetchall():
            doc_type = row[0]
            field_name = row[1]
            
            if doc_type not in field_suggestions:
                field_suggestions[doc_type] = []
                
            field_suggestions[doc_type].append(field_name)
        
        cursor.close()
        conn.close()
        
        # Check if we found any fields
        if not field_suggestions:
            result = {
                "status": "no_results",
                "message": f"No fields found containing value '{search_value}'",
                "suggestions": {}
            }
        else:
            result = {
                "status": "success",
                "message": f"Found {sum(len(fields) for fields in field_suggestions.values())} fields across {len(field_suggestions)} document types",
                "suggestions": field_suggestions
            }
        
        # Return JSON string
        return json.dumps(result)
        
    except Exception as e:
        error_response = {
            "status": "error",
            "message": str(e),
            "suggestions": {}
        }
        return json.dumps(error_response)

def get_field_suggestions_for_multiple_values(search_values, document_type=None):
    """
    Find fields containing each of the provided search values and return consolidated results.
    
    Args:
        conn_string (str): Database connection string
        search_values (list): List of values to search for across document fields
        document_type (str, optional): Limit suggestions to a specific document type
        
    Returns:
        str: A JSON string containing consolidated results for all search values
    """
    import json
    from collections import defaultdict
    
    # Initialize results structure
    consolidated_results = {
        "status": "success",
        "search_summary": {},
        "field_suggestions": defaultdict(lambda: defaultdict(list))
    }
    
    # Track search statuses
    search_counts = {
        "total_searches": len(search_values),
        "successful": 0,
        "no_results": 0,
        "errors": 0
    }
    
    # Process each search value
    for search_value in search_values:
        # Call the field suggestion function, passing document_type if specified
        result_json = suggest_fields_for_value(search_value, document_type)
        result = json.loads(result_json)
        
        # Update search status counts
        if result["status"] == "success":
            search_counts["successful"] += 1
        elif result["status"] == "no_results":
            search_counts["no_results"] += 1
        else:
            search_counts["errors"] += 1
        
        # Add this search value to the summary
        consolidated_results["search_summary"][search_value] = {
            "status": result["status"],
            "message": result["message"]
        }
        
        # Add field suggestions if available
        if "suggestions" in result and result["suggestions"]:
            for doc_type, fields in result["suggestions"].items():
                # Add each field along with the search value that found it
                for field in fields:
                    if search_value not in consolidated_results["field_suggestions"][doc_type][field]:
                        consolidated_results["field_suggestions"][doc_type][field].append(search_value)
    
    # Convert defaultdict to regular dict for JSON serialization
    consolidated_results["field_suggestions"] = dict(
        (doc_type, dict(fields)) 
        for doc_type, fields in consolidated_results["field_suggestions"].items()
    )
    
    # Add search statistics
    consolidated_results["statistics"] = search_counts
    
    # Return JSON string
    return json.dumps(consolidated_results, indent=2)

import json

def create_search_message(suggestions_json):
    """
    Create a formatted message about the search results for the AI.
    
    Args:
        suggestions_json (str): The JSON string returned from get_field_suggestions_for_multiple_values
        
    Returns:
        str: A formatted message describing the search results
    """
    # Parse the JSON
    result = json.loads(suggestions_json)
    
    # Extract statistics
    stats = result['statistics']
    successful = stats['successful']
    total = stats['total_searches']
    no_results = stats['no_results']
    errors = stats['errors']
    
    # Base message
    message = f"Searched for {total} {'value' if total == 1 else 'values'}: "
    
    if successful == 0:
        message += "None of the values were found in any document fields."
        
        if errors > 0:
            message += f" {errors} {'search' if errors == 1 else 'searches'} encountered errors."
            
        return message
    
    # Some searches were successful
    message += f"{successful} of {total} {'value was' if successful == 1 else 'values were'} found. "
    
    # Add document type breakdown
    doc_types = result['field_suggestions'].keys()
    if doc_types:
        message += f"Values were found in {len(doc_types)} document {'type' if len(doc_types) == 1 else 'types'}: "
        message += ", ".join(doc_types) + ". "
    
    # Add specific matches example
    if successful > 0:
        # Include example of values that were found
        example_fields = []
        for doc_type, fields in result['field_suggestions'].items():
            for field, values in fields.items():
                example_fields.append(f"'{values[0]}' in {field}")
                if len(example_fields) >= 2:  # Limit to 2 examples
                    break
            if len(example_fields) >= 2:
                break
        
        if example_fields:
            message += f"Examples: {' and '.join(example_fields)}."
    
    return message

def rank_search_results(results: List[Dict[str, Any]], user_question: str) -> List[Dict[str, Any]]:
    """
    Use AI to rank search results by relevance to the original user question
    and deduplicate results.
    
    Parameters:
    -----------
    results : List[Dict[str, Any]]
        List of search results to rank
    user_question : str
        The original user question
        
    Returns:
    --------
    List[Dict[str, Any]]
        Ranked and deduplicated list of search results
    """
    # First, deduplicate results
    seen_pages = {}  # Use dict to store the highest-ranked instance of each page
    
    # Group by document & page
    for result in results:
        key = (result.get("document_id"), result.get("page_number"))
        if key not in seen_pages:
            seen_pages[key] = result
        elif result.get("search_method") == "field" and seen_pages[key].get("search_method") == "semantic":
            # Prefer field search results over semantic if we have both
            seen_pages[key] = result
    
    unique_results = list(seen_pages.values())
    
    # If we only have one result or none, return as is
    if len(unique_results) <= 1:
        return unique_results
    else:
        return unique_results  # NOTE: SKIPPING RESULT RANKING - TOO MUCH OVERHEAD W/ QUESTIONABLE VALUE!!!
    
    # Limit the number of results to analyze to avoid token limits
    max_results_to_rank = 15
    results_to_rank = unique_results[:max_results_to_rank]
    
    # Prepare the prompt for AI to rank results
    system_prompt = """You are an expert document retrieval specialist.
    Your task is to rank document search results by relevance to a user's original question.
    Return only a JSON array of ranked results with their IDs and explanations."""
    
    # Prepare snippets for each result (limited to save tokens)
    result_snippets = []
    for i, result in enumerate(results_to_rank):
        snippet = result.get("snippet", "")
        if snippet and len(snippet) > 300:
            snippet = snippet[:300] + "..."
            
        fields_text = ""
        if result.get("all_fields"):
            # Include a few key fields that might help assess relevance
            fields = result.get("all_fields", {})
            important_fields = []
            for k, v in fields.items():
                if any(key_term in k.lower() for key_term in ["id", "number", "date", "name", "amount", "total"]):
                    important_fields.append(f"{k}: {v}")
                if len(important_fields) >= 3:
                    break
            fields_text = ", ".join(important_fields)
            
        result_snippets.append({
            "id": i,
            "document_id": result.get("document_id"),
            "page_number": result.get("page_number"),
            "document_type": result.get("document_type"),
            "snippet": snippet,
            "key_fields": fields_text,
            "search_method": result.get("search_method", "unknown")
        })
    
    prompt = f"""
    Given the user's question: "{user_question}"
    
    Rank these document snippets by relevance to the question:
    {json.dumps(result_snippets, indent=2)}
    
    Return a JSON array with objects in the format:
    [
        {{"id": 0, "relevance_score": 0.95, "explanation": "This document directly addresses..."}},
        ...
    ]
    
    Sort the array by relevance_score in descending order (most relevant first).
    Focus on how well the content answers the question, not just keyword matches.
    """
    
    # Call Azure OpenAI to rank results
    try:
        ranking_json = azureQuickPrompt(prompt=prompt, system=system_prompt)
        ranking = json.loads(ranking_json)
        
        # Create a new sorted list based on the ranking
        ranked_unique_results = []
        for rank_item in ranking:
            result_id = rank_item.get("id")
            if result_id is not None and result_id < len(results_to_rank):
                result = results_to_rank[result_id].copy()
                result["ai_relevance_score"] = rank_item.get("relevance_score")
                result["relevance_explanation"] = rank_item.get("explanation")
                ranked_unique_results.append(result)
        
        # Add any results that weren't ranked at the end (if they exist beyond max_results_to_rank)
        ranked_ids = {rank_item.get("id") for rank_item in ranking if rank_item.get("id") is not None}
        unranked_results = [r for i, r in enumerate(results_to_rank) if i not in ranked_ids]
        remaining_results = unique_results[max_results_to_rank:]
        
        # Combine and return
        return ranked_unique_results + unranked_results + remaining_results
    except Exception as e:
        # If ranking fails, return the original unique results
        return unique_results


# TODO: Integrate this into document_search
def detect_high_token_usage(results: List[Dict]) -> bool:
    """
    Detects if the token usage is too high and returns a boolean value.
    """
    ####################################################################
    # BEGIN NEW TOKEN CHECK LOGIC
    ####################################################################
    # Calculate current token usage
    HIGH_TOKEN_USAGE_DETECTED = False
    total_text = ""
    for result in results:
        total_text += result.get("snippet", "")
        total_text += json.dumps(result.get("all_fields", {}))
    
    estimated_tokens = len(total_text) // cfg.DOC_CHARS_PER_TOKEN
    print(86 * '*')
    print(f"Estimated tokens: {estimated_tokens}")

    # Only summarize if we exceed the token limit
    if estimated_tokens > cfg.DOC_INTELLIGENT_MAX_CONTEXT_TOKENS:
        HIGH_TOKEN_USAGE_DETECTED = True
        print('Exceeded token limit, trimming snippets...')
    else:
        HIGH_TOKEN_USAGE_DETECTED = False
        print('Did not exceed token limit, not trimming snippets...')

    print(86 * '*')

    return HIGH_TOKEN_USAGE_DETECTED

def document_search(
    conn_string: str,
    document_type: Optional[str] = None,
    search_query: str = '',
    field_filters: Optional[List[Dict[str, str]]] = None,
    include_metadata: bool = True,
    max_results: int = 500,
    user_question: Optional[str] = None,
    check_completeness: bool = False,
    ai_selected_fields: Optional[List[str]] = None
) -> str:
    """
    Search documents with flexible field filtering and return results as JSON string.
    Designed for AI agent analysis of document data.
    
    Parameters:
    -----------
    conn_string : str
        Database connection string for SQL Server
    document_type : str, optional
        Filter by specific document type
    search_query : str, optional
        Text to search for in documents
    field_filters : List[Dict[str, str]], optional
        List of field filters in the format:
        [
            {
                'field_name': 'total_amount',
                'operator': 'equals',  # See supported operators below
                'value': '500.00'
            },
            # Additional filters...
        ]
        
        Supported operators:
        - 'equals': Exact match
        - 'not_equals': Not equal to value
        - 'contains': Contains substring (case-insensitive)
        - 'not_contains': Does not contain substring
        - 'starts_with': Starts with value
        - 'ends_with': Ends with value
        - 'greater_than': Numeric/date greater than (attempts CAST to FLOAT)
        - 'greater_than_equal': Numeric/date greater than or equal
        - 'less_than': Numeric/date less than
        - 'less_than_equal': Numeric/date less than or equal
        - 'between': Range query (requires 'value2' parameter)
        - 'in': Value in list (expects comma-separated values)
        - 'not_in': Value not in list
        - 'is_null': Field value is NULL or empty
        - 'is_not_null': Field value is not NULL and not empty
        - 'regex': Regular expression match (SQL Server LIKE with pattern)
        - 'length_equals': Text length equals specified number
        - 'length_greater': Text length greater than specified number
        - 'length_less': Text length less than specified number
        - 'exists': Field exists in the document (regardless of value)
        - 'not_exists': Field does not exist in the document
        
    include_metadata : bool, default=True
        Whether to include system-wide metadata (field lists, document types, counts)
    max_results : int, default=500
        Maximum number of results to return
    
    Returns:
    --------
    str
        JSON string containing:
        - results: List of document results
        - available_fields: Available fields for search (if include_metadata=True)
        - document_types: List of available document types (if include_metadata=True)
        - document_counts: Document count by type (if include_metadata=True)
        - error: Error message if one occurred
    """
    # Initialize results
    search_results = []
    available_fields = []
    error_message = None
    document_types = []
    document_counts = {}
    
    # Default to empty list if no field filters provided
    if field_filters is None:
        field_filters = []
    
    try:
        print('Starting document search...')
        # Verify document type
        is_document_type_valid = True  # Default to True
        if document_type is not None:
            # Only validate if a document type was actually provided
            doc_types_json = get_document_types()
            doc_types_data = json.loads(doc_types_json)
            valid_types = doc_types_data.get('document_types', [])
            is_document_type_valid = document_type in valid_types

        # If document type is invalid, return a helpful message to the AI
        if not is_document_type_valid:
            print('Invalid document type...')
            response = {
                "results": "",
                "error": f"Invalid document_type parameter value '{document_type}'. Use the get_document_types tool to get a list of valid document types and try again with a valid document_type parameter."
            }
            return json.dumps(response, default=str)

        print('Connecting to database...')
        # Connect to database
        conn = pyodbc.connect(conn_string)
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        print('Values:', include_metadata, search_query, field_filters)

        if ai_selected_fields:
            for field in field_filters:
                if field['field_name'] not in ai_selected_fields:
                    ai_selected_fields.append(field['field_name'])
                    print('Added AI selected field:', field['field_name'])
        
        # Get metadata if requested
        if include_metadata:
            print('Getting metadata...')
            # Get document types for reference
            cursor.execute("SELECT DISTINCT document_type FROM Documents ORDER BY document_type")
            document_types = [row[0] for row in cursor.fetchall()]
            
            # Get document counts
            cursor.execute("""
                SELECT document_type, COUNT(*) as doc_count 
                FROM Documents 
                GROUP BY document_type 
                ORDER BY doc_count DESC
            """)
            document_counts = {row[0]: row[1] for row in cursor.fetchall()}
            
            # Get available fields with sample values for the specified document type (or all fields if no type specified)
            # TODO: This is a hack to get the fields with the most values. We should use a more efficient approach.
            if document_type:
                print(f'Getting fields with counts using document type {document_type}...')
                # Get fields with counts
                cursor.execute("""
                    SELECT df.field_name, COUNT(*) as field_count
                    FROM DocumentFields df
                    JOIN DocumentPages dp ON df.page_id = dp.page_id
                    JOIN Documents d ON dp.document_id = d.document_id
                    WHERE d.document_type = ?
                    GROUP BY df.field_name
                    ORDER BY field_count DESC, field_name
                """, (document_type,))
                
                field_data = cursor.fetchall()
                
                # Process fields and get sample values
                for field_name, field_count in field_data:
                    field_info = {
                        'name': field_name,
                        'display_name': field_name.replace('_', ' ').title(),
                        'count': field_count,
                        'sample_values': []
                    }
                    
                    # Get sample values for this field
                    sample_count = cfg.DOC_FIELD_SAMPLE_VALUES_COUNT if hasattr(cfg, 'DOC_FIELD_SAMPLE_VALUES_COUNT') else 3
                    cursor.execute("""
                        SELECT TOP (?) DISTINCT df.field_value
                        FROM DocumentFields df
                        JOIN DocumentPages dp ON df.page_id = dp.page_id
                        JOIN Documents d ON dp.document_id = d.document_id
                        WHERE d.document_type = ? 
                        AND df.field_name = ?
                        AND df.field_value IS NOT NULL 
                        AND df.field_value != ''
                        ORDER BY df.field_value
                    """, (sample_count, document_type, field_name))
                    
                    sample_values = [row[0] for row in cursor.fetchall() if row[0]]
                    field_info['sample_values'] = sample_values[:sample_count]  # Ensure we don't exceed the limit
                    
                    available_fields.append(field_info)
            else:
                # Get all available fields with sample values
                print('Getting all available fields with sample values...')
                cursor.execute("""
                    SELECT TOP(1000) df.field_name, COUNT(*) as field_count
                    FROM DocumentFields df
                    GROUP BY df.field_name
                    ORDER BY field_count DESC, field_name
                """)
                
                field_data = cursor.fetchall()
                
                # Process fields and get sample values
                for field_name, field_count in field_data:
                    field_info = {
                        'name': field_name,
                        'display_name': field_name.replace('_', ' ').title(),
                        'count': field_count,
                        'sample_values': []
                    }
                    
                    # Get sample values for this field (across all document types)
                    sample_count = cfg.DOC_FIELD_SAMPLE_VALUES_COUNT if hasattr(cfg, 'DOC_FIELD_SAMPLE_VALUES_COUNT') else 3
                    cursor.execute("""
                        SELECT TOP (?) DISTINCT df.field_value
                        FROM DocumentFields df
                        WHERE df.field_name = ?
                        AND df.field_value IS NOT NULL 
                        AND df.field_value != ''
                        ORDER BY df.field_value
                    """, (sample_count, field_name))
                    
                    sample_values = [row[0] for row in cursor.fetchall() if row[0]]
                    field_info['sample_values'] = sample_values[:sample_count]  # Ensure we don't exceed the limit
                    
                    available_fields.append(field_info)
        
        # Perform search if query or field filters are provided
        if search_query or field_filters:
            print('Performing search query...')
            # First step: If we have field filters, find matching documents
            matching_page_ids = set()
            matching_fields_by_page = {}
            
            if field_filters:
                query_parts = []
                params = []
                
                for filter_item in field_filters:
                    field_name = filter_item.get('field_name', '')
                    operator = filter_item.get('operator', 'equals')
                    value = filter_item.get('value', '')
                    value2 = filter_item.get('value2', '')  # For range queries
                    
                    # Skip if missing required fields
                    if not field_name:
                        continue
                    
                    # Handle operators that don't require a value
                    if operator in ['is_null', 'is_not_null', 'exists', 'not_exists']:
                        pass  # These don't need a value
                    elif operator == 'between' and (not value or not value2):
                        continue  # Between requires both values
                    elif not value and operator not in ['is_null', 'is_not_null', 'exists', 'not_exists']:
                        continue  # All other operators require at least one value

                    TRY_CAST_VALUE = 'FLOAT'
                    if any(keyword in str(field_name).lower() for keyword in cfg.DOC_DATE_FIELD_KEYWORDS):
                        TRY_CAST_VALUE = 'DATE'

                    # Build SQL condition based on operator
                    try:
                        if operator == 'equals':
                            query_parts.append("(df.field_name LIKE ? AND df.field_value = ?)")
                            params.extend([f'%{field_name}%', value])
                            
                        elif operator == 'not_equals':
                            query_parts.append("(df.field_name LIKE ? AND (df.field_value != ? OR df.field_value IS NULL))")
                            params.extend([f'%{field_name}%', value])
                            
                        elif operator == 'contains':
                            query_parts.append("(df.field_name LIKE ? AND LOWER(df.field_value) LIKE LOWER(?))")
                            params.extend([f'%{field_name}%', f'%{value}%'])
                            
                        elif operator == 'not_contains':
                            query_parts.append("(df.field_name LIKE ? AND (LOWER(df.field_value) NOT LIKE LOWER(?) OR df.field_value IS NULL))")
                            params.extend([f'%{field_name}%', f'%{value}%'])
                            
                        elif operator == 'starts_with':
                            query_parts.append("(df.field_name LIKE ? AND LOWER(df.field_value) LIKE LOWER(?))")
                            params.extend([f'%{field_name}%', f'{value}%'])
                            
                        elif operator == 'ends_with':
                            query_parts.append("(df.field_name LIKE ? AND LOWER(df.field_value) LIKE LOWER(?))")
                            params.extend([f'%{field_name}%', f'%{value}'])
                            
                        # Try cast handling here...
                        elif operator == 'greater_than':
                            query_parts.append(f"(df.field_name LIKE ? AND TRY_CAST(df.field_value AS {TRY_CAST_VALUE}) > TRY_CAST(? AS {TRY_CAST_VALUE}) AND TRY_CAST(df.field_value AS {TRY_CAST_VALUE}) IS NOT NULL)")
                            params.extend([f'%{field_name}%', value])
                            
                        elif operator == 'greater_than_equal':
                            query_parts.append(f"(df.field_name LIKE ? AND TRY_CAST(df.field_value AS {TRY_CAST_VALUE}) >= TRY_CAST(? AS {TRY_CAST_VALUE}) AND TRY_CAST(df.field_value AS {TRY_CAST_VALUE}) IS NOT NULL)")
                            params.extend([f'%{field_name}%', value])
                            
                        elif operator == 'less_than':
                            query_parts.append(f"(df.field_name LIKE ? AND TRY_CAST(df.field_value AS {TRY_CAST_VALUE}) < TRY_CAST(? AS {TRY_CAST_VALUE}) AND TRY_CAST(df.field_value AS {TRY_CAST_VALUE}) IS NOT NULL)")
                            params.extend([f'%{field_name}%', value])
                            
                        elif operator == 'less_than_equal':
                            query_parts.append(f"(df.field_name LIKE ? AND TRY_CAST(df.field_value AS {TRY_CAST_VALUE}) <= TRY_CAST(? AS {TRY_CAST_VALUE}) AND TRY_CAST(df.field_value AS {TRY_CAST_VALUE}) IS NOT NULL)")
                            params.extend([f'%{field_name}%', value])
                            
                        elif operator == 'between':
                            query_parts.append(f"(df.field_name LIKE ? AND TRY_CAST(df.field_value AS {TRY_CAST_VALUE}) BETWEEN TRY_CAST(? AS {TRY_CAST_VALUE}) AND TRY_CAST(? AS {TRY_CAST_VALUE}) AND TRY_CAST(df.field_value AS {TRY_CAST_VALUE}) IS NOT NULL)")
                            params.extend([f'%{field_name}%', value, value2])
                            

                        elif operator == 'in':
                            # Split comma-separated values and create IN clause
                            values_list = [v.strip() for v in value.split(',') if v.strip()]
                            if values_list:
                                placeholders = ', '.join(['?' for _ in values_list])
                                query_parts.append(f"(df.field_name LIKE ? AND df.field_value IN ({placeholders}))")
                                params.extend([f'%{field_name}%'] + values_list)
                                
                        elif operator == 'not_in':
                            # Split comma-separated values and create NOT IN clause
                            values_list = [v.strip() for v in value.split(',') if v.strip()]
                            if values_list:
                                placeholders = ', '.join(['?' for _ in values_list])
                                query_parts.append(f"(df.field_name LIKE ? AND (df.field_value NOT IN ({placeholders}) OR df.field_value IS NULL))")
                                params.extend([f'%{field_name}%'] + values_list)
                                
                        elif operator == 'is_null':
                            query_parts.append("(df.field_name LIKE ? AND (df.field_value IS NULL OR df.field_value = ''))")
                            params.extend([f'%{field_name}%'])
                            
                        elif operator == 'is_not_null':
                            query_parts.append("(df.field_name LIKE ? AND df.field_value IS NOT NULL AND df.field_value != '')")
                            params.extend([f'%{field_name}%'])
                            
                        elif operator == 'regex':
                            # SQL Server LIKE pattern matching (limited regex functionality)
                            query_parts.append("(df.field_name LIKE ? AND df.field_value LIKE ?)")
                            params.extend([f'%{field_name}%', value])
                            
                        elif operator == 'length_equals':
                            query_parts.append("(df.field_name LIKE ? AND LEN(df.field_value) = TRY_CAST(? AS INT) AND TRY_CAST(? AS INT) IS NOT NULL)")
                            params.extend([f'%{field_name}%', value, value])
                            
                        elif operator == 'length_greater':
                            query_parts.append("(df.field_name LIKE ? AND LEN(df.field_value) > TRY_CAST(? AS INT) AND TRY_CAST(? AS INT) IS NOT NULL)")
                            params.extend([f'%{field_name}%', value, value])
                            
                        elif operator == 'length_less':
                            query_parts.append("(df.field_name LIKE ? AND LEN(df.field_value) < TRY_CAST(? AS INT) AND TRY_CAST(? AS INT) IS NOT NULL)")
                            params.extend([f'%{field_name}%', value, value])
                            
                        elif operator == 'exists':
                            query_parts.append("(df.field_name LIKE ?)")
                            params.extend([f'%{field_name}%'])
                            
                        elif operator == 'not_exists':
                            # This is more complex - we need to find pages that DON'T have this field
                            # We'll handle this by finding pages that DO have other fields but NOT this one
                            # This requires a different approach using NOT EXISTS subquery
                            subquery = """
                                NOT EXISTS (
                                    SELECT 1 FROM DocumentFields df_inner 
                                    WHERE df_inner.page_id = dp.page_id 
                                    AND df_inner.field_name LIKE ?
                                )
                            """
                            # Note: This will be handled differently in the main query construction
                            # For now, we'll mark it specially
                            query_parts.append(f"__NOT_EXISTS__{field_name}")
                            params.extend([f'%{field_name}%'])
                            
                        else:
                            # Unknown operator, log and skip
                            print(f"Warning: Unknown operator '{operator}' for field '{field_name}', skipping...")
                            continue
                            
                    except Exception as operator_error:
                        print(f"Error processing operator '{operator}' for field '{field_name}': {str(operator_error)}")
                        continue
                
                ############################################################################
                # NOTE: This is the main query that uses the fields to find the pages
                ############################################################################
                # Build the complete SQL query for field filtering (ie using the fields to find the pages)
                if query_parts:
                    # Create attribute versions of the query parts
                    attribute_query_parts = []
                    for part in query_parts:
                        attribute_part = part.replace('df.field_name', 'da.attribution_type').replace('df.field_value', 'da.attribution_value')
                        attribute_query_parts.append(attribute_part)
                        
                    print('Building field filter sql with query parts...')
                    # Check if we have any 'not_exists' operators that need special handling
                    not_exists_conditions = [part for part in query_parts if part.startswith("__NOT_EXISTS__")]
                    regular_conditions = [part for part in query_parts if not part.startswith("__NOT_EXISTS__")]
                    
                    if not_exists_conditions:
                        print('Building field filter sql with not exists conditions...')
                        # Handle not_exists operators with a different query structure
                        not_exists_params = []
                        not_exists_clauses = []
                        
                        # Build the not exists conditions
                        param_index = 0
                        for condition in not_exists_conditions:
                            field_name = condition.replace("__NOT_EXISTS__", "")
                            not_exists_clauses.append("""
                                NOT EXISTS (
                                    SELECT 1 FROM DocumentFields df_inner 
                                    WHERE df_inner.page_id = dp.page_id 
                                    AND df_inner.field_name LIKE ?
                                )
                            """)
                            # Find the corresponding parameter
                            for i, param in enumerate(params):
                                if f'%{field_name}%' == param:
                                    not_exists_params.append(param)
                                    break
                        
                        # If we have both regular conditions and not_exists conditions
                        if regular_conditions:
                            # Remove not_exists params from regular params
                            regular_params = []
                            skip_next = False
                            for i, param in enumerate(params):
                                if param in not_exists_params:
                                    continue
                                regular_params.append(param)
                            
                            # Combine both types of conditions
                            field_filter_sql = f"""
                                SELECT DISTINCT dp.page_id, df.field_name, df.field_value
                                FROM DocumentFields df
                                JOIN DocumentPages dp ON df.page_id = dp.page_id
                                JOIN Documents d ON dp.document_id = d.document_id
                                WHERE ({' OR '.join(regular_conditions)})
                                {f"AND d.document_type = '{document_type}'" if document_type else ""}
                                
                                UNION
                                
                                SELECT DISTINCT dp.page_id, '' as field_name, 'NOT_EXISTS_MATCH' as field_value
                                FROM DocumentPages dp
                                JOIN Documents d ON dp.document_id = d.document_id
                                WHERE {' AND '.join(not_exists_clauses)}
                                {f"AND d.document_type = '{document_type}'" if document_type else ""}
                            """
                            all_params = regular_params + not_exists_params
                        else:
                            # Only not_exists conditions
                            field_filter_sql = f"""
                                SELECT DISTINCT dp.page_id, '' as field_name, 'NOT_EXISTS_MATCH' as field_value
                                FROM DocumentPages dp
                                JOIN Documents d ON dp.document_id = d.document_id
                                WHERE {' AND '.join(not_exists_clauses)}
                                {f"AND d.document_type = '{document_type}'" if document_type else ""}
                            """
                            all_params = not_exists_params
                        
                        print(86 * '!')
                        # print(field_filter_sql)
                        # print(all_params)
                        print('Executing field filter sql...')
                        print(86 * '!')
                        #time.sleep(30)

                        cursor.execute(field_filter_sql, all_params)
                    else:
                        # Regular query without not_exists conditions
                        print('Building field filter sql with regular conditions...')
                        field_filter_sql = f"""
                            SELECT dp.page_id, df.field_name, df.field_value
                            FROM DocumentFields df
                            JOIN DocumentPages dp ON df.page_id = dp.page_id
                            JOIN Documents d ON dp.document_id = d.document_id
                            WHERE ({' OR '.join(query_parts)})
                            {f"AND d.document_type = '{document_type}'" if document_type else ""}

                            UNION ALL
                            
                            SELECT dp.page_id, da.attribution_type as field_name, da.attribution_value as field_value--, 'attribute' as source_type
                            FROM DocumentAttributions da
                            JOIN Documents d ON da.document_id = d.document_id
                            JOIN DocumentPages dp ON d.document_id = dp.document_id
                            WHERE ({' OR '.join(attribute_query_parts)})
                            {f"AND d.document_type = '{document_type}'" if document_type else ""}
                        """

                        # TODO: This is a hack that excludes the field filters until field search can accurately match up date values (requires better formatting upon extraction)
                        if cfg.DOC_IGNORE_FIELD_FILTERS:
                            print('WARNING: Ignoring field filters (this can be less efficient)...')
                            field_filter_sql = f"""
                                SELECT dp.page_id, df.field_name, df.field_value
                                FROM DocumentFields df
                                JOIN DocumentPages dp ON df.page_id = dp.page_id
                                JOIN Documents d ON dp.document_id = d.document_id
                                WHERE {f"d.document_type = '{document_type}'" if document_type else "1=1"}
                            """
                            print("==========>>>>>>>>>> SQL:")
                            print(field_filter_sql)
                            cursor.execute(field_filter_sql)
                        else:
                            print("==========>>>>>>>>>> SQL:")
                            print(field_filter_sql)
                            
                            all_params = params + params

                            print("==========>>>>>>>>>> Params:")
                            print(params)
                            cursor.execute(field_filter_sql, all_params)
                        
                        print(86 * '!')
                        print('Executed field filter sql...')
                        print(86 * '!')

                    field_matches = cursor.fetchall()

                    # ==========================================
                    # Print distinct count of page_ids returned
                    # ==========================================
                    distinct_page_ids = set(row[0] for row in field_matches if row[0] is not None)
                    print(f"Distinct page_id count returned by field filter query: {len(distinct_page_ids)}")
                                        
                    # Process matched pages
                    for page_id, field_name, field_value in field_matches:
                        matching_page_ids.add(page_id)
                        
                        if page_id not in matching_fields_by_page:
                            matching_fields_by_page[page_id] = []
                        
                        # Don't add the special NOT_EXISTS_MATCH to the display fields
                        if field_value != 'NOT_EXISTS_MATCH':
                            matching_fields_by_page[page_id].append({
                                'name': field_name.replace('_', ' ').title(),
                                'value': field_value
                            })

                    print(86 * '!')
                    print('No of pages found in field search:', len(matching_page_ids))
                    print(86 * '!')
            
            ####################################################################################################
            # NOTE: This is the query that uses the pages (found from field search) to find the data for the AI
            ####################################################################################################
            # If we have field matches or just a text search, proceed with search
            if matching_page_ids or search_query:
                print('Found matching page ids or search query...')
                results = []
                
                # TODO: THIS SHOULD BE HANDLED OUTSIDE BY VECTOR SEARCHING
                # Perform full-text search if search_query is provided
                if search_query:
                    # Add LIMIT to control result size
                    print('Executing search query using full text from question...', search_query, document_type, max_results)
                    cursor.execute("""
                        SELECT TOP (?) dp.page_id, d.document_id, d.filename, d.document_type, 
                            dp.page_number, dp.full_text, d.page_count, d.archived_path [link_to_document]
                        FROM DocumentPages dp
                        JOIN Documents d ON dp.document_id = d.document_id
                        WHERE dp.full_text LIKE ?
                        AND (? IS NULL OR d.document_type = ?) 
                        ORDER BY dp.page_id
                    """, (max_results, f'%{search_query}%', document_type, document_type))
                    
                    for page_id, document_id, filename, doc_type, page_number, full_text, page_count, link_to_document in cursor.fetchall():
                        # Create snippet
                        document_text = get_document_search_content(
                            page_id=page_id, 
                            document_type=doc_type,
                            full_text=full_text[:int(cfg.DOC_PAGE_TEXT_LIMIT_IN_RESULTS)]
                        )
                        
                        results.append({
                            "page_id": page_id,
                            "document_id": document_id,
                            "filename": filename,
                            "document_type": doc_type,
                            "page_number": page_number,
                            "page_count": page_count,
                            "snippet": document_text,
                            "link_to_document": get_base_url() + f"/document/view/{document_id}?page={page_number or '1'}"
                        })
                else:
                    print('Executing search using matching pages...')
                    # If no text query but we have field filters, get basic info for all matching pages
                    if matching_page_ids:
                        # Convert set to list and join for SQL IN clause
                        page_ids_list = list(matching_page_ids)[:max_results]  # Limit to max_results

                        print(86 * '!')
                        print('Number of pages to include in search:', len(page_ids_list))
                        print(86 * '!')
                        
                        # Use parameterized query with proper placeholders
                        placeholders = ', '.join(['?' for _ in page_ids_list])
                        
                        # Construct and execute query
                        # TODO: This is a hack until field search can accurately match up date values (requires better formatting upon extraction)
                        if ai_selected_fields and cfg.DOC_LIMIT_SEARCH_AI_SELECTED_FIELDS:
                            print('Building field filter sql with ai selected fields (also limited to ai selected fields)...')

                            # Create pivot columns for each selected field
                            pivot_columns = []
                            for field_name in ai_selected_fields:
                                pivot_columns.append(f"MAX(CASE WHEN df.field_name = '{field_name}' THEN df.field_value END) AS [{field_name}]")
                            
                            pivot_sql = ", " + ", ".join(pivot_columns) if pivot_columns else ""
                            
                            query = f"""
                                SELECT dp.page_id, d.document_id, d.filename, d.document_type, 
                                    dp.page_number, dp.full_text, d.page_count, d.archived_path AS [link_to_document]
                                    {pivot_sql}
                                FROM DocumentPages dp
                                JOIN Documents d ON dp.document_id = d.document_id
                                LEFT JOIN DocumentFields df ON dp.page_id = df.page_id 
                                    AND df.field_name IN ({','.join([f"'{field}'" for field in ai_selected_fields])})
                                WHERE dp.page_id IN ({placeholders})
                                GROUP BY dp.page_id, d.document_id, d.filename, d.document_type, 
                                    dp.page_number, dp.full_text, d.page_count, d.archived_path
                            """

                            query_params = page_ids_list
                        elif ai_selected_fields and cfg.DOC_INCLUDE_AI_SELECTED_FIELDS_IN_RESULT:
                            print('Building field filter sql with ai selected fields...')

                            # Create pivot columns for each selected field
                            pivot_columns = []
                            for field_name in ai_selected_fields:
                                pivot_columns.append(f"MAX(CASE WHEN df.field_name = '{field_name}' THEN df.field_value END) AS [{field_name}]")
                            
                            pivot_sql = ", " + ", ".join(pivot_columns) if pivot_columns else ""
                            
                            query = f"""
                                SELECT dp.page_id, d.document_id, d.filename, d.document_type, 
                                    dp.page_number, dp.full_text, d.page_count, d.archived_path AS [link_to_document]
                                    {pivot_sql}
                                FROM DocumentPages dp
                                JOIN Documents d ON dp.document_id = d.document_id
                                LEFT JOIN DocumentFields df ON dp.page_id = df.page_id 
                                WHERE dp.page_id IN ({placeholders})
                                GROUP BY dp.page_id, d.document_id, d.filename, d.document_type, 
                                    dp.page_number, dp.full_text, d.page_count, d.archived_path
                            """

                            query_params = page_ids_list
                        else:
                            print('Building field filter sql with regular fields...')
                            query = f"""
                                SELECT dp.page_id, d.document_id, d.filename, d.document_type, 
                                    dp.page_number, dp.full_text, d.page_count, d.archived_path [link_to_document]
                                FROM DocumentPages dp
                                JOIN Documents d ON dp.document_id = d.document_id
                                WHERE dp.page_id IN ({placeholders})
                            """
                            
                            query_params = page_ids_list
                        
                        print(86 * '!')
                        print(query)
                        print(query_params)
                        if ai_selected_fields:
                            print('Executing ai selected data sql...')
                        else:
                            print('Executing data sql...')
                        print(86 * '!')
                        #time.sleep(30)

                        cursor.execute(query, query_params)

                        # Get column names from cursor to handle dynamic fields
                        columns = [desc[0] for desc in cursor.description]

                        for row in cursor.fetchall():
                            # Convert row to dictionary for easy field access
                            row_dict = dict(zip(columns, row))
                            
                            # Extract base fields
                            page_id = row_dict['page_id']
                            document_id = row_dict['document_id']
                            filename = row_dict['filename']
                            doc_type = row_dict['document_type']
                            page_number = row_dict['page_number']
                            full_text = row_dict['full_text']
                            page_count = row_dict['page_count']
                            link_to_document = row_dict['link_to_document']

                            # Build base result
                            if cfg.DOC_INCLUDE_SNIPPET_IN_RESULT:
                                document_text = get_document_search_content(
                                    page_id=page_id, 
                                    document_type=doc_type,
                                    full_text=full_text[:int(cfg.DOC_PAGE_TEXT_LIMIT_IN_RESULTS)]
                                )
                                result = {
                                    "page_id": page_id,
                                    "document_id": document_id,
                                    "filename": filename,
                                    "document_type": doc_type,
                                    "page_number": page_number,
                                    "page_count": page_count,
                                    "snippet": document_text,
                                    "link_to_document": get_base_url() + f"/document/view/{document_id}?page={page_number or '1'}"
                                }
                            else:
                                result = {
                                    "page_id": page_id,
                                    "document_id": document_id,
                                    "filename": filename,
                                    "document_type": doc_type,
                                    "page_number": page_number,
                                    "page_count": page_count,
                                    "snippet": "",
                                    "link_to_document": get_base_url() + f"/document/view/{document_id}?page={page_number or '1'}"
                                }

                            RESULT_OK = True
    
                            # Add AI selected fields in nested "relevant_fields" section
                            if ai_selected_fields and cfg.DOC_INCLUDE_AI_SELECTED_FIELDS_IN_RESULT:
                                relevant_fields = {}
                                has_relevant_data = False
                                
                                for field_name in ai_selected_fields:
                                    field_value = row_dict.get(field_name)
                                    if field_value is not None:  # Only add non-null values
                                        relevant_fields[field_name] = field_value
                                        has_relevant_data = True
                                
                                # Only add relevant_fields section if we have data
                                if has_relevant_data:
                                    result["relevant_fields"] = relevant_fields
                                    RESULT_OK = True
                                else:
                                    result["relevant_fields"] = {}
                                    RESULT_OK = False

                            if RESULT_OK:
                                print('Result:', result)
                                print('--------------------------------')
                                results.append(result)
                
                # For combined search (text + fields), filter by matching page IDs
                if search_query and field_filters:
                    # Filter text search results to only include pages that also match field criteria
                    results = [r for r in results if r["page_id"] in matching_page_ids]
                    print('No of pages found in combined search:', len(results))
                
                # Format results with all available metadata for AI analysis
                formatted_results = []
                base_results = {}  # Dictionary to store results by document_id and page_number

                # TODO: Evaluate the high token usage detection function to ensure it is truly adding value... not convinced yet..
                HIGH_TOKEN_USAGE_DETECTED = detect_high_token_usage(results)
                print('Formatting results...')
                if HIGH_TOKEN_USAGE_DETECTED:
                    print('High token usage detected, truncating snippets...')
                for result in results:
                    # Create basic result
                    if HIGH_TOKEN_USAGE_DETECTED:
                        snippet = result["snippet"][:250] + "..." if result["snippet"] and len(result["snippet"]) > 250 else (result["snippet"] or "")
                    else:
                        snippet = result["snippet"]

                    formatted_result = {
                        "document_id": result["document_id"],
                        "page_id": result["page_id"],
                        "filename": result["filename"],
                        "document_type": result["document_type"],
                        "page_number": result["page_number"],
                        "page_count": result["page_count"],
                        "snippet": snippet,
                        "link_to_document": get_base_url() + f"/document/view/{result['document_id']}?page={result['page_number'] or '1'}",
                        "relevant_fields": result.get("relevant_fields", {})
                    }
                    
                    # Get additional document info
                    if cfg.DOC_GET_ADDITIONAL_DOCUMENT_INFO:
                        print('Getting additional document info...')

                        cursor.execute("""
                            SELECT processed_at, reference_number, customer_id, vendor_id, document_date
                            FROM Documents 
                            WHERE document_id = ?
                        """, (result["document_id"],))
                        
                        doc_info = cursor.fetchone()
                        if doc_info:
                            formatted_result["processed_at"] = doc_info[0].isoformat() if doc_info[0] else None
                            formatted_result["reference_number"] = doc_info[1]
                            formatted_result["customer_id"] = doc_info[2]
                            formatted_result["vendor_id"] = doc_info[3]
                            formatted_result["document_date"] = doc_info[4] if doc_info[4] else None
                    
                    # TODO: Already done above and fields from search are also included
                    # Add matching fields if this was a field search
                    # if result["page_id"] in matching_fields_by_page:
                    #     formatted_result["matching_fields"] = matching_fields_by_page[result["page_id"]]
                    
                    # # Get all fields for this page for comprehensive AI analysis
                    # cursor.execute("""
                    #     SELECT field_name, field_value
                    #     FROM DocumentFields
                    #     WHERE page_id = ?
                    # """, (result["page_id"],))
                    
                    # all_fields = {}
                    # for field_name, field_value in cursor.fetchall():
                    #     all_fields[field_name] = field_value
                        
                    # formatted_result["all_fields"] = all_fields

                    #######################################################################################
                    #######################################################################################
                    #######################################################################################
                    # TODO Evaluate this code and test before next release
                    # Always include a properly formatted clickable link
                    # Debug: Log what paths we're getting

                    # print(86 * '#')
                    # print(86 * '#')
                    # print(86 * '#')
                    # print(f"DEBUG - Document paths for {result['filename']}:")
                    # print(f"  - link_to_document: {result.get('link_to_document')}")
                    # print(f"  - archived_path: {result.get('archived_path')}")
                    # print(f"  - path_to_document: {result.get('path_to_document')}")

                    raw_path = (result.get("link_to_document") or 
                               result.get("archived_path") or 
                               result.get("path_to_document") or "")
                    
                    formatted_result["clickable_link"] = format_document_link(raw_path)
                    
                    # print(f"  - formatted_result: {formatted_result}")
                    # print(86 * '#')
                    # print(86 * '#')
                    # print(86 * '#')
                    #######################################################################################
                    #######################################################################################
                    #######################################################################################

                    # Store result in base_results with key as (document_id, page_number)
                    key = (result["document_id"], result["page_number"])
                    base_results[key] = formatted_result

                print(86 * '!')
                print('No of pages found after formatting search results:', len(base_results))
                print(86 * '!')
                
                # Add additional pages based on cfg.DOC_RETURN_ADDITIONAL_PAGES
                additional_pages = cfg.DOC_RETURN_ADDITIONAL_PAGES
                if additional_pages > 0:
                    print('Adding additional pages...')
                    # For each document in base_results
                    documents_to_process = set([(res["document_id"], res["page_number"], res["page_count"]) for res in results])
                    
                    for doc_id, page_number, page_count in documents_to_process:
                        # Get next n pages
                        for i in range(1, additional_pages + 1):
                            next_page_number = page_number + i
                            
                            # Skip if beyond page_count
                            if next_page_number > page_count:
                                continue
                                
                            # Check if we already have this page
                            if (doc_id, next_page_number) in base_results:
                                continue
                                
                            # Get the next page content
                            cursor.execute("""
                                SELECT dp.page_id, dp.page_number, dp.full_text
                                FROM DocumentPages dp
                                WHERE dp.document_id = ? AND dp.page_number = ?
                            """, (doc_id, next_page_number))
                            
                            next_page = cursor.fetchone()
                            if next_page:
                                page_id, page_number, full_text = next_page
                                
                                # Get document info (reuse from the original page)
                                base_key = next((k for k in base_results.keys() if k[0] == doc_id), None)
                                
                                if base_key:
                                    base_doc_info = base_results[base_key].copy()
                                    
                                    # Update with new page info
                                    additional_result = {
                                        "document_id": doc_id,
                                        "page_id": page_id,
                                        "filename": base_doc_info["filename"],
                                        "document_type": base_doc_info["document_type"],
                                        "page_number": page_number,
                                        "page_count": base_doc_info["page_count"],
                                        "snippet": f"[Page {page_number}]\n" + (full_text[:250] + "..." if full_text and len(full_text) > 250 else (full_text or "")),
                                        "processed_at": base_doc_info.get("processed_at"),
                                        "reference_number": base_doc_info.get("reference_number"),
                                        "customer_id": base_doc_info.get("customer_id"),
                                        "vendor_id": base_doc_info.get("vendor_id"),
                                        "document_date": base_doc_info.get("document_date"),
                                        "is_additional_page": True  # Flag to indicate this is an automatically added page
                                    }
                                    
                                    # Get all fields for this page
                                    cursor.execute("""
                                        SELECT field_name, field_value
                                        FROM DocumentFields
                                        WHERE page_id = ?
                                    """, (page_id,))
                                    
                                    all_fields = {}
                                    for field_name, field_value in cursor.fetchall():
                                        all_fields[field_name] = field_value
                                        
                                    additional_result["all_fields"] = all_fields
                                    
                                    # Add to base_results
                                    base_results[(doc_id, page_number)] = additional_result
                
                # Convert base_results dictionary to list
                formatted_results = list(base_results.values())
                
                # Sort by document_id and page_number
                formatted_results.sort(key=lambda x: (x["document_id"], x["page_number"]))
                
                search_results = formatted_results
        
        conn.close()
            
    except Exception as e:
        error_message = str(e)
        print(f"Error in document search: {error_message}")
    
    # Convert document counts to list for JSON serialization
    doc_count_list = [{"type": k, "count": v} for k, v in document_counts.items()]
    
    # Prepare the response object
    response = {
        "results": search_results,
    }
    
    # Only include metadata if requested
    if include_metadata:
        response.update({
            "available_fields": available_fields,
            "document_types": document_types,
            "document_counts": doc_count_list,
        })
    
    # Always include error if present
    response["error"] = error_message

    # If additional pages were included, add a note about it
    if any(result.get("is_additional_page", False) for result in search_results):
        additional_page_count = sum(1 for result in search_results if result.get("is_additional_page", False))
        total_page_count = len(search_results)
        response["note"] = f"Automatically included {additional_page_count} additional pages beyond the initial search results. Total of {total_page_count} pages returned."

    # If no results, return a helpful message to the AI if possible with alternative fields to try
    if len(search_results) == 0:
        print('No results found, checking field filters...')
        if field_filters:
            field_values = []
            for filter_item in field_filters:
                field_name = filter_item.get('field_name', '')
                operator = filter_item.get('operator', 'equals')
                value = filter_item.get('value', '')
                
                # Skip if missing required fields
                if not field_name or not value:
                    continue
                else:
                    field_values.append(value)

            if len(field_values) > 0:
                suggested_field_results = get_field_suggestions_for_multiple_values(field_values, document_type=document_type)
                suggested_field_results = json.loads(suggested_field_results)
                if suggested_field_results['statistics']['successful'] > 0:
                    print("Found at least one successful search")
                    helpful_error_message = create_search_message(json.dumps(suggested_field_results))
                    response["error"] = "No documents were found, however, a search shows that there are other fields that have these values. Try again using these other suggested fields. Here are the results of the field search: " + helpful_error_message
    else:
        # Check document completeness and potentially suggest pulling additional pages
        try:
            if user_question is not None and check_completeness and cfg.DOC_RETURN_ADDITIONAL_PAGES <= 0:
                print('Checking document completeness...')
                print(86 * '%')
                print('Auto-return of additional pages not set, checking document completeness...')
                print('User Question:', user_question)
                document_metadata = response["results"][0]
                print('Got document metadata...')
                print(document_metadata)
                current_document_text = response["results"][0]["snippet"]
                print('Got document text...')
                result = check_document_completeness(
                    user_question,
                    current_document_text,
                    document_metadata
                    )
                print('Result:', result)
                if not result.get('has_sufficient_information', True):
                    # If we're already returning additional pages, modify the message
                    if any(result.get("is_additional_page", False) for result in search_results):
                        response["note"] = response.get("note", "") + " " + result.get('instruction', '')
                    else:
                        response["note"] = result.get('instruction', '')
                    print('Full Document Response:', response)
                print(86 * '%')
        except Exception as e:
            print('Failed to check document completeness...')
            print(str(e))
    
    # Return as JSON string
    return json.dumps(response, default=str)
	

def get_next_document_page_util(conn_string: str, page_id: str) -> str:
    """
    Retrieve the next page in a document based on the current page ID.
    
    Parameters:
    -----------
    conn_string : str
        Database connection string for SQL Server
    page_id : str
        The ID of the current page
        
    Returns:
    --------
    str
        JSON string containing the next page data or an error message
    """
    try:
        # Connect to database
        conn = pyodbc.connect(conn_string)
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # First, get the current document ID and page number
        cursor.execute("""
            SELECT dp.document_id, dp.page_number
            FROM DocumentPages dp
            WHERE dp.page_id = ?
        """, (page_id,))
        
        current_page_data = cursor.fetchone()
        if not current_page_data:
            return json.dumps({"error": "Current page not found", "page": None})
        
        document_id, current_page_number = current_page_data
        
        # Get the next page
        cursor.execute("""
            SELECT dp.page_id, dp.page_number, dp.full_text, d.document_id, 
                   d.filename, d.document_type, d.page_count
            FROM DocumentPages dp
            JOIN Documents d ON dp.document_id = d.document_id
            WHERE dp.document_id = ? AND dp.page_number = ?
        """, (document_id, current_page_number + 1))
        
        next_page = cursor.fetchone()
        if not next_page:
            return json.dumps({"error": "No next page available", "page": None})
        
        # Extract page data
        next_page_id, next_page_number, full_text, doc_id, filename, doc_type, page_count = next_page
        
        # Create result object
        result = {
            "page_id": next_page_id,
            "document_id": doc_id,
            "filename": filename,
            "document_type": doc_type,
            "page_number": next_page_number,
            "page_count": page_count,
            "text": full_text[:int(cfg.DOC_PAGE_TEXT_LIMIT_IN_RESULTS)],
            "is_last_page": (next_page_number == page_count)
        }
        
        # Get all fields for this page
        cursor.execute("""
            SELECT field_name, field_value
            FROM DocumentFields
            WHERE page_id = ?
        """, (next_page_id,))
        
        fields = {}
        for field_name, field_value in cursor.fetchall():
            fields[field_name] = field_value
        
        result["fields"] = fields
        
        conn.close()
        return json.dumps({"page": result, "error": None}, default=str)
        
    except Exception as e:
        return json.dumps({"error": str(e), "page": None})


def get_previous_document_page_util(conn_string: str, page_id: str) -> str:
    """
    Retrieve the previous page in a document based on the current page ID.
    
    Parameters:
    -----------
    conn_string : str
        Database connection string for SQL Server
    page_id : str
        The ID of the current page
        
    Returns:
    --------
    str
        JSON string containing the previous page data or an error message
    """
    try:
        # Connect to database
        conn = pyodbc.connect(conn_string)
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # First, get the current document ID and page number
        cursor.execute("""
            SELECT dp.document_id, dp.page_number
            FROM DocumentPages dp
            WHERE dp.page_id = ?
        """, (page_id,))
        
        current_page_data = cursor.fetchone()
        if not current_page_data:
            return json.dumps({"error": "Current page not found", "page": None})
        
        document_id, current_page_number = current_page_data
        
        # Check if there is a previous page
        if current_page_number <= 1:
            return json.dumps({"error": "No previous page available", "page": None})
        
        # Get the previous page
        cursor.execute("""
            SELECT dp.page_id, dp.page_number, dp.full_text, d.document_id, 
                   d.filename, d.document_type, d.page_count
            FROM DocumentPages dp
            JOIN Documents d ON dp.document_id = d.document_id
            WHERE dp.document_id = ? AND dp.page_number = ?
        """, (document_id, current_page_number - 1))
        
        prev_page = cursor.fetchone()
        if not prev_page:
            return json.dumps({"error": "Previous page not found", "page": None})
        
        # Extract page data
        prev_page_id, prev_page_number, full_text, doc_id, filename, doc_type, page_count = prev_page
        
        # Create result object
        result = {
            "page_id": prev_page_id,
            "document_id": doc_id,
            "filename": filename,
            "document_type": doc_type,
            "page_number": prev_page_number,
            "page_count": page_count,
            "text": full_text,
            "is_first_page": (prev_page_number == 1)
        }
        
        # Get all fields for this page
        cursor.execute("""
            SELECT field_name, field_value
            FROM DocumentFields
            WHERE page_id = ?
        """, (prev_page_id,))
        
        fields = {}
        for field_name, field_value in cursor.fetchall():
            fields[field_name] = field_value
        
        result["fields"] = fields
        
        conn.close()
        return json.dumps({"page": result, "error": None}, default=str)
        
    except Exception as e:
        return json.dumps({"error": str(e), "page": None})


def get_document_by_id(conn_string: str, document_id: str) -> str:
    """
    Retrieve a document by its document id.
    
    Parameters:
    -----------
    conn_string : str
        Database connection string for SQL Server
    document_id : str
        The ID of the document
        
    Returns:
    --------
    str
        JSON string containing the document data or an error message
    """
    try:
        # Connect to database
        conn = pyodbc.connect(conn_string)
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Verify the document exists and check its page count
        cursor.execute("""
            SELECT page_count
            FROM Documents
            WHERE document_id = ?
        """, (document_id,))
        
        doc_data = cursor.fetchone()
        if not doc_data:
            return json.dumps({"error": f"Document with ID {document_id} not found", "page": None})
        
        page_count = doc_data[0]
        # Get all pages for the document
        cursor.execute("""
            SELECT dp.page_id, dp.page_number, dp.full_text, d.document_id, 
                   d.filename, d.document_type, d.page_count
            FROM DocumentPages dp
            JOIN Documents d ON dp.document_id = d.document_id
            WHERE dp.document_id = ? 
            ORDER BY dp.page_number
        """, (document_id,))
        
        page_data = cursor.fetchall()
        if not page_data:
            return json.dumps({"error": f"No pages found for document {document_id}", "pages": None})
        
        print('page_data', page_data)
        # Extract page data
        results = []
        for page_id, page_num, full_text, doc_id, filename, doc_type, total_pages in page_data:
            # Create result object
            result = {
                "page_id": page_id,
                "document_id": doc_id,
                "filename": filename,
                "document_type": doc_type,
                "page_number": page_num,
                "page_count": total_pages,
                "text": full_text[:int(cfg.DOC_PAGE_TEXT_LIMIT_IN_RESULTS)],
                "is_first_page": (page_num == 1),
                "is_last_page": (page_num == total_pages)
            }
            results.append(result)
        print('results', results)
        conn.close()
        return json.dumps({"pages": results, "error": None}, default=str)
        
    except Exception as e:
        return json.dumps({"error": str(e), "pages": None})


def get_documents_by_ids(conn_string: str, document_ids: List[str]) -> str:
    """
    Retrieve multiple documents by their document IDs.
    
    Parameters:
    -----------
    conn_string : str
        Database connection string for SQL Server
    document_ids : List[str]
        List of document IDs to retrieve
        
    Returns:
    --------
    str
        JSON string containing the documents data or an error message
        Format: {
            "documents": {
                "document_id_1": {"pages": [...], "error": None},
                "document_id_2": {"pages": [...], "error": None},
                ...
            },
            "error": None
        }
    """
    try:
        if not document_ids:
            return json.dumps({"error": "No document IDs provided", "documents": None})
        
        # Connect to database
        conn = pyodbc.connect(conn_string)
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Create placeholders for IN clause
        placeholders = ','.join(['?' for _ in document_ids])
        
        # First, verify which documents exist and get their page counts
        cursor.execute(f"""
            SELECT document_id, page_count
            FROM Documents
            WHERE document_id IN ({placeholders})
        """, document_ids)
        
        existing_docs = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Track not found documents
        not_found_docs = [doc_id for doc_id in document_ids if doc_id not in existing_docs]
        
        # Get all pages for the existing documents
        if existing_docs:
            cursor.execute(f"""
                SELECT dp.page_id, dp.page_number, dp.full_text, d.document_id, 
                       d.filename, d.document_type, d.page_count, d.archived_path [link_to_document]
                FROM DocumentPages dp
                JOIN Documents d ON dp.document_id = d.document_id
                WHERE dp.document_id IN ({placeholders})
                ORDER BY d.document_id, dp.page_number
            """, list(existing_docs.keys()))
            
            all_pages_data = cursor.fetchall()
        else:
            all_pages_data = []
        
        # Group pages by document_id
        documents_result = {}
        
        # Initialize result structure for all requested documents
        for doc_id in document_ids:
            if doc_id in not_found_docs:
                documents_result[doc_id] = {
                    "pages": None,
                    "error": f"Document with ID {doc_id} not found"
                }
            else:
                documents_result[doc_id] = {
                    "pages": [],
                    "error": None
                }
        
        # Process pages data
        for page_id, page_num, full_text, doc_id, filename, doc_type, total_pages, link_to_document in all_pages_data:
            # Create result object
            page_result = {
                "page_id": page_id,
                "document_id": doc_id,
                "filename": filename,
                "document_type": doc_type,
                "page_number": page_num,
                "page_count": total_pages,
                "text": full_text[:int(cfg.DOC_PAGE_TEXT_LIMIT_IN_RESULTS)],
                "document_link": link_to_document if link_to_document else '',
                "is_first_page": (page_num == 1),
                "is_last_page": (page_num == total_pages)
            }
            documents_result[doc_id]["pages"].append(page_result)
        
        # Check for documents with no pages
        for doc_id in existing_docs:
            if not documents_result[doc_id]["pages"]:
                documents_result[doc_id]["error"] = f"No pages found for document {doc_id}"
                documents_result[doc_id]["pages"] = None
        
        conn.close()
        return json.dumps({
            "documents": documents_result, 
            "error": None,
            "summary": {
                "total_requested": len(document_ids),
                "found": len(existing_docs),
                "not_found": len(not_found_docs),
                "not_found_ids": not_found_docs
            }
        }, default=str)
        
    except Exception as e:
        return json.dumps({"error": str(e), "documents": None})


def get_document_page_by_number_util(conn_string: str, document_id: str, page_number: int) -> str:
    """
    Retrieve a specific page from a document by its number.
    
    Parameters:
    -----------
    conn_string : str
        Database connection string for SQL Server
    document_id : str
        The ID of the document
    page_number : int
        The page number to retrieve
        
    Returns:
    --------
    str
        JSON string containing the page data or an error message
    """
    try:
        # Connect to database
        conn = pyodbc.connect(conn_string)
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Verify the document exists and check its page count
        cursor.execute("""
            SELECT page_count
            FROM Documents
            WHERE document_id = ?
        """, (document_id,))
        
        doc_data = cursor.fetchone()
        if not doc_data:
            return json.dumps({"error": f"Document with ID {document_id} not found", "page": None})
        
        page_count = doc_data[0]
        
        # Validate page number
        if page_number < 1 or page_number > page_count:
            return json.dumps({"error": f"Page number {page_number} is out of range (1-{page_count})", "page": None})
        
        # Get the requested page
        cursor.execute("""
            SELECT dp.page_id, dp.page_number, dp.full_text, d.document_id, 
                   d.filename, d.document_type, d.page_count
            FROM DocumentPages dp
            JOIN Documents d ON dp.document_id = d.document_id
            WHERE dp.document_id = ? AND dp.page_number = ?
        """, (document_id, page_number))
        
        page_data = cursor.fetchone()
        if not page_data:
            return json.dumps({"error": f"Page {page_number} data not found for document {document_id}", "page": None})
        
        # Extract page data
        page_id, page_num, full_text, doc_id, filename, doc_type, total_pages = page_data
        
        # Create result object
        result = {
            "page_id": page_id,
            "document_id": doc_id,
            "filename": filename,
            "document_type": doc_type,
            "page_number": page_num,
            "page_count": total_pages,
            "text": full_text,
            "is_first_page": (page_num == 1),
            "is_last_page": (page_num == total_pages)
        }
        
        # Get all fields for this page
        cursor.execute("""
            SELECT field_name, field_value
            FROM DocumentFields
            WHERE page_id = ?
        """, (page_id,))
        
        fields = {}
        for field_name, field_value in cursor.fetchall():
            fields[field_name] = field_value
        
        result["fields"] = fields
        
        conn.close()
        return json.dumps({"page": result, "error": None}, default=str)
        
    except Exception as e:
        return json.dumps({"error": str(e), "page": None})


def get_document_universe(
    conn_string: str, 
    document_types: Optional[List[str]] = None
) -> str:
    """
    Provides comprehensive metadata about the document universe to help AI understand
    document types, fields, relationships, and usage patterns.
    
    This function is designed to be called before search operations to give the AI
    context about the document ecosystem, enabling it to make intelligent inferences
    when users provide incomplete search criteria.
    
    The function dynamically discovers all document types and their characteristics
    without relying on hardcoded values, ensuring it remains effective as new document
    types are added to the system.
    
    Parameters:
    -----------
    conn_string : str
        Database connection string for SQL Server
    document_types : List[str], optional
        List of specific document types to get metadata for
        If None (default), metadata for all document types will be returned
    
    Returns:
    --------
    str
        JSON string containing:
        - document_types: List of all document types with counts and descriptions
        - field_metadata: Detailed information about all fields (frequency, related document types)
        - common_field_combinations: Frequently co-occurring fields
        - search_recommendations: Suggestions for effective search combinations
        - field_value_examples: Sample values for key fields to aid in pattern recognition
        - error: Error message if one occurred
    """
    import time
    # Initialize result containers
    document_types_info = []
    field_metadata = []
    common_combinations = []
    search_recommendations = []
    field_value_examples = {}
    field_distribution = defaultdict(list)
    error_message = None
    
    try:
        # Connect to database
        conn = pyodbc.connect(conn_string)
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # 1. Dynamically get document types with counts
        if document_types is None:
            # Get all document types if none specified
            cursor.execute("""
                SELECT 
                    document_type, 
                    COUNT(*) as doc_count,
                    MIN(processed_at) as first_seen,
                    MAX(processed_at) as last_seen
                FROM Documents 
                GROUP BY document_type 
                ORDER BY doc_count DESC
            """)
        else:
            # Get only the specified document types
            # Create a parameterized query with the right number of placeholders
            placeholders = ', '.join(['?' for _ in document_types])
            query = f"""
                SELECT 
                    document_type, 
                    COUNT(*) as doc_count,
                    MIN(processed_at) as first_seen,
                    MAX(processed_at) as last_seen
                FROM Documents 
                WHERE document_type IN ({placeholders})
                GROUP BY document_type 
                ORDER BY doc_count DESC
            """
            cursor.execute(query, document_types)
        
        document_types_with_counts = cursor.fetchall()
        
        # Process document type information with inferred descriptions
        for doc_type, count, first_seen, last_seen in document_types_with_counts:
            # Initialize a document type info object
            doc_type_info = {
                "type": doc_type,
                "count": count,
                "first_seen": first_seen.isoformat() if first_seen else None,
                "last_seen": last_seen.isoformat() if last_seen else None,
                "description": ""  # Will be populated based on common fields
            }
            
            # Get most common fields for this document type to infer description
            try:
                cursor.execute("""
                    SELECT TOP 10
                        field_name
                    FROM DocumentFields df
                    JOIN DocumentPages dp ON df.page_id = dp.page_id
                    JOIN Documents d ON dp.document_id = d.document_id
                    WHERE d.document_type = ?
                    GROUP BY field_name
                    ORDER BY COUNT(*) DESC
                """, doc_type)
                
                common_fields = [row[0] for row in cursor.fetchall()]
                
                # Generate dynamic description based on common fields and type name
                description = f"Document type containing "
                
                # Look for patterns in the common fields to infer document purpose
                financial_fields = ["amount", "total", "price", "cost", "payment", "balance", "invoice"]
                shipping_fields = ["tracking", "shipment", "delivery", "carrier", "freight", "shipping"]
                product_fields = ["product", "item", "sku", "upc", "quantity", "inventory"]
                customer_fields = ["customer", "client", "account", "contact"]
                vendor_fields = ["vendor", "supplier", "manufacturer"]
                
                field_str = ' '.join(common_fields).lower() if common_fields else ""
                
                # Check for patterns in document type name first
                if "invoice" in doc_type.lower():
                    description = "Financial document listing items, services, and amounts for payment"
                elif "receipt" in doc_type.lower():
                    description = "Document confirming payment has been received"
                elif "statement" in doc_type.lower():
                    description = "Periodic document summarizing account activity and balances"
                elif "bill_of_lading" in doc_type.lower() or "bol" in doc_type.lower():
                    description = "Transportation document serving as receipt of goods and contract of carriage"
                elif "packing" in doc_type.lower() and "slip" in doc_type.lower():
                    description = "Document listing contents of a shipment"
                elif "delivery" in doc_type.lower() and "confirmation" in doc_type.lower():
                    description = "Document confirming successful delivery of goods"
                elif "purchase_order" in doc_type.lower() or "po" in doc_type.lower():
                    description = "Document authorizing purchase of goods or services"
                elif "delivery_confirmation" in doc_type.lower():
                    description = "Document confirming successful delivery of goods"
                elif "packing_slip" in doc_type.lower():
                    description = "Document listing contents of a shipment"
                elif "bank_statement" in doc_type.lower():
                    description = "Financial document showing account activity"
                elif "payment_journal" in doc_type.lower():
                    description = "Record of payment transactions"
                elif "letter_of_credit_advice" in doc_type.lower():
                    description = "Document related to international trade financing"
                # Otherwise, infer from fields
                elif any(field in field_str for field in financial_fields):
                    description = "Financial document containing transaction details"
                elif any(field in field_str for field in shipping_fields):
                    description = "Shipping-related document with logistics information"
                elif any(field in field_str for field in product_fields):
                    description = "Product-related document with inventory information"
                else:
                    # Generic description as fallback
                    description = f"Document type containing {', '.join(common_fields[:3]) if common_fields else 'various fields'}"
                
                doc_type_info["description"] = description
                doc_type_info["common_fields"] = common_fields if common_fields else []
            except Exception as field_error:
                # Handle errors in field retrieval gracefully
                doc_type_info["description"] = f"Document type with {count} instances"
                doc_type_info["common_fields"] = []
                doc_type_info["field_error"] = str(field_error)

            # Override the hard coded document type description to the document type info
            doc_type_info["description"] = f"{doc_type} with {count} instances"
            
            document_types_info.append(doc_type_info)
        
        # 2. Get comprehensive field metadata and document type relationships
        try:
            # Adjust SQL query based on whether specific document types are requested
            if document_types is None:
                cursor.execute(f"""
                    SELECT TOP({cfg.DOC_TOP_N_FIELDS_INCLUDED_IN_RESULTS})
                        df.field_name, 
                        --df.field_path,
                        COUNT(*) as field_count,
                        COUNT(DISTINCT d.document_id) as document_count
                    FROM DocumentFields df
                    JOIN DocumentPages dp ON df.page_id = dp.page_id
                    JOIN Documents d ON dp.document_id = d.document_id
                    WHERE df.field_name NOT IN ({cfg.DOC_IGNORE_FIELDS_IN_FIELD_METADATA})
                    GROUP BY df.field_name--, df.field_path
                    ORDER BY document_count DESC
                """)
            else:
                # Create a parameterized query with the right number of placeholders
                placeholders = ', '.join(['?' for _ in document_types])
                query = f"""
                    SELECT TOP({cfg.DOC_TOP_N_FIELDS_INCLUDED_IN_RESULTS}) 
                        df.field_name, 
                        --df.field_path,
                        COUNT(*) as field_count, 
                        COUNT(DISTINCT d.document_id) as document_count 
                    FROM DocumentFields df 
                    JOIN DocumentPages dp ON df.page_id = dp.page_id 
                    JOIN Documents d ON dp.document_id = d.document_id 
                    WHERE d.document_type IN ({placeholders}) 
                    AND df.field_name NOT IN ({cfg.DOC_IGNORE_FIELDS_IN_FIELD_METADATA})
                    GROUP BY df.field_name--, df.field_path
                    ORDER BY document_count DESC 
                """

                # Create the actual final query with substituted values
                final_query = query
                for param in document_types:
                    final_query = final_query.replace('?', f"'{param}'", 1)

                cursor.execute(query, document_types)
            
            field_rows = cursor.fetchall()

            # print('field_rows Results:')
            # for row in field_rows:
            #     print(row)

            #time.sleep(5)
            
            # Create a dictionary for mapping field names to document types
            field_to_doc_types = {}
            
            # Execute this query with filtering based on document_types if provided
            if document_types is None:
                cursor.execute("""
                    SELECT 
                        df.field_name,
                        d.document_type
                    FROM DocumentFields df
                    JOIN DocumentPages dp ON df.page_id = dp.page_id
                    JOIN Documents d ON dp.document_id = d.document_id
                    GROUP BY df.field_name, d.document_type
                """)
            else:
                # Create a parameterized query for specific document types
                placeholders = ', '.join(['?' for _ in document_types])
                query = f"""
                    SELECT 
                        df.field_name,
                        d.document_type
                    FROM DocumentFields df
                    JOIN DocumentPages dp ON df.page_id = dp.page_id
                    JOIN Documents d ON dp.document_id = d.document_id
                    WHERE d.document_type IN ({placeholders})
                    GROUP BY df.field_name, d.document_type
                """
                cursor.execute(query, (document_types))
            
            # Build a mapping of field_name -> list of document_types
            for field_name, doc_type in cursor.fetchall():
                if field_name not in field_to_doc_types:
                    field_to_doc_types[field_name] = []
                if doc_type not in field_to_doc_types[field_name]:
                    field_to_doc_types[field_name].append(doc_type)
            
            # Now process the field metadata with the document types with sample values
            sample_count = cfg.DOC_FIELD_SAMPLE_VALUES_COUNT if cfg.DOC_FIELD_SAMPLE_VALUES_COUNT else 3

            print(f'Getting sample values for {len(field_rows)} fields...')

            for field_name, field_count, document_count in field_rows: 
                # Build field metadata with inferred field purpose
                field_info = {
                    "name": field_name,
                    #"field_path": field_path,
                    "display_name": field_name.replace('_', ' ').title(),
                    "count": field_count,
                    "document_count": document_count,
                    "document_types": field_to_doc_types.get(field_name, []),
                    "sample_values": []  # New field for sample values
                }

                # Get sample values for this field
                if cfg.DOC_INCLUDE_FIELD_SAMPLES_VALUES:
                    if document_types is None:
                        cursor.execute("""
                            SELECT TOP (3) field_value
                            FROM DocumentFields
                            WHERE field_name = ?
                            AND field_value IS NOT NULL 
                            AND field_value != ''
                            GROUP BY field_value
                            ORDER BY field_value
                        """, (field_name))
                    else:
                        placeholders = ', '.join(['?' for _ in document_types])
                        params = [field_name] + document_types
                        query = f"""
                            SELECT TOP (3) df.field_value
                            FROM DocumentFields df
                            JOIN DocumentPages dp ON df.page_id = dp.page_id
                            JOIN Documents d ON dp.document_id = d.document_id
                            WHERE df.field_name = ?
                            AND d.document_type IN ({placeholders})
                            AND df.field_value IS NOT NULL 
                            AND df.field_value != ''
                            GROUP BY df.field_value
                            ORDER BY df.field_value
                        """
                        cursor.execute(query, (params))
                
                    sample_values = [row[0] for row in cursor.fetchall() if row[0]]
                else:
                    sample_values = []

                field_info["sample_values"] = sample_values[:sample_count]

                #print(f"Field Info: {field_info}")

                field_metadata.append(field_info)
        except Exception as e:
            error_message = f"Error retrieving field metadata: {str(e)}"
            print(f"Error retrieving field metadata: {str(e)}")
            # Continue with other parts of the function
        
        if cfg.DOC_INCLUDE_KEY_FIELDS_IN_METADATA:
            # 3. Get sample values for important fields to help with pattern recognition
            try:
                # Dynamically determine identifier fields instead of hardcoding
                if document_types is None:
                    cursor.execute("""
                        SELECT TOP 15 field_name 
                        FROM DocumentFields
                        WHERE field_name LIKE '%number%'
                        OR field_name LIKE '%id%'
                        OR field_name LIKE '%reference%'
                        OR field_name LIKE '%code%'
                        OR field_name LIKE '%order%'
                        GROUP BY field_name
                        ORDER BY COUNT(*) DESC
                    """)
                else:
                    # Create a parameterized query for specific document types
                    placeholders = ', '.join(['?' for _ in document_types])
                    query = f"""
                        SELECT TOP 15 field_name 
                        FROM DocumentFields df
                        JOIN DocumentPages dp ON df.page_id = dp.page_id
                        JOIN Documents d ON dp.document_id = d.document_id
                        WHERE (field_name LIKE '%number%'
                        OR field_name LIKE '%id%'
                        OR field_name LIKE '%reference%'
                        OR field_name LIKE '%code%'
                        OR field_name LIKE '%order%')
                        AND d.document_type IN ({placeholders})
                        GROUP BY field_name
                        ORDER BY COUNT(*) DESC
                    """
                    cursor.execute(query, document_types)
                
                key_fields = [row[0] for row in cursor.fetchall()]
                
                if cfg.DOC_INCLUDE_FIELD_SAMPLES_VALUES:
                    for field in key_fields:
                        try:
                            if document_types is None:
                                cursor.execute("""
                                    SELECT TOP 5 field_value 
                                    FROM DocumentFields 
                                    WHERE field_name = ? 
                                    GROUP BY field_value
                                    ORDER BY COUNT(*) DESC
                                """, field)
                            else:
                                # Create a parameterized query with field name and document types
                                placeholders = ', '.join(['?' for _ in document_types])
                                params = [field] + document_types
                                query = f"""
                                    SELECT TOP 5 field_value 
                                    FROM DocumentFields df
                                    JOIN DocumentPages dp ON df.page_id = dp.page_id
                                    JOIN Documents d ON dp.document_id = d.document_id
                                    WHERE field_name = ? 
                                    AND d.document_type IN ({placeholders})
                                    GROUP BY field_value
                                    ORDER BY COUNT(*) DESC
                                """
                                cursor.execute(query, (params))
                            
                            sample_values = [row[0] for row in cursor.fetchall()]
                            if sample_values:
                                field_value_examples[field] = sample_values
                        except:
                            # Skip problematic fields but continue processing
                            continue
            except Exception as e:
                # Add error info but continue execution
                error_message = f"{error_message or ''} Error retrieving sample values: {str(e)}"
                print(f"Error retrieving sample values: {str(e)}")
                #time.sleep(5)
        
        if cfg.DOC_INCLUDE_FIELD_USAGE_STATS:
            # 4. Generate common field combinations by document type
            try:
                print("Generating common field combinations by document type...")
                doc_types_to_process = [item["type"] for item in document_types_info]
                
                for doc_type in doc_types_to_process:
                    try:
                        cursor.execute("""
                            SELECT TOP 10
                                field_name,
                                COUNT(*) as frequency
                            FROM DocumentFields df
                            JOIN DocumentPages dp ON df.page_id = dp.page_id
                            JOIN Documents d ON dp.document_id = d.document_id
                            WHERE d.document_type = ?
                            GROUP BY field_name
                            ORDER BY frequency DESC
                        """, doc_type)
                        
                        common_fields = [row[0] for row in cursor.fetchall()]
                        
                        if common_fields:
                            common_combinations.append({
                                "document_type": doc_type,
                                "common_fields": common_fields
                            })
                    except:
                        # Skip this document type but continue with others
                        continue
            except Exception as e:
                # Add error info but continue execution
                error_message = f"{error_message or ''} Error generating field combinations: {str(e)}"
                print(f"Error generating field combinations: {str(e)}")
                #time.sleep(5)
        
            # 5. Generate search recommendations based on data patterns
            try:
                print("Generate search recommendations based on data patterns...")
                # For each document type, suggest the most effective search fields
                doc_types_to_process = [item["type"] for item in document_types_info]
                
                for doc_type in doc_types_to_process:
                    try:
                        # First, focus on identifier fields which are most useful for exact matches
                        cursor.execute("""
                            SELECT TOP 5
                                field_name
                            FROM DocumentFields df
                            JOIN DocumentPages dp ON df.page_id = dp.page_id
                            JOIN Documents d ON dp.document_id = d.document_id
                            WHERE d.document_type = ?
                            AND (
                                field_name LIKE '%number%' OR
                                field_name LIKE '%id%' OR
                                field_name LIKE '%reference%' OR
                                field_name LIKE '%code%'
                            )
                            GROUP BY field_name
                            ORDER BY COUNT(*) DESC
                        """, doc_type)
                        
                        identifier_fields = [row[0] for row in cursor.fetchall()]
                        
                        # Now get other high-value search fields
                        if identifier_fields:
                            # Build a parameter list for the NOT IN clause
                            params = [doc_type]
                            placeholders = []
                            
                            for field in identifier_fields:
                                params.append(field)
                                placeholders.append('?')
                            
                            not_in_clause = f"AND field_name NOT IN ({', '.join(placeholders)})"
                            
                            query = f"""
                                SELECT TOP 10
                                    field_name
                                FROM DocumentFields df
                                JOIN DocumentPages dp ON df.page_id = dp.page_id
                                JOIN Documents d ON dp.document_id = d.document_id
                                WHERE d.document_type = ?
                                {not_in_clause}
                                GROUP BY field_name
                                ORDER BY COUNT(*) DESC
                            """
                            
                            cursor.execute(query, (params))
                        else:
                            # If no identifier fields, just get the most common fields
                            cursor.execute("""
                                SELECT TOP 10
                                    field_name
                                FROM DocumentFields df
                                JOIN DocumentPages dp ON df.page_id = dp.page_id
                                JOIN Documents d ON dp.document_id = d.document_id
                                WHERE d.document_type = ?
                                GROUP BY field_name
                                ORDER BY COUNT(*) DESC
                            """, doc_type)
                        
                        secondary_fields = [row[0] for row in cursor.fetchall()]
                        
                        search_recommendations.append({
                            "document_type": doc_type,
                            "primary_search_fields": identifier_fields,
                            "secondary_search_fields": secondary_fields,
                            "recommended_combinations": [
                                [identifier_fields[0]] if identifier_fields else [],
                                identifier_fields[:2] if len(identifier_fields) >= 2 else identifier_fields,
                                [identifier_fields[0], secondary_fields[0]] if identifier_fields and secondary_fields else []
                            ]
                        })
                    except Exception as e:
                        # Skip this document type but continue with others
                        continue
            except Exception as e:
                # Add error info but continue execution
                error_message = f"{error_message or ''} Error generating search recommendations: {str(e)}"
                print(f"Error generating search recommendations: {str(e)}")
                #time.sleep(5)
        
            # 6. Get field distribution by document type (to understand unique fields per type)
            try:
                print("Get field distribution by document type (to understand unique fields per type)...")
                doc_types_to_process = [item["type"] for item in document_types_info]
                
                for doc_type in doc_types_to_process:
                    try:
                        # Get document IDs for this document type to count them correctly
                        cursor.execute("""
                            SELECT COUNT(DISTINCT document_id) 
                            FROM Documents 
                            WHERE document_type = ?
                        """, doc_type)
                        
                        doc_count = cursor.fetchone()[0]
                        
                        if doc_count > 0:
                            # Now get field frequencies counting distinct document_id to get per-document count
                            cursor.execute("""
                                SELECT 
                                    df.field_name,
                                    COUNT(DISTINCT d.document_id) as doc_frequency
                                FROM DocumentFields df
                                JOIN DocumentPages dp ON df.page_id = dp.page_id
                                JOIN Documents d ON dp.document_id = d.document_id
                                WHERE d.document_type = ?
                                GROUP BY df.field_name
                                ORDER BY doc_frequency DESC
                            """, doc_type)
                            
                            for field_name, doc_frequency in cursor.fetchall():
                                percentage = (doc_frequency * 100.0) / doc_count
                                
                                field_distribution[doc_type].append({
                                    "field_name": field_name,
                                    "document_frequency": doc_frequency,
                                    "percentage": round(percentage, 2)  # Round to 2 decimal places
                                })
                    except Exception as e:
                        # Skip this document type but continue with others
                        continue
            except Exception as e:
                # Add error info but continue execution
                error_message = f"{error_message or ''} Error calculating field distribution: {str(e)}"
                print(f"Error calculating field distribution: {str(e)}")
                #time.sleep(5)

        conn.close()
        
    except Exception as e:
        error_message = f"Main execution error: {str(e)}"
        print(f"Main execution error: {str(e)}")
        #time.sleep(5)

    print('Getting document attributes metadata...')
    if document_types:
        custom_attribute_metadata = get_document_attributes_metadata(document_type=document_types)
    else:
        custom_attribute_metadata = get_document_attributes_metadata()
    
    # Prepare complete metadata response
    metadata = {
        "document_types": document_types_info,
        "field_metadata": field_metadata,
        "custom_attribute_metadata": custom_attribute_metadata['attribute_metadata'] if custom_attribute_metadata else [],
        "common_field_combinations": common_combinations,
        "search_recommendations": search_recommendations,
        "field_value_examples": field_value_examples,
        "field_distribution_by_type": dict(field_distribution),
        "error": error_message,
        "filtered_document_types": document_types  # Include the filter that was applied
    }
    
    # Dynamically generate system context based on discovered document types
    try:
        print('Dynamically generating system context based on discovered document types...')
        primary_doc_types = [doc["type"] for doc in document_types_info[:5]]
        
        use_cases = []
        typical_queries = []
        
        # Generate use cases and typical queries based on discovered document types
        for doc_type in primary_doc_types:
            type_lower = doc_type.lower()
            
            if "bill_of_lading" in type_lower or "bol" in type_lower:
                use_cases.append("Tracking shipments and carrier information")
                typical_queries.append(f"Find {doc_type} with BOL number X")
            elif "invoice" in type_lower:
                use_cases.append("Managing accounts receivable and payments")
                typical_queries.append(f"Get all {doc_type}s for customer Y")
            elif "payment" in type_lower:
                use_cases.append("Reconciling payments and accounts")
                typical_queries.append(f"Find {doc_type} records from last month")
            elif "delivery" in type_lower:
                use_cases.append("Confirming successful deliveries")
                typical_queries.append(f"Get {doc_type} for order Z")
            elif "statement" in type_lower:
                use_cases.append("Reviewing account status and history")
                typical_queries.append(f"Find {doc_type}s from January 2025")
            elif "purchase" in type_lower or "order" in type_lower:
                use_cases.append("Managing orders and procurement")
                typical_queries.append(f"Get {doc_type} for reference number A")
            elif "lease" in type_lower:
                use_cases.append("Managing and researching lease agreements")
                typical_queries.append(f"Show me all active leases set to expire in the next 90 days")
            else:
                # Generic use case
                use_cases.append(f"Processing {doc_type} documents")
                typical_queries.append(f"Find {doc_type} with ID X")
        
        # Add general queries
        typical_queries.extend([
            "Find documents related to customer ABC",
            "Get all documents from February 2025",
            "Find documents with reference to order 12345"
        ])
        
        metadata["system_context"] = {
            "purpose": "Document management and search system for business document processing",
            "primary_use_cases": list(set(use_cases)),  # Remove duplicates
            "typical_user_queries": list(set(typical_queries)),  # Remove duplicates
            "primary_document_types": primary_doc_types
        }
    except Exception as e:
        metadata["system_context"] = {
            "purpose": "Document management and search system",
            "error": f"Error generating system context: {str(e)}"
        }
    
    return json.dumps(metadata, default=str)


def get_document_fields(document_types=None):
    """
    Execute a query to extract all document types and their fields with sample values,
    returning a JSON structure where fields are grouped by document type.
    
    Args:
        document_types (list or None): List of document types to filter by, or None for all types
    
    Returns:
        str: JSON string with document types, their associated fields, and sample values
    """
    try:
        # Establish connection to the SQL Server database
        conn = get_db_connection()
        
        # Create a cursor
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get sample count from config
        sample_count = cfg.DOC_FIELD_SAMPLE_VALUES_COUNT if hasattr(cfg, 'DOC_FIELD_SAMPLE_VALUES_COUNT') else 3
        
        # Execute the query based on whether document types are provided
        if document_types is None:
            query = """
            SELECT distinct d.document_type, f.field_name
            FROM [dbo].[Documents] d
            JOIN [dbo].[DocumentPages] p on p.document_id = d.document_id
            JOIN [dbo].[DocumentFields] f on f.page_id = p.page_id
            ORDER BY d.document_type, f.field_name
            """
            
            print('Running query for all types...')
            cursor.execute(query)
        else:
            # Convert single string to list if needed
            if isinstance(document_types, str):
                document_types = [document_types]
                
            # Create parameterized query with the right number of placeholders
            placeholders = ','.join(['?' for _ in document_types])
            query = f"""
            SELECT distinct d.document_type, f.field_name
            FROM [dbo].[Documents] d
            JOIN [dbo].[DocumentPages] p on p.document_id = d.document_id
            JOIN [dbo].[DocumentFields] f on f.page_id = p.page_id
            WHERE d.document_type IN ({placeholders})
            ORDER BY d.document_type, f.field_name
            """
            
            print(f'Running query for specific types: {", ".join(document_types)}')
            cursor.execute(query, document_types)
        
        # Use defaultdict to group fields by document type
        document_fields = defaultdict(list)
        
        # Process the results to get unique field names per document type
        field_mapping = defaultdict(set)
        for row in cursor.fetchall():
            document_type = row[0]
            field_name = row[1]
            field_mapping[document_type].add(field_name)
        
        # Now get sample values for each field per document type
        for doc_type, fields in field_mapping.items():
            fields_with_samples = []
            
            for field_name in sorted(fields):
                field_info = {
                    "name": field_name,
                    "display_name": field_name.replace('_', ' ').title(),
                    "sample_values": []
                }
                
                # Get sample values for this field in this document type
                cursor.execute("""
                    SELECT TOP (?) DISTINCT f.field_value
                    FROM [dbo].[DocumentFields] f
                    JOIN [dbo].[DocumentPages] p ON f.page_id = p.page_id
                    JOIN [dbo].[Documents] d ON p.document_id = d.document_id
                    WHERE d.document_type = ? 
                    AND f.field_name = ?
                    AND f.field_value IS NOT NULL 
                    AND f.field_value != ''
                    ORDER BY f.field_value
                """, (sample_count, doc_type, field_name))
                
                sample_values = [row[0] for row in cursor.fetchall() if row[0]]
                field_info["sample_values"] = sample_values[:sample_count]
                
                fields_with_samples.append(field_info)
            
            document_fields[doc_type] = fields_with_samples
        
        # Close the connection
        conn.close()
        
        # Convert to regular dict for JSON serialization
        result = dict(document_fields)
        
        # Return as JSON string
        return json.dumps(result, indent=2)
        
    except Exception as e:
        print(str(e))
        return json.dumps({"error": str(e)})


# Add the AI decision function
def ask_ai_for_best_field(original_field: str, document_type: str, user_question: Optional[str] = None) -> List[str]:
    """
    Retrieves possible field names for a specific document type and uses AI to determine the best alternative field names that match the original search intent.
    
    Parameters:
    -----------
    original_field : The original field name that returned no results
    document_type : The document type being searched
    user_question : The original question or query from the user, providing additional context to help determine the most relevant alternative fields

    Returns:
    --------
    List of the best alternative field names to try, ordered by relevance.
    Returns an empty list if no suitable alternatives are found.
    """
    try:
        field_list = get_document_fields(document_type=document_type)
        print('Field List:', field_list)

        print('Sending request to AI...')
        # Create the prompt for the AI
        system_message = """You are a helpful assistant specialized in document fields analysis. 
        Your task is to identify multiple field matches based on semantic meaning, not just text similarity.
        Provide a JSON array of field names that would be suitable alternatives for the user's search.
        If there are no suitable alternatives, return an empty array [].
        The response should be ONLY a valid JSON array of strings. No explanation or other text."""
        
        user_message = f"""
        I'm searching documents of type '{document_type}' and tried to search for the field '{original_field}' but got no results.

        {'Original user question: "' + user_question + '"' if user_question else ''}

        Here are all available fields for this document type:
        {field_list}

        Based on my original search field '{original_field}' {'and the user question' if user_question else ''}, which fields (if any) would be the best alternatives to search on instead? 
        List them in order of relevance, with the most relevant first.

        Only respond with a JSON array of field names, like this: ["field1", "field2", "field3"]. 
        If none are suitable, respond with an empty array: [].
        """
        #print(user_message)
        
        result = azureQuickPrompt(prompt=user_message, system=system_message)
        print('Results:', result)
        
        # Parse the JSON response
        try:
            suggested_fields = json.loads(result)
            print('Suggested Fields:', suggested_fields)
        except json.JSONDecodeError:
            # If JSON parsing fails, try to extract field names from the text
            # This is a fallback in case the AI doesn't return proper JSON
            import re
            matches = re.findall(r'"([^"]+)"', result)
            suggested_fields = matches if matches else []
        
        # Verify that the responses are actually fields in our list
        valid_field_names = [field for field in field_list]
        verified_fields = []
        print(valid_field_names)
        
        for field in suggested_fields:
            print(field)
            if field in valid_field_names:
                verified_fields.append(field)
            else:
                # Try case-insensitive match
                for valid_field in valid_field_names:
                    if valid_field.lower() == field.lower():
                        verified_fields.append(valid_field)
                        break
        
        return verified_fields
        
    except Exception as e:
        print(f"Error in AI field matching: {str(e)}")
        return []


def check_document_completeness(
    user_question,
    current_document_text,
    document_metadata
):
    """
    Analyze if the agent has enough document information to answer the user's question.
    
    Args:
        user_question (str): The original question asked by the user
        current_document_text (str): The text content the agent currently has from the document
        document_metadata (dict): Metadata about the complete document
        
    Returns:
        dict: Analysis results with guidance for the agent
    """
    # Define the prompt template with placeholders
    prompt_template = sysp.DOC_CHECK_DOCUMENT_COMPLETENESS_MESSAGE

    # Define system message
    system_message = sysp.DOC_CHECK_DOCUMENT_COMPLETENESS_SYSTEM
    
    # Replace placeholders in the prompt template
    prompt = prompt_template.replace(
        "{user_question}", user_question
    ).replace(
        "{document_text}", current_document_text
    ).replace(
        "{current_page}", str(document_metadata.get('current_page', 1))
    ).replace(
        "{total_pages}", str(document_metadata.get('total_pages', 1))
    ).replace(
        "{document_type}", document_metadata.get('document_type', 'Unknown')
    ).replace(
        "{document_reference}", document_metadata.get('document_reference', 'Unknown')
    )
    
    # Call the LLM using the prebuilt function
    llm_response = azureQuickPrompt(prompt=prompt, system=system_message)
    
    # Parse the response
    try:
        analysis = json.loads(llm_response)
        
        # Add a natural language instruction for the agent
        if not analysis.get("has_sufficient_information", False):
            instruction = f"""IMPORTANT: The current document page doesn't contain all the information needed to answer the user's question.

            What's missing: {analysis.get('missing_information_description', 'Some important information')}

            Likely location: {analysis.get('likely_location', 'Other pages of the document')}

            Recommended next step: {analysis.get('recommended_action', 'Check other pages')}

            {analysis.get('explanation', '')}"""
        else:
            instruction = "The current document page contains sufficient information to answer the user's question."
        
        analysis["instruction"] = instruction
        return analysis
        
    except json.JSONDecodeError:
        # Fallback if LLM doesn't return valid JSON
        current_page = document_metadata.get('current_page', 1)
        total_pages = document_metadata.get('total_pages', 1)
        
        if current_page < total_pages:
            fallback_action = "get_next_document_page"
            fallback_location = "next page"
        elif current_page > 1:
            fallback_action = "get_previous_document_page"
            fallback_location = "previous page"
        else:
            fallback_action = None
            fallback_location = "other sources"
            
        return {
            "has_sufficient_information": False,
            "missing_information_description": "Cannot determine what information is missing",
            "likely_location": fallback_location,
            "recommended_action": fallback_action,
            "explanation": f"The document has {total_pages} pages and you're currently on page {current_page}. Consider checking other pages for complete information.",
            "instruction": f"I notice this document has {total_pages} pages, but you're only viewing page {current_page}. Consider checking other pages to see if they contain information relevant to the user's question."
        }

# Example usage
def guide_agent_document_navigation(user_question, current_document_text, document_metadata):
    """
    Analyze if the current document page has enough information and guide the agent on next steps.
    """
    analysis = check_document_completeness(
        user_question,
        current_document_text,
        document_metadata
    )
    
    return analysis["instruction"]

def format_document_link(path: str) -> str:
    """
    Format a document path into a clickable link for the UI.
    """
    if not path:
        return None
    from CommonUtils import get_base_url

    # Replace backslashes with forward slashes
    formatted_path = path.replace('\\', '/')
    
    # Ensure it has the proper prefix
    if not formatted_path.startswith('/document/serve/'):
        if not formatted_path.startswith('/'):
            formatted_path = f"{get_base_url()}/document/serve?path={formatted_path}"
        else:
            formatted_path = f"{get_base_url()}/document/serve?path={formatted_path}"
    
    return formatted_path

def ensure_document_has_formatted_link(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensures a document result has a properly formatted clickable link.
    Modifies the result dict to include a 'formatted_link' field that the AI will use.
    """
    from CommonUtils import get_base_url

    # Get the raw path from various possible fields
    raw_path = (result.get('link_to_document') or 
                result.get('archived_path') or 
                result.get('path_to_document') or 
                result.get('clickable_link') or 
                result.get('document_url') or '')
    
    if raw_path:
        # If it's already a formatted /document/serve/ path, use it
        if raw_path.startswith('/document/serve/'):
            formatted_link = raw_path
        else:
            # Format the path
            formatted_path = raw_path.replace('\\', '/')
            if not formatted_path.startswith('/'):
                formatted_link = f"{get_base_url()}/document/serve/{formatted_path}"
            else:
                formatted_link = f"{get_base_url()}/document/serve{formatted_path}"
        
        # Add the formatted link to the result
        result['formatted_link'] = formatted_link
        result['clickable_document_link'] = f"{formatted_link}"
        
        # Also update the snippet to include the link
        if result.get('snippet'):
            result['snippet'] += f"\n\nView Document: {formatted_link}"
    else:
        result['formatted_link'] = None
        result['clickable_document_link'] = "[Document path not available]"
    
    return result


def summarize_snippets_for_token_reduction(results: List[Dict], user_question: str) -> List[Dict]:
    """
    Summarize snippets in results to reduce token usage when the total estimated tokens
    exceed DOC_INTELLIGENT_MAX_CONTEXT_TOKENS.
    
    This function modifies the snippets in-place to create shorter, focused summaries
    that preserve information relevant to the user's question.
    """
    import time
    
    # Calculate current token usage
    total_text = ""
    for result in results:
        total_text += result.get("snippet", "")
        total_text += json.dumps(result.get("all_fields", {}))
    
    estimated_tokens = len(total_text) // cfg.DOC_CHARS_PER_TOKEN
    print(86 * '*')
    print(f"Estimated tokens: {estimated_tokens}")
    print(86 * '*')
    
    # Only summarize if we exceed the token limit
    if estimated_tokens <= cfg.DOC_INTELLIGENT_MAX_CONTEXT_TOKENS:
        return results
    
    print(f"Token limit exceeded ({estimated_tokens} > {cfg.DOC_INTELLIGENT_MAX_CONTEXT_TOKENS}). Summarizing {len(results)} snippets...")
    
    # Summarize snippets that are longer than a threshold
    summarization_threshold = 300  # Only summarize snippets longer than 300 chars
    summarized_count = 0
    
    for result in results:
        snippet = result.get("snippet", "")
        
        # Only summarize long snippets
        if len(snippet) > summarization_threshold:
            try:
                # Use AI to create focused summary
                summarized_snippet = azureMiniQuickPrompt(
                    system=sysp.AI_SNIPPET_SUMMARIZATION_SYSTEM_PROMPT,
                    prompt=sysp.AI_SNIPPET_SUMMARIZATION_USER_PROMPT.format(
                        user_question=user_question,
                        snippet=snippet
                    ),
                    temp=0.1  # Low temperature for consistent summaries
                )
                #print('original_snippet:', snippet)
                #print(86 * '-')
                print('summarized_snippet:', summarized_snippet)
                #time.sleep(5)
                # Update the result with summarized snippet
                result["snippet"] = summarized_snippet.strip()
                result["original_snippet_length"] = len(snippet)
                result["snippet_summarized"] = True
                summarized_count += 1
                
            except Exception as e:
                print(f"Error summarizing snippet: {str(e)}")
                # If summarization fails, truncate the snippet instead
                result["snippet"] = snippet[:200] + "... [truncated due to length]"
                result["snippet_summarized"] = "truncated"
    
    print(f"Successfully summarized {summarized_count} snippets")
    
    # Add metadata about summarization
    if summarized_count > 0:
        # You could add this to a response metadata field if needed
        pass
    
    return results













def ai_select_relevant_fields(user_question: str, available_fields: List[Dict], available_attributes: List[Dict], max_fields: int = 8) -> Dict[str, Any]:
    """
    Use AI to select the most relevant fields needed to answer the user's question.
    
    Parameters:
    -----------
    user_question : str
        The user's natural language question
    available_fields : List[Dict]
        List of field metadata with structure: 
        [{"field_name": "name", "usage_count": 123, "sample_values": ["val1", "val2"]}, ...]
    max_fields : int
        Maximum number of fields to select (default: 8)
        
    Returns:
    --------
    Dict containing selected fields and AI reasoning
    """
    
    if not available_fields and not available_attributes:
        return {
            "selected_fields": [],
            "reasoning": "No fields available for selection",
            "confidence": "low"
        }
    
    field_analysis = []
    
    # Custom Attributes
    sorted_fields = sorted(available_attributes, key=lambda x: x.get('document_count', 0), reverse=True)

    #print('Sorted Attributes:')
    #print(sorted_fields)

    for field in sorted_fields[:cfg.DOC_TOP_N_FIELDS_INCLUDED_IN_RESULTS]:  # Limit to top n fields for prompt size
        if cfg.DOC_INCLUDE_COUNTS_IN_AI_FIELD_DATA:
            field_info = {
                "field_name": field.get("field_name", "") if field.get("field_name", "") else field.get("attribute_name", ""),
                "usage_count": field.get("usage_count", 0) if field.get("usage_count", 0) else field.get("document_count", 0),
                "type": "custom attribute"
            }
        else:
            field_info = {
                "field_name": field.get("field_name", "") if field.get("field_name", "") else field.get("attribute_name", ""),
                "type": "custom attribute"
            }
        field_analysis.append(field_info)
    
    # Document Fields
    sorted_fields = sorted(available_fields, key=lambda x: x.get('usage_count', 0), reverse=True)

    #print('Sorted Fields:')
    #print(sorted_fields)
    
    # Create field analysis for AI
    for field in sorted_fields[:cfg.DOC_TOP_N_FIELDS_INCLUDED_IN_RESULTS]:  # Limit to top n fields for prompt size
        if cfg.DOC_INCLUDE_COUNTS_IN_AI_FIELD_DATA:
            field_info = {
                "field_name": field.get("field_name", "") if field.get("field_name", "") else field.get("attribute_name", ""),
                #"field_path": field.get("field_path", "") if field.get("field_name", "") else "",
                "usage_count": field.get("usage_count", 0) if field.get("usage_count", 0) else field.get("document_count", 0),
                "type": "document field"
            }
        else:
            field_info = {
                "field_name": field.get("field_name", "") if field.get("field_name", "") else field.get("attribute_name", ""),
                "type": "document field"
            }
        field_analysis.append(field_info)

    print(86 * '=')
    print(86 * '=')
    print(86 * '=')
    print('FIELDS FOR ANALYSIS:')
    print(field_analysis)
    print(86 * '=')
    print(86 * '=')
    print(86 * '=')
    #time.sleep(30)

    # Create AI prompt
    system_prompt = sysp.DOCUMENT_AI_FIELD_SELECTION_FOR_PRECISION_SYSTEM
    if cfg.DOC_USE_RECALL_PROMPTING_FOR_AI_FIELD_SELECTION:
        system_prompt = sysp.DOCUMENT_AI_FIELD_SELECTION_FOR_RECALL_SYSTEM

    user_prompt = f"""
                    Analyze this user question and select the relevant document fields or attributes needed to answer it:

                    USER QUESTION: "{user_question}"

                    AVAILABLE FIELDS (with usage counts and sample values):
                    {json.dumps(field_analysis, indent=2)}

                    SELECTION CRITERIA:
                    - Select fields that help answer the question
                    - Consider fields needed for filtering active/current records
                    - Include fields that provide context for the answer

                    ANALYSIS QUESTIONS TO CONSIDER:
                    1. What specific information is the user seeking?
                    2. What fields would help identify relevant documents?
                    3. What fields would help filter or sort results?
                    4. What fields provide the actual answer content?
                    5. Are there date fields needed for "active" or "current" queries?

                    Return your analysis as JSON:
                    {{
                        "selected_fields": ["field1", "field2", "field3"],
                        "reasoning": "Detailed explanation of why each field was selected and how it helps answer the question",
                        "field_purposes": {{
                            "field1": "identification|filtering|content|context",
                            "field2": "identification|filtering|content|context"
                        }},
                        "confidence": "high|medium|low",
                        "question_type": "lookup|analysis|filtering|exploration"
                    }}
                    """

    try:
        #print(user_prompt)
        # Call Azure OpenAI
        if cfg.DOC_USE_MINI_MODEL_FOR_AI_FIELD_SELECTION:
            ai_response = azureMiniQuickPrompt(
                prompt=user_prompt,
                system=system_prompt
            )
        else:
            ai_response = azureQuickPrompt(
                prompt=user_prompt,
                system=system_prompt
            )
        
        # Parse AI response
        selection_result = json.loads(ai_response)

        print(86  * '=')
        print(86  * '=')
        print('~~~~~~~~~~~~~~~~~~~~~~~~~~~ AI FIELD SELECTION COMPLETE ANALYSIS ~~~~~~~~~~~~~~~~~~~~~~~~~~~')
        print(selection_result)
        print(86  * '=')
        print(86  * '=')
        
        # Validate the response
        #if not validate_field_selection(selection_result, available_fields + available_attributes, max_fields):
            #return get_fallback_field_selection(available_fields, user_question, max_fields)
        
        # Add metadata about the selection process
        selection_result["total_available_fields"] = len(available_fields)
        selection_result["selection_method"] = "ai_analysis"
        selection_result["user_question"] = user_question
        
        return selection_result
        
    except Exception as e:
        print(f"AI field selection failed: {str(e)}")
        return get_fallback_field_selection(available_fields, user_question, max_fields)


def validate_field_selection(selection_result: Dict, available_fields: List[Dict], max_fields: int) -> bool:
    """
    Validate that the AI returned a proper field selection
    """
    try:
        # Check required keys
        required_keys = ["selected_fields", "reasoning", "confidence"]
        if not all(key in selection_result for key in required_keys):
            return False
        
        selected_fields = selection_result["selected_fields"]
        
        # Check that it's a list
        if not isinstance(selected_fields, list):
            return False
        
        # Check field count
        if len(selected_fields) > max_fields:
            return False
        
        # Check that selected fields exist in available fields
        available_field_names = [f.get("field_name", "") for f in available_fields]
        for field in selected_fields:
            if field not in available_field_names:
                return False
        
        return True
        
    except Exception:
        return False


def get_fallback_field_selection(available_fields: List[Dict], user_question: str, max_fields: int) -> Dict[str, Any]:
    """
    Fallback field selection when AI fails
    """
    # Core fields that are commonly useful
    core_fields = ["document_type", "title", "snippet", "document_date", "document_id"]
    
    # Get available field names
    available_field_names = [f.get("field_name", "") for f in available_fields]
    
    # Select core fields that exist
    selected_fields = [f for f in core_fields if f in available_field_names]
    
    # Fill remaining slots with highest usage fields
    remaining_slots = max_fields - len(selected_fields)
    sorted_fields = sorted(available_fields, key=lambda x: x.get('usage_count', 0), reverse=True)
    
    for field in sorted_fields:
        if len(selected_fields) >= max_fields:
            break
        field_name = field.get("field_name", "")
        if field_name not in selected_fields:
            selected_fields.append(field_name)
    
    # Simple keyword matching for question-specific fields
    question_lower = user_question.lower()
    question_keywords = {
        "date": ["date", "time", "created", "modified", "expiration", "commencement"],
        "money": ["amount", "cost", "price", "rent", "payment", "fee"],
        "location": ["address", "location", "city", "state", "property"],
        "status": ["status", "active", "inactive", "current"],
        "person": ["name", "person", "contact", "tenant", "landlord", "customer"]
    }
    
    for category, keywords in question_keywords.items():
        if any(keyword in question_lower for keyword in keywords):
            for field in available_fields:
                field_name = field.get("field_name", "").lower()
                if any(keyword in field_name for keyword in keywords):
                    if field.get("field_name") not in selected_fields and len(selected_fields) < max_fields:
                        selected_fields.append(field.get("field_name"))
    
    return {
        "selected_fields": selected_fields[:max_fields],
        "reasoning": f"Fallback selection: Core fields + highest usage fields + keyword matching for '{user_question}'",
        "confidence": "medium",
        "selection_method": "fallback_heuristic",
        "total_available_fields": len(available_fields)
    }







def get_document_attributes_metadata(document_type=None, return_format='dict'):
    """
    Reusable function to get document attributes metadata.
    
    Args:
        document_type (str or list, optional): Filter by specific document type(s)
        return_format (str): 'dict' for Python dict, 'json' for JSON string
    
    Returns:
        dict or str: Attributes metadata based on return_format
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))

        # Handle multiple document types
        if document_type:
            if isinstance(document_type, list):
                # Multiple document types
                placeholders = ', '.join(['?' for _ in document_type])
                where_clause = f"WHERE d.document_type IN ({placeholders}) AND d.is_knowledge_document = 0"
                params = document_type
            else:
                # Single document type
                where_clause = "WHERE d.document_type = ? AND d.is_knowledge_document = 0"
                params = [document_type]
        else:
            where_clause = "WHERE d.is_knowledge_document = 0"
            params = []
        
        # Get unique attribute names with examples
        cursor.execute(f"""
            SELECT 
                da.attribution_type,
                COUNT(*) as usage_count,
                COUNT(DISTINCT da.document_id) as documents_with_attribute,
                MIN(da.attribution_value) as sample_value_1,
                MAX(da.attribution_value) as sample_value_2
            FROM DocumentAttributions da
            JOIN Documents d ON da.document_id = d.document_id
            {where_clause}
            GROUP BY da.attribution_type
            ORDER BY usage_count DESC
        """, params)
        
        attribute_metadata = []
        for row in cursor.fetchall():
            sample_values = [row[3]]
            if row[4] and row[4] != row[3]:
                sample_values.append(row[4])
            
            attribute_metadata.append({
                'attribute_name': row[0],
                'usage_count': row[1],
                'documents_with_attribute': row[2],
                'sample_values': sample_values
            })
        
        # Build result
        result = {
            'attribute_metadata': attribute_metadata,
            'total_unique_attributes': len(attribute_metadata),
            'filtered_by_document_type': document_type,
            'search_tips': [
                'Use exact attribute names for precise filtering',
                'Combine multiple attributes for more specific searches',  
                'Use "contains" operator for partial matches',
                'Most common attributes: ' + ', '.join([attr['attribute_name'] for attr in attribute_metadata[:5]])
            ]
        }
        
        if return_format == 'json':
            return json.dumps(result)
        else:
            return result
        
    except Exception as e:
        print(f"Error getting document attributes metadata: {str(e)}")
        error_result = {
            'error': str(e),
            'attribute_metadata': [],
            'common_combinations': [],
            'total_unique_attributes': 0
        }
        
        if return_format == 'json':
            return json.dumps(error_result)
        else:
            return error_result
        
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'conn' in locals():
            conn.close()




def document_search_super_enhanced_debug(
        conn_string: str,
        user_question: Optional[str] = None,
        max_results: int = 800,
        check_completeness: bool = False
    ) -> str:
    """
    Enhanced document search that uses AI to determine the best search strategy based on the user's question.
    
    This function combines semantic and field-based searches intelligently, choosing the most appropriate 
    document types and search fields based on the user's intent, with comprehensive fallback strategies.
    
    Parameters:
    -----------
    conn_string : str
        Database connection string for SQL Server
    user_question : str
        The user's natural language question
    max_results : int, default=500
        Maximum number of results to return
    check_completeness : bool, default=False
        Whether to check if the document has all information needed to answer the question
    
    Returns:
    --------
    str
        JSON string containing search results and metadata
    """
    import time 

    if not user_question:
        return json.dumps({"error": "A user question is required for enhanced search"})

    # Initialize tracking variables
    search_attempts = []
    fallback_attempts = []
    
    # Step 1: Determine relevant document types using the specialized prompt
    print('Getting document types...')
    document_types_json = get_document_types()
    
    # Use the specialized system prompt to determine relevant document types
    system = sysp.SYS_PROMPT_DOCUMENT_TYPE_SEARCH_SYSTEM
    prompt = sysp.SYS_PROMPT_DOCUMENT_TYPE_SEARCH_PROMPT.replace(
        '{list_of_documents}', document_types_json).replace(
        '{input_question}', user_question)
    
    relevant_doc_types_json = azureMiniQuickPrompt(system=system, prompt=prompt)
    
    try:
        relevant_doc_types = json.loads(relevant_doc_types_json)
        if not isinstance(relevant_doc_types, list):
            relevant_doc_types = []
        print('Relevant Document Types:')
        print(relevant_doc_types)
        #time.sleep(5)
    except json.JSONDecodeError:
        # If we can't parse the result, assume no specific document types
        relevant_doc_types = []
        search_attempts.append("Failed to parse document type suggestions - proceeding with all document types")
        print('Failed to parse document type suggestions - proceeding with all document types')
    
    # Step 2: Get document universe metadata for available fields and other metadata
    print('Getting document universe...')
    universe_json = get_document_universe(conn_string, document_types=relevant_doc_types if relevant_doc_types else None)
    universe_data = json.loads(universe_json)
    print('Universe Data: <disabled print>')
    
    # Enhanced prompt with explicit field validation
    available_field_names = [field.get('name') for field in universe_data.get('field_metadata', [])]
    
    field_metadata = universe_data.get('field_metadata', [])
    print('Total Fields in Metadata:', len(field_metadata))

    simplified_fields = [{'field_name': field['name'], 'document_count': field['document_count']} for field in field_metadata]
    #simplified_fields = [{'field_name': field['name'], 'field_path': field['field_path'], 'document_count': field['document_count']} for field in field_metadata]

    print('Getting attributes from universe_data...')
    attribute_metadata = universe_data.get('custom_attribute_metadata', [])

    attribute_field_names = []
    if attribute_metadata:
        attribute_field_names = [item['attribute_name'] for item in attribute_metadata]

    print('Creating attributes from universe_data...')
    simplified_attributes = [{'attribute_name': attr['attribute_name'], 'document_count': attr['documents_with_attribute']} for attr in attribute_metadata]

    ai_selected_fields = None
    ai_strategy_prompt = ""
    if cfg.DOC_USE_AI_SELECTED_FIELDS:
        print('Using AI selected fields...')
        print('Total Fields:', len(simplified_fields))
        print('Total Attrs:', len(simplified_attributes))
        relevant_fields_response = ai_select_relevant_fields(user_question, simplified_fields, simplified_attributes, max_fields=8)
        # print('Relevant Fields Response:')
        # time.sleep(5)
        # print(relevant_fields_response)
        ai_selected_fields = relevant_fields_response['selected_fields']
        print('AI Selected Fields:')
        print(ai_selected_fields)
        #time.sleep(30)
        ai_strategy_prompt = "AI Suggested Fields: " + str(ai_selected_fields)

    # NOTE: This WAS in the below prompt but removed b/c it was thought to be unnecessary
    # CRITICAL FIELD VALIDATION RULE:
    # Only use field names that appear EXACTLY in this list of available fields:
    # {json.dumps(available_field_names + attribute_field_names, indent=2)}

    # Step 3: Enhanced search strategy determination with explicit field guidance
    system_prompt = """You are an expert document retrieval specialist. 
    Your task is to analyze a user's question and determine the optimal search strategy 
    for the specified document types.
    Your response must be in valid JSON format only.
    
    Available search strategies:
        - "semantic": Use semantic/vector search for conceptual matching (preferred)
        - "field": Use structured field search for precise data retrieval  
        - "hybrid": Combine semantic and field search
        - "wide_net_filter": Cast wide net to find all pages with key terms, then AI filters for relevance
    """

    prompt = f"""
    Current Date for Context: {datetime.now().strftime('%Y-%m-%d')}

    IMPORTANT FOR DATE FILTERS: 
    When creating field filters that reference "current", "active", "today", or similar time-based concepts, refer to the current date provided above in YYYY-MM-DD format. Do NOT use placeholders. Use the literal date values.

    Based on the user's question: "{user_question}"
    
    And the relevant document types: {json.dumps(relevant_doc_types)}

    {ai_strategy_prompt}
    
    Detailed field metadata with usage statistics:
    {json.dumps(universe_data.get('field_metadata', []), indent=2)}
    {json.dumps(attribute_metadata, indent=2)}
    
    FIELD SELECTION GUIDELINES:
    - If no suitable fields exist for your intended search, use semantic search instead
    
    Please analyze and provide a search strategy in the following JSON format:
    {{
        "search_approach": "semantic", // One of: "semantic", "field", "hybrid", or "wide_net_filter"
        "reasoning": "Explanation of your choice and which specific fields you selected",
        "confidence": "high|medium|low", // Your confidence in this strategy
        "semantic_search": {{            // Include if semantic or hybrid search
            "search_terms": ["term1", "term2"]  // Key terms for semantic search
        }},
        "field_search": {{               // Include if field or hybrid search
            "field_filters": [
                {{
                    "field_name": "exact_field_name_from_available_list",  // MUST match available fields exactly
                    "operator": "equals",  // See supported operators below
                    "value": "search_value",
                    "value2": "end_value"  // Only for 'between' operator
                }}
            ]
        }},
        "wide_net_search": {{            // Include if wide_net_filter search
            "explanation": "Why this strategy is best for comprehensive content review"
        }}
    }}

    Supported field search operators:
        - 'equals': Exact match
        - 'not_equals': Not equal to value
        - 'contains': Contains substring (case-insensitive)
        - 'not_contains': Does not contain substring
        - 'starts_with': Starts with value
        - 'ends_with': Ends with value
        - 'greater_than': Numeric/date greater than (attempts CAST to FLOAT)
        - 'greater_than_equal': Numeric/date greater than or equal
        - 'less_than': Numeric/date less than
        - 'less_than_equal': Numeric/date less than or equal
        - 'between': Range query (requires 'value2' parameter)
        - 'in': Value in list (expects comma-separated values)
        - 'not_in': Value not in list
        - 'is_null': Field value is NULL or empty
        - 'is_not_null': Field value is not NULL and not empty
        - 'regex': Regular expression match (SQL Server LIKE with pattern)
        - 'length_equals': Text length equals specified number
        - 'length_greater': Text length greater than specified number
        - 'length_less': Text length less than specified number
        - 'exists': Field exists in the document (regardless of value)
        - 'not_exists': Field does not exist in the document
    
    Smart Operator Selection Guidelines:
    - Use 'equals' for ID fields (customer_id, reference_number, invoice_number)
    - Use 'contains' for text fields (description, notes, address)
    - Use 'greater_than'/'between' for amount fields (total_amount, price)
    - Use 'between' for date fields with date ranges
    - Use 'exists'/'not_exists' to check for required/missing fields
    - Use 'in' for multiple specific values (status values, categories)
    
    Return ONLY the JSON with your analysis.
    """
    
    # Call Azure OpenAI to determine search strategy
    print('Determining search strategy...')
    #print(prompt)
    search_strategy_json = azureQuickPrompt(prompt=prompt, system=system_prompt)
    # print(86 * '-')
    # print(86 * '-')
    # print('Search Strategy Prompt:')
    # print(prompt)
    # print(86 * '-')
    # print(86 * '-')
    print('Search Strategy Result:')
    print(search_strategy_json)
    #time.sleep(30)
    
    # Parse the search strategy
    try:
        search_strategy = json.loads(search_strategy_json)
        #print('Search Strategy:')
        #print(search_strategy)
    except json.JSONDecodeError:
        # Fallback if AI doesn't return valid JSON
        search_attempts.append("Failed to parse AI search strategy - using fallback approach")
        search_strategy = {
            "search_approach": "semantic",
            "reasoning": "Fallback to semantic search due to AI parsing error",
            "confidence": "low",
            "semantic_search": {"search_terms": [user_question]}
        }
    
    # Add the document types to the search strategy
    search_strategy["document_types"] = relevant_doc_types
    
    # Initialize result containers
    combined_results = []
    error_message = None
    available_fields = universe_data.get("field_metadata", [])
    document_types = universe_data.get("document_types", [])
    document_counts = universe_data.get("document_counts", [])
    # print('Available Fields:')
    # print(available_fields)
    # time.sleep(2)
    
    # Step 4: Execute the search strategy
    if search_strategy.get("search_approach") in ["semantic", "hybrid"]:
        semantic_results = []
        semantic_terms = search_strategy.get("semantic_search", {}).get("search_terms", [])
        
        if not semantic_terms:
            semantic_terms = [user_question]
        
        search_attempts.append(f"Attempting semantic search with {len(semantic_terms)} terms")
        print(f"Attempting semantic search with {len(semantic_terms)} terms")
        #time.sleep(2)
        
        # TODO: The new semantic vector search should be used here...
        VECTOR_SEARCH_ERROR = False
        VECTOR_SEARCH = False
        for term in semantic_terms:
            # Try each relevant document type for semantic search
            if relevant_doc_types:
                print('Attempting semantic vector search with document types...')
                if search_strategy.get("search_approach") in ["semantic","hybrid"]:
                    try:
                        VECTOR_SEARCH_ERROR = False
                        from vector_engine_client import VectorEngineClient
                        vector_client = VectorEngineClient()
                        print('Searching for AI with vector client (w/ document type filter)...')
                        doc_typ_filter = {"document_type": {"$in": relevant_doc_types}}
                        search_result = vector_client.search_for_ai(term, filters=doc_typ_filter)
                        semantic_results.append(search_result.get("results", []))
                        VECTOR_SEARCH = True
                        print(f"Semantic search found {len(search_result.get('results', []))} results for '{term}'")
                    except Exception as e:
                        print('Error during vector search using vector engine...', str(e))
                        VECTOR_SEARCH_ERROR = True

                if VECTOR_SEARCH_ERROR:
                    for doc_type in relevant_doc_types:
                        search_result_json = document_search(
                            conn_string=conn_string,
                            document_type=doc_type,
                            search_query=term,
                            field_filters=[],
                            include_metadata=False,
                            max_results=max_results // (len(semantic_terms) * len(relevant_doc_types)),
                            user_question=user_question,
                            check_completeness=False,
                            ai_selected_fields=ai_selected_fields
                        )
                        
                        try:
                            search_result = json.loads(search_result_json)
                            if search_result.get("results"):
                                for result in search_result.get("results", []):
                                    result["search_method"] = f"semantic_{doc_type}"
                                semantic_results.extend(search_result.get("results", []))
                                search_attempts.append(f"Semantic search found {len(search_result.get('results', []))} results for '{term}' in {doc_type}")
                                print(f"Semantic search found {len(search_result.get('results', []))} results for '{term}' in {doc_type}")
                                #time.sleep(2)
                        except json.JSONDecodeError:
                            search_attempts.append(f"Failed to parse semantic search results for '{term}' in {doc_type}")
                            continue
            else:
                # Search without document type restriction
                print('Attempting global semantic search...')
                if search_strategy.get("search_approach") in ["semantic","hybrid"]:
                    try:
                        VECTOR_SEARCH_ERROR = False
                        from vector_engine_client import VectorEngineClient
                        vector_client = VectorEngineClient()
                        search_result = vector_client.search_for_ai(term)
                        semantic_results.append(search_result.get("results", []))
                        VECTOR_SEARCH = True
                        print(f"Semantic search found {len(search_result.get('results', []))} results for '{term}'")
                    except Exception as e:
                        print('Error during vector search using vector engine...', str(e))
                        VECTOR_SEARCH_ERROR = True

                if VECTOR_SEARCH_ERROR:
                    search_result_json = document_search(
                        conn_string=conn_string,
                        document_type=None,
                        search_query=term,
                        field_filters=[],
                        include_metadata=False,
                        max_results=max_results // len(semantic_terms),
                        user_question=user_question,
                        check_completeness=False,
                        ai_selected_fields=ai_selected_fields
                    )
                    
                    try:
                        search_result = json.loads(search_result_json)
                        if search_result.get("results"):
                            for result in search_result.get("results", []):
                                result["search_method"] = "semantic_global"
                            semantic_results.extend(search_result.get("results", []))
                            search_attempts.append(f"Global semantic search found {len(search_result.get('results', []))} results for '{term}'")
                            print(f"Global semantic search found {len(search_result.get('results', []))} results for '{term}'")
                            #time.sleep(2)
                    except json.JSONDecodeError:
                        search_attempts.append(f"Failed to parse global semantic search results for '{term}'")
                        continue
        
        combined_results.extend(semantic_results)

        if VECTOR_SEARCH:
            combined_results = []

            deduped_results = deduplicate_search_results(
                *semantic_results,
                keep_best=True
            )

            ai_result = ''
            ai_result = format_search_results_for_ai(deduped_results)
            combined_results.append(ai_result)
            semantic_result_count = 0
            for s_result in semantic_results:
                semantic_result_count += len(s_result)
            print('semantic_results length:', (semantic_result_count))
            print('deduped_results length:', len(deduped_results))
            return ai_result
    
    if search_strategy.get("search_approach") in ["field", "hybrid"]:
        field_filters = search_strategy.get("field_search", {}).get("field_filters", [])
        
        if field_filters:
            search_attempts.append(f"Attempting field-based search with {len(field_filters)} filters")
            print(f"Attempting field-based search with {len(field_filters)} filters")

            # Try field search for each relevant document type
            target_doc_types = relevant_doc_types or [None]
            
            for doc_type in target_doc_types:
                field_result_json = document_search(
                    conn_string=conn_string,
                    document_type=doc_type,
                    search_query="",
                    field_filters=field_filters,
                    include_metadata=False,
                    max_results=max_results,
                    user_question=user_question,
                    check_completeness=False,
                    ai_selected_fields=ai_selected_fields
                )
                
                try:
                    field_result = json.loads(field_result_json)
                    if field_result.get("results"):
                        for result in field_result.get("results", []):
                            result["search_method"] = f"field_{doc_type or 'global'}"
                        combined_results.extend(field_result.get("results", []))
                        search_attempts.append(f"Field search found {len(field_result.get('results', []))} results for {doc_type or 'all document types'}")
                        print(f"Field search found {len(field_result.get('results', []))} results for {doc_type or 'all document types'}")
                        #time.sleep(10)
                    else:
                        search_attempts.append(f"Field search returned no results for {doc_type or 'all document types'}")
                        print(f"Field search returned no results for {doc_type or 'all document types'}")
                        #time.sleep(10)
                except json.JSONDecodeError:
                    search_attempts.append(f"Failed to parse field search results for {doc_type or 'all document types'}")
                    print(f"Failed to parse field search results for {doc_type or 'all document types'}")
                    #time.sleep(2)
                    continue

    if search_strategy.get("search_approach") in ["wide_net_filter"]:
        combined_results = document_search_wide_net_strategy(user_question, document_types)
        print('Finished wide net search...')
    
    # Step 5: Enhanced fallback strategies if no results found
    if not combined_results:
        fallback_attempts.append("Primary search strategies returned no results - initiating fallback sequence")
        print("Primary search strategies returned no results - initiating fallback sequence")
        #time.sleep(2)
        # Fallback 1: Relaxed field search (convert equals to contains)
        if search_strategy.get("field_search", {}).get("field_filters"):
            fallback_attempts.append("Fallback 1: Relaxing field search operators")
            print("Fallback 1: Relaxing field search operators")
            #time.sleep(2)
            relaxed_filters = []
            
            for filter_item in search_strategy.get("field_search", {}).get("field_filters", []):
                if filter_item.get("operator") == "equals":
                    relaxed_filter = filter_item.copy()
                    relaxed_filter["operator"] = "contains"
                    relaxed_filters.append(relaxed_filter)
            
            if relaxed_filters:
                for doc_type in relevant_doc_types or [None]:
                    print('Attempting relaxed field search...')
                    fallback_result_json = document_search(
                        conn_string=conn_string,
                        document_type=doc_type,
                        search_query="",
                        field_filters=relaxed_filters,
                        include_metadata=False,
                        max_results=max_results,
                        user_question=user_question,
                        check_completeness=False,
                        ai_selected_fields=ai_selected_fields
                    )
                    
                    try:
                        fallback_result = json.loads(fallback_result_json)
                        if fallback_result.get("results"):
                            for result in fallback_result.get("results", []):
                                result["search_method"] = f"fallback_relaxed_field_{doc_type or 'global'}"
                            combined_results.extend(fallback_result.get("results", []))
                            fallback_attempts.append(f"Relaxed field search found {len(fallback_result.get('results', []))} results for {doc_type or 'all types'}")
                            print(f"Relaxed field search found {len(fallback_result.get('results', []))} results for {doc_type or 'all types'}")
                            #time.sleep(2)
                    except json.JSONDecodeError:
                        continue
        
        # Fallback 2: Broader semantic search with key terms extracted from question
        if not combined_results:
            fallback_attempts.append("Fallback 2: Extracting key terms for broader semantic search")
            print("Fallback 2: Extracting key terms for broader semantic search")
            #time.sleep(2)
            # Extract key terms from user question (simple approach)
            import re
            key_terms = []
            words = re.findall(r'\b[a-zA-Z]{3,}\b', user_question.lower())
            
            # Filter out common words
            stop_words = {'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can', 'had', 'her', 'was', 'one', 'our', 'out', 'day', 'get', 'has', 'him', 'his', 'how', 'its', 'may', 'new', 'now', 'old', 'see', 'two', 'who', 'boy', 'did', 'man', 'use', 'what', 'with', 'have', 'this', 'will', 'your', 'from', 'they', 'know', 'want', 'been', 'good', 'much', 'some', 'time', 'very', 'when', 'come', 'here', 'just', 'like', 'long', 'make', 'many', 'over', 'such', 'take', 'than', 'them', 'well', 'were'}
            
            for word in words:
                if word not in stop_words and len(word) > 3:
                    key_terms.append(word)
            
            # Limit to top 5 key terms
            key_terms = key_terms[:5]
            
            if key_terms:
                for term in key_terms:
                    for doc_type in relevant_doc_types or [None]:
                        fallback_result_json = document_search(
                            conn_string=conn_string,
                            document_type=doc_type,
                            search_query=term,
                            field_filters=[],
                            include_metadata=False,
                            max_results=max_results // (len(key_terms) * len(relevant_doc_types or [None])),
                            user_question=user_question,
                            check_completeness=False,
                            ai_selected_fields=ai_selected_fields
                        )
                        
                        try:
                            fallback_result = json.loads(fallback_result_json)
                            if fallback_result.get("results"):
                                for result in fallback_result.get("results", []):
                                    result["search_method"] = f"fallback_key_term_{doc_type or 'global'}"
                                combined_results.extend(fallback_result.get("results", []))
                                fallback_attempts.append(f"Key term '{term}' found {len(fallback_result.get('results', []))} results for {doc_type or 'all types'}")
                                print(f"Key term '{term}' found {len(fallback_result.get('results', []))} results for {doc_type or 'all types'}")
                                #time.sleep(2)
                        except json.JSONDecodeError:
                            continue
        
        # Fallback 3: Global semantic search with full question
        if not combined_results:
            fallback_attempts.append("Fallback 3: Global semantic search with full question")
            print("Fallback 3: Global semantic search with full question")
            #time.sleep(2)
            fallback_result_json = document_search(
                conn_string=conn_string,
                document_type=None,
                search_query=user_question,
                field_filters=[],
                include_metadata=False,
                max_results=max_results,
                user_question=user_question,
                check_completeness=False,
                ai_selected_fields=ai_selected_fields
            )
            
            try:
                fallback_result = json.loads(fallback_result_json)
                if fallback_result.get("results"):
                    for result in fallback_result.get("results", []):
                        result["search_method"] = "fallback_global_semantic"
                    combined_results.extend(fallback_result.get("results", []))
                    fallback_attempts.append(f"Global semantic search found {len(fallback_result.get('results', []))} results")
                    print(f"Global semantic search found {len(fallback_result.get('results', []))} results")
                    #time.sleep(2)
                else:
                    fallback_attempts.append("Global semantic search returned no results")
                    print("Global semantic search returned no results")
                    #time.sleep(2)
            except json.JSONDecodeError:
                fallback_attempts.append("Failed to parse global semantic search results")
                print("Failed to parse global semantic search results")
                #time.sleep(2)
        # Fallback 4: Existence-based search (find documents with any common fields)
        if not combined_results and relevant_doc_types:
            fallback_attempts.append("Fallback 4: Searching for documents with common fields")
            print("Fallback 4: Searching for documents with common fields")
            #time.sleep(2)
            # Try to find documents that simply exist for the relevant document types
            for doc_type in relevant_doc_types:
                # Look for documents with common identifier fields
                common_id_fields = ['customer_id', 'reference_number', 'invoice_number', 'order_number', 'id']
                
                for field_name in common_id_fields:
                    if field_name in available_field_names:
                        existence_filter = [{"field_name": field_name, "operator": "is_not_null"}]
                        
                        fallback_result_json = document_search(
                            conn_string=conn_string,
                            document_type=doc_type,
                            search_query="",
                            field_filters=existence_filter,
                            include_metadata=False,
                            max_results=max_results // len(relevant_doc_types),
                            user_question=user_question,
                            check_completeness=False,
                            ai_selected_fields=ai_selected_fields
                        )
                        
                        try:
                            fallback_result = json.loads(fallback_result_json)
                            if fallback_result.get("results"):
                                for result in fallback_result.get("results", []):
                                    result["search_method"] = f"fallback_existence_{doc_type}"
                                combined_results.extend(fallback_result.get("results", []))
                                fallback_attempts.append(f"Existence search found {len(fallback_result.get('results', []))} {doc_type} documents with {field_name}")
                                print(f"Existence search found {len(fallback_result.get('results', []))} {doc_type} documents with {field_name}")
                                #time.sleep(2)
                                break  # Found results, no need to try other fields for this doc type
                        except json.JSONDecodeError:
                            continue
        
        # Set error message if still no results
        if not combined_results:
            error_message = "No documents found matching your query after trying multiple search strategies and fallback methods."
            fallback_attempts.append("All fallback strategies exhausted - no results found")
            print("All fallback strategies exhausted - no results found")
            #time.sleep(2)
    # Step 6: Rank and deduplicate results
    if combined_results and len(combined_results) > 1 and cfg.DOC_INCLUDE_SNIPPET_IN_RESULT:
        print('Ranking and deduping results...')
        original_count = len(combined_results)
        combined_results = rank_search_results(combined_results, user_question)
        search_attempts.append(f"Ranked and deduplicated results: {original_count} → {len(combined_results)}")
        print(f"Ranked and deduplicated results: {original_count} → {len(combined_results)}")
        #time.sleep(2)
    # Step 7: Apply document completeness check if requested
    note = None
    if check_completeness and combined_results:
        try:
            document_metadata = combined_results[0]
            current_document_text = combined_results[0].get("snippet", "")
            result = check_document_completeness(
                user_question,
                current_document_text,
                document_metadata
            )
            if not result.get('has_sufficient_information', True):
                note = result.get('instruction', '')
        except Exception as e:
            search_attempts.append(f"Document completeness check failed: {str(e)}")
            print(f"Document completeness check failed: {str(e)}")
            #time.sleep(2)

    # Step 8: Format links for documents if available
    # for result in combined_results:
    #     if 'link_to_document' in result and result['link_to_document']:
    #         link = result['link_to_document'].replace('\\', '/')
    #         if not link.startswith('/document/serve/'):
    #             link = f"/document/serve/{link}"
    #         result['document_url'] = link

    # Step 8.5: Get document counts for each document type
    distinct_document_ids = {result['document_id'] for result in combined_results if 'document_id' in result}
    count_distinct = len(distinct_document_ids)
    print(f"Total distinct document IDs found: {count_distinct}")

    special_instructions = ""
    if cfg.ENABLE_AGENT_KNOWLEDGE_MANAGEMENT:
        special_instructions = cfg.DOC_KNOWLEDGE_SPECIAL_INSTRUCTIONS
    
    # Step 9: Build enhanced final response
    response = {
        "results": combined_results[:max_results],
        "search_strategy": search_strategy,
        "query_analysis": {
            "original_question": user_question,
            "document_types": relevant_doc_types,
            "search_approach": search_strategy.get("search_approach"),
            "reasoning": search_strategy.get("reasoning"),
            "confidence": search_strategy.get("confidence", "unknown")
        },
        # "search_execution": {
        #     "search_attempts": search_attempts,
        #     "fallback_attempts": fallback_attempts,
        #     "total_page_results_found": len(combined_results),
        #     "total_document_results_found": count_distinct,
        #     "result_sources": list(set([r.get("search_method", "unknown") for r in combined_results]))
        # },
        #"available_fields": available_fields,
        #"document_types": document_types,
        #"document_counts": document_counts,
        "error": error_message,
        "special_instructions": special_instructions
    }
    
    if note:
        response["note"] = note

    print(86 * '-')
    print('Search Attempts:')
    print(search_attempts)
    print(86 * '-')
    print('Fallback Attempts:')
    print(fallback_attempts)
    print(86 * '-')

    # Return as JSON string
    return str(json.dumps(response, default=str)).replace('link_to_document', 'document_page_url')


def deduplicate_search_results(*result_lists, 
                               keep_best: bool = True,
                               max_results: int = None) -> List[Dict[str, Any]]:
    """
    Deduplicate results from multiple search calls.
    
    Args:
        *result_lists: Multiple lists of search results to deduplicate
        keep_best: If True, keep the result with highest relevance score for duplicates
        max_results: Maximum number of results to return (optional)
        
    Returns:
        Deduplicated list of search results
    """
    
    # Combine all results into one list
    all_results = []
    for result_list in result_lists:
        if result_list:  # Skip empty lists
            all_results.extend(result_list)
    
    if not all_results:
        return []
    
    # Track unique results by document_id
    unique_results = {}
    
    for result in all_results:
        doc_id = result.get('document_id', '')
        
        # Skip if no document_id
        if not doc_id:
            continue
            
        # If we haven't seen this document, add it
        if doc_id not in unique_results:
            unique_results[doc_id] = result
        else:
            # We have a duplicate - decide which to keep
            existing_result = unique_results[doc_id]
            
            if keep_best:
                # Keep the one with higher relevance score
                existing_score = existing_result.get('relevance_score', 0)
                new_score = result.get('relevance_score', 0)
                
                if new_score > existing_score:
                    print(f"Duplicate found for doc_id {doc_id}; updating to higher score {new_score} from {existing_score}")
                    unique_results[doc_id] = result
                else:  
                    print(f"Duplicate found for doc_id {doc_id}; keeping original score {existing_score} over {new_score}")
            else:  
                # If keep_best is False, keep the first one and log  
                print(f"Duplicate found for doc_id {doc_id}; keeping first result (keep_best=False)")  
    
    # Convert back to list and sort by relevance score
    deduplicated_results = list(unique_results.values())
    deduplicated_results.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)
    
    # Limit results if requested
    if max_results:
        deduplicated_results = deduplicated_results[:max_results]

    print(f"Deduplication complete: {len(deduplicated_results)} unique results after processing {len(all_results)} total results")  
    
    return deduplicated_results

import hashlib
def deduplicate_search_results_new(*result_lists, 
                               keep_best: bool = True,
                               max_results: int = None) -> List[Dict[str, Any]]:
    """
    Deduplicate results from multiple search calls.
    
    Args:
        *result_lists: Multiple lists of search results to deduplicate
        keep_best: If True, keep the result with highest relevance score for duplicates
        max_results: Maximum number of results to return (optional)
        
    Returns:
        Deduplicated list of search results
    """
    
    # Combine all results into one list
    all_results = []
    for result_list in result_lists:
        if result_list:  # Skip empty lists
            all_results.extend(result_list)
    
    if not all_results:
        return []
    
    # Track unique results by document_id
    unique_results = {}
    
    for result in all_results:
        doc_id = result.get('document_id', '')
        text = result.get('text', '')

        # Create a hash of the text for content-based deduplication  
        text_hash = hashlib.md5(text.encode('utf-8')).hexdigest() if text else '' 

        # Create a composite key for deduplication (document_id + text_hash)  
        composite_id = f"{doc_id}_{text_hash}" if text_hash else doc_id  
        
        # Skip if no document_id
        if not doc_id:
            print(f"Missing document_id for result: {result}")
            continue
            
        # If we haven't seen this composite_id, add it  
        if composite_id not in unique_results:  
            unique_results[composite_id] = result  
        else:  
            # We have a duplicate - decide which to keep and log the event  
            existing_result = unique_results[composite_id]  
            existing_score = existing_result.get('relevance_score', 0)  
            new_score = result.get('relevance_score', 0)  
              
            if keep_best:  
                # Keep the one with higher relevance score  
                if new_score > existing_score:  
                    print(f"Duplicate found for composite_id {composite_id}; updating to higher score {new_score} from {existing_score}")  
                    unique_results[composite_id] = result  
                else:  
                    print(f"Duplicate found for composite_id {composite_id}; keeping original score {existing_score} over {new_score}")  
            else:  
                # If keep_best is False, keep the first one and log  
                print(f"Duplicate found for composite_id {composite_id}; keeping first result (keep_best=False)")  
      
    # Convert back to list and sort by relevance score  
    deduplicated_results = list(unique_results.values())  
    deduplicated_results.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)  
      
    # Limit results if requested  
    if max_results:  
        deduplicated_results = deduplicated_results[:max_results]  
      
    print(f"Deduplication complete: {len(deduplicated_results)} unique results after processing {len(all_results)} total results")  
      
    return deduplicated_results


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

            document_id = metadata.get('document_id', '')
            link_to_document = ''
            if document_id:
                link_to_document = get_base_url() + f"/document/view/{document_id}?page={page_num or '1'}"
            
            # Clean filename 
            clean_filename = filename.split('/')[-1].split('\\')[-1]
            if '.' in clean_filename:
                clean_filename = '.'.join(clean_filename.split('.')[:-1])
            
            # Format this result
            result_text = f"[Source {i}: {clean_filename} - Page {page_num}] ({doc_type}) (Relevance: {relevance:.2f})\n{text.strip()}\n Document URL: {link_to_document}"
            
            # Check length constraint
            if current_length + len(result_text) > max_length:
                # Try to fit truncated version
                remaining_space = max_length - current_length - 50
                if remaining_space > 100:
                    truncated_text = text[:remaining_space] + "..."
                    result_text = f"[Source {i}: {clean_filename} - Page {page_num}] ({doc_type}) (Relevance: {relevance:.2f})\n{truncated_text}\n Document URL: {link_to_document}"
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


def get_document_id_by_filename(filename_search: str) -> str:
    """
    Search for document IDs by filename.
    
    Parameters:
    -----------
    filename_search : str
        The filename or partial filename to search for
        
    Returns:
    --------
    str
        JSON string containing matching documents with their IDs and filenames
    """
    try:
        # Establish connection to the SQL Server database
        conn = get_db_connection()
        
        # Create a cursor
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Execute the query with LIKE pattern search
        query = """
        SELECT document_id, filename, document_type, page_count, processed_at, archived_path
        FROM [dbo].[Documents]
        WHERE filename LIKE ?
        ORDER BY filename
        """
        
        # Add wildcards to the search term
        search_pattern = f"%{filename_search}%"
        
        print(f'Searching for documents with filename pattern: {search_pattern}')
        cursor.execute(query, search_pattern)
        
        # Process the results
        results = []
        for row in cursor.fetchall():
            document_id, filename, document_type, page_count, processed_at, archived_path = row
            
            result = {
                "document_id": document_id,
                "filename": filename,
                "document_type": document_type,
                "page_count": page_count,
                "processed_at": processed_at.isoformat() if processed_at else None,
                "archived_path": archived_path
            }
            results.append(result)
        
        # Close the connection
        conn.close()
        
        # Prepare response
        response = {
            "search_pattern": search_pattern,
            "total_results": len(results),
            "results": results
        }
        
        # Return as JSON string
        return json.dumps(response, indent=2, default=str)
        
    except Exception as e:
        print(f"Error searching for documents by filename: {str(e)}")
        return json.dumps({
            "error": str(e),
            "search_pattern": f"%{filename_search}%",
            "total_results": 0,
            "results": []
        })


def get_document_id_by_filename_simple(filename_search: str) -> str:
    """
    Simple version that returns just document IDs matching the filename pattern.
    
    Parameters:
    -----------
    filename_search : str
        The filename or partial filename to search for
        
    Returns:
    --------
    str
        JSON string containing array of matching document IDs
    """
    try:
        # Establish connection to the SQL Server database
        conn = get_db_connection()
        
        # Create a cursor
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Execute the query with LIKE pattern search
        query = """
        SELECT document_id
        FROM [dbo].[Documents]
        WHERE filename LIKE ?
        ORDER BY filename
        """
        
        # Add wildcards to the search term
        search_pattern = f"%{filename_search}%"
        
        print(f'Searching for document IDs with filename pattern: {search_pattern}')
        cursor.execute(query, search_pattern)
        
        # Process the results
        document_ids = [row[0] for row in cursor.fetchall()]
        
        # Close the connection
        conn.close()
        
        # Return as JSON string
        return json.dumps({
            "search_pattern": search_pattern,
            "document_ids": document_ids,
            "count": len(document_ids)
        }, indent=2)
        
    except Exception as e:
        print(f"Error searching for document IDs by filename: {str(e)}")
        return json.dumps({
            "error": str(e),
            "search_pattern": f"%{filename_search}%",
            "document_ids": [],
            "count": 0
        })


def get_document_ids_by_filenames(filename_searches: List[str]) -> str:
    """
    Search for document IDs by multiple filenames.
    
    Parameters:
    -----------
    filename_searches : List[str]
        List of filenames or partial filenames to search for
        
    Returns:
    --------
    str
        JSON string containing matching documents with their IDs, filenames, and which search pattern matched them
    """
    try:
        if not filename_searches or len(filename_searches) == 0:
            return json.dumps({
                "error": "No filename patterns provided",
                "total_results": 0,
                "results": [],
                "results_by_pattern": {}
            })
        
        # Establish connection to the SQL Server database
        conn = get_db_connection()
        
        # Create a cursor
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Build the query with multiple LIKE conditions
        like_conditions = []
        search_patterns = []
        params = []
        
        for filename_search in filename_searches:
            search_pattern = f"%{filename_search}%"
            search_patterns.append(search_pattern)
            like_conditions.append("filename LIKE ?")
            params.append(search_pattern)
        
        # Combine all LIKE conditions with OR
        where_clause = " OR ".join(like_conditions)
        
        query = f"""
        SELECT document_id, filename, document_type, page_count, processed_at, archived_path
        FROM [dbo].[Documents]
        WHERE {where_clause}
        ORDER BY filename
        """
        
        print(f'Searching for documents with {len(filename_searches)} filename patterns: {search_patterns}')
        cursor.execute(query, params)
        
        # Process the results
        results = []
        results_by_pattern = {}
        
        # Initialize results_by_pattern dict
        for i, pattern in enumerate(search_patterns):
            results_by_pattern[filename_searches[i]] = []
        
        for row in cursor.fetchall():
            document_id, filename, document_type, page_count, processed_at, archived_path = row
            
            result = {
                "document_id": document_id,
                "filename": filename,
                "document_type": document_type,
                "page_count": page_count,
                "processed_at": processed_at.isoformat() if processed_at else None,
                "archived_path": archived_path,
                "matched_patterns": []  # Will store which search patterns this result matched
            }
            
            # Check which search patterns this filename matches
            for i, search_pattern in enumerate(search_patterns):
                # Convert SQL LIKE pattern back to simple contains check
                simple_pattern = search_pattern.replace('%', '')
                if simple_pattern.lower() in filename.lower():
                    original_search = filename_searches[i]
                    result["matched_patterns"].append(original_search)
                    results_by_pattern[original_search].append(result.copy())
            
            results.append(result)
        
        # Close the connection
        conn.close()
        
        # Prepare response
        response = {
            "search_patterns": filename_searches,
            "total_results": len(results),
            "results": results,
            "results_by_pattern": results_by_pattern,
            "pattern_summary": {
                pattern: len(matches) for pattern, matches in results_by_pattern.items()
            }
        }
        
        # Return as JSON string
        return json.dumps(response, indent=2, default=str)
        
    except Exception as e:
        print(f"Error searching for documents by multiple filenames: {str(e)}")
        return json.dumps({
            "error": str(e),
            "search_patterns": filename_searches if 'filename_searches' in locals() else [],
            "total_results": 0,
            "results": [],
            "results_by_pattern": {},
            "pattern_summary": {}
        })


def get_document_ids_by_filenames_simple(filename_searches: List[str]) -> str:
    """
    Simple version that returns just document IDs matching multiple filename patterns.
    
    Parameters:
    -----------
    filename_searches : List[str]
        List of filenames or partial filenames to search for
        
    Returns:
    --------
    str
        JSON string containing arrays of matching document IDs organized by search pattern
    """
    try:
        if not filename_searches or len(filename_searches) == 0:
            return json.dumps({
                "error": "No filename patterns provided",
                "document_ids": [],
                "document_ids_by_pattern": {},
                "total_count": 0
            })
        
        # Establish connection to the SQL Server database
        conn = get_db_connection()
        
        # Create a cursor
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Build the query with multiple LIKE conditions
        like_conditions = []
        search_patterns = []
        params = []
        
        for filename_search in filename_searches:
            search_pattern = f"%{filename_search}%"
            search_patterns.append(search_pattern)
            like_conditions.append("filename LIKE ?")
            params.append(search_pattern)
        
        # Combine all LIKE conditions with OR
        where_clause = " OR ".join(like_conditions)
        
        query = f"""
        SELECT document_id, filename
        FROM [dbo].[Documents]
        WHERE {where_clause}
        ORDER BY filename
        """
        
        print(f'Searching for document IDs with {len(filename_searches)} filename patterns: {search_patterns}')
        cursor.execute(query, params)
        
        # Process the results
        all_document_ids = []
        document_ids_by_pattern = {}
        
        # Initialize document_ids_by_pattern dict
        for pattern in filename_searches:
            document_ids_by_pattern[pattern] = []
        
        for row in cursor.fetchall():
            document_id, filename = row
            all_document_ids.append(document_id)
            
            # Check which search patterns this filename matches
            for i, search_pattern in enumerate(search_patterns):
                # Convert SQL LIKE pattern back to simple contains check
                simple_pattern = search_pattern.replace('%', '')
                if simple_pattern.lower() in filename.lower():
                    original_search = filename_searches[i]
                    if document_id not in document_ids_by_pattern[original_search]:
                        document_ids_by_pattern[original_search].append(document_id)
        
        # Close the connection
        conn.close()
        
        # Remove duplicates from all_document_ids while preserving order
        seen = set()
        unique_document_ids = []
        for doc_id in all_document_ids:
            if doc_id not in seen:
                seen.add(doc_id)
                unique_document_ids.append(doc_id)
        
        # Return as JSON string
        return json.dumps({
            "search_patterns": filename_searches,
            "document_ids": unique_document_ids,
            "document_ids_by_pattern": document_ids_by_pattern,
            "total_count": len(unique_document_ids),
            "count_by_pattern": {
                pattern: len(ids) for pattern, ids in document_ids_by_pattern.items()
            }
        }, indent=2)
        
    except Exception as e:
        print(f"Error searching for document IDs by multiple filenames: {str(e)}")
        return json.dumps({
            "error": str(e),
            "search_patterns": filename_searches if 'filename_searches' in locals() else [],
            "document_ids": [],
            "document_ids_by_pattern": {},
            "total_count": 0,
            "count_by_pattern": {}
        })
    

###########################################
# Wide Net Search Strategy Functions
###########################################
def document_search_wide_net_strategy(
    user_question: str,
    document_type: str = None,
    max_results: int = 800
) -> str:
    """
    New search strategy: Cast wide net first, then AI filters down
    
    1. AI extracts search terms from user question
    2. Find ALL pages containing any of those terms
    3. AI reviews page content to filter relevant ones
    4. Handle token limits by batching AI requests
    """
    try:
        # Step 1: AI extracts search terms from user question
        search_terms = ai_extract_search_terms(user_question)
        print(f"AI extracted search terms: {search_terms}")
        
        # Step 2: Find ALL pages containing any search terms
        all_candidate_pages = find_all_candidate_pages(search_terms, document_type)
        print(f"Found {len(all_candidate_pages)} candidate pages")
        
        if not all_candidate_pages:
            return json.dumps({
                "results": [],
                "total_results": 0,
                "message": "No pages found containing the search terms",
                "search_terms_used": search_terms
            })
        
        # Step 3: AI reviews and filters pages in batches
        relevant_pages = ai_filter_pages_in_batches(
            user_question, 
            all_candidate_pages, 
            cfg.DOC_INTELLIGENT_MAX_CONTEXT_TOKENS
        )
        
        print(f"AI filtered down to {len(relevant_pages)} relevant pages")
        
        # Step 4: Format results
        final_results = format_wide_net_results(relevant_pages, max_results)

        print('Returning final results from wide net search...')
        # return json.dumps({
        #     "results": final_results,
        #     "total_results": len(final_results),
        #     "message": f"Found {len(final_results)} relevant pages after AI filtering",
        #     "search_terms_used": search_terms,
        #     "pages_reviewed": len(all_candidate_pages),
        #     "search_strategy": "wide_net_ai_filter"
        # }, default=str)
        return final_results
            
    except Exception as e:
        print(f"Wide net search error: {str(e)}")
        # return json.dumps({
        #     "results": [],
        #     "total_results": 0,
        #     "error": f"Search failed: {str(e)}",
        #     "search_strategy": "wide_net_ai_filter"
        # })
        return None


def ai_extract_search_terms(user_question: str) -> List[str]:
    """
    AI extracts the most important search terms from user question
    """
    system_prompt = """You are an expert at extracting search terms for document retrieval.
    
    Extract the most important terms from the user's question that would likely appear in relevant document pages.
    Focus on:
    - Key concepts and topics
    - Specific entities or subjects
    - Important keywords that would appear in relevant text
    - Technical terms or domain-specific language
    
    Return a JSON array of 3-8 search terms that would cast a wide net to find all potentially relevant pages."""
    
    user_prompt = f"""
    Extract search terms from this question: "{user_question}"
    
    Return ONLY a JSON array of search terms, like: ["term1", "term2", "term3"]
    
    Focus on terms that would appear in document text that could answer this question.
    """
    
    try:
        response = azureQuickPrompt(user_prompt, system_prompt)
        search_terms = json.loads(response)
        
        # Ensure it's a list
        if isinstance(search_terms, list):
            return [str(term).strip() for term in search_terms if str(term).strip()]
        else:
            return []
            
    except Exception as e:
        print(f"AI search term extraction failed: {e}")
        # Fallback: simple keyword extraction
        import re
        words = re.findall(r'\b[A-Za-z]{3,}\b', user_question)
        return [word for word in words if word.lower() not in ['the', 'and', 'for', 'with', 'what', 'how', 'when', 'where', 'that', 'this', 'are', 'was', 'were']][:6]


def find_all_candidate_pages(search_terms: List[str], document_type = None) -> List[Dict]:
    """
    Find ALL pages that contain any of the search terms
    
    Args:
        search_terms: List of search terms
        document_type: Can be:
            - None or "ALL": Search all document types
            - str: Single document type
            - List[str]: Multiple specific document types
    """
    conn = pyodbc.connect(get_db_connection_string())
    cursor = conn.cursor()
    cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
    
    if not search_terms:
        return []
    
    # Build OR conditions for all search terms
    search_conditions = []
    params = []
    
    for term in search_terms:
        search_conditions.append("dp.full_text LIKE ?")
        params.append(f'%{term}%')
    
    # Handle document type filtering
    doc_type_filter = ""
    
    if document_type and document_type != "ALL":
        if isinstance(document_type, str):
            # Single document type
            doc_type_filter = "AND d.document_type = ?"
            params.append(document_type)
        elif isinstance(document_type, list) and len(document_type) > 0:
            # Handle list of document types or document type objects
            doc_types = []
            for dt in document_type:
                if isinstance(dt, dict):
                    # Extract type from document type metadata object
                    if 'type' in dt:
                        doc_types.append(dt['type'])
                    elif 'document_type' in dt:
                        doc_types.append(dt['document_type'])
                elif isinstance(dt, str):
                    # Already a string
                    doc_types.append(dt)
            
            if doc_types:
                placeholders = ",".join("?" * len(doc_types))
                doc_type_filter = f"AND d.document_type IN ({placeholders})"
                params.extend(doc_types)
    # If document_type is None or "ALL", no filter is added (search all types)

    query = f"""
        SELECT DISTINCT 
            dp.page_id,
            dp.document_id,
            dp.page_number,
            dp.full_text,
            d.filename,
            d.document_type,
            isnull(d.archived_path, d.original_path) link_to_document
        FROM DocumentPages dp
        JOIN Documents d ON dp.document_id = d.document_id
        WHERE d.is_knowledge_document = 0
          AND ({' OR '.join(search_conditions)})
          {doc_type_filter}
        ORDER BY 1,2,3
    """

    print(query)
    print(params)
    
    cursor.execute(query, params)
    
    candidate_pages = []
    for row in cursor.fetchall():
        candidate_pages.append({
            'page_id': row[0],
            'document_id': row[1], 
            'page_number': row[2],
            'snippet': row[3],
            'filename': row[4],
            'document_type': row[5],
            'link_to_document': row[6]
        })

    cursor.close()
    
    return candidate_pages

def ai_filter_pages_in_batches(user_question: str, candidate_pages: List[Dict], max_context_tokens: int) -> List[Dict]:
    """
    AI reviews pages in batches to filter for relevance
    Automatically determines batch size based on token limits
    """
    if not candidate_pages:
        return []
    
    # AI determines optimal batch size based on content
    batch_size = ai_determine_batch_size(candidate_pages, max_context_tokens)
    print(f"AI determined batch size: {batch_size}")
    
    relevant_pages = []
    
    for i in range(0, len(candidate_pages), batch_size):
        batch = candidate_pages[i:i + batch_size]
        print(f"Processing batch {i//batch_size + 1} of {(len(candidate_pages) + batch_size - 1)//batch_size}")
        
        batch_relevant = ai_filter_single_batch(user_question, batch)
        relevant_pages.extend(batch_relevant)
    
    return relevant_pages


def ai_determine_batch_size(candidate_pages: List[Dict], max_context_tokens: int) -> int:
   """
   Determine optimal batch size based on token estimates and limits
   """
   if not candidate_pages:
       return 1
   
   # Get tokens per page estimate from config
   tokens_per_page = getattr(cfg, 'DOC_TOKENS_PER_PAGE_ESTIMATE', 1000)  # Default fallback
   
   # Calculate overhead tokens for the AI request
   # - User question and instructions: ~500 tokens
   # - JSON formatting and response: ~200 tokens  
   # - Safety margin: 20% of max tokens
   overhead_tokens = 500 + 200 + int(max_context_tokens * 0.2)
   
   # Available tokens for page content
   available_tokens = max_context_tokens - overhead_tokens
   
   # Calculate batch size
   batch_size = max(1, available_tokens // tokens_per_page)
   
   # Apply reasonable bounds
   #batch_size = min(batch_size, 100)  # Never more than 50 pages per batch
   #batch_size = max(batch_size, 1)   # Always at least 1 page
   
   return batch_size


def ai_filter_single_batch(user_question: str, batch_pages: List[Dict]) -> List[Dict]:
    """
    AI filters a single batch of pages for relevance
    """
    system_prompt = """You are an expert document analyst reviewing pages for relevance to a user's question.
    
    Your task:
    1. Read each page carefully
    2. Determine if the page contains information that could help answer the user's question
    3. Be inclusive - if there's any reasonable chance the page is relevant, include it
    4. Focus on content relevance, not just keyword matching
    
    Return a JSON array of page_ids for pages that are relevant to the question."""
    
    # Prepare page data for AI review
    pages_for_review = []
    for page in batch_pages:
        pages_for_review.append({
            'page_id': page['page_id'],
            'filename': page['filename'],
            'page_number': page['page_number'],
            'snippet': page['snippet'],
            'link_to_document': page['link_to_document']
        })
    
    user_prompt = f"""
    User's Question: "{user_question}"
    
    Review these pages and determine which ones contain information relevant to answering the question:
    
    {json.dumps(pages_for_review, indent=2)}
    
    Return a JSON array of page_ids for relevant pages:
    ["page_id_1", "page_id_2", "page_id_3"]
    
    If no pages are relevant, return an empty array: []
    
    Be inclusive - include pages that might contain useful context or partial answers.
    """
    try:
        response = azureQuickPrompt(user_prompt, system_prompt)
        relevant_page_ids = json.loads(response)
        
        # Filter original pages to return full page data
        relevant_pages = []
        for page in batch_pages:
            if page['page_id'] in relevant_page_ids:
                relevant_pages.append(page)
        
        return relevant_pages
        
    except Exception as e:
        print(f"AI batch filtering failed: {e}")
        # Fallback: return all pages in batch (conservative approach)
        return batch_pages


def format_wide_net_results(relevant_pages: List[Dict], max_results: int) -> List[Dict]:
    """
    Format the filtered results for return
    """
    # Sort by document name and page number for logical ordering
    sorted_pages = sorted(relevant_pages, key=lambda x: (x['document_id'], x['page_number']))
    
    # Limit results
    limited_pages = sorted_pages[:max_results]
    
    # Format for output
    formatted_results = []
    for page in limited_pages:
        document_text = get_document_search_content(
                    page_id=page['page_id'], 
                    document_type=page['document_type'],
                    full_text=page['snippet'][:int(cfg.DOC_PAGE_TEXT_LIMIT_IN_RESULTS)]
                )
                
        formatted_results.append({
            'page_id': page['page_id'],
            'document_id': page['document_id'],
            'filename': page['filename'],
            'document_type': page['document_type'],
            'page_number': page['page_number'],
            'snippet': document_text,
            #'link_to_document': page['link_to_document'],
            "clickable_link": format_document_link(page['link_to_document']),
            'search_method': 'wide_net_ai_filter'
        })
    
    return formatted_results






def estimate_token_count(text: str) -> int:
    """Simple token estimation"""
    return len(text) // cfg.DOC_CHARS_PER_TOKEN

def calculate_result_set_size(results: List[Dict]) -> Dict[str, int]:
    """
    Calculate the approximate size of the result set in tokens
    """
    total_text = ""
    for result in results:
        total_text += result.get("snippet", "")
        total_text += json.dumps(result.get("all_fields", {}))
    
    return {
        "estimated_tokens": estimate_token_count(total_text),
        "result_count": len(results),
        "avg_tokens_per_result": estimate_token_count(total_text) // len(results) if results else 0
    }

def should_summarize_results(results: List[Dict], user_question: str) -> Dict[str, Any]:
    """
    Decide if results should be summarized based on ACTUAL size, not arbitrary limits
    """
    result_count = len(results)
    
    # Calculate actual token usage
    total_text = ""
    for result in results:
        total_text += result.get("snippet", "")
        total_text += json.dumps(result.get("all_fields", {}))
    
    estimated_tokens = estimate_token_count(total_text)
    
    # Decision logic based on actual data
    if result_count <= cfg.DOC_SMALL_RESULT_THRESHOLD and estimated_tokens < cfg.DOC_INTELLIGENT_MAX_CONTEXT_TOKENS:
        strategy = "full_results"
        reasoning = f"Small result set ({result_count} docs, ~{estimated_tokens} tokens) - showing all results"
        
    elif estimated_tokens > cfg.DOC_INTELLIGENT_MAX_CONTEXT_TOKENS:
        # Too much content - need to summarize
        if "summary" in user_question.lower() or "overview" in user_question.lower():
            strategy = "smart_summary"
            reasoning = f"Large content (~{estimated_tokens} tokens) + user wants summary"
        elif result_count > cfg.DOC_LARGE_RESULT_THRESHOLD:
            strategy = "progressive_disclosure" 
            reasoning = f"Large result set ({result_count} docs, ~{estimated_tokens} tokens) - progressive disclosure"
        else:
            strategy = "clustered_summary"
            reasoning = f"Moderate results ({result_count} docs) but high token count (~{estimated_tokens}) - clustering"
            
    elif result_count > cfg.DOC_LARGE_RESULT_THRESHOLD:
        # Many results but not too much text
        strategy = "progressive_disclosure"
        reasoning = f"Many results ({result_count} docs) - progressive disclosure"
        
    else:
        # Medium size - let AI decide based on question type
        if any(word in user_question.lower() for word in ["what", "show me", "find", "get"]) and not any(word in user_question.lower() for word in ["summary", "overview", "analyze"]):
            strategy = "full_results"
            reasoning = f"Specific lookup query ({result_count} docs, ~{estimated_tokens} tokens)"
        else:
            strategy = "smart_summary"
            reasoning = f"Exploratory query ({result_count} docs, ~{estimated_tokens} tokens)"
    
    return {
        "strategy": strategy,
        "reasoning": reasoning,
        "result_count": result_count,
        "estimated_tokens": estimated_tokens,
        "token_limit": cfg.DOC_INTELLIGENT_MAX_CONTEXT_TOKENS
    }

def determine_response_strategy(
    user_question: str, 
    results: List[Dict], 
    max_context_tokens: int = 4000
) -> Dict[str, Any]:
    """
    Analyze the user question and result set to determine the best response strategy
    """
    size_info = calculate_result_set_size(results)
    
    # Analyze question type using AI
    analysis_prompt = f"""
    Analyze this user question and determine the best response strategy:
    
    Question: "{user_question}"
    Result count: {size_info['result_count']}
    Estimated tokens: {size_info['estimated_tokens']}
    
    Classify the question type and recommend a strategy:
    
    Question types:
    - "specific_lookup": User wants specific information from one document
    - "comparison": User wants to compare multiple documents  
    - "summary": User wants aggregated/summarized information
    - "exploration": User is exploring what documents are available
    
    Response strategies:
    - "full_results": Return all results (< 50 results, < 4000 tokens)
    - "smart_summary": Return executive summary + top results
    - "clustered_summary": Group similar documents and summarize each cluster
    - "progressive_disclosure": Return summary with drill-down capability
    
    Return JSON:
    {{
        "question_type": "...",
        "recommended_strategy": "...",
        "reasoning": "...",
        "confidence": "high|medium|low"
    }}
    """
    
    try:
        analysis_result = azureMiniQuickPrompt(
            system="You are an expert in document search UX. Respond only with valid JSON.",
            prompt=analysis_prompt
        )
        strategy = json.loads(analysis_result)
    except:
        # Fallback strategy based on simple heuristics
        if size_info["result_count"] <= 10 and size_info["estimated_tokens"] < max_context_tokens:
            strategy = {
                "question_type": "specific_lookup",
                "recommended_strategy": "full_results",
                "reasoning": "Small result set, can return everything",
                "confidence": "high"
            }
        elif "summary" in user_question.lower() or "overview" in user_question.lower():
            strategy = {
                "question_type": "summary", 
                "recommended_strategy": "smart_summary",
                "reasoning": "User explicitly asked for summary",
                "confidence": "high"
            }
        else:
            strategy = {
                "question_type": "exploration",
                "recommended_strategy": "progressive_disclosure", 
                "reasoning": "Large result set requires progressive disclosure",
                "confidence": "medium"
            }
    
    return strategy

def create_smart_summary(results: List[Dict], user_question: str, top_count: int = 99) -> Dict[str, Any]:
    """
    Create summary with flexible top_count (AI decides, not config)
    """
    if not results:
        return {"summary": "No documents found", "top_results": []}
    
    # Ensure all results have clickable links
    for result in results:
        if not result.get("clickable_link"):
            raw_path = (result.get("link_to_document") or 
                       result.get("archived_path") or 
                       result.get("path_to_document") or "")
            result["clickable_link"] = format_document_link(raw_path)
            if result["clickable_link"]:
                result["document_access"] = f"{result['clickable_link']}"
            else:
                result["document_access"] = "Document path not available"
    
    # Group by document type
    by_type = defaultdict(list)
    for result in results:
        doc_type = result.get("document_type", "Unknown")
        by_type[doc_type].append(result)
    
    # Create summary statistics
    summary_stats = {
        "total_documents": len(results),
        "document_types": {doc_type: len(docs) for doc_type, docs in by_type.items()},
        "date_range": None
    }
    
    # Extract date range
    dates = [r.get("document_date") for r in results if r.get("document_date")]
    if dates:
        dates.sort()
        summary_stats["date_range"] = {"earliest": dates[0], "latest": dates[-1]}
    
    # Generate AI summary
    summary_prompt = f"""
    Create a 2-3 sentence executive summary of these document search results:
    
    User Question: "{user_question}"
    Summary: {len(results)} documents found across {len(by_type)} document types
    Top Document Types: {dict(list(by_type.items())[:3])}
    
    Focus on directly answering the user's question with specific, actionable information.
    """
    
    try:
        ai_summary = azureQuickPrompt(
            prompt=summary_prompt,
            system="Create concise, actionable document summaries."
        )
    except:
        ai_summary = f"Found {len(results)} documents across {len(by_type)} document types."
    
    return {
        "summary": ai_summary,
        "statistics": summary_stats,
        "top_results": results[:top_count],  # Use the count the AI wants
        "document_type_breakdown": dict(by_type)
    }

def create_clustered_summary(results: List[Dict], user_question: str) -> Dict[str, Any]:
    """
    Group similar documents and provide summaries for each cluster
    """
    # Simple clustering by document type and key fields
    clusters = defaultdict(list)
    
    for result in results:
        # Create cluster key based on document type and key identifying fields
        doc_type = result.get("document_type", "Unknown")
        
        # Look for key identifier fields
        key_fields = {}
        for field, value in result.get("all_fields", {}).items():
            if any(identifier in field.lower() for identifier in ['customer', 'vendor', 'reference', 'order']):
                key_fields[field] = value
        
        cluster_key = (doc_type, tuple(sorted(key_fields.items())))
        clusters[cluster_key].append(result)
    
    # Summarize each cluster
    cluster_summaries = []
    for (doc_type, key_fields), cluster_results in clusters.items():
        cluster_summary = {
            "document_type": doc_type,
            "key_characteristics": dict(key_fields) if key_fields else {},
            "document_count": len(cluster_results),
            "sample_documents": cluster_results[:3],
            "date_range": None
        }
        
        # Get date range for this cluster
        dates = [r.get("document_date") for r in cluster_results if r.get("document_date")]
        if dates:
            dates.sort()
            cluster_summary["date_range"] = {"earliest": dates[0], "latest": dates[-1]}
        
        cluster_summaries.append(cluster_summary)
    
    # Sort clusters by size (largest first)
    cluster_summaries.sort(key=lambda x: x["document_count"], reverse=True)
    
    return {
        "cluster_count": len(cluster_summaries),
        "clusters": cluster_summaries,
        "total_documents": len(results)
    }

def create_progressive_disclosure_response(results: List[Dict], user_question: str) -> Dict[str, Any]:
    """
    Create a response that shows overview with ability to drill down
    """
    # Create overview
    overview = create_smart_summary(results, user_question)
    
    # Create drill-down categories
    drill_down_options = []
    
    # By document type
    by_type = defaultdict(list)
    for result in results:
        by_type[result.get("document_type", "Unknown")].append(result)
    
    for doc_type, docs in by_type.items():
        if len(docs) > 1:
            drill_down_options.append({
                "category": "document_type",
                "value": doc_type,
                "count": len(docs),
                "description": f"View all {len(docs)} {doc_type} documents"
            })
    
    # By time period (if dates available)
    dates_available = any(r.get("document_date") for r in results)
    if dates_available:
        drill_down_options.append({
            "category": "time_period",
            "value": "all",
            "count": len([r for r in results if r.get("document_date")]),
            "description": "View documents by time period"
        })
    
    # By key field values
    common_fields = defaultdict(lambda: defaultdict(list))
    for result in results:
        for field, value in result.get("all_fields", {}).items():
            if value and any(key in field.lower() for key in ['customer', 'vendor', 'reference']):
                common_fields[field][str(value)].append(result)
    
    for field, values in common_fields.items():
        if len(values) > 1 and len(values) <= 20:  # Reasonable number of categories
            drill_down_options.append({
                "category": "field_value",
                "field": field,
                "values": [{"value": v, "count": len(docs)} for v, docs in values.items()],
                "description": f"View documents by {field.replace('_', ' ').title()}"
            })
    
    return {
        "overview": overview,
        "drill_down_options": drill_down_options,
        "pagination_info": {
            "total_results": len(results),
            "showing": min(5, len(results)),
            "has_more": len(results) > 5
        }
    }

def document_search_super_enhanced_with_intelligent_sizing(
    conn_string: str,
    user_question: Optional[str] = None,
    max_results: int = 50,  # AI specifies this, we just use it
    check_completeness: bool = False,
    force_strategy: Optional[str] = None
) -> str:
    """
    Enhanced document search with intelligent result sizing.
    
    The AI agent specifies max_results based on what it wants.
    This function decides HOW to present those results based on their actual size/complexity.
    """
    
    # Safety check - prevent runaway queries
    if max_results > cfg.DOC_ABSOLUTE_MAX_RESULTS:
        max_results = cfg.DOC_ABSOLUTE_MAX_RESULTS
    
    # Get the raw results first
    original_response = document_search_super_enhanced(
        conn_string=conn_string,
        user_question=user_question,
        max_results=max_results,
        check_completeness=check_completeness
    )
    
    try:
        original_data = json.loads(original_response)
    except json.JSONDecodeError:
        return original_response
    
    results = original_data.get("results", [])
    
    if not results:
        return original_response
    
    # Decide strategy based on ACTUAL results, not arbitrary configs
    if force_strategy:
        strategy_info = {"strategy": force_strategy, "reasoning": "Forced by parameter"}
    else:
        strategy_info = should_summarize_results(results, user_question or "")
    
    # Apply the strategy
    if strategy_info["strategy"] == "full_results":
        # Return everything - the AI asked for this many results, give them all
        enhanced_response = original_data
        enhanced_response["response_strategy"] = strategy_info
        
    elif strategy_info["strategy"] == "smart_summary":
        # Create summary but let AI decide how many top results to show
        top_count = min(5, len(results))  # Show up to 5 top results
        summary_data = create_smart_summary(results, user_question, top_count)
        
        enhanced_response = {
            "response_type": "smart_summary",
            "summary": summary_data["summary"],
            "statistics": summary_data["statistics"],
            "top_results": summary_data["top_results"],
            "total_available_results": len(results),
            "response_strategy": strategy_info,
            "available_fields": original_data.get("available_fields", []),
            "document_types": original_data.get("document_types", []),
            "document_counts": original_data.get("document_counts", []),
            "note": f"Showing summary of {len(results)} results due to size. Use drill-down tools for specifics."
        }
        
    elif strategy_info["strategy"] == "clustered_summary":
        cluster_data = create_clustered_summary(results, user_question)
        enhanced_response = {
            "response_type": "clustered_summary", 
            "clusters": cluster_data["clusters"],
            "total_documents": len(results),
            "response_strategy": strategy_info,
            "available_fields": original_data.get("available_fields", []),
            "document_types": original_data.get("document_types", []),
            "document_counts": original_data.get("document_counts", []),
            "note": f"Grouped {len(results)} results into {len(cluster_data['clusters'])} clusters for easier navigation."
        }
        
    elif strategy_info["strategy"] == "progressive_disclosure":
        disclosure_data = create_progressive_disclosure_response(results, user_question)
        enhanced_response = {
            "response_type": "progressive_disclosure",
            "overview": disclosure_data["overview"], 
            "drill_down_options": disclosure_data["drill_down_options"],
            "response_strategy": strategy_info,
            "available_fields": original_data.get("available_fields", []),
            "document_types": original_data.get("document_types", []),
            "document_counts": original_data.get("document_counts", []),
            "note": f"Found {len(results)} results. Use drill-down options to explore specific areas."
        }
    
    else:
        # Fallback
        enhanced_response = original_data
        enhanced_response["response_strategy"] = strategy_info
    
    return json.dumps(enhanced_response, default=str)

# Additional helper tools for agents to drill down

def drill_down_by_document_type(
    conn_string: str,
    document_type: str,
    user_question: str,
    max_results: int = 20
) -> str:
    """
    Get detailed results for a specific document type from previous search
    """
    return document_search(
        conn_string=conn_string,
        document_type=document_type,
        search_query=user_question,
        field_filters=[],
        include_metadata=False,
        max_results=max_results,
        user_question=user_question,
        check_completeness=False
    )

def drill_down_by_field_value(
    conn_string: str,
    field_name: str,
    field_value: str,
    max_results: int = 20,
    document_type: Optional[str] = None
) -> str:
    """
    Get detailed results for documents with specific field values
    """
    field_filters = [{
        "field_name": field_name,
        "operator": "equals",
        "value": field_value
    }]
    
    return document_search(
        conn_string=conn_string,
        document_type=document_type,
        search_query="",
        field_filters=field_filters,
        include_metadata=False,
        max_results=max_results,
        user_question=None,
        check_completeness=False
    )

def get_paginated_results(
    conn_string: str,
    user_question: str,
    page: int = 1,
    page_size: int = 10,
    document_type: Optional[str] = None
) -> str:
    """
    Get paginated results for large result sets
    """
    # Calculate offset
    offset = (page - 1) * page_size
    
    # Get more results than needed to support pagination
    max_results = offset + (page_size * 3)  # Get a buffer
    
    # Run the search
    search_result = document_search_super_enhanced(
        conn_string=conn_string,
        user_question=user_question,
        max_results=max_results,
        check_completeness=False
    )
    
    try:
        data = json.loads(search_result)
        all_results = data.get("results", [])
        
        # Apply pagination
        start_idx = offset
        end_idx = offset + page_size
        page_results = all_results[start_idx:end_idx]
        
        # Calculate pagination info
        total_results = len(all_results)
        total_pages = ceil(total_results / page_size)
        
        paginated_response = {
            "results": page_results,
            "pagination": {
                "current_page": page,
                "page_size": page_size,
                "total_results": total_results,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_previous": page > 1
            },
            # Include original metadata
            "available_fields": data.get("available_fields", []),
            "document_types": data.get("document_types", []),
            "document_counts": data.get("document_counts", []),
        }
        
        return json.dumps(paginated_response, default=str)
        
    except json.JSONDecodeError:
        return search_result  # Return original if parsing fails
    

# Add these functions to DocUtilsEnhanced.py

def ai_post_process_intelligent_search_results(
    search_results: List[Dict[str, Any]], 
    user_question: str,
    max_results_to_analyze: int = None
) -> List[Dict[str, Any]]:
    """
    AI post-processing filter for document_intelligent_search results.
    Analyzes search results against user's question to ensure relevance.
    
    Parameters:
    -----------
    search_results : List[Dict[str, Any]]
        The search results from document_search_super_enhanced
    user_question : str
        The original user question
    max_results_to_analyze : int, optional
        Maximum number of results to analyze (uses config default if None)
        
    Returns:
    --------
    List[Dict[str, Any]]
        Filtered search results that better match user intent
    """
    
    if not search_results:
        return search_results
    
    # Use config default if not specified
    if max_results_to_analyze is None:
        max_results_to_analyze = cfg.AI_FILTER_MAX_RESULTS_TO_ANALYZE
    
    # Skip AI filtering if not needed
    if _should_skip_intelligent_search_filtering(user_question, search_results):
        return search_results
    
    # Limit results to analyze for token management
    results_to_analyze = search_results[:max_results_to_analyze]
    
    # Call AI for filtering
    try:
        filtered_indices = _call_ai_for_intelligent_search_filtering(
            user_question=user_question,
            analysis_data=results_to_analyze
        )
        
        # Apply filtering
        filtered_results = []
        for idx in filtered_indices:
            if 0 <= idx < len(search_results):
                result = search_results[idx].copy()
                result["ai_post_processing"] = {
                    "filtered_by_ai": True,
                    "relevance_confirmed": True,
                    "analysis_type": "simple" if len(results_to_analyze) <= cfg.AI_FILTER_SIMPLE_THRESHOLD else "full"
                }
                filtered_results.append(result)
        
        # Add any remaining results that weren't analyzed
        if len(search_results) > max_results_to_analyze:
            for result in search_results[max_results_to_analyze:]:
                result_copy = result.copy()
                result_copy["ai_post_processing"] = {
                    "filtered_by_ai": False,
                    "reason": "Not analyzed due to result limit"
                }
                filtered_results.append(result_copy)
        
        return filtered_results
        
    except Exception as e:
        print(f"AI post-processing failed: {str(e)}")
        # Return original results with error info
        for result in search_results:
            result["ai_post_processing"] = {
                "filtered_by_ai": False,
                "error": str(e)
            }
        return search_results


def _should_skip_intelligent_search_filtering(user_question: str, search_results: List[Dict]) -> bool:
    """
    Determines if AI filtering should be skipped for intelligent search results.
    """
    question_lower = user_question.lower()
    
    # Skip if user explicitly asks for "all"
    if any(term in question_lower for term in ["all documents", "every document", "complete list", "show all"]):
        return True
    
    # Skip if question is very generic with no specific filtering intent
    generic_terms = ["documents", "files", "show me", "find", "search"]
    if len(question_lower.split()) <= 3 and all(term in question_lower for term in generic_terms):
        return True
    
    return False


def _call_ai_for_intelligent_search_filtering(
    user_question: str,
    analysis_data: List[Dict[str, Any]]
) -> List[int]:
    """
    Calls AI to analyze intelligent search results and return indices of relevant documents.
    Uses simple filtering for small result sets, full analysis for larger ones.
    """
    
    # Determine if we should use simple or full analysis
    use_simple_filtering = len(analysis_data) <= cfg.AI_FILTER_SIMPLE_THRESHOLD
    
    try:
        if use_simple_filtering:
            # Use lightweight analysis for small result sets
            system_prompt = sysp.AI_INTELLIGENT_SEARCH_FILTER_SIMPLE_SYSTEM_PROMPT
            user_prompt = sysp.AI_INTELLIGENT_SEARCH_FILTER_SIMPLE_USER_PROMPT.format(
                user_question=user_question,
                current_date=date.today().isoformat(),
                analysis_data=json.dumps(analysis_data, indent=2, default=str)
            )
            
            ai_response = azureMiniQuickPrompt(system=system_prompt, prompt=user_prompt)
        else:
            # Use full analysis for larger result sets
            system_prompt = sysp.AI_INTELLIGENT_SEARCH_FILTER_SYSTEM_PROMPT
            user_prompt = sysp.AI_INTELLIGENT_SEARCH_FILTER_USER_PROMPT.format(
                user_question=user_question,
                current_date=date.today().isoformat(),
                analysis_data=json.dumps(analysis_data, indent=2, default=str)
            )
            
            ai_response = azureQuickPrompt(prompt=user_prompt, system=system_prompt)
        
        # Parse AI response
        try:
            filter_result = json.loads(ai_response)
            return filter_result.get("relevant_indices", list(range(len(analysis_data))))
            
        except json.JSONDecodeError:
            # Fallback: try to extract indices from text response
            import re
            indices_match = re.findall(r'\d+', ai_response)
            if indices_match:
                return [int(idx) for idx in indices_match if int(idx) < len(analysis_data)]
            else:
                # If parsing completely fails, return all indices
                return list(range(len(analysis_data)))
                
    except Exception as e:
        print(f"AI filtering API call failed: {str(e)}")
        # Return all indices if AI call fails
        return list(range(len(analysis_data)))

# Enhanced version of document_intelligent_search with AI post-processing
def document_intelligent_search_with_ai_filtering(
    user_question: str, 
    max_results: int = 50, 
    force_strategy: Optional[str] = None,
    enable_ai_post_processing: bool = None
) -> str:
    """
    Enhanced version of document_intelligent_search that includes AI post-processing
    to filter results for better relevance to user's question.
    
    Parameters:
    -----------
    user_question : str
        User's question for context
    max_results : int, default=50
        How many results to retrieve initially
    force_strategy : str, optional
        Force a specific presentation strategy
    enable_ai_post_processing : bool, optional
        Enable/disable AI post-processing (uses config default if None)
        
    Returns:
    --------
    str
        JSON string with intelligently sized and filtered results
    """
    
    # Use config default if not specified
    if enable_ai_post_processing is None:
        enable_ai_post_processing = cfg.AI_FILTER_ENABLE_BY_DEFAULT
    
    # Get the original intelligent search results
    conn_str = get_db_connection_string()
    original_response = document_search_super_enhanced_with_intelligent_sizing(
        conn_string=conn_str,
        user_question=user_question,
        max_results=max_results,
        check_completeness=cfg.DOC_CHECK_COMPLETENESS,
        force_strategy=force_strategy
    )
    
    if not enable_ai_post_processing:
        return original_response
    
    try:
        response_data = json.loads(original_response)
        
        # Apply AI post-processing to results if present
        if response_data.get("results"):
            original_count = len(response_data["results"])
            
            # Apply AI filtering to the results
            filtered_results = ai_post_process_intelligent_search_results(
                search_results=response_data["results"],
                user_question=user_question,
                max_results_to_analyze=cfg.AI_FILTER_MAX_RESULTS_TO_ANALYZE
            )
            
            # Update the response
            response_data["results"] = filtered_results
            
            # Add AI post-processing metadata
            response_data["ai_post_processing"] = {
                "applied": True,
                "original_count": original_count,
                "filtered_count": len(filtered_results),
                "filtering_reason": "AI relevance analysis for intelligent search"
            }
            
            # Update response strategy info if it was affected by filtering
            if "response_strategy" in response_data:
                response_data["response_strategy"]["post_ai_filtering"] = {
                    "original_count": original_count,
                    "filtered_count": len(filtered_results)
                }
        else:
            response_data["ai_post_processing"] = {
                "applied": False,
                "reason": "No results to filter"
            }
        
        return json.dumps(response_data, default=str)
        
    except Exception as e:
        print(f"Error in enhanced intelligent search: {str(e)}")
        return original_response  # Return original results if enhancement fails
    


