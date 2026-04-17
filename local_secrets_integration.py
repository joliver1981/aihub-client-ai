"""
Local Secrets Integration for Tool Execution
=============================================

This module provides integration between the local secrets manager
and the tool execution system in GeneralAgent.py.

Usage:
    1. Import the integration functions
    2. Add secrets functions to tool globals
    3. Optionally inject secrets as environment variables

Integration Steps:
    - Add imports to GeneralAgent.py
    - Modify the tool execution to include secrets access
    - Update replace_code_placeholders if using placeholder syntax
"""

import os
import re
import logging
from typing import Dict, List, Optional, Any

# Import local secrets manager
from local_secrets import (
    get_local_secret,
    has_local_secret,
    get_secrets_manager,
    SecretsEnvironmentContext,
    inject_secrets_to_environment,
    restore_environment
)

logger = logging.getLogger(__name__)


# =============================================================================
# Functions to Add to Tool Globals
# =============================================================================

def get_tool_execution_globals() -> Dict[str, Any]:
    """
    Get dict of functions to add to tool execution globals.
    
    These functions become available to all custom tools.
    
    Usage in GeneralAgent.py:
        from local_secrets_integration import get_tool_execution_globals
        
        # When executing tools:
        tool_globals = globals().copy()
        tool_globals.update(get_tool_execution_globals())
        exec(function_str, tool_globals)
    """
    return {
        'get_local_secret': get_local_secret,
        'has_local_secret': has_local_secret,
        # Legacy alias for compatibility
        'get_secret': get_local_secret,
    }


# =============================================================================
# Placeholder Replacement
# =============================================================================

def replace_secret_placeholders(code_string: str) -> str:
    """
    Replace {{SECRET:name}} placeholders with actual secret values.
    
    This extends the existing placeholder system to support secrets.
    
    Placeholder formats supported:
        {{SECRET:OPENWEATHERMAP_API_KEY}}
        {{secret:OPENWEATHERMAP_API_KEY}}  (case-insensitive)
    
    Usage:
        Add to replace_code_placeholders() in AppUtils.py:
        
        def replace_code_placeholders(code_string):
            code_string = replace_connection_placeholders(code_string)
            code_string = replace_secret_placeholders(code_string)  # Add this
            return code_string
    """
    # Pattern matches {{SECRET:NAME}} or {{secret:NAME}}
    pattern = r'\{\{[Ss][Ee][Cc][Rr][Ee][Tt]:([A-Za-z_][A-Za-z0-9_]*)\}\}'
    
    matches = re.findall(pattern, code_string)
    
    if not matches:
        return code_string
    
    manager = get_secrets_manager()
    
    for secret_name in matches:
        placeholder_pattern = rf'\{{\{{[Ss][Ee][Cc][Rr][Ee][Tt]:{secret_name}\}}\}}'
        value = manager.get(secret_name.upper(), '')
        
        if value:
            # Escape the value for safe string insertion
            escaped_value = value.replace('\\', '\\\\').replace("'", "\\'").replace('"', '\\"')
            replacement = f"'{escaped_value}'"
        else:
            # Return empty string if secret not found
            replacement = "''"
            logger.warning(f"Secret '{secret_name}' not found in local secrets")
        
        code_string = re.sub(placeholder_pattern, replacement, code_string)
    
    return code_string


# =============================================================================
# Environment Variable Injection
# =============================================================================

class ToolExecutionContext:
    """
    Context manager for tool execution with secrets.
    
    Handles:
    - Injecting secrets as environment variables
    - Adding secrets functions to globals
    - Cleanup after execution
    
    Usage:
        with ToolExecutionContext(inject_env=True) as ctx:
            exec(tool_code, ctx.globals)
    """
    
    def __init__(self, 
                 base_globals: dict = None,
                 inject_env: bool = True,
                 secret_names: List[str] = None):
        """
        Args:
            base_globals: Base globals dict (typically globals())
            inject_env: Whether to inject secrets as environment variables
            secret_names: Specific secrets to inject (None = all)
        """
        self.base_globals = base_globals or {}
        self.inject_env = inject_env
        self.secret_names = secret_names
        self._original_env = {}
        self._tool_globals = None
    
    def __enter__(self):
        # Build tool globals
        self._tool_globals = self.base_globals.copy()
        self._tool_globals.update(get_tool_execution_globals())
        
        # Inject secrets as environment variables
        if self.inject_env:
            self._original_env = inject_secrets_to_environment(self.secret_names)
        
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        # Restore original environment
        if self.inject_env:
            restore_environment(self._original_env)
        return False
    
    @property
    def globals(self) -> dict:
        """Get the prepared globals dict for exec()."""
        return self._tool_globals


def execute_tool_with_secrets(code: str, 
                               tool_globals: dict = None,
                               inject_env: bool = True) -> Any:
    """
    Execute tool code with secrets support.
    
    Args:
        code: The tool code to execute
        tool_globals: Base globals dict
        inject_env: Whether to inject secrets as env vars
        
    Returns:
        Result of execution (if any)
        
    Example:
        result = execute_tool_with_secrets(
            "api_key = get_local_secret('OPENWEATHERMAP_API_KEY')",
            globals()
        )
    """
    with ToolExecutionContext(tool_globals, inject_env) as ctx:
        exec(code, ctx.globals)


# =============================================================================
# Patch Instructions for GeneralAgent.py
# =============================================================================

PATCH_INSTRUCTIONS = """
================================================================================
INTEGRATION INSTRUCTIONS FOR GeneralAgent.py
================================================================================

1. ADD IMPORTS (near top of file):
   
   from local_secrets_integration import (
       get_tool_execution_globals,
       replace_secret_placeholders,
       ToolExecutionContext
   )
   from local_secrets import get_local_secret, has_local_secret

2. UPDATE replace_code_placeholders() IN AppUtils.py:

   def replace_code_placeholders(code_string):
       # Existing connection placeholders
       code_string = replace_connection_placeholders(code_string)
       
       # NEW: Replace secret placeholders
       from local_secrets_integration import replace_secret_placeholders
       code_string = replace_secret_placeholders(code_string)
       
       return code_string

3. ADD SECRETS TO TOOL GLOBALS (in load_custom_tools or similar):

   Option A - Simple (add to globals):
   
       # After: exec(function_str, globals())
       # Before the exec, add:
       tool_globals = globals().copy()
       tool_globals.update(get_tool_execution_globals())
       exec(function_str, tool_globals)
   
   Option B - With environment injection:
   
       with ToolExecutionContext(globals(), inject_env=True) as ctx:
           exec(function_str, ctx.globals)

4. FOR AGENT EXECUTION (in GeneralAgent class __init__ or run method):

   # Add secrets functions to the tools available to agents
   # This can be done by adding to the globals before tool loading
   
   globals()['get_local_secret'] = get_local_secret
   globals()['has_local_secret'] = has_local_secret

================================================================================
EXAMPLE TOOL CODE (how users will write tools):
================================================================================

# Method 1: Using get_local_secret() function
def get_weather(location: str) -> dict:
    api_key = get_local_secret('OPENWEATHERMAP_API_KEY')
    if not api_key:
        return {"error": "API key not configured"}
    # ... use api_key

# Method 2: Using placeholder (replaced at load time)
def get_weather(location: str) -> dict:
    api_key = {{SECRET:OPENWEATHERMAP_API_KEY}}
    # ... use api_key

# Method 3: Using environment variable (if inject_env=True)
import os
def get_weather(location: str) -> dict:
    api_key = os.environ.get('OPENWEATHERMAP_API_KEY', '')
    # ... use api_key

================================================================================
"""

def print_patch_instructions():
    """Print integration instructions."""
    print(PATCH_INSTRUCTIONS)


# =============================================================================
# Verification
# =============================================================================

def verify_integration() -> Dict[str, bool]:
    """
    Verify that local secrets integration is working.
    
    Returns:
        Dict with verification results
    """
    results = {
        'secrets_manager_available': False,
        'get_local_secret_available': False,
        'placeholder_replacement_works': False,
        'env_injection_works': False
    }
    
    try:
        # Check secrets manager
        manager = get_secrets_manager()
        results['secrets_manager_available'] = manager is not None
        
        # Check function availability
        results['get_local_secret_available'] = callable(get_local_secret)
        
        # Test placeholder replacement
        test_code = "api_key = {{SECRET:TEST_KEY}}"
        replaced = replace_secret_placeholders(test_code)
        results['placeholder_replacement_works'] = replaced != test_code or "{{SECRET:" not in replaced
        
        # Test env injection
        with SecretsEnvironmentContext(['TEST_KEY']):
            results['env_injection_works'] = True
            
    except Exception as e:
        logger.error(f"Integration verification failed: {e}")
    
    return results


if __name__ == '__main__':
    print_patch_instructions()
    
    print("\n" + "=" * 60)
    print("VERIFICATION RESULTS")
    print("=" * 60)
    
    results = verify_integration()
    for check, passed in results.items():
        status = "✓" if passed else "✗"
        print(f"  {status} {check}")
