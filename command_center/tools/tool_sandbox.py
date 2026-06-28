"""
Command Center — Tool Sandbox
================================
Runs generated tool code in a restricted subprocess for testing.
"""
import asyncio
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

SANDBOX_TIMEOUT = 10


async def test_tool_in_sandbox(tool_spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Test a generated tool in a restricted subprocess.

    Returns:
        {"success": bool, "output": str, "error": str}
    """
    tool_name = tool_spec.get("tool_name", "test_tool")
    code = tool_spec.get("code", "")
    parameters = tool_spec.get("parameters", {})

    # Build test parameters based on parameter types
    test_params = {}
    for param_name, param_desc in parameters.items():
        param_type = tool_spec.get("parameter_types", {}).get(param_name, "str")
        if param_type == "int":
            test_params[param_name] = 1
        elif param_type == "float":
            test_params[param_name] = 1.0
        elif param_type == "bool":
            test_params[param_name] = True
        elif param_type == "List":
            test_params[param_name] = []
        else:
            test_params[param_name] = "test"

    param_args = ", ".join(f"{k}={repr(v)}" for k, v in test_params.items())

    test_script = f"""
import sys
import json

# Tool code
def {tool_name}({", ".join(parameters.keys())}):
{_indent_code(code)}

# Test execution
try:
    result = {tool_name}({param_args})
    print(json.dumps({{"success": True, "output": str(result)}}))
except Exception as e:
    print(json.dumps({{"success": False, "error": str(e)}}))
"""

    try:
        # Write to a temp file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(test_script)
            temp_path = f.name

        # Run in subprocess with timeout
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                [sys.executable, temp_path],
                capture_output=True,
                text=True,
                timeout=SANDBOX_TIMEOUT,
            ),
        )

        # Clean up
        Path(temp_path).unlink(missing_ok=True)

        if result.returncode == 0 and result.stdout.strip():
            try:
                output = __import__("json").loads(result.stdout.strip())
                return output
            except Exception:
                return {"success": True, "output": result.stdout.strip()}
        else:
            return {
                "success": False,
                "error": result.stderr or f"Exit code {result.returncode}",
                "output": result.stdout,
            }

    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Tool timed out after {SANDBOX_TIMEOUT}s"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _indent_code(code: str, indent: str = "    ") -> str:
    """Indent code block for insertion into a function body."""
    lines = code.split("\n")
    return "\n".join(indent + line for line in lines)
