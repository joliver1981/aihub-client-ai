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
OPENAI_PRIMARY_MODELS = [
    'gpt-5.2',
    'gpt-5.4',
    'gpt-4o',
    'gpt-4.1',
    'o1',
    'o3',
]

# Mini/fast chat model.
OPENAI_MINI_MODELS = [
    'gpt-5.4-mini',
    'gpt-4o-mini',
    'gpt-4.1-mini',
    'o1-mini',
    'o3-mini',
]

# Vision-capable model (image analysis fallback in command_center_service).
OPENAI_VISION_MODELS = [
    'gpt-4o',
    'gpt-4.1',
    'gpt-5.2',
]

# Embedding model used for RAG / vector search.
OPENAI_EMBEDDING_MODELS = [
    'text-embedding-3-small',
    'text-embedding-3-large',
    'text-embedding-ada-002',
]

# Image-generation model used by the Command Center generate_image tool.
# Parameter handling per family lives in
# command_center_service/graph/image_params.py.
OPENAI_IMAGE_MODELS = [
    'dall-e-3',
    'dall-e-2',
    'gpt-image-1',
    'gpt-image-1.5',
]

# Anthropic primary/advanced Claude model.
ANTHROPIC_PRIMARY_MODELS = [
    'claude-opus-4-7',
    'claude-opus-4-6',
    'claude-sonnet-4-6',
    'claude-opus-4-1',
    'claude-3-7-sonnet-20250219',
    'claude-3-5-sonnet-20241022',
]

# Anthropic mini/fast Claude model.
ANTHROPIC_MINI_MODELS = [
    'claude-sonnet-4-6',
    'claude-haiku-4-5',
    'claude-3-5-haiku-20241022',
    'claude-3-haiku-20240307',
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
