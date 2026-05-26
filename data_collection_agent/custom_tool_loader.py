"""
Per-schema custom tool loader.

Schema authors can opt their data-collection agent into platform custom
tools by listing them in the schema:

    {
      "id": "...",
      "custom_tools": ["google_places_venue_lookup", "yelp_venue_reviews"]
    }

Tools live in the platform's standard `tools/<tool_name>/` folder
(cfg.CUSTOM_TOOLS_FOLDER), exactly as authored for any other agent —
same `config.json` + `code.py` shape, same secret resolution via
`local_secrets.get_local_secret(...)`. We don't fork the format. The
DCA agent simply loads the named tools and appends them to its own
`self.tools` list at agent-init time, so the LangChain executor binds
them like any other tool.

Errors are isolated: if a named tool is missing or its code fails to
compile, we log a warning, skip that tool, and proceed without it.
The agent runs without the missing tool rather than failing the whole
session.
"""

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _import_platform_helpers() -> Tuple[Optional[Any], Optional[Any], Optional[str]]:
    """Lazy import of the platform's tool-building helper + the tools
    folder path. We DON'T use `AppUtils.load_custom_tool_by_name`
    because it pre-indents the code — combined with
    `build_custom_tool_function`'s wrapping it produces double-indented
    output that won't compile. We read the file ourselves at column 0
    and let the build helper handle indentation.
    """
    try:
        from AppUtils import build_custom_tool_function
        import config as cfg
        return _read_tool_files, build_custom_tool_function, cfg.CUSTOM_TOOLS_FOLDER
    except Exception as e:
        logger.debug("custom_tool_loader: platform helpers unavailable: %s", e)
        return None, None, None


def _read_tool_files(name: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """Read a tool's config.json + code.py without altering indentation.
    Mirrors GeneralAgent.load_custom_tool(folder, indent_code=False) but
    locally so we don't pull GeneralAgent into a DCA import path."""
    try:
        from AppUtils import load_custom_tool_by_name as _legacy  # type checker shim
    except Exception:
        pass
    import json as _json
    try:
        import config as cfg  # for CUSTOM_TOOLS_FOLDER
    except Exception:
        return None, ''
    folder = os.path.join(cfg.CUSTOM_TOOLS_FOLDER, name)
    if not os.path.isdir(folder):
        return None, ''
    config: Optional[Dict[str, Any]] = None
    cfg_path = os.path.join(folder, 'config.json')
    code_path = os.path.join(folder, 'code.py')
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                config = _json.load(f)
        except Exception as e:
            logger.warning("Could not read %s: %s", cfg_path, e)
    code = ''
    if os.path.isfile(code_path):
        try:
            with open(code_path, 'r', encoding='utf-8') as f:
                code = f.read()
        except Exception as e:
            logger.warning("Could not read %s: %s", code_path, e)
    return config, code


def load_schema_custom_tools(tool_names: List[str]) -> List[Any]:
    """
    Load each named platform custom tool and return a list of
    LangChain Tool objects ready to be appended to a DataCollectionAgent's
    `self.tools` list.

    Each tool name corresponds to a folder under cfg.CUSTOM_TOOLS_FOLDER
    containing a `config.json` and `code.py` file. Tools that fail to
    load are skipped with a warning rather than raising.

    Returns an empty list if nothing loadable was found.
    """
    if not tool_names:
        return []

    load_one, build_fn, tools_folder = _import_platform_helpers()
    if not (load_one and build_fn and tools_folder):
        logger.warning(
            "Custom tools requested by schema (%s) but the platform's "
            "tool-loading helpers are not importable. Skipping all.",
            tool_names,
        )
        return []

    out: List[Any] = []
    for name in tool_names:
        try:
            tool_obj = _load_one_tool(name, load_one, build_fn, tools_folder)
            if tool_obj is not None:
                out.append(tool_obj)
                logger.info("Loaded custom tool: %s", name)
        except Exception as e:
            # Per-tool isolation: don't let one bad tool break the agent.
            logger.warning(
                "Skipping custom tool %r — failed to load: %s",
                name, e, exc_info=True,
            )
    return out


def _load_one_tool(name: str, load_one, build_fn, tools_folder: str) -> Optional[Any]:
    folder = os.path.join(tools_folder, name)
    if not os.path.isdir(folder):
        logger.warning("Custom tool folder not found: %s", folder)
        return None

    config, code = load_one(name)
    if not config:
        logger.warning("Custom tool %r has no usable config.json", name)
        return None

    function_name = config.get('function_name')
    if not function_name:
        logger.warning("Custom tool %r config missing 'function_name'", name)
        return None

    function_source = build_fn(config, code)

    # Compile + exec into a fresh namespace. The compiled function is
    # decorated with @tool (per the platform format) which produces a
    # LangChain StructuredTool instance bound to the function name.
    # We need `tool` (langchain_core.tools.tool) in scope before exec.
    namespace: Dict[str, Any] = {}
    try:
        from langchain_core.tools import tool  # the @tool decorator the source string references
        namespace['tool'] = tool
    except Exception as e:
        logger.warning("langchain_core.tools.tool unavailable; cannot load custom tool %r: %s", name, e)
        return None

    # Helpful builtins the tool body might use (mirroring the legacy
    # GeneralAgent.load_custom_tools globals environment).
    try:
        from local_secrets import get_local_secret  # secrets store
        namespace['get_local_secret'] = get_local_secret
    except Exception:
        pass

    try:
        exec(function_source, namespace)  # pylint: disable=exec-used
    except SyntaxError as e:
        logger.warning("Custom tool %r failed to compile: %s", name, e)
        return None
    except Exception as e:
        logger.warning("Custom tool %r raised on import: %s", name, e)
        return None

    obj = namespace.get(function_name)
    if obj is None:
        logger.warning(
            "Custom tool %r compiled but exposed no symbol named %r",
            name, function_name,
        )
        return None
    return obj
