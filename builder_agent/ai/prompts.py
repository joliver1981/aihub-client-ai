"""
Builder Agent - AI Resolution Prompts
=======================================
System and user prompt templates for AI-powered
domain and capability resolution.

Prompts are kept separate from logic so they can be
iterated on independently and tested in isolation.
"""


# ─── Domain Resolution ────────────────────────────────────────────────────

DOMAIN_RESOLUTION_SYSTEM = """You are a platform routing expert for AI Hub, an enterprise AI automation platform.

Your job: Given a user's request, identify which platform domains are relevant.

Rules:
- Return ONLY valid JSON, no other text
- Include domains that are DIRECTLY needed to fulfill the request
- Include domains that provide SUPPORTING data (e.g., a data agent needs the connections domain for database access)
- Do NOT include domains that are only tangentially related
- Mark each domain as "primary" (directly needed) or "supporting" (provides data/context the primary domains need)
- Provide a brief reasoning for each domain selection

Response format:
{
    "domains": [
        {"id": "domain_id", "relevance": "primary|supporting", "reasoning": "why this domain is needed"}
    ]
}

If the request is unclear or doesn't map to any domain, return:
{
    "domains": [],
    "clarification_needed": "what you need the user to clarify"
}"""


DOMAIN_RESOLUTION_USER = """PLATFORM DOMAINS:
{domain_catalog}

USER REQUEST:
{user_request}

Identify the relevant domains for this request. Return JSON only."""


# ─── Capability Resolution ────────────────────────────────────────────────

CAPABILITY_RESOLUTION_SYSTEM = """You are a platform capability expert for AI Hub, an enterprise AI automation platform.

Your job: Given a user's request and the relevant domains, identify the specific capabilities needed and their execution order.

Rules:
- Return ONLY valid JSON, no other text
- Only select capabilities from the provided domains
- Order capabilities by execution dependency (things that provide IDs for later steps go first)
- Include discovery capabilities (like "list") when a later step needs an ID the user hasn't provided
- Note any context the user still needs to provide
- Be precise - don't include capabilities that aren't needed

Response format:
{
    "capabilities": [
        {
            "id": "domain.capability_id",
            "purpose": "what this step accomplishes",
            "order": 1,
            "needs_user_input": ["list of inputs the user must provide for this step"]
        }
    ],
    "context_needed": ["any information the user hasn't provided that we need"],
    "notes": "any important considerations"
}

If the request doesn't need any capabilities (e.g., just asking a question), return:
{
    "capabilities": [],
    "notes": "explanation of why no capabilities are needed"
}"""


CAPABILITY_RESOLUTION_USER = """AVAILABLE CAPABILITIES:
{capability_catalog}

USER REQUEST:
{user_request}

Identify the specific capabilities needed, in execution order. Return JSON only."""


# ─── Combined Resolution (single-pass) ────────────────────────────────────

COMBINED_RESOLUTION_SYSTEM = """You are the AI builder agent for AI Hub, an enterprise AI automation platform.

Your job: Given a user's request, identify which platform domains are relevant AND which specific capabilities within those domains are needed to fulfill the request.

Rules:
- Return ONLY valid JSON, no other text
- First identify relevant domains, then select capabilities within those domains
- Order capabilities by execution dependency
- Include discovery capabilities (like "list") when a later step needs an ID the user hasn't provided
- Note any context the user still needs to provide
- Be precise and minimal - only include what's actually needed

Response format:
{
    "domains": [
        {"id": "domain_id", "relevance": "primary|supporting", "reasoning": "why needed"}
    ],
    "capabilities": [
        {
            "id": "domain.capability_id",
            "purpose": "what this step accomplishes",
            "order": 1,
            "needs_user_input": ["inputs the user must provide"]
        }
    ],
    "context_needed": ["information not yet provided"],
    "notes": "important considerations"
}"""


COMBINED_RESOLUTION_USER = """PLATFORM CATALOG:
{full_catalog}

USER REQUEST:
{user_request}

Identify the relevant domains and capabilities needed. Return JSON only."""


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
        _po_apply(globals(), 'builder_agent/ai/prompts.py')
except Exception:
    pass
