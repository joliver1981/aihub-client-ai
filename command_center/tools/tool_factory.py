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
    """List all generated tools."""
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
            })
        except Exception as e:
            logger.warning(f"Error reading tool config at {config_path}: {e}")

    return tools


def get_generated_tool(tool_name: str) -> Optional[Dict[str, Any]]:
    """Get a specific generated tool's config and code."""
    tools_dir = _get_tools_dir()
    tool_dir = tools_dir / tool_name

    config_path = tool_dir / "config.json"
    code_path = tool_dir / "code.py"

    if not config_path.exists():
        return None

    result = {}
    with open(config_path, "r", encoding="utf-8") as f:
        result["config"] = json.load(f)

    if code_path.exists():
        with open(code_path, "r", encoding="utf-8") as f:
            result["code"] = f.read()

    return result
