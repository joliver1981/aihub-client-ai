"""
Agent Environment Executor Module
Handles execution of agents within their assigned custom Python environments
"""

import os
import sys
import json
import logging
from logging.handlers import WatchedFileHandler
import subprocess
import tempfile
import pickle
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime
import pyodbc
from CommonUtils import rotate_logs_on_startup, get_log_path
from telemetry import capture_exception, track_agent_executed, add_breadcrumb


# Configure logging
def setup_logging():
    """Configure logging"""
    logger = logging.getLogger("EnvironmentExecutor")
    log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
    log_level = getattr(logging, log_level_name, logging.DEBUG)
    logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler = WatchedFileHandler(filename=os.getenv('ENVIRONMENT_EXECUTOR_LOG', get_log_path('environment_executor_log.txt')), encoding='utf-8')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger

rotate_logs_on_startup(os.getenv('AGENT_API_LOG', get_log_path('agent_api_log.txt')))

logger = setup_logging()


class AgentEnvironmentExecutor:
    """Manages execution of agents in custom Python environments"""
    
    def __init__(self, connection_string: str, tenant_id: str):
        self.connection_string = connection_string
        self.tenant_id = tenant_id
        self.logger = logger
        from CommonUtils import get_app_path
        self.base_path = get_app_path('agent_environments', f'tenant_{tenant_id}')

        # Create tenant-level logs folder
        logs_path = os.path.join(self.base_path, 'logs')
        os.makedirs(logs_path, exist_ok=True)
        
    def get_db_connection(self):
        """Get database connection"""
        return pyodbc.connect(self.connection_string)
    
    def get_agent_environment(self, agent_id: int) -> Optional[Dict]:
        """Get the environment assigned to an agent"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            # Set tenant context
            cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
            
            # Get agent's environment assignment
            cursor.execute("""
                SELECT 
                    aea.environment_id,
                    ae.name as environment_name,
                    ae.python_version,
                    ae.status
                FROM AgentEnvironmentAssignments aea
                JOIN AgentEnvironments ae ON aea.environment_id = ae.environment_id
                WHERE aea.agent_id = ? 
                    AND aea.is_active = 1
                    AND ae.is_deleted = 0
                    AND ae.status = 'active'
            """, agent_id)
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return {
                    'environment_id': row.environment_id,
                    'environment_name': row.environment_name,
                    'python_version': row.python_version,
                    'base_path': self.base_path,
                    'status': row.status
                }
            return None
            
        except Exception as e:
            self.logger.error(f"Error getting agent environment: {e}")
            return None
    
    def get_agent_details(self, agent_id: int) -> Optional[Dict]:
        """Get agent configuration from database"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            # Set tenant context
            cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
            
            # Get agent details
            cursor.execute("""
                SELECT 
                    id,
                    description,
                    objective
                FROM Agents
                WHERE id = ?
            """, agent_id)
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return {
                    'id': row.id,
                    'name': row.description,
                    'description': row.description,
                    'objective': row.objective,
                    'system_message': row.objective or "You are a helpful assistant."
                }
            return None
            
        except Exception as e:
            self.logger.error(f"Error getting agent details: {e}")
            return None
    
    def get_agent_tools(self, agent_id: int) -> List[str]:
        """Get list of tools assigned to the agent"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            # Set tenant context
            cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
            
            # Get agent tools
            cursor.execute("""
                SELECT at.tool_name AS name
                FROM AgentTools at
                WHERE at.agent_id = ?
                ORDER BY at.tool_name
            """, agent_id)
            
            tools = [row.name for row in cursor.fetchall()]
            conn.close()
            
            return tools
            
        except Exception as e:
            self.logger.error(f"Error getting agent tools: {e}")
            return []
    
    def get_python_executable(self, environment_id: str, base_path: str) -> str:
        """Get the Python executable path for an environment"""
        # Check for virtual environment in the standard location
        venv_path = os.path.join(base_path, environment_id)
        
        if os.path.exists(venv_path):
            # Windows
            python_exe = os.path.join(venv_path, 'Scripts', 'python.exe')
            if os.path.exists(python_exe):
                return python_exe
            
            # Linux/Mac
            python_exe = os.path.join(venv_path, 'bin', 'python')
            if os.path.exists(python_exe):
                return python_exe
        
        # Fallback to system Python
        return sys.executable
    
    def create_execution_script(self, 
                              agent_config: Dict, 
                              prompt: str, 
                              chat_history: List,
                              tools: List[str],
                              use_smart_render: bool = False,
                              user_id: int = None,
                              app_root: str = None) -> str:
        """Create a Python script to execute in the custom environment"""
        
        import json
        
        # Escape special characters in strings
        def escape_string(s):
            if s is None:
                return ""
            return s.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"').replace('\n', '\\n')
        
        # Pre-process chat_history
        if isinstance(chat_history, str):
            try:
                chat_history = json.loads(chat_history)
            except:
                chat_history = []
        
        if chat_history is None:
            chat_history = []
        elif not isinstance(chat_history, list):
            chat_history = []
        
        # Convert to JSON for embedding in script
        chat_history_json = json.dumps(chat_history)
        tools_json = json.dumps(tools)
        
        # Convert boolean to Python boolean string
        use_smart_render_str = 'True' if use_smart_render else 'False'
        
        script = f"""
import sys
import json
import logging
from logging.handlers import WatchedFileHandler
import os
from datetime import datetime
from CommonUtils import rotate_logs_on_startup, get_log_path


# Configure logging
def setup_logging():
    logger = logging.getLogger("AgentEnvironmentSubprocess")
    log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
    log_level = getattr(logging, log_level_name, logging.DEBUG)
    logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler = WatchedFileHandler(filename=os.getenv('AGENT_ENV_SUBPROCESS_LOG', get_log_path('agent_environment_subprocess_log.txt')), encoding='utf-8')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger

rotate_logs_on_startup(os.getenv('AGENT_ENV_SUBPROCESS_LOG', get_log_path('agent_environment_subprocess_log.txt')))

logger = setup_logging()

# ============================================================================
# CRITICAL: Add application path to Python path BEFORE imports
# ============================================================================
logger.info("Starting subprocess script execution...")

logger.info("Checking paths...")
app_root = r"{app_root}"
os.chdir(app_root)  # Uses main application directory for reference files like core_tools.yaml
if app_root not in sys.path:
    sys.path.insert(0, app_root)

# Also add tools directory if it exists
tools_path = os.path.join(app_root, 'tools')
if os.path.exists(tools_path) and tools_path not in sys.path:
    sys.path.insert(0, tools_path)

try:
    logger.info("Importing GeneralAgent...")
    # Import the GeneralAgent framework
    from GeneralAgent import GeneralAgent
    
    logger.info("Initializing GeneralAgent...")
    # Initialize the agent
    agent_id = {agent_config['id']}
    user_id = {user_id if user_id is not None else 'None'}
    
    # Create agent instance
    if user_id is not None:
        agent = GeneralAgent(agent_id, user_id)
    else:
        agent = GeneralAgent(agent_id)
    
    logger.info("Setting properties for GeneralAgent...")
    # Set agent properties if available
    if hasattr(agent, 'AGENT_NAME'):
        agent.AGENT_NAME = "{escape_string(agent_config.get('name', agent_config.get('description', '')))}"
    
    if hasattr(agent, 'AGENT_DESCRIPTION'):
        agent.AGENT_DESCRIPTION = "{escape_string(agent_config.get('description', ''))}"
    
    if hasattr(agent, 'AGENT_OBJECTIVE'):
        agent.AGENT_OBJECTIVE = "{escape_string(agent_config.get('objective', ''))}"
    
    # Initialize chat history - parse JSON string to Python list
    chat_history_str = '''{chat_history_json}'''
    chat_history = json.loads(chat_history_str)
    
    # Validate chat_history is a list
    if not isinstance(chat_history, list):
        print(f"WARNING: chat_history is not a list, it's a {{type(chat_history)}}")
        chat_history = []
    
    # Initialize with the list
    agent.initialize_chat_history(chat_history)
    
    # Execute the prompt
    user_prompt = '''{escape_string(prompt)}'''
    
    # Use Python boolean (not JSON boolean)
    use_smart_render = {use_smart_render_str}
    
    render_type = ''

    logger.info("Running GeneralAgent...")
    # Run the agent with appropriate method
    if hasattr(agent, 'run'):
        if use_smart_render:
            # Try to use smart render if available
            try:
                if user_id is not None:
                    response = agent.run(user_prompt, use_smart_render=True, user_id=user_id)
                else:
                    response = agent.run(user_prompt, use_smart_render=True)
                render_type = 'smart'
            except TypeError:
                # Fallback if use_smart_render parameter not supported
                response = agent.run(user_prompt)
                render_type = 'text'
        else:
            response = agent.run(user_prompt)
            render_type = 'text'
    else:
        render_type = 'text'
        # Fallback to direct executor invocation
        result = agent.agent_executor.invoke({{
            "input": user_prompt,
            "chat_history": agent.chat_history
        }})
        response = result.get("output", str(result))
    
    logger.info("Processing return values...")
    # Get updated chat history
    updated_chat_history = []
    if hasattr(agent, 'get_chat_history'):
        updated_chat_history = agent.get_chat_history()
    elif hasattr(agent, 'chat_history'):
        updated_chat_history = agent.chat_history
    
    # Prepare output - using dict() to avoid f-string brace issues
    output = dict()
    output['response'] = response
    output['chat_history'] = updated_chat_history
    output['render_type'] = render_type
    output['success'] = True
    
    # Build execution_info separately
    execution_info = dict()
    execution_info['agent_id'] = agent_id
    execution_info['environment'] = 'custom'
    execution_info['timestamp'] = datetime.now().isoformat()
    
    # Parse tools JSON string back to Python list
    tools_list = json.loads('''{tools_json}''')
    execution_info['tools_available'] = tools_list
    
    output['execution_info'] = execution_info
    
    # Output with clear JSON markers
    print("===JSON_RESULT_START===")
    print(json.dumps(output, ensure_ascii=False))
    print("===JSON_RESULT_END===")
    logger.info("Process finished.")
except ImportError as e:
    error_output = dict()
    error_output['error'] = f'Failed to import required modules: {{str(e)}}'
    error_output['type'] = 'import_error'
    error_output['success'] = False
    logger.error(error_output)
    print("===JSON_RESULT_START===")
    print(json.dumps(error_output))
    print("===JSON_RESULT_END===")
    sys.exit(1)
    
except Exception as e:
    import traceback
    error_output = dict()
    error_output['error'] = str(e)
    error_output['traceback'] = traceback.format_exc()
    error_output['type'] = 'execution_error'
    error_output['success'] = False
    logger.error(str(e))
    print("===JSON_RESULT_START===")
    print(json.dumps(error_output))
    print("===JSON_RESULT_END===")
    sys.exit(1)
"""
        return script
    
    def _find_app_root(self):
        """Dead simple - trust APP_ROOT"""
        import os
        
        app_root = os.getenv('APP_ROOT')
        if app_root:
            return app_root
        
        # Fallback for development
        return os.getcwd()
        
    def _find_app_root_extensive(self):
        """Find application root directory - works with PyInstaller frozen apps"""
        import os
        import sys
        
        # Check if we're running as a frozen PyInstaller application
        if getattr(sys, 'frozen', False):
            # We're running in a PyInstaller bundle
            # sys._MEIPASS is the temp folder where PyInstaller extracts files
            # sys.executable is the actual exe file location
            
            self.logger.info(f"Running as frozen app. Executable: {sys.executable}")
            self.logger.info(f"_MEIPASS: {getattr(sys, '_MEIPASS', 'Not found')}")
            
            # Option 1: Check if GeneralAgent is in the extracted temp directory
            if hasattr(sys, '_MEIPASS'):
                meipass = sys._MEIPASS
                
                # Check for GeneralAgent.py or GeneralAgent.pyc in MEIPASS
                for filename in ['GeneralAgent.py', 'GeneralAgent.pyd']:
                    ga_path = os.path.join(meipass, filename)
                    if os.path.exists(ga_path):
                        self.logger.info(f"Found {filename} in MEIPASS: {meipass}")
                        return meipass
                
                # Check if it's in a subdirectory
                for root, dirs, files in os.walk(meipass):
                    if 'GeneralAgent.py' in files or 'GeneralAgent.pyd' in files:
                        self.logger.info(f"Found GeneralAgent in: {root}")
                        return root
            
            # Option 2: Use the directory where the .exe is located
            # This assumes GeneralAgent files are deployed alongside the exe
            exe_dir = os.path.dirname(os.path.abspath(sys.executable))
            
            # Check if GeneralAgent files are in the exe directory
            for filename in ['GeneralAgent.py', 'GeneralAgent.pyc']:
                if os.path.exists(os.path.join(exe_dir, filename)):
                    self.logger.info(f"Found {filename} in exe dir: {exe_dir}")
                    return exe_dir
            
            # Option 3: Check for a 'app' or 'lib' subdirectory next to the exe
            for subdir in ['app', 'lib', 'python', 'src']:
                app_dir = os.path.join(exe_dir, subdir)
                if os.path.exists(app_dir):
                    for filename in ['GeneralAgent.py', 'GeneralAgent.pyc']:
                        if os.path.exists(os.path.join(app_dir, filename)):
                            self.logger.info(f"Found {filename} in {app_dir}")
                            return app_dir
            
            # Option 4: Use a configuration file or environment variable
            # You can set this during installation
            if os.getenv('APP_ROOT'):
                app_root = os.getenv('APP_ROOT')
                self.logger.info(f"Using APP_ROOT env var: {app_root}")
                return app_root
            
            # Option 5: Check if there's a config file with the path
            config_file = os.path.join(exe_dir, 'app_config.json')
            if os.path.exists(config_file):
                try:
                    import json
                    with open(config_file, 'r') as f:
                        config = json.load(f)
                        if 'app_root' in config:
                            self.logger.info(f"Using app_root from config: {config['app_root']}")
                            return config['app_root']
                except:
                    pass
            
            # Fallback: Use exe directory even if GeneralAgent not found
            # The script execution will fail later with a clearer error
            self.logger.warning(f"GeneralAgent not found, using exe directory: {exe_dir}")
            return exe_dir
            
        else:
            # Not frozen - development environment
            # Original logic for development
            
            # Start from THIS file's directory
            current_dir = os.path.dirname(os.path.abspath(__file__))
            self.logger.info(f"Development mode - searching from {current_dir}")
            
            # Search upward for GeneralAgent.py (max 5 levels)
            for _ in range(5):
                if os.path.exists(os.path.join(current_dir, 'GeneralAgent.py')):
                    self.logger.info(f"Found GeneralAgent.py at {current_dir}")
                    return current_dir
                current_dir = os.path.dirname(current_dir)
            
            # Try current working directory as fallback
            if os.path.exists(os.path.join(os.getcwd(), 'GeneralAgent.py')):
                self.logger.info(f"Found GeneralAgent.py at cwd: {os.getcwd()}")
                return os.getcwd()
            
            # Last resort - check common locations
            for path in ['./GeneralAgent.py', '../GeneralAgent.py', '../../GeneralAgent.py']:
                if os.path.exists(path):
                    app_root = os.path.dirname(os.path.abspath(path))
                    self.logger.info(f"Found GeneralAgent.py at {app_root}")
                    return app_root
            
            raise RuntimeError(f"Cannot find app root with GeneralAgent.py from {__file__}")

    def _find_app_root_old(self):
        """Find application root directory dynamically"""
        import os

        current_dir = os.path.dirname(os.getenv('APP_ROOT', './'))
        self.logger.info(f"Searching for app root from {current_dir}")

        # Search upward for GeneralAgent.py (max 5 levels)
        for _ in range(5):
            if os.path.exists(os.path.join(current_dir, 'GeneralAgent.py')):
                self.logger.info(f"GeneralAgent found! {current_dir}")
                return current_dir
            current_dir = os.path.dirname(current_dir)
        
        # Start from THIS file's directory
        current_dir = os.path.dirname(self.base_path)
        self.logger.info(f"Searching for app root from {current_dir}")
        
        # Search upward for GeneralAgent.py (max 5 levels)
        for _ in range(5):
            if os.path.exists(os.path.join(current_dir, 'GeneralAgent.py')):
                self.logger.info(f"GeneralAgent found! {current_dir}")
                return current_dir
            current_dir = os.path.dirname(current_dir)
        
        # Try current working directory as fallback
        if os.path.exists(os.path.join(os.getcwd(), 'GeneralAgent.py')):
            self.logger.info(f"GeneralAgent found at {os.getcwd()}")
            return os.getcwd()
        
        # Try current working directory as fallback
        if os.path.exists(os.path.join('./', 'GeneralAgent.py')):
            self.logger.info(f"GeneralAgent found at root")
            return './'
        
        raise RuntimeError(f"Cannot find app root with GeneralAgent.py from {__file__}")
    
    def execute_in_environment(self,
                               agent_id: int,
                               prompt: str,
                               chat_history: List = None,
                               use_smart_render: bool = False,
                               timeout: int = 300,
                               user_id: int = None) -> Dict:
        """Execute an agent in its assigned custom environment"""
        
        try:
            add_breadcrumb(
                message="Starting execute_in_environment",
                category="environment",
                level="info",
                data={"agent_id": agent_id, "prompt": prompt, "user_id": user_id}
            )
            # Get agent's environment
            env_info = self.get_agent_environment(agent_id)

            add_breadcrumb(
                message="Get environment information",
                category="environment",
                level="info",
                data={"env_info": env_info}
            )
            
            if not env_info:
                # No custom environment assigned
                return {
                    'status': 'no_environment',
                    'message': 'Agent has no custom environment assigned',
                    'fallback': True
                }
            
            # Get agent details
            agent_config = self.get_agent_details(agent_id)
            if not agent_config:
                raise Exception(f"Agent {agent_id} not found")
            
            # Get agent tools
            tools = self.get_agent_tools(agent_id)
            
            # Get Python executable
            python_path = self.get_python_executable(
                env_info['environment_id'],
                env_info['base_path']
            )

            # This should be the directory containing your Python application
            app_root = self._find_app_root()
            self.logger.info(f"Setting application root to {app_root}")
            
            # Create execution script
            script = self.create_execution_script(
                agent_config,
                prompt,
                chat_history or [],
                tools,
                use_smart_render,
                user_id,
                app_root
            )
            
            # Write script to temporary file
            with tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.py',
                delete=False,
                encoding='utf-8'
            ) as f:
                f.write(script)
                script_path = f.name
            
            try:
                # Set up environment variables
                env = os.environ.copy()
                env['PYTHONIOENCODING'] = 'utf-8'
                # ================================================================
                # KEY FIX: Set PYTHONPATH to include application directory
                # ================================================================
                pythonpath_entries = [
                    app_root,  # Main application directory
                    os.path.join(app_root, 'tools'),  # Tools directory
                    env_info['base_path'],  # The custom environment path
                ]

                # Add any existing PYTHONPATH
                if 'PYTHONPATH' in env:
                    pythonpath_entries.append(env['PYTHONPATH'])

                env['PYTHONPATH'] = os.pathsep.join(pythonpath_entries)

                # Log the paths for debugging
                self.logger.info(f"Executing with PYTHONPATH: {env['PYTHONPATH']}")
                self.logger.info(f"Using Python: {python_path}")
                # ================================================================

                #env['PYTHONPATH'] = env_info['base_path']
                env['AGENT_ENVIRONMENT'] = env_info['environment_id']
                env['AGENT_ID'] = str(agent_id)
                
                # Execute the script
                self.logger.info(f"Executing agent {agent_id} in environment {env_info['environment_name']}")

                add_breadcrumb(
                message="Executing agent in environment",
                category="environment",
                level="info",
                data={"environment_name": env_info['environment_name'], "agent_id": agent_id}
                )
                
                result = subprocess.run(
                    [python_path, script_path],
                    capture_output=True,
                    text=True,
                    encoding='utf-8',   
                    errors='replace',    
                    timeout=timeout,
                    env=env,
                    cwd=app_root  #env_info['base_path']
                )

                # ================================================================
                # PARSE OUTPUT: Look for JSON between markers
                # ================================================================
                output = result.stdout
                
                # Find JSON result between markers
                start_marker = "===JSON_RESULT_START==="
                end_marker = "===JSON_RESULT_END==="
                
                if start_marker in output and end_marker in output:
                    # Extract JSON between markers
                    start_idx = output.index(start_marker) + len(start_marker)
                    end_idx = output.index(end_marker)
                    json_str = output[start_idx:end_idx].strip()
                    
                    try:
                        response_data = json.loads(json_str)
                        
                        # Log the captured logs separately if needed
                        if response_data.get('logs'):
                            self.logger.debug(f"Execution logs: {response_data['logs'][:500]}...")
                        
                        if response_data.get('success'):
                            return {
                                'status': 'success',
                                'response': response_data.get('response'),
                                'chat_history': response_data.get('chat_history', []),
                                'used_custom_environment': True,
                                'environment': env_info['environment_name']
                            }
                        else:
                            return {
                                'status': 'error',
                                'error': response_data.get('error', 'Unknown error'),
                                'traceback': response_data.get('traceback', '')
                            }
                            
                    except json.JSONDecodeError as e:
                        self.logger.error(f"Failed to parse JSON result: {e}")
                        self.logger.error(f"JSON string was: {json_str[:500]}...")
                        return {
                            'status': 'error',
                            'error': f"Failed to parse execution result: {e}"
                        }
                else:
                    # No JSON markers found - this is an error
                    self.logger.error("No JSON result markers found in output")
                    self.logger.error(f"Stdout: {output[:1000]}...")
                    self.logger.error(f"Stderr: {result.stderr[:1000]}...")
                    
                    # Don't pass logging output to AI
                    return {
                        'status': 'error',
                        'error': 'Execution failed - no valid response received'
                    }
                
            finally:
                # Clean up temporary file
                try:
                    os.unlink(script_path)
                except:
                    pass
            
        except subprocess.TimeoutExpired:
            self.logger.error(f"Agent {agent_id} execution timed out")
            return {
                'status': 'error',
                'error': 'Agent execution timed out',
                'timeout': timeout,
                'used_custom_environment': True
            }
            
        except Exception as e:
            self.logger.error(f"Error executing agent {agent_id}: {e}")
            capture_exception(e)
            return {
                'status': 'error',
                'error': str(e),
                'used_custom_environment': False
            }
    
    def _update_usage_stats(self, environment_id: str):
        """Update usage statistics for an environment"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            # Set tenant context
            cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
            
            # Update usage count and last used date
            cursor.execute("""
                UPDATE AgentEnvironments
                SET usage_count = usage_count + 1,
                    last_used_date = getutcdate()
                WHERE environment_id = ?
            """, environment_id)
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            self.logger.error(f"Error updating usage stats: {e}")
    
    def test_environment(self, 
                        environment_id: str,
                        test_code: str = None) -> Dict:
        """Test if an environment is working properly"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            # Set tenant context
            cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
            
            # Get environment details
            cursor.execute("""
                SELECT python_version, status
                FROM AgentEnvironments
                WHERE environment_id = ? AND is_deleted = 0
            """, environment_id)
            
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                return {
                    'status': 'error',
                    'message': 'Environment not found'
                }
            
            base_path = self.base_path
            python_path = self.get_python_executable(environment_id, base_path)
            
            # Default test code if none provided
            if not test_code:
                test_code = """
import sys
import json
import platform

# Test basic imports
try:
    from GeneralAgent import GeneralAgent
    agent_import = True
except ImportError:
    agent_import = False

# Get environment info
info = {
    'python_version': platform.python_version(),
    'platform': platform.platform(),
    'executable': sys.executable,
    'path': sys.path[:5],  # First 5 paths
    'agent_framework_available': agent_import
}

print(json.dumps(info))
"""
            
            # Write test script
            with tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.py',
                delete=False,
                encoding='utf-8'
            ) as f:
                f.write(test_code)
                script_path = f.name
            
            try:
                # Execute test
                result = subprocess.run(
                    [python_path, script_path],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=base_path
                )
                
                if result.returncode == 0:
                    try:
                        output = json.loads(result.stdout)
                        return {
                            'status': 'success',
                            'environment_id': environment_id,
                            'test_output': output
                        }
                    except:
                        return {
                            'status': 'success',
                            'environment_id': environment_id,
                            'raw_output': result.stdout
                        }
                else:
                    return {
                        'status': 'error',
                        'environment_id': environment_id,
                        'error': result.stderr
                    }
                    
            finally:
                try:
                    os.unlink(script_path)
                except:
                    pass
                    
        except Exception as e:
            return {
                'status': 'error',
                'message': str(e)
            }
