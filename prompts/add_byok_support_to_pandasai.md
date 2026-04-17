# Task: Add BYOK Support to PandasAI Integration

## Objective

Two files in the codebase create PandasAI LLM instances using hardcoded Azure config values, bypassing the centralized `get_openai_config()` function that handles BYOK (Bring Your Own Key) routing. Refactor both files to use `get_openai_config()` so that when a user has BYOK enabled with their own OpenAI API key, the PandasAI analytical engine and Excel analysis tool use their key via direct OpenAI instead of the system's Azure deployment.

## Background

The app has a centralized config function `api_keys_config.get_openai_config()` that returns the correct API configuration based on a priority system:

1. **BYOK enabled + user key configured** → Direct OpenAI (`api_type: 'open_ai'`) with user's key
2. **`cfg.USE_OPENAI_API=True`** → Direct OpenAI with system key
3. **Default** → Azure OpenAI (with alternate or primary deployment)

All major LLM consumers (GeneralAgent, WorkflowAgent, AppUtils quick prompts, TextChunker) already use this function. The two PandasAI files are the only ones that still hardcode `cfg.AZURE_OPENAI_*_ALTERNATE` values directly.

## Files to Modify

### 1. `LLMAnalyticalEngine.py` (line 34-41)

**Current code:**
```python
from pandasai_openai import AzureOpenAI

self.llm = AzureOpenAI(
    api_token=cfg.AZURE_OPENAI_API_KEY_ALTERNATE,
    azure_endpoint=cfg.AZURE_OPENAI_BASE_URL_ALTERNATE,
    api_version=cfg.AZURE_OPENAI_API_VERSION_ALTERNATE,
    deployment_name=cfg.AZURE_OPENAI_DEPLOYMENT_NAME_ALTERNATE,
    temperature=float(cfg.LLM_TEMPERATURE),
    seed=int(cfg.LLM_SEED),
)
```

### 2. `agent_excel_tools.py` (lines 1131-1142)

**Current code:**
```python
from pandasai import Agent as PandasAIAgent
from pandasai_openai import AzureOpenAI

llm = AzureOpenAI(
    api_token=cfg.AZURE_OPENAI_API_KEY_ALTERNATE,
    azure_endpoint=cfg.AZURE_OPENAI_BASE_URL_ALTERNATE,
    api_version=cfg.AZURE_OPENAI_API_VERSION_ALTERNATE,
    deployment_name=cfg.AZURE_OPENAI_DEPLOYMENT_NAME_ALTERNATE,
    temperature=float(cfg.LLM_TEMPERATURE),
    seed=int(cfg.LLM_SEED),
)
```

## Reference Implementation

`GeneralAgent.py` `_create_llm()` (line ~2303) is the canonical pattern:

```python
from api_keys_config import get_openai_config

config = get_openai_config(use_alternate_api=True)

if config['api_type'] == 'open_ai':
    # Direct OpenAI (BYOK or system key)
    llm = ChatOpenAI(
        model=config['model'],
        api_key=config['api_key'],
        temperature=temperature,
        model_kwargs=model_kwargs,
    )
else:
    # Azure OpenAI
    llm = AzureChatOpenAI(
        azure_deployment=config['deployment_id'],
        api_version=config['api_version'],
        azure_endpoint=config['api_base'],
        api_key=config['api_key'],
        temperature=temperature,
        model_kwargs=model_kwargs,
    )
```

## Config Dict Structure (returned by `get_openai_config()`)

```python
{
    'api_type': 'open_ai' or 'azure',
    'api_key': str,
    'api_base': str or None,        # OpenAI base URL or Azure endpoint
    'api_version': str or None,      # Azure only
    'deployment_id': str or None,    # Azure deployment name
    'model': str or None,            # Direct OpenAI model name (e.g. 'gpt-5.2')
    'source': 'byok' | 'system_openai' | 'azure' | 'azure_alternate',
    'reasoning_effort': str or None  # 'low', 'medium', 'high' for reasoning models
}
```

## PandasAI LLM Classes Available

The `pandasai_openai` package (v0.1.6) provides two LLM classes:

### `pandasai_openai.AzureOpenAI`
```python
AzureOpenAI(
    api_token=str,           # Azure API key
    azure_endpoint=str,      # e.g. "https://YOUR_RESOURCE.openai.azure.com/"
    api_version=str,         # e.g. "2024-12-01-preview"
    deployment_name=str,     # Azure deployment name
    temperature=float,       # Default 0
    seed=int,                # Optional
    # ...other BaseOpenAI kwargs: max_tokens, top_p, etc.
)
```
- Does NOT validate model name against a supported list (uses `deployment_name` directly)
- Works with any Azure deployment including gpt-5.2

### `pandasai_openai.OpenAI`
```python
OpenAI(
    api_token=str,           # OpenAI API key
    model=str,               # Model name, default "gpt-4.1-mini"
    temperature=float,       # Default 0
    seed=int,                # Optional
    # ...other BaseOpenAI kwargs
)
```

**CRITICAL GOTCHA**: This class has a hardcoded `_supported_chat_models` list that does NOT include `gpt-5.2` or any `gpt-5` variant. The constructor validates the model against this list and raises `UnsupportedModelError` if the model isn't found. You will need to handle this — options include:

1. **Monkey-patch the supported list** before instantiation:
   ```python
   from pandasai_openai import OpenAI as PandasAIOpenAI
   if model_name not in PandasAIOpenAI._supported_chat_models:
       PandasAIOpenAI._supported_chat_models.append(model_name)
   ```
2. **Subclass** `OpenAI` and extend `_supported_chat_models`
3. **Check if `pandasai_openai` has been updated** since v0.1.6 to include gpt-5 models (run `pip show pandasai-openai` to check version)

The currently supported models in v0.1.6 are: `gpt-3.5-turbo*`, `gpt-4*`, `gpt-4o*`, `gpt-4.1`, `gpt-4.1-mini`, `gpt-4.1-nano`.

## Reasoning Model Considerations

When `config['reasoning_effort']` is not None (i.e., the model is gpt-5.2, o1, o3, o4):

1. **Temperature must be 1.0** — reasoning models reject other values
2. **The `reasoning` parameter** (`{"effort": "low"}`) should be passed to the API

However, `pandasai_openai`'s `BaseOpenAI` class does NOT support a `reasoning` parameter in its `_default_params` or `_invocation_params`. The `chat_completion()` method calls `self.client.create(**params)` where params only include standard fields (temperature, top_p, model, messages, etc.).

Options to handle this:
1. **Skip reasoning for pandasai calls** — simplest approach, pandasai generates Python code (not complex reasoning), so reasoning effort may not help much. Just set `temperature=1.0` for reasoning models.
2. **Monkey-patch `_default_params`** to inject reasoning
3. **Subclass and override `chat_completion()`** to add reasoning to params
4. **Check if a newer version of pandasai-openai supports it**

## Implementation Pattern

Create a helper function (either shared in a utility module or duplicated in both files):

```python
def _create_pandasai_llm(use_alternate_api=True):
    """Create a PandasAI LLM using centralized config with BYOK support."""
    from api_keys_config import get_openai_config
    from pandasai_openai import AzureOpenAI as PandasAIAzureOpenAI
    from pandasai_openai import OpenAI as PandasAIOpenAI
    import config as cfg

    config = get_openai_config(use_alternate_api=use_alternate_api)

    temperature = float(cfg.LLM_TEMPERATURE)
    # Reasoning models require temperature=1.0
    if config.get('reasoning_effort'):
        temperature = 1.0

    if config['api_type'] == 'open_ai':
        model = config['model']
        # Handle unsupported model list in pandasai_openai
        if model not in PandasAIOpenAI._supported_chat_models:
            PandasAIOpenAI._supported_chat_models.append(model)

        return PandasAIOpenAI(
            api_token=config['api_key'],
            model=model,
            temperature=temperature,
            seed=int(cfg.LLM_SEED),
        )
    else:
        return PandasAIAzureOpenAI(
            api_token=config['api_key'],
            azure_endpoint=config['api_base'],
            api_version=config['api_version'],
            deployment_name=config['deployment_id'],
            temperature=temperature,
            seed=int(cfg.LLM_SEED),
        )
```

Then replace the hardcoded constructors:
```python
# LLMAnalyticalEngine.py __init__:
self.llm = _create_pandasai_llm(use_alternate_api=True)

# agent_excel_tools.py analyze_excel_data:
llm = _create_pandasai_llm(use_alternate_api=True)
```

## Where to Put the Helper

Options:
- **In `api_keys_config.py`** alongside `get_openai_config()` — keeps all LLM config centralized, but adds a pandasai dependency to that module
- **In a new `pandasai_utils.py`** — clean separation
- **Inline in both files** — simple but duplicated

Recommended: Put it in `api_keys_config.py` or a shared utility, since both files need it.

## Testing

### Existing test file
Check `tests/TEST_MAP.md` for corresponding test files. As of the last check, neither `LLMAnalyticalEngine.py` nor `agent_excel_tools.py` had dedicated unit tests covering LLM instantiation.

### Manual verification
1. **Default (Azure)**: BYOK disabled → verify PandasAI uses Azure alternate deployment
2. **BYOK enabled**: Set BYOK enabled + configure a user OpenAI key → verify PandasAI uses direct OpenAI with user's key
3. **System OpenAI**: Set `USE_OPENAI_API=True` → verify PandasAI uses direct OpenAI with system key

### Run existing tests after changes
```bash
python -m pytest tests/unit/ -m unit -v --tb=short -x
```

## Known Deferred Issues (Out of Scope for This Task)

PandasAI 3.0 also introduced breaking API changes that affect `LLMAnalyticalEngine.py` at runtime:

1. **`Agent.explain()`** removed — used at lines 71-91
2. **`Agent.get_conversation()`** removed — used at lines 149, 156
3. **`dfs_desc` constructor param** removed from `Agent` — used at line 142/154

These are separate runtime errors that occur only when those specific code paths execute. They should be addressed in a separate task (likely requires rewriting the NLQ analytical engine to use pandasai 3.0's new API).

## Files Reference

| File | Purpose |
|------|---------|
| `api_keys_config.py` | Centralized config — `get_openai_config()`, `is_byok_enabled()`, `_is_reasoning_model()` |
| `config.py` | Static config values — `AZURE_OPENAI_*_ALTERNATE`, `LLM_TEMPERATURE`, `LLM_SEED` |
| `LLMAnalyticalEngine.py` | NLQ analytical engine — data analysis chat feature |
| `agent_excel_tools.py` | Excel analysis tool (line ~1131) — agent tool for spreadsheet queries |
| `GeneralAgent.py` `_create_llm()` (line ~2303) | Reference implementation of BYOK-aware LLM creation |
