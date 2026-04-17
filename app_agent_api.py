"""
Agent API Service
Separate Flask application for handling agent chat and execution
Designed to run in its own Python environment with isolated dependencies
"""

import os
import sys
import logging
from logging.handlers import WatchedFileHandler
import json
import threading
import uuid
from datetime import datetime
import pickle
import traceback

from flask import Flask, request, jsonify, session
from flask_cors import CORS, cross_origin
from flask_session import Session
import pyodbc

# Import agent-specific modules
from GeneralAgent import GeneralAgent
from agent_communication_tool import (
    register_agent,
    unregister_agent,
    get_active_agents
)
from tool_dependency_manager import load_tool_dependencies
import config as cfg
from CommonUtils import get_db_connection, rotate_logs_on_startup, get_db_connection_string, get_log_path
from AppUtils import azureMiniQuickPrompt
from DataUtils import get_agent_ids
import copy
from agent_environments.cloud_config_manager import CloudConfigManager


# Create Flask app for agents
app = Flask(__name__)
CORS(app)

# Configure session
app.config['SECRET_KEY'] = cfg.SECRET_KEY

ccm = CloudConfigManager(os.getenv('API_KEY'))
_tenant_settings = ccm.get_tenant_settings()

AGENT_ENVIRONMENTS_ENABLED = _tenant_settings['environments_enabled']
_environment_managers = {}

# Configure logging
def setup_logging():
    """Configure logging for the agent API"""
    logger = logging.getLogger("AgentAPI")
    log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
    log_level = getattr(logging, log_level_name, logging.DEBUG)
    logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler = WatchedFileHandler(filename=os.getenv('AGENT_API_LOG', get_log_path('agent_api_log.txt')), encoding='utf-8')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger

rotate_logs_on_startup(os.getenv('AGENT_API_LOG', get_log_path('agent_api_log.txt')))

logger = setup_logging()

logger.info(f'Cloud Config: {_tenant_settings}')


def initialize_environments_for_agent_api():
    """Initialize environment support for agent API"""
    global AGENT_ENVIRONMENTS_ENABLED
    
    try:
        if _tenant_settings['environments_enabled']:
            AGENT_ENVIRONMENTS_ENABLED = True
            print("Agent Environments enabled for Agent API")
            return True
    except ImportError:
        print("Agent Environments module not available for Agent API")
    except Exception as e:
        print(f"Error initializing environments for Agent API: {e}")
    
    return False


def get_environment_manager(tenant_id):
    """Get or create environment manager for tenant"""
    if not AGENT_ENVIRONMENTS_ENABLED:
        return None
    
    if tenant_id not in _environment_managers:
        try:
            from agent_environments import AgentEnvironmentManager
            _environment_managers[tenant_id] = AgentEnvironmentManager(tenant_id)
        except Exception as e:
            logger.error(f"Failed to create environment manager: {e}")
            return None
    
    return _environment_managers[tenant_id]

# Update your agent loading function to use custom environment
def load_agent_with_environment(agent_id):
    """Load agent with optional custom environment"""
    try:
        # Your existing agent loading code
        agent = agent_registry.load_agent(agent_id)
        
        # Check for custom environment
        if AGENT_ENVIRONMENTS_ENABLED:
            tenant_id = os.getenv('API_KEY')
            env_manager = get_environment_manager(tenant_id)
            
            if env_manager:
                # Get Python executable for agent's environment
                python_path = env_manager.get_environment_for_agent(agent_id)
                
                if python_path:
                    # Store the custom Python path for this agent
                    agent.custom_python_path = python_path
                    logger.info(f"Agent {agent_id} using custom environment: {python_path}")
        
        return agent
        
    except Exception as e:
        logger.error(f"Error loading agent with environment: {e}")
        return None


initialize_environments_for_agent_api()


# Legacy Approach
# active_agents = {}
# agent_ids = get_agent_ids()
# for id in agent_ids:
#     print(f'Initializing agent {id}...')
#     temp_agent = GeneralAgent(id)
#     active_agents[id] = temp_agent





# Global agent registry
class AgentRegistry:
    """Thread-safe registry for managing active agents"""
    
    def __init__(self):
        self.agents = {}
        self.lock = threading.Lock()
        self.sessions = {}  # Track agent sessions
    
    def load_agent(self, agent_id):
        """Load or retrieve an agent instance"""
        with self.lock:
            if agent_id not in self.agents:
                try:
                    logger.info(f"Loading agent {agent_id}")
                    agent = GeneralAgent(agent_id)
                    self.agents[agent_id] = {
                        'instance': agent,
                        'last_accessed': datetime.now(),
                        'sessions': {}
                    }
                except Exception as e:
                    logger.error(f"Failed to load agent {agent_id}: {str(e)}")
                    raise
            else:
                self.agents[agent_id]['last_accessed'] = datetime.now()
            
            return self.agents[agent_id]['instance']
    
    def get_agent(self, agent_id):
        """Get an agent instance if it exists"""
        with self.lock:
            if agent_id in self.agents:
                self.agents[agent_id]['last_accessed'] = datetime.now()
                return self.agents[agent_id]['instance']
            return None
    
    def unload_agent(self, agent_id):
        """Unload an agent and cleanup resources"""
        with self.lock:
            if agent_id in self.agents:
                try:
                    agent = self.agents[agent_id]['instance']
                    agent.cleanup()
                    del self.agents[agent_id]
                    logger.info(f"Unloaded agent {agent_id}")
                except Exception as e:
                    logger.error(f"Error unloading agent {agent_id}: {str(e)}")
    
    def get_all_agents(self):
        """Get information about all loaded agents"""
        with self.lock:
            return {
                agent_id: {
                    'name': agent_data['instance'].AGENT_NAME,
                    'last_accessed': agent_data['last_accessed'].isoformat(),
                    'session_count': len(agent_data['sessions'])
                }
                for agent_id, agent_data in self.agents.items()
            }
    
    def cleanup_idle_agents(self, idle_minutes=30):
        """Cleanup agents that haven't been accessed recently"""
        with self.lock:
            current_time = datetime.now()
            agents_to_unload = []
            
            for agent_id, agent_data in self.agents.items():
                idle_time = (current_time - agent_data['last_accessed']).total_seconds() / 60
                if idle_time > idle_minutes:
                    agents_to_unload.append(agent_id)
            
            for agent_id in agents_to_unload:
                self.unload_agent(agent_id)
                logger.info(f"Cleaned up idle agent {agent_id}")

# Initialize registry
agent_registry = AgentRegistry()

# Session management
class AgentSessionManager:
    """Manage chat sessions for agents"""
    
    def __init__(self):
        self.sessions = {}
        self.lock = threading.Lock()
    
    def create_session(self, agent_id, user_id=None):
        """Create a new chat session"""
        session_id = str(uuid.uuid4())
        with self.lock:
            self.sessions[session_id] = {
                'agent_id': agent_id,
                'user_id': user_id,
                'created_at': datetime.now(),
                'last_activity': datetime.now(),
                'chat_history': [],
                'context': {}
            }
        return session_id
    
    def get_session(self, session_id):
        """Get session data"""
        with self.lock:
            if session_id in self.sessions:
                self.sessions[session_id]['last_activity'] = datetime.now()
                return self.sessions[session_id]
            return None
    
    def update_session(self, session_id, chat_history=None, context=None):
        """Update session data"""
        with self.lock:
            if session_id in self.sessions:
                if chat_history is not None:
                    self.sessions[session_id]['chat_history'] = chat_history
                if context is not None:
                    self.sessions[session_id]['context'].update(context)
                self.sessions[session_id]['last_activity'] = datetime.now()
                return True
            return False
    
    def delete_session(self, session_id):
        """Delete a session"""
        with self.lock:
            if session_id in self.sessions:
                del self.sessions[session_id]
                return True
            return False

session_manager = AgentSessionManager()

# API Routes

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'agent-api',
        'timestamp': datetime.now().isoformat(),
        # 'loaded_agents': len(agent_registry.agents)
    })

@app.route('/agents', methods=['GET'])
@cross_origin()
def list_agents():
    """List all available agents"""
    try:
        agent_ids = get_agent_ids()
        agents = []
        
        for agent_id in agent_ids:
            # Try to get basic info without loading the agent
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
                cursor.execute("""
                    SELECT agent_name, agent_description, agent_objective, agent_enabled
                    FROM AgentConfigurations
                    WHERE agent_id = ?
                """, agent_id)
                
                row = cursor.fetchone()
                if row:
                    agents.append({
                        'id': agent_id,
                        'name': row[0],
                        'description': row[1],
                        'objective': row[2],
                        'enabled': row[3],
                        'loaded': agent_id in agent_registry.agents
                    })
                
                cursor.close()
                conn.close()
            except Exception as e:
                logger.error(f"Error getting info for agent {agent_id}: {str(e)}")
        
        return jsonify({
            'status': 'success',
            'agents': agents
        })
    
    except Exception as e:
        logger.error(f"Error listing agents: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/agents/<int:agent_id>', methods=['GET'])
@cross_origin()
def get_agent_info(agent_id):
    """Get detailed information about a specific agent"""
    try:
        # agent = agent_registry.get_agent(agent_id)
        # if not agent:
        #     agent = agent_registry.load_agent(agent_id)

        # Get info from database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get agent info from database
        cursor.execute("""
            SELECT 
                a.description as agent_name,
                a.objective as system_prompt,
                a.enabled,
                a.description
            FROM Agents a
            WHERE a.id = ?
        """, agent_id)

        row = cursor.fetchone()
        if not row:
            return jsonify({
                'status': 'error',
                'message': f'Agent {agent_id} not found'
            }), 404

        # print(86 * '^')
        # print(86 * '^')
        # print(86 * '^')
        # print(86 * '^')
        # print(86 * '^')
        # agent = GeneralAgent(agent_id)
        # print(86 * '^')
        # print(86 * '^')
        # print(86 * '^')
        # print(86 * '^')
        # print(86 * '^')

        # Get tools for this agent
        cursor.execute("""
            SELECT tool_name
            FROM AgentTools
            WHERE agent_id = ?
        """, agent_id)
        
        tools = [row[0] for row in cursor.fetchall()]
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'agent': {
                'id': agent_id,
                'name': row[0],
                'system_prompt': row[1],
                'tools': tools,
                'enabled': row[2],
                'description': row[3],
                'model': ''
            }
        })
    
    except Exception as e:
        logger.error(f"Error getting agent {agent_id}: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/agents/<int:agent_id>/load', methods=['POST'])
@cross_origin()
def load_agent(agent_id):
    """Explicitly load an agent into memory"""
    try:
        agent = agent_registry.load_agent(agent_id)
        return jsonify({
            'status': 'success',
            'message': f'Agent {agent_id} loaded successfully',
            'agent_name': agent.AGENT_NAME
        })
    
    except Exception as e:
        logger.error(f"Error loading agent {agent_id}: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/agents/<int:agent_id>/unload', methods=['POST'])
@cross_origin()
def unload_agent(agent_id):
    """Unload an agent from memory"""
    try:
        agent_registry.unload_agent(agent_id)
        return jsonify({
            'status': 'success',
            'message': f'Agent {agent_id} unloaded successfully'
        })
    
    except Exception as e:
        logger.error(f"Error unloading agent {agent_id}: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/sessions', methods=['POST'])
@cross_origin()
def create_session():
    """Create a new chat session"""
    try:
        data = request.get_json()
        agent_id = data.get('agent_id')
        user_id = data.get('user_id')
        
        if not agent_id:
            return jsonify({
                'status': 'error',
                'message': 'agent_id is required'
            }), 400
        
        # Ensure agent is loaded
        agent = agent_registry.load_agent(agent_id)
        
        # Create session
        session_id = session_manager.create_session(agent_id, user_id)
        
        return jsonify({
            'status': 'success',
            'session_id': session_id,
            'agent_name': agent.AGENT_NAME
        })
    
    except Exception as e:
        logger.error(f"Error creating session: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/sessions/<session_id>', methods=['DELETE'])
@cross_origin()
def delete_session(session_id):
    """Delete a chat session"""
    try:
        if session_manager.delete_session(session_id):
            return jsonify({
                'status': 'success',
                'message': 'Session deleted'
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Session not found'
            }), 404
    
    except Exception as e:
        logger.error(f"Error deleting session: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/chat', methods=['POST'])
@cross_origin()
def chat():
    """Main chat endpoint - stateless version"""
    try:
        data = request.get_json()
        agent_id = data.get('agent_id')
        prompt = data.get('prompt')
        chat_history = data.get('chat_history', [])
        use_smart_render_str = data.get('use_smart_render', 'false')
        use_smart_render = str(use_smart_render_str).lower() == 'true'
        user_id = data.get('user_id', None)

        if not agent_id or not prompt:
            logger.error(f"AGENT API: ERROR - Agent id or prompt not provided.")
            return jsonify({
                'status': 'error',
                'message': 'agent_id and prompt are required'
            }), 400
        
        logger.info(f"AGENT API: Initializing agent {agent_id} for user id {user_id}...")
        print(f"AGENT API: Initializing agent {agent_id} for user id {user_id}...")

        agent = GeneralAgent(agent_id, user_id=user_id)

        logger.info(f"AGENT API: Initializing chat history...")
        print(f"AGENT API: Initializing chat history...")

        # Initialize chat history
        agent.initialize_chat_history(chat_history)
        
        # Run agent
        logger.info(f"Processing chat for agent {agent_id}: {prompt[:100]}...")
        response = agent.run(prompt, use_smart_render=use_smart_render, user_id=user_id)
        
        # Get updated chat history
        updated_history = agent.get_chat_history()
        
        return jsonify({
            'status': 'success',
            'response': response,
            'chat_history': updated_history
        })
    
    except Exception as e:
        logger.error(f"Error in chat: {str(e)}")
        logger.error(traceback.format_exc())
        
        # Try to generate a friendly error response
        try:
            friendly_response = azureMiniQuickPrompt(
                f"The user asked: {prompt}. An error occurred: {str(e)}. Please provide a helpful response.",
                "You are a helpful assistant. Explain the error in a user-friendly way."
            )
            return jsonify({
                'status': 'error',
                'response': friendly_response,
                'chat_history': chat_history
            })
        except:
            return jsonify({
                'status': 'error',
                'response': "I encountered an error processing your request. Please try again.",
                'chat_history': chat_history
            })

@app.route('/chat/session', methods=['POST'])
@cross_origin()
def chat_with_session():
    """Chat endpoint with session management"""
    try:
        data = request.get_json()
        session_id = data.get('session_id')
        prompt = data.get('prompt')
        
        if not session_id or not prompt:
            return jsonify({
                'status': 'error',
                'message': 'session_id and prompt are required'
            }), 400
        
        # Get session
        session_data = session_manager.get_session(session_id)
        if not session_data:
            return jsonify({
                'status': 'error',
                'message': 'Session not found'
            }), 404
        
        agent_id = session_data['agent_id']
        chat_history = session_data['chat_history']
        
        # Load or get agent
        agent = agent_registry.get_agent(agent_id)
        if not agent:
            agent = agent_registry.load_agent(agent_id)
        
        # Initialize chat history
        agent.initialize_chat_history(chat_history)
        
        # Run agent
        logger.info(f"Processing chat for session {session_id}: {prompt[:100]}...")
        response = agent.run(prompt)
        
        # Get updated chat history
        updated_history = agent.get_chat_history()
        
        # Update session
        session_manager.update_session(session_id, chat_history=updated_history)
        
        return jsonify({
            'status': 'success',
            'response': response,
            'session_id': session_id
        })
    
    except Exception as e:
        logger.error(f"Error in session chat: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/agents/<int:agent_id>/execute', methods=['POST'])
@cross_origin()
def execute_agent_task():
    """Execute a specific task with an agent (for inter-agent communication)"""
    try:
        data = request.get_json()
        agent_id = data.get('agent_id', agent_id)
        message = data.get('message')
        context = data.get('context', {})
        
        if not message:
            return jsonify({
                'status': 'error',
                'message': 'message is required'
            }), 400
        
        # Load or get agent
        logger.info(f"AGENT API: Initializing agent {agent_id} for inter-agent communication...")
        
        # Initialize agent
        agent = GeneralAgent(agent_id)
        
        # Handle the agent request
        result = agent.handle_agent_request(message, context)
        
        return jsonify(result)
    
    except Exception as e:
        logger.error(f"Error executing agent task: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e),
            'metadata': {
                'agent_id': agent_id,
                'execution_time': datetime.now().isoformat()
            }
        }), 500

@app.route('/agents/reload', methods=['POST'])
@cross_origin()
def reload_agents():
    """Reload all agents (useful after configuration changes)"""
    try:
        # Unload all current agents
        for agent_id in list(agent_registry.agents.keys()):
            agent_registry.unload_agent(agent_id)
        
        # Load all agents
        agent_ids = get_agent_ids()
        loaded = []
        failed = []
        
        for agent_id in agent_ids:
            try:
                agent_registry.load_agent(agent_id)
                loaded.append(agent_id)
            except Exception as e:
                failed.append({'id': agent_id, 'error': str(e)})
        
        return jsonify({
            'status': 'success',
            'loaded': loaded,
            'failed': failed
        })
    
    except Exception as e:
        logger.error(f"Error reloading agents: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/cleanup', methods=['POST'])
@cross_origin()
def cleanup_idle():
    """Cleanup idle agents and sessions"""
    try:
        idle_minutes = request.get_json().get('idle_minutes', 30)
        agent_registry.cleanup_idle_agents(idle_minutes)
        
        return jsonify({
            'status': 'success',
            'message': f'Cleaned up agents idle for more than {idle_minutes} minutes'
        })
    
    except Exception as e:
        logger.error(f"Error in cleanup: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

# Background cleanup task
def periodic_cleanup():
    """Run periodic cleanup of idle agents"""
    import time
    while True:
        time.sleep(1800)  # Run every 30 minutes
        try:
            agent_registry.cleanup_idle_agents(60)  # Cleanup agents idle for 60+ minutes
            logger.info("Completed periodic cleanup")
        except Exception as e:
            logger.error(f"Error in periodic cleanup: {str(e)}")

# Start cleanup thread
# cleanup_thread = threading.Thread(target=periodic_cleanup, daemon=True)
# cleanup_thread.start()

# Initialize agents on startup (optional)
# def initialize_agents():
#     """Pre-load frequently used agents"""
#     try:
#         # You can customize this to load specific agents
#         agent_ids = get_agent_ids()
#         for agent_id in agent_ids[:5]:  # Load first 5 agents as example
#             try:
#                 agent_registry.load_agent(agent_id)
#                 logger.info(f"Pre-loaded agent {agent_id}")
#             except Exception as e:
#                 logger.error(f"Failed to pre-load agent {agent_id}: {str(e)}")
#     except Exception as e:
#         logger.error(f"Error initializing agents: {str(e)}")


@app.route('/agents/<int:agent_id>/chat', methods=['POST'])
@cross_origin()
def agent_chat_with_environment(agent_id):
    """Enhanced chat endpoint with environment support"""
    try:
        data = request.get_json()
        message = data.get('message')
        chat_history = data.get('chat_history', [])
        
        # Get or load agent
        agent = agent_registry.get_agent(agent_id)
        if not agent:
            agent = load_agent_with_environment(agent_id)  # Use the enhanced loading
        
        if not agent:
            return jsonify({
                'status': 'error',
                'message': 'Failed to load agent'
            }), 404
        
        # Check if we need to execute in custom environment
        if hasattr(agent, 'custom_python_path') and agent.custom_python_path:
            # Execute using custom Python environment
            response = execute_in_custom_environment(
                agent.custom_python_path,
                agent_id,
                message,
                chat_history
            )
        else:
            # Normal execution
            agent.initialize_chat_history(chat_history)
            response = agent.run(message)
        
        return jsonify({
            'status': 'success',
            'response': response,
            'used_custom_environment': hasattr(agent, 'custom_python_path')
        })
        
    except Exception as e:
        logger.error(f"Error in agent chat: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

def execute_in_custom_environment(python_path, agent_id, message, chat_history):
    """Execute agent code in custom Python environment"""
    try:
        import subprocess
        import json
        import tempfile
        
        # Create a temporary script to run in the custom environment
        script = f"""
                    import sys
                    import json
                    from GeneralAgent import GeneralAgent

                    # Load agent
                    agent = GeneralAgent({agent_id})
                    agent.initialize_chat_history({json.dumps(chat_history)})

                    # Run the agent
                    response = agent.run({json.dumps(message)})

                    # Output the response as JSON
                    print(json.dumps({{'response': response}}))
                    """
        
        # Write script to temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(script)
            script_path = f.name
        
        # Execute with custom Python
        result = subprocess.run(
            [python_path, script_path],
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        
        if result.returncode == 0:
            # Parse the output
            output = json.loads(result.stdout)
            return output['response']
        else:
            logger.error(f"Environment execution failed: {result.stderr}")
            raise Exception(f"Execution failed: {result.stderr}")
            
    except subprocess.TimeoutExpired:
        raise Exception("Agent execution timed out")
    except Exception as e:
        logger.error(f"Error executing in custom environment: {e}")
        raise
    finally:
        # Clean up temp file
        if 'script_path' in locals():
            try:
                os.unlink(script_path)
            except:
                pass
