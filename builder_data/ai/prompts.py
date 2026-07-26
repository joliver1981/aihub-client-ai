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


# ============================================================================
# AIHUB_PROMPT_OVERRIDE_HOOK - admin system-prompt overrides (additive)
# ----------------------------------------------------------------------------
# Overlays admin-set prompts from data/prompt_overrides.json on top of the
# defaults defined above, so they can be edited from the System Prompts admin
# screen (/settings/system-prompts) without changing code.
#
#   * No override file  -> this is a no-op and behaviour is unchanged.
#   * Fails open        -> any problem leaves the code defaults untouched.
#   * Nothing above this line is modified, and reverting an override in the UI
#     restores the shipped default exactly.
#
# See prompt_overrides.py for the validation rules (a value must be a string
# and must keep every {placeholder} the default relies on).
# ============================================================================
try:
    try:
        from prompt_overrides import apply_prompt_overrides as _po_apply
    except ImportError:
        # The repo root is not on sys.path in this service's process. Load the
        # module straight off disk rather than mutating sys.path, so import
        # resolution for this process is left exactly as it was.
        import os as _po_os
        import importlib.util as _po_ilu
        _po_apply = None
        _po_dir = _po_os.path.dirname(_po_os.path.abspath(__file__))
        for _po_i in range(6):
            _po_file = _po_os.path.join(_po_dir, 'prompt_overrides.py')
            if _po_os.path.isfile(_po_file):
                _po_spec = _po_ilu.spec_from_file_location(
                    '_aihub_prompt_overrides', _po_file)
                _po_mod = _po_ilu.module_from_spec(_po_spec)
                _po_spec.loader.exec_module(_po_mod)
                _po_apply = _po_mod.apply_prompt_overrides
                break
            _po_parent = _po_os.path.dirname(_po_dir)
            if _po_parent == _po_dir:
                break
            _po_dir = _po_parent
    if _po_apply:
        _po_apply(globals(), 'builder_data/ai/prompts.py')
except Exception:
    pass
