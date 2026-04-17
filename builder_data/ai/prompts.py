"""
Builder Data — AI Prompts
============================
Prompt templates used by the data agent graph nodes.
"""

QUALITY_ANALYSIS_PROMPT = """You are analyzing data quality based on the user's request.

Available connections:
{connections}

The user wants to: {user_request}

Determine what quality operation(s) to perform:
1. **profile** — Get column-level statistics (nulls, types, distributions)
2. **compare** — Compare two data sources to find discrepancies
3. **deduplicate** — Find and remove duplicate rows
4. **validate** — Check data against rules (types, ranges, patterns)
5. **cleanse** — Clean up data (trim whitespace, normalize case, fill nulls)

Return a JSON object describing the operation:
{{
    "operation": "profile|compare|deduplicate|validate|cleanse",
    "params": {{
        // For profile:
        "connection_id": <int>,
        "query": "<SQL query or table name>"

        // For compare:
        "source_a": {{"connection_id": <int>, "query": "..."}},
        "source_b": {{"connection_id": <int>, "query": "..."}},
        "key_columns": ["col1", "col2"],
        "compare_columns": ["col3", "col4"]  // optional

        // For deduplicate:
        "connection_id": <int>,
        "query": "...",
        "key_columns": ["col1"],
        "strategy": "exact|fuzzy",
        "fuzzy_threshold": 0.85

        // For validate:
        "connection_id": <int>,
        "query": "...",
        "rules": [
            {{"column": "email", "validation_type": "pattern", "params": {{"pattern": "^[^@]+@[^@]+$"}}}},
            {{"column": "age", "validation_type": "range", "params": {{"min": 0, "max": 150}}}}
        ]

        // For cleanse:
        "connection_id": <int>,
        "query": "...",
        "cleanse_rules": [
            {{"column": "email", "operation": "normalize_case", "params": {{"case": "lower"}}}},
            {{"column": "phone", "operation": "trim_whitespace"}}
        ]
    }}
}}

Return ONLY the JSON object."""


RESULTS_PRESENTATION_PROMPT = """You are presenting data pipeline or quality results to the user.

Summarize the results in a clear, concise way:
- Lead with the key finding (pass/fail, row counts, quality score)
- Highlight any issues or warnings
- If there are mismatches or duplicates, mention the counts
- Suggest next steps if appropriate

Keep it conversational and direct. Use markdown formatting for tables and lists.

Results data:
{results_json}

User's original request: {user_request}"""
