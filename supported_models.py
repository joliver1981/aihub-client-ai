"""
supported_models.py
-------------------
Curated dropdown lists for the admin "Model Overrides" UI at /admin/api-keys.

These lists are the "known supported" choices shown to admins. The UI also
allows manual entry with a warning. To add/remove choices, edit this file and
restart the services. Everything else (storage, env var mapping, warnings)
lives in model_overrides.py.

Convention: this project configures Azure deployment names to match OpenAI
model names (e.g. an Azure deployment called 'gpt-5.2' fronts the gpt-5.2
model). So one list serves both direct-OpenAI and Azure-OpenAI for each role.
"""

# Primary chat/completion model (both OpenAI-direct and Azure deployment name).
# (o1 was removed from the OpenAI API in 2025; gpt-5.5 is the current frontier
# model, gpt-5.4 the recommended default.)
OPENAI_PRIMARY_MODELS = [
    'gpt-5.4',
    'gpt-5.5',
    'gpt-5.2',
    'gpt-4.1',
    'gpt-4o',
    'o3',
]

# Mini/fast chat model.
OPENAI_MINI_MODELS = [
    'gpt-5.4-mini',
    'gpt-5.4-nano',
    'gpt-4o-mini',
    'gpt-4.1-mini',
    'o3-mini',
]

# Vision-capable model (image analysis fallback in command_center_service).
OPENAI_VISION_MODELS = [
    'gpt-4o',
    'gpt-5.4',
    'gpt-5.2',
    'gpt-4.1',
]

# Embedding model used for RAG / vector search.
# (text-embedding-ada-002 was shut down in Jan 2024.)
OPENAI_EMBEDDING_MODELS = [
    'text-embedding-3-small',
    'text-embedding-3-large',
]

# Image-generation model used by the Command Center generate_image tool.
# Parameter handling per family lives in
# command_center_service/graph/image_params.py.
# (dall-e-2/3 were shut down 2026-05-12; gpt-image-1 retires 2026-12-01.)
OPENAI_IMAGE_MODELS = [
    'gpt-image-2',
    'gpt-image-1.5',
]

# Anthropic primary/advanced Claude model.
# (claude-3-x models are retired; claude-opus-4-1 retires 2026-08-05.)
ANTHROPIC_PRIMARY_MODELS = [
    'claude-opus-4-8',
    'claude-opus-4-7',
    'claude-opus-4-6',
    'claude-sonnet-5',
    'claude-sonnet-4-6',
]

# Anthropic mini/fast Claude model.
ANTHROPIC_MINI_MODELS = [
    'claude-sonnet-5',
    'claude-sonnet-4-6',
    'claude-haiku-4-5',
]


# Map each override key (used by model_overrides.py) to its dropdown list.
# model_overrides.get_override_status() serves this to the UI.
DROPDOWNS = {
    'openai_primary':   OPENAI_PRIMARY_MODELS,
    'openai_mini':      OPENAI_MINI_MODELS,
    'openai_vision':    OPENAI_VISION_MODELS,
    'openai_embedding': OPENAI_EMBEDDING_MODELS,
    'openai_image':     OPENAI_IMAGE_MODELS,
    'anthropic_primary': ANTHROPIC_PRIMARY_MODELS,
    'anthropic_mini':    ANTHROPIC_MINI_MODELS,
}
