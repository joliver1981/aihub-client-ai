import json
from typing import Dict, List, Optional, Any, Union, Tuple
import pyodbc
from math import ceil
import os
from collections import defaultdict
from AppUtils import get_db_connection_string, azureQuickPrompt, get_db_connection, azureMiniQuickPrompt
import config as cfg
import system_prompts as sysp
from DocUtils import document_search_super_enhanced, document_search, document_search_super, format_document_link
from datetime import datetime, date


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
    


