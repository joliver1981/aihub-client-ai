"""
Command Center — Tool Factory
================================
LLM-driven tool generation. Creates config.json + code.py
in the standard AI Hub custom tool format.
"""
import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Directory where generated tools are stored (resolved lazily).
_GENERATED_TOOLS_DIR = None


def _get_tools_dir() -> Path:
    global _GENERATED_TOOLS_DIR
    if _GENERATED_TOOLS_DIR is None:
        _GENERATED_TOOLS_DIR = Path(__file__).parent.parent.parent / "command_center_service" / "data" / "generated_tools"
        _GENERATED_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    return _GENERATED_TOOLS_DIR


def _get_platform_tools_dir() -> Optional[Path]:
    """The PLATFORM custom-tools store — the one /save (builder tools.create)
    writes and /api/tools/packages lists (AIHUB-0020 F1: tools created there
    were invisible to run_generated_tool, so a 'created and verified' tool was
    not runnable). Env-first resolution mirroring config.py CUSTOM_TOOLS_FOLDER;
    a bare Path(__file__) fallback would break under PyInstaller onedir."""
    import os
    folder = os.getenv("CUSTOM_TOOLS_FOLDER", "tools")
    root = os.getenv("APP_ROOT")
    if root:
        p = Path(os.path.join(root, folder))
    else:
        p = Path(__file__).parent.parent.parent / folder
    return p if p.is_dir() else None


def _adapt_platform_config(config: Dict[str, Any], code: str) -> Dict[str, Any]:
    """Convert the platform tool format to the CC/sandbox format: the platform
    stores parameters/parameter_types as parallel LISTS and imports in
    'modules'; the sandbox wants dicts and self-contained code (imports are
    legal inside a function body)."""
    params = config.get("parameters")
    ptypes = config.get("parameter_types")
    defaults = config.get("parameter_defaults")
    if isinstance(params, list):
        type_list = ptypes if isinstance(ptypes, list) else []
        default_list = defaults if isinstance(defaults, list) else []
        param_dict = {str(p): str(p) for p in params}
        type_dict = {
            str(p): (type_list[i] if i < len(type_list) else "str")
            for i, p in enumerate(params)
        }
        default_dict = {
            str(p): default_list[i]
            for i, p in enumerate(params)
            if i < len(default_list) and default_list[i] not in (None, "")
        }
    else:
        param_dict = params or {}
        type_dict = ptypes if isinstance(ptypes, dict) else {}
        default_dict = defaults if isinstance(defaults, dict) else {}
    # The platform 'modules' field holds bare module names ("pyodbc"), not
    # import statements — joining them raw made every imported-module tool
    # die with NameError. Normalize to real imports.
    modules = [
        m if str(m).strip().startswith(("import ", "from ")) else f"import {m}"
        for m in (config.get("modules") or [])
        if str(m).strip()
    ]
    full_code = ("\n".join(modules) + "\n" + code) if modules else code
    return {
        "config": {
            "function_name": config.get("function_name"),
            "description": config.get("description", ""),
            "parameters": param_dict,
            "parameter_types": type_dict,
            "parameter_defaults": default_dict,
            "output_type": config.get("output_type", "str"),
            "generated": False,
            "source": "platform",
            # Platform DB tools embed {CONN:name} placeholders the platform
            # runtime substitutes with live connection strings; the CC sandbox
            # cannot resolve them.
            "requires_platform_runtime": "{CONN:" in (code or ""),
        },
        "code": full_code,
    }


def _get_platform_tool(tool_name: str) -> Optional[Dict[str, Any]]:
    pdir = _get_platform_tools_dir()
    if pdir is None:
        return None
    tool_dir = pdir / tool_name
    config_path = tool_dir / "config.json"
    code_path = tool_dir / "code.py"
    if not config_path.exists():
        return None
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        code = ""
        if code_path.exists():
            with open(code_path, "r", encoding="utf-8") as f:
                code = f.read()
        return _adapt_platform_config(config, code)
    except Exception as e:
        logger.warning(f"Error reading platform tool '{tool_name}': {e}")
        return None


def save_generated_tool(tool_spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Save a generated tool to disk in the standard custom tool format.

    Args:
        tool_spec: Dict with keys: tool_name, description, parameters,
                   parameter_types, code, output_type

    Returns:
        Dict with save status and path.
    """
    tool_name = tool_spec.get("tool_name", "unnamed_tool")
    description = tool_spec.get("description", "")
    parameters = tool_spec.get("parameters", {})
    parameter_types = tool_spec.get("parameter_types", {})
    code = tool_spec.get("code", "return 'Not implemented'")
    output_type = tool_spec.get("output_type", "str")

    tools_dir = _get_tools_dir()
    tool_dir = tools_dir / tool_name
    tool_dir.mkdir(parents=True, exist_ok=True)

    # Build config.json
    config = {
        "function_name": tool_name,
        "description": description,
        "parameters": parameters,
        "parameter_types": parameter_types,
        "output_type": output_type,
        "generated": True,
        "generated_at": datetime.utcnow().isoformat(),
    }

    config_path = tool_dir / "config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    # Write code.py
    code_path = tool_dir / "code.py"
    with open(code_path, "w", encoding="utf-8") as f:
        f.write(code)

    # Compute a code hash for audit
    code_hash = hashlib.sha256(code.encode()).hexdigest()

    logger.info(f"Saved generated tool: {tool_name} at {tool_dir}")

    return {
        "tool_name": tool_name,
        "path": str(tool_dir),
        "code_hash": code_hash,
        "status": "saved",
    }


def list_generated_tools() -> list:
    """List all runnable tools: the CC-generated store merged with the
    platform custom-tools store (AIHUB-0020 F1 — builder-created tools were
    invisible here, so run_generated_tool reported 'Tool not found' for tools
    the platform verified as present). CC-store entries win name collisions."""
    tools_dir = _get_tools_dir()
    tools = []

    for item in tools_dir.iterdir():
        if not item.is_dir():
            continue
        config_path = item / "config.json"
        if not config_path.exists():
            continue
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            tools.append({
                "name": config.get("function_name", item.name),
                "description": config.get("description", ""),
                "generated": config.get("generated", False),
                "generated_at": config.get("generated_at"),
                "source": "cc",
            })
        except Exception as e:
            logger.warning(f"Error reading tool config at {config_path}: {e}")

    seen = {t["name"] for t in tools}
    pdir = _get_platform_tools_dir()
    if pdir is not None:
        try:
            for item in sorted(pdir.iterdir()):
                if not item.is_dir():
                    continue
                config_path = item / "config.json"
                if not config_path.exists():
                    continue
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        config = json.load(f)
                    name = config.get("function_name", item.name)
                    if name in seen:
                        continue
                    tools.append({
                        "name": name,
                        "description": config.get("description", ""),
                        "generated": False,
                        "generated_at": None,
                        "source": "platform",
                    })
                except Exception as e:
                    logger.warning(f"Error reading platform tool config at {config_path}: {e}")
        except OSError as e:
            logger.warning(f"Could not list platform tools dir {pdir}: {e}")

    return tools


def get_generated_tool(tool_name: str) -> Optional[Dict[str, Any]]:
    """Get a specific tool's config and code — CC store first, then the
    platform store (adapted to the CC/sandbox format)."""
    tools_dir = _get_tools_dir()
    tool_dir = tools_dir / tool_name

    config_path = tool_dir / "config.json"
    code_path = tool_dir / "code.py"

    if not config_path.exists():
        # AIHUB-0020 F1: fall through to the platform custom-tools store so
        # builder-created tools are runnable, not just listed as present.
        return _get_platform_tool(tool_name)

    result = {}
    with open(config_path, "r", encoding="utf-8") as f:
        result["config"] = json.load(f)

    if code_path.exists():
        with open(code_path, "r", encoding="utf-8") as f:
            result["code"] = f.read()

    return result
