"""
Agent Environments API
Flask blueprint for environment management endpoints
Full implementation with SQL Server (no SQLAlchemy)
"""

from flask import Blueprint, request, jsonify, render_template, current_app, redirect, url_for, flash, stream_with_context, Response
from flask_login import login_required, current_user
from functools import wraps
import logging
from logging.handlers import WatchedFileHandler
import os
import pyodbc
from datetime import datetime

from .environment_manager import AgentEnvironmentManager
from .cloud_config_manager import CloudConfigManager
from .environment_config import EnvironmentConfig

from flask import current_app, render_template_string
import time
from CommonUtils import rotate_logs_on_startup, get_log_path
from admin_tier_usage import tier_allows_feature
from telemetry import track_feature_usage, track_environment_cloned, track_environment_created, track_environment_deleted


# Create Blueprint
environments_bp = Blueprint('environments', __name__, 
                          template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates'),
                          static_folder='static',
                          url_prefix='/environments')

# Setup logging
# Configure logging
def setup_logging():
    """Configure logging"""
    logger = logging.getLogger("EnvironmentAPI")
    log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
    log_level = getattr(logging, log_level_name, logging.DEBUG)
    logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler = WatchedFileHandler(filename=os.getenv('ENVIRONMENT_API_LOG', get_log_path('environment_api_log.txt')), encoding='utf-8')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger

rotate_logs_on_startup(os.getenv('ENVIRONMENT_API_LOG', get_log_path('environment_api_log.txt')))

logger = setup_logging()


# Manager instances cache
_manager_instances = {}

def get_connection_string():
    """Get database connection string from app config or build it"""
    connection_string = current_app.config.get('DB_CONNECTION_STRING')
    if not connection_string:
        # Build connection string from your config
        try:
            import config as cfg
            connection_string = f"DRIVER={{SQL Server}};SERVER={cfg.DATABASE_SERVER};DATABASE={cfg.DATABASE_NAME};UID={cfg.DATABASE_UID};PWD={cfg.DATABASE_PWD}"
        except ImportError:
            # Fallback to environment variables
            connection_string = (
                f"DRIVER={{SQL Server}};"
                f"SERVER={os.getenv('DATABASE_SERVER')};"
                f"DATABASE={os.getenv('DATABASE_NAME')};"
                f"UID={os.getenv('DATABASE_UID')};"
                f"PWD={os.getenv('DATABASE_PWD')}"
            )
    return connection_string

def get_manager(tenant_id=os.getenv('API_KEY')):
    """Get or create manager instance for tenant"""
    # Recheck cloud config periodically (every request or cached)
    if tenant_id not in _manager_instances:
        # Manager will check cloud config internally
        _manager_instances[tenant_id] = AgentEnvironmentManager(tenant_id)
        
        # Check if still enabled (subscription might have expired)
        if not _manager_instances[tenant_id].enabled:
            # Clear from cache
            del _manager_instances[tenant_id]
            return None
    
    return _manager_instances[tenant_id]

def developer_role_required(f):
    """Decorator that checks cloud-based permissions"""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        # Get settings from app config (set during initialization)
        enabled = current_app.config.get('AGENT_ENVIRONMENTS_ENABLED', False)
        settings = current_app.config.get('AGENT_ENVIRONMENTS_SETTINGS', {})
        
        # Check if feature is enabled for tenant
        if not enabled:
            tier = settings.get('tier_display', 'Free')
            max_envs = settings.get('max_environments', 0)
            
            # Show upgrade message
            upgrade_template = '''
            <!DOCTYPE html>
            <html>
            <head>
                <title>Upgrade Required</title>
                <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
                <style>
                    .upgrade-container {
                        max-width: 800px;
                        margin: 50px auto;
                    }
                    .tier-card {
                        border: 2px solid #f0f0f0;
                        border-radius: 10px;
                        padding: 20px;
                        margin: 10px;
                        text-align: center;
                    }
                    .current-tier {
                        background-color: #f8f9fa;
                    }
                    .recommended-tier {
                        border-color: #667eea;
                        background: linear-gradient(135deg, rgba(102,126,234,0.1) 0%, rgba(118,75,162,0.1) 100%);
                    }
                </style>
            </head>
            <body>
                <div class="container upgrade-container">
                    <div class="text-center mb-4">
                        <h1><i class="fas fa-cube text-warning"></i></h1>
                        <h2>Agent Environments - Premium Feature</h2>
                        <p class="lead">Your current subscription doesn't include Agent Environments</p>
                    </div>
                    
                    <div class="row">
                        <div class="col-md-6">
                            <div class="tier-card current-tier">
                                <h4>Your Current Tier</h4>
                                <h3 class="text-muted">{{ tier }}</h3>
                                <ul class="list-unstyled">
                                    <li>❌ No custom environments</li>
                                    <li>✓ Basic agent features</li>
                                </ul>
                            </div>
                        </div>
                        
                        <div class="col-md-6">
                            <div class="tier-card recommended-tier">
                                <h4>Upgrade to Professional</h4>
                                <h3 class="text-primary">$99/month</h3>
                                <ul class="list-unstyled">
                                    <li>✓ Up to 10 environments</li>
                                    <li>✓ Unlimited packages</li>
                                    <li>✓ Sandbox testing</li>
                                    <li>✓ Priority support</li>
                                </ul>
                                <a href="/subscription/upgrade" class="btn btn-primary mt-3">
                                    Upgrade Now
                                </a>
                            </div>
                        </div>
                    </div>
                    
                    <div class="text-center mt-4">
                        <a href="/" class="btn btn-outline-secondary">Back to Dashboard</a>
                    </div>
                </div>
            </body>
            </html>
            '''
            from flask import render_template_string
            return render_template_string(upgrade_template, tier=tier), 403
        
        # Check user role
        if not hasattr(current_user, 'role') or current_user.role not in [2, 3]:
            flash('You need developer or administrator privileges', 'danger')
            return redirect(url_for('home'))
        
        return f(*args, **kwargs)
    return decorated_function


def feature_required(f):
    """Decorator that handles disabled state gracefully"""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        # Check if feature is enabled
        if not current_app.config.get('AGENT_ENVIRONMENTS_ENABLED', False):
            # Return a nice disabled message instead of 404
            disabled_template = '''
            <!DOCTYPE html>
            <html>
            <head>
                <title>Feature Disabled</title>
                <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
            </head>
            <body>
                <div class="container mt-5">
                    <div class="alert alert-warning">
                        <h4 class="alert-heading">Agent Environments - Feature Disabled</h4>
                        <p>This feature is currently disabled. To enable it:</p>
                        <ol>
                            <li>Upgrade to the PRO tier or higher</li>
                            <li>Restart the application</li>
                        </ol>
                        <hr>
                        <p class="mb-0">Contact your administrator for access to this premium feature.</p>
                    </div>
                    <a href="/" class="btn btn-primary">Return to Dashboard</a>
                </div>
            </body>
            </html>
            '''
            return render_template_string(disabled_template), 403
        
        # Check role requirements
        if not hasattr(current_user, 'role') or current_user.role not in [2, 3]:
            return "Access denied", 403
            
        return f(*args, **kwargs)
    return decorated_function

# ====================
# UI Routes
# ====================

@environments_bp.route('/')
@feature_required
@tier_allows_feature('environments')
def index():
    """Main environment manager UI"""
    tenant_id = os.getenv('API_KEY')
    manager = get_manager(tenant_id)
    
    context = {
        'user_role': current_user.role,
        'is_admin': current_user.role == 3,
        'settings': manager.settings,
        'tenant_id': tenant_id,
        'max_environments': manager.settings.get('max_environments', 0),
        'tier_display': manager.settings.get('tier_display', 'Free')
    }
    
    return render_template('environment_manager.html', **context)



@environments_bp.route('/editor/<env_id>')
@developer_role_required
@tier_allows_feature('environments')
def editor(env_id):
    """Environment editor UI for managing packages"""
    tenant_id = os.getenv('API_KEY')
    manager = get_manager(tenant_id)
    
    # Verify user has access to this environment
    environments = manager.list_environments(user_id=current_user.id if current_user.role == 2 else None)
    env_exists = any(env['environment_id'] == env_id for env in environments)
    
    if not env_exists:
        flash('Environment not found or access denied', 'danger')
        return redirect(url_for('environments.index'))
    
    # Get environment details
    env_details = next((env for env in environments if env['environment_id'] == env_id), None)
    
    context = {
        'env_id': env_id,
        'env_details': env_details,
        'user_role': current_user.role,
        'is_admin': current_user.role == 3,
        'settings': manager.settings,
        'allowed_packages': manager.DEFAULT_ALLOWED_PACKAGES
    }
    
    return render_template('environment_editor.html', **context)

# ====================
# API Routes
# ====================

@environments_bp.route('/api/config', methods=['GET'])
@developer_role_required
def get_feature_config():
    """Get feature configuration for current tenant"""
    try:
        tenant_id = os.getenv('API_KEY')
        manager = get_manager(tenant_id)
        
        return jsonify({
            'status': 'success',
            'config': {
                'enabled': manager.settings.get('environments_enabled', False),
                'max_environments': manager.settings.get('max_environments', 0),
                'max_packages_per_env': manager.settings.get('max_packages_per_env', 50),
                'tier': manager.settings.get('tier_display', 'Free'),
                'subscription_status': manager.settings.get('subscription_status', 'none'),
                'allowed_packages': manager.DEFAULT_ALLOWED_PACKAGES
            }
        })
    except Exception as e:
        logger.error(f"Error getting feature config: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@environments_bp.route('/api/list', methods=['GET'])
@developer_role_required
def list_environments():
    """List all environments for the current user/tenant"""
    try:
        tenant_id = os.getenv('API_KEY')
        manager = get_manager(tenant_id)
        
        # Developers see only their environments, admins see all
        if current_user.role == 3:  # Admin
            environments = manager.list_environments()
        else:  # Developer
            environments = manager.list_environments(user_id=current_user.id)
        
        return jsonify({
            'status': 'success',
            'environments': environments,
            'user_role': current_user.role,
            'max_environments': manager.settings.get('max_environments', 0),
            'current_count': len(environments)
        })
    except Exception as e:
        logger.error(f"Error listing environments: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@environments_bp.route('/api/create', methods=['POST'])
@developer_role_required
def create_environment():
    """Create a new environment"""
    try:
        data = request.get_json()
        name = data.get('name')
        description = data.get('description', '')
        python_version = data.get('python_version')
        
        if not name:
            return jsonify({'status': 'error', 'message': 'Name is required'}), 400
        
        # Validate name
        import re
        if not re.match(r'^[a-zA-Z0-9\-_\s]+$', name):
            return jsonify({'status': 'error', 'message': 'Invalid name. Use only letters, numbers, hyphens, underscores, and spaces'}), 400
        
        tenant_id = os.getenv('API_KEY')
        manager = get_manager(tenant_id)
        
        success, env_id, message = manager.create_environment(
            name=name,
            description=description,
            created_by=current_user.id,
            python_version=python_version
        )
        
        if success:
            # If initial packages were specified, add them
            initial_packages = data.get('initial_packages', [])
            for package in initial_packages:
                try:
                    manager.add_package(env_id, package, None, current_user.id)
                except Exception as pkg_error:
                    logger.warning(f"Failed to add initial package {package}: {pkg_error}")

            try:
                track_feature_usage('environment', 'created')
                track_environment_created(env_id)
            except Exception as e:
                logger.warning(f'Failed to track environment created: {e}')
            
            return jsonify({
                'status': 'success',
                'environment_id': env_id,
                'message': message
            })
        else:
            return jsonify({'status': 'error', 'message': message}), 400
            
    except Exception as e:
        logger.error(f"Error creating environment: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@environments_bp.route('/api/<env_id>/info', methods=['GET'])
@developer_role_required
def get_environment_info(env_id):
    """Get detailed information about an environment"""
    try:
        tenant_id = os.getenv('API_KEY')
        manager = get_manager(tenant_id)
        
        # Verify access
        environments = manager.list_environments(user_id=current_user.id if current_user.role >= 2 else None)
        env_info = next((env for env in environments if env['environment_id'] == env_id), None)
        
        if not env_info:
            return jsonify({'status': 'error', 'message': 'Environment not found or access denied'}), 404
        
        # Get installed packages
        packages = manager.list_local_packages(env_id)
        
        # Add package info to environment info
        env_info['packages'] = packages
        env_info['package_details'] = len(packages)
        
        return jsonify({
            'status': 'success',
            'environment': env_info
        })
    except Exception as e:
        logger.error(f"Error getting environment info: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@environments_bp.route('/api/<env_id>/packages', methods=['GET'])
@developer_role_required
def get_packages(env_id):
    """Get packages in an environment"""
    try:
        tenant_id = os.getenv('API_KEY')
        manager = get_manager(tenant_id)
        
        # Verify access
        environments = manager.list_environments(user_id=current_user.id if current_user.role >= 2 else None)
        if not any(env['environment_id'] == env_id for env in environments):
            return jsonify({'status': 'error', 'message': 'Access denied'}), 403
        
        packages = manager.list_local_packages(env_id)
        
        return jsonify({
            'status': 'success',
            'packages': packages,
            'count': len(packages),
            'max_allowed': manager.settings.get('max_packages_per_env', 50)
        })
    except Exception as e:
        logger.error(f"Error getting packages: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@environments_bp.route('/api/<env_id>/packages', methods=['POST'])
@developer_role_required
def add_package(env_id):
    """Add a package to an environment"""
    try:
        data = request.get_json()
        package_name = data.get('package')
        version = data.get('version')
        
        if not package_name:
            return jsonify({'status': 'error', 'message': 'Package name required'}), 400
        
        tenant_id = os.getenv('API_KEY')
        manager = get_manager(tenant_id)
        
        # Verify access
        environments = manager.list_environments(user_id=current_user.id if current_user.role >= 2 else None)
        if not any(env['environment_id'] == env_id for env in environments):
            return jsonify({'status': 'error', 'message': 'Access denied'}), 403
        
        success, message = manager.add_package(env_id, package_name, version, current_user.id)
        
        return jsonify({
            'status': 'success' if success else 'error',
            'message': message
        })
    except Exception as e:
        logger.error(f"Error adding package: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@environments_bp.route('/api/<env_id>/packages/<package_name>', methods=['DELETE'])
@developer_role_required
def remove_package(env_id, package_name):
    """Remove a package from an environment"""
    try:
        tenant_id = os.getenv('API_KEY')
        manager = get_manager(tenant_id)
        
        # Verify access
        environments = manager.list_environments(user_id=current_user.id if current_user.role == 2 else None)
        if not any(env['environment_id'] == env_id for env in environments):
            return jsonify({'status': 'error', 'message': 'Access denied'}), 403
        
        success, message = manager.remove_package(env_id, package_name, current_user.id)
        
        return jsonify({
            'status': 'success' if success else 'error',
            'message': message
        })
    except Exception as e:
        logger.error(f"Error removing package: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@environments_bp.route('/api/<env_id>/clone', methods=['POST'])
@developer_role_required
def clone_environment(env_id):
    """Clone an environment"""
    try:
        data = request.get_json()
        new_name = data.get('name')
        
        if not new_name:
            return jsonify({'status': 'error', 'message': 'New name required'}), 400
        
        tenant_id = os.getenv('API_KEY')
        manager = get_manager(tenant_id)
        
        # Verify access to source environment
        environments = manager.list_environments(user_id=current_user.id if current_user.role == 2 else None)
        if not any(env['environment_id'] == env_id for env in environments):
            return jsonify({'status': 'error', 'message': 'Source environment not found or access denied'}), 403
        
        success, new_env_id, message = manager.clone_environment(
            env_id, new_name, current_user.id
        )

        try:
            track_feature_usage('environment', 'cloned')
            track_environment_cloned(env_id, new_env_id)
        except Exception as e:
            logger.warning(f'Failed to track environment cloned: {e}')
        
        return jsonify({
            'status': 'success' if success else 'error',
            'environment_id': new_env_id,
            'message': message
        })
    except Exception as e:
        logger.error(f"Error cloning environment: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@environments_bp.route('/api/<env_id>', methods=['DELETE'])
@developer_role_required
def delete_environment(env_id):
    """Delete an environment"""
    try:
        tenant_id = os.getenv('API_KEY')
        manager = get_manager(tenant_id)
        
        # Verify ownership (only owner or admin can delete)
        environments = manager.list_environments()
        env = next((e for e in environments if e['environment_id'] == env_id), None)
        
        if not env:
            return jsonify({'status': 'error', 'message': 'Environment not found'}), 404
        
        if current_user.role != 3 and env['created_by'] != current_user.id:
            return jsonify({'status': 'error', 'message': 'Only the owner or admin can delete this environment'}), 403
        
        success, message = manager.delete_environment(env_id, current_user.id)

        try:
            track_feature_usage('environment', 'deleted')
            track_environment_deleted(env_id)
        except Exception as e:
            logger.warning(f'Failed to track environment deleted: {e}')
        
        return jsonify({
            'status': 'success' if success else 'error',
            'message': message
        })
    except Exception as e:
        logger.error(f"Error deleting environment: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@environments_bp.route('/api/<env_id>/assign/<int:agent_id>', methods=['POST'])
@developer_role_required
def assign_to_agent(env_id, agent_id):
    """Assign environment to an agent"""
    try:
        tenant_id = os.getenv('API_KEY')
        manager = get_manager(tenant_id)
        
        # Verify access to environment
        environments = manager.list_environments(user_id=current_user.id if current_user.role == 2 else None)
        if not any(env['environment_id'] == env_id for env in environments):
            return jsonify({'status': 'error', 'message': 'Environment not found or access denied'}), 403
        
        # Verify agent exists and user has access
        connection_string = get_connection_string()
        conn = pyodbc.connect(connection_string)
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Check agent exists
        cursor.execute("SELECT id FROM Agents WHERE id = ?", agent_id)
        if not cursor.fetchone():
            conn.close()
            return jsonify({'status': 'error', 'message': 'Agent not found'}), 404
        
        conn.close()
        
        success, message = manager.assign_environment_to_agent(env_id, agent_id, current_user.id)
        
        return jsonify({
            'status': 'success' if success else 'error',
            'message': message
        })
    except Exception as e:
        logger.error(f"Error assigning environment to agent: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@environments_bp.route('/api/<env_id>/unassign/<int:agent_id>', methods=['POST'])
@developer_role_required
def unassign_from_agent(env_id, agent_id):
    """Remove environment assignment from an agent"""
    try:
        tenant_id = os.getenv('API_KEY')
        connection_string = get_connection_string()
        
        conn = pyodbc.connect(connection_string)
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Deactivate assignment
        cursor.execute("""
            UPDATE AgentEnvironmentAssignments 
            SET is_active = 0 
            WHERE environment_id = ? AND agent_id = ? AND is_active = 1
        """, env_id, agent_id)
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'message': 'Environment unassigned from agent'
        })
    except Exception as e:
        logger.error(f"Error unassigning environment: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@environments_bp.route('/api/agents/<int:agent_id>/environment', methods=['GET'])
@developer_role_required
def get_agent_environment(agent_id):
    """Get the environment assigned to an agent"""
    try:
        tenant_id = os.getenv('API_KEY')
        connection_string = get_connection_string()
        
        conn = pyodbc.connect(connection_string)
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get assigned environment
        cursor.execute("""
            SELECT 
                a.environment_id,
                e.name,
                e.description,
                a.assigned_date
            FROM AgentEnvironmentAssignments a
            INNER JOIN AgentEnvironments e ON a.environment_id = e.environment_id
            WHERE a.agent_id = ? AND a.is_active = 1
        """, agent_id)
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return jsonify({
                'status': 'success',
                'environment': {
                    'environment_id': row.environment_id,
                    'name': row.name,
                    'description': row.description,
                    'assigned_date': row.assigned_date.isoformat() if row.assigned_date else None
                }
            })
        else:
            return jsonify({
                'status': 'success',
                'environment': None,
                'message': 'No environment assigned'
            })
            
    except Exception as e:
        logger.error(f"Error getting agent environment: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@environments_bp.route('/api/templates', methods=['GET'])
@developer_role_required
def get_templates():
    """Get available environment templates"""
    try:
        tenant_id = os.getenv('API_KEY')
        connection_string = get_connection_string()
        
        conn = pyodbc.connect(connection_string)
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get templates
        cursor.execute("""
            SELECT 
                id,
                name,
                description,
                packages,
                category
            FROM AgentEnvironmentTemplates
            WHERE is_public = 1 OR created_by = ?
            ORDER BY name
        """, current_user.id)
        
        templates = []
        for row in cursor.fetchall():
            templates.append({
                'id': row.id,
                'name': row.name,
                'description': row.description,
                'packages': row.packages,
                'category': row.category
            })
        
        conn.close()
        
        return jsonify({
            'status': 'success',
            'templates': templates
        })
    except Exception as e:
        logger.error(f"Error getting templates: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@environments_bp.route('/api/templates/<int:template_id>/apply', methods=['POST'])
@developer_role_required
def apply_template(template_id):
    """Create an environment from a template"""
    try:
        data = request.get_json()
        env_name = data.get('name')
        
        if not env_name:
            return jsonify({'status': 'error', 'message': 'Environment name required'}), 400
        
        tenant_id = os.getenv('API_KEY')
        manager = get_manager(tenant_id)
        connection_string = get_connection_string()
        
        # Get template
        conn = pyodbc.connect(connection_string)
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        cursor.execute("""
            SELECT name, description, packages
            FROM AgentEnvironmentTemplates
            WHERE id = ?
        """, template_id)
        
        template = cursor.fetchone()
        conn.close()
        
        if not template:
            return jsonify({'status': 'error', 'message': 'Template not found'}), 404
        
        # Create environment
        success, env_id, message = manager.create_environment(
            name=env_name,
            description=f"Created from template: {template.name}",
            created_by=current_user.id
        )
        
        if success and template.packages:
            # Add packages from template
            import json
            try:
                packages = json.loads(template.packages)
                for package in packages:
                    if isinstance(package, dict):
                        manager.add_package(env_id, package.get('name'), package.get('version'), current_user.id)
                    else:
                        manager.add_package(env_id, package, None, current_user.id)
            except Exception as pkg_error:
                logger.warning(f"Error adding template packages: {pkg_error}")
        
        return jsonify({
            'status': 'success' if success else 'error',
            'environment_id': env_id if success else None,
            'message': message
        })
        
    except Exception as e:
        logger.error(f"Error applying template: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@environments_bp.route('/api/usage/stats', methods=['GET'])
@developer_role_required
def get_usage_stats():
    """Get usage statistics (admin only)"""
    try:
        if current_user.role != 3:  # Admin only
            return jsonify({'status': 'error', 'message': 'Admin access required'}), 403
        
        tenant_id = os.getenv('API_KEY')
        connection_string = get_connection_string()
        
        conn = pyodbc.connect(connection_string)
        cursor = conn.cursor()
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        # Get usage stats
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT environment_id) as total_environments,
                COUNT(DISTINCT user_id) as active_users,
                COUNT(*) as total_actions,
                SUM(CASE WHEN action = 'create' THEN 1 ELSE 0 END) as creates,
                SUM(CASE WHEN action = 'add_package' THEN 1 ELSE 0 END) as package_installs,
                MAX(timestamp) as last_activity
            FROM AgentEnvironmentUsage
            WHERE timestamp > DATEADD(day, -30, getutcdate())
        """)
        
        stats = cursor.fetchone()
        
        # Get top users
        cursor.execute("""
            SELECT TOP 10
                u.user_id,
                COUNT(*) as action_count
            FROM AgentEnvironmentUsage u
            WHERE u.timestamp > DATEADD(day, -30, getutcdate())
            GROUP BY u.user_id
            ORDER BY action_count DESC
        """)
        
        top_users = []
        for row in cursor.fetchall():
            top_users.append({
                'user_id': row.user_id,
                'action_count': row.action_count
            })
        
        conn.close()
        
        return jsonify({
            'status': 'success',
            'stats': {
                'total_environments': stats.total_environments or 0,
                'active_users': stats.active_users or 0,
                'total_actions': stats.total_actions or 0,
                'creates': stats.creates or 0,
                'package_installs': stats.package_installs or 0,
                'last_activity': stats.last_activity.isoformat() if stats.last_activity else None
            },
            'top_users': top_users
        })
    except Exception as e:
        logger.error(f"Error getting usage stats: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

# Error handlers
@environments_bp.errorhandler(404)
def not_found(error):
    if request.path.startswith('/environments/api/'):
        return jsonify({'status': 'error', 'message': 'Resource not found'}), 404
    return render_template('404.html'), 404

@environments_bp.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal error: {error}")
    if request.path.startswith('/environments/api/'):
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
    return render_template('500.html'), 500


@environments_bp.route('/sandbox')
@feature_required
@tier_allows_feature('environments')
def sandbox():
    """Environment sandbox/testing UI"""
    tenant_id = os.getenv('API_KEY')
    manager = get_manager(tenant_id)
    
    # Get list of agents for testing
    connection_string = get_connection_string()
    conn = pyodbc.connect(connection_string)
    cursor = conn.cursor()
    cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
    
    # Get agents
    cursor.execute("""
        SELECT id, description, objective
        FROM Agents
        WHERE enabled = 1
        ORDER BY description
    """)
    
    agents = []
    for row in cursor.fetchall():
        agents.append({
            'id': row.id,
            'description': row.description,
            'objective': row.objective
        })
    
    conn.close()
    
    context = {
        'user_role': current_user.role,
        'is_admin': current_user.role == 3,
        'settings': manager.settings,
        'agents': agents,
        'tenant_id': tenant_id
    }
    
    return render_template('environment_sandbox.html', **context)

@environments_bp.route('/api/sandbox/test', methods=['POST'])
@developer_role_required
def test_environment():
    """Test an environment with sample code"""
    try:
        data = request.get_json()
        env_id = data.get('environment_id')
        test_code = data.get('code')
        test_type = data.get('test_type', 'simple')  # simple or agent
        agent_id = data.get('agent_id')

        import os
        
        if not env_id or not test_code:
            return jsonify({'status': 'error', 'message': 'Environment and code required'}), 400
        
        tenant_id = os.getenv('API_KEY')
        manager = get_manager(tenant_id)
        
        # Get Python executable for environment
        python_path = manager.get_python_executable(env_id)
        if not python_path:
            return jsonify({'status': 'error', 'message': 'Environment not found or not initialized'}), 404
        
        import subprocess
        import tempfile
        from DataUtils import replace_connection_placeholders
        test_code = replace_connection_placeholders(test_code)
        
        # Create temporary test script
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            if test_type == 'simple':
                # Simple code execution
                f.write(test_code)
            else:
                # Agent execution test
                agent_test = f"""
                            import sys
                            import json
                            import os

                            # Test code from user
                            {test_code}

                            # Try to use the environment
                            try:
                                result = main()  # User should define a main() function
                                print(json.dumps({{'success': True, 'result': str(result)}}))
                            except Exception as e:
                                print(json.dumps({{'success': False, 'error': str(e)}}))
                            """
                f.write(agent_test)
            
            script_path = f.name
        
        # Execute test
        try:
            result = subprocess.run(
                [python_path, script_path],
                capture_output=True,
                text=True,
                timeout=30  # 30 second timeout for tests
            )
            
            # Clean up
            os.unlink(script_path)
            
            return jsonify({
                'status': 'success',
                'stdout': result.stdout,
                'stderr': result.stderr,
                'returncode': result.returncode
            })
            
        except subprocess.TimeoutExpired:
            os.unlink(script_path)
            return jsonify({
                'status': 'error',
                'message': 'Test execution timed out (30 seconds)'
            }), 408
            
    except Exception as e:
        logger.error(f"Error testing environment: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@environments_bp.route('/api/sandbox/assign', methods=['POST'])
@developer_role_required
def sandbox_assign_environment():
    """Temporarily assign environment to agent for testing"""
    try:
        data = request.get_json()
        env_id = data.get('environment_id')
        agent_id = data.get('agent_id')
        
        if not env_id or not agent_id:
            return jsonify({'status': 'error', 'message': 'Environment and agent required'}), 400
        
        tenant_id = os.getenv('API_KEY')
        manager = get_manager(tenant_id)
        
        # Assign environment
        success, message = manager.assign_environment_to_agent(env_id, agent_id, current_user.id)
        
        return jsonify({
            'status': 'success' if success else 'error',
            'message': message
        })
        
    except Exception as e:
        logger.error(f"Error assigning environment: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@environments_bp.route('/api/sandbox/package-info/<package_name>', methods=['GET'])
@developer_role_required
def get_package_info(package_name):
    """Get information about a Python package from PyPI"""
    try:
        import requests
        
        # Query PyPI API
        response = requests.get(f'https://pypi.org/pypi/{package_name}/json', timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            return jsonify({
                'status': 'success',
                'package': {
                    'name': data['info']['name'],
                    'version': data['info']['version'],
                    'summary': data['info']['summary'],
                    'home_page': data['info']['home_page'],
                    'license': data['info']['license'],
                    'requires_python': data['info']['requires_python']
                }
            })
        else:
            return jsonify({'status': 'error', 'message': 'Package not found'}), 404
            
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    



import yaml
import os
from flask import render_template_string

@environments_bp.route('/docs')
@login_required  # Don't require developer role for docs
def documentation():
    """Render documentation from YAML file"""
    try:
        # Path to documentation file
        docs_path = os.path.join(
            os.path.dirname(__file__), 
            'docs', 
            'environments_guide.yaml'
        )
        print('Loading documentation...')
        # Check if custom docs exist, otherwise use default
        if not os.path.exists(docs_path):
            # Use embedded default documentation
            docs_content = get_default_documentation()
        else:
            print('Opening yaml file...')
            # Load documentation from YAML file
            with open(docs_path, 'r', encoding='utf-8') as f:
                docs_content = yaml.safe_load(f)
        
        # Render documentation template
        return render_template('environment_docs.html', docs=docs_content)
        
    except Exception as e:
        logger.error(f"Error loading documentation: {e}")
        print(f"Error loading documentation: {e}")
        # Fallback to basic documentation
        return render_template_string('''
            <div class="container mt-5">
                <h1>Documentation</h1>
                <p>Error loading documentation. Please contact support.</p>
                <a href="/environments/" class="btn btn-primary">Back to Environments</a>
            </div>
        ''')

def get_default_documentation():
    """Return default documentation if file not found"""
    return {
        'title': 'Agent Environments Documentation',
        'sections': [
            {
                'id': 'overview',
                'title': 'Overview',
                'content': 'Agent Environments allow you to create isolated Python environments for your agents.',
                'features': [
                    'Create isolated environments',
                    'Install custom packages',
                    'Assign to agents',
                    'Test in sandbox'
                ]
            }
        ],
        'faqs': [],
        'support': {
            'email': 'support@example.com'
        }
    }

# API endpoint to reload documentation (for admins)
@environments_bp.route('/api/docs/reload', methods=['POST'])
@login_required
def reload_documentation():
    """Reload documentation from file (admin only)"""
    if current_user.role != 3:  # Admin only
        return jsonify({'error': 'Admin access required'}), 403
    
    # Clear any cached documentation
    # Force reload on next access
    return jsonify({'status': 'success', 'message': 'Documentation will be reloaded'})



"""
Environment Import/Export API Routes
"""

from flask import send_file, request, jsonify
from io import BytesIO
import json


@environments_bp.route('/api/<env_id>/export', methods=['GET'])
@developer_role_required
def export_environment(env_id):
    """Export an environment as a downloadable ZIP file"""
    try:
        tenant_id = os.getenv('API_KEY')
        manager = get_manager(tenant_id)
        
        # Verify access
        environments = manager.list_environments(
            user_id=current_user.id if current_user.role == 2 else None
        )
        if not any(env['environment_id'] == env_id for env in environments):
            return jsonify({'status': 'error', 'message': 'Access denied'}), 403
        
        # Export environment
        success, message, zip_data = manager.export_environment(env_id, current_user.id)
        
        if not success:
            return jsonify({'status': 'error', 'message': message}), 500
        
        # Get environment name for filename
        env_details = next((env for env in environments if env['environment_id'] == env_id), {})
        env_name = env_details.get('name', 'environment')
        
        # Clean filename
        import re
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', env_name)
        filename = f"{safe_name}_export.zip"
        
        # Return ZIP file
        return send_file(
            BytesIO(zip_data),
            mimetype='application/zip',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        logger.error(f"Error exporting environment: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@environments_bp.route('/api/import', methods=['POST'])
@developer_role_required
def import_environment():
    """Import an environment from uploaded ZIP file"""
    try:
        # Check if file is present
        if 'file' not in request.files:
            return jsonify({'status': 'error', 'message': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'status': 'error', 'message': 'No file selected'}), 400
        
        # Get optional parameters
        new_name = request.form.get('name')
        skip_packages = request.form.get('skip_packages', 'false').lower() == 'true'
        
        # Read file data
        zip_data = file.read()
        
        # Get manager
        tenant_id = os.getenv('API_KEY')
        manager = get_manager(tenant_id)
        print('Importing environment...')
        # Import environment
        success, env_id, message = manager.import_environment(
            zip_data=zip_data,
            user_id=current_user.id,
            new_name=new_name,
            skip_packages=skip_packages
        )
        print('Finished importing environment.')
        if success:
            return jsonify({
                'status': 'success',
                'environment_id': env_id,
                'message': message
            })
        else:
            return jsonify({
                'status': 'error',
                'message': message
            }), 400
            
    except Exception as e:
        logger.error(f"Error importing environment: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@environments_bp.route('/api/import/analyze', methods=['POST'])
@developer_role_required
def analyze_import():
    """Analyze an import package without actually importing it"""
    try:
        # Check if file is present
        if 'file' not in request.files:
            return jsonify({'status': 'error', 'message': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'status': 'error', 'message': 'No file selected'}), 400
        
        # Read and analyze ZIP file
        import zipfile
        import tempfile
        
        zip_data = file.read()
        
        with tempfile.TemporaryDirectory() as temp_dir:
            # Save and extract ZIP
            zip_path = os.path.join(temp_dir, 'analyze.zip')
            with open(zip_path, 'wb') as f:
                f.write(zip_data)
            
            try:
                with zipfile.ZipFile(zip_path, 'r') as zipf:
                    # Check for manifest
                    if 'manifest.json' not in zipf.namelist():
                        return jsonify({
                            'status': 'error',
                            'message': 'Invalid package: manifest.json not found'
                        }), 400
                    
                    # Extract and read manifest
                    manifest_data = zipf.read('manifest.json')
                    manifest = json.loads(manifest_data)
                    
                    # Check for requirements files
                    has_requirements = 'requirements.txt' in zipf.namelist()
                    has_freeze = 'requirements-freeze.txt' in zipf.namelist()
                    
                    # Get file list
                    files = zipf.namelist()
                    
                    # Prepare analysis result
                    analysis = {
                        'valid': True,
                        'manifest_version': manifest.get('version'),
                        'environment': manifest.get('environment', {}),
                        'packages': manifest.get('packages', []),
                        'package_count': len(manifest.get('packages', [])),
                        'export_metadata': manifest.get('export_metadata', {}),
                        'files': {
                            'manifest': True,
                            'requirements': has_requirements,
                            'requirements_freeze': has_freeze,
                            'total_files': len(files)
                        },
                        'warnings': []
                    }
                    
                    # Check for potential issues
                    if not analysis['environment'].get('name'):
                        analysis['warnings'].append('Environment name is missing')
                    
                    if analysis['package_count'] == 0:
                        analysis['warnings'].append('No packages found in environment')
                    
                    # Check Python version compatibility
                    import sys
                    env_python = analysis['environment'].get('python_version', '')
                    current_python = f"{sys.version_info.major}.{sys.version_info.minor}"
                    if env_python and not env_python.startswith(current_python[:3]):
                        analysis['warnings'].append(
                            f"Python version mismatch: Environment uses {env_python}, "
                            f"current system uses {current_python}"
                        )
                    
                    return jsonify({
                        'status': 'success',
                        'analysis': analysis
                    })
                    
            except zipfile.BadZipFile:
                return jsonify({
                    'status': 'error',
                    'message': 'Invalid ZIP file'
                }), 400
            except json.JSONDecodeError:
                return jsonify({
                    'status': 'error',
                    'message': 'Invalid manifest.json format'
                }), 400
                
    except Exception as e:
        logger.error(f"Error analyzing import: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@environments_bp.route('/api/<env_id>/export/requirements', methods=['GET'])
@developer_role_required
def export_requirements(env_id):
    """Export just the requirements.txt file for an environment"""
    try:
        tenant_id = os.getenv('API_KEY')
        manager = get_manager(tenant_id)
        
        # Verify access
        environments = manager.list_environments(
            user_id=current_user.id if current_user.role >= 2 else None
        )
        env_details = next((env for env in environments if env['environment_id'] == env_id), None)
        
        if not env_details:
            return jsonify({'status': 'error', 'message': 'Access denied'}), 403
        
        # Get packages
        packages = manager.list_packages(env_id)
        
        # Build requirements.txt content
        requirements = []
        for pkg in packages:
            requirements.append(f"{pkg['name']}=={pkg['version']}")
        
        requirements_content = '\n'.join(sorted(requirements))
        
        # Clean environment name for filename
        import re
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', env_details['name'])
        filename = f"{safe_name}_requirements.txt"
        
        # Return as downloadable text file
        return send_file(
            BytesIO(requirements_content.encode('utf-8')),
            mimetype='text/plain',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        logger.error(f"Error exporting requirements: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    

import queue
import threading

@environments_bp.route('/api/create-stream', methods=['POST'])
@developer_role_required
def create_environment_stream():
    """Create environment with real-time progress streaming"""
    logger.info(f"Called create_environment_stream...")
    print("Called create_environment_stream...")

    data = request.get_json()

    user_id = current_user.id

    logger.info(f"Creating environment via stream for user {user_id} with data {str(data)}")
    
    def generate(user_id=None):
        """Generator that yields Server-Sent Events"""
        # Create a queue to receive progress updates
        progress_queue = queue.Queue()
        logger.info(f"Created queue...")
        
        # Validate input first
        name = data.get('name')
        description = data.get('description', '')

        logger.info(f"Processing env {name} for user {user_id}")
        
        if not name:
            logger.error(f"Name is required")
            yield f"data: {json.dumps({'step': 'Error', 'progress': 0, 'message': 'Name is required', 'error': True})}\n\n"
            return
        
        tenant_id = os.getenv('API_KEY')
        manager = get_manager(tenant_id)
        
        # Run create_environment in a separate thread so we can stream progress
        result = {'success': False, 'env_id': None, 'message': None}

        def run_creation():
            print(f"Running create_environment_with_progress...")
            print(f"User: {user_id}")

            def progress_callback(step, progress, message):
                """Callback that puts progress updates into the queue"""
                progress_queue.put({
                    'step': step,
                    'progress': progress,
                    'message': message
                })

            try:
                success, env_id, message = manager.create_environment_with_progress(
                    name=name,
                    description=description,
                    created_by=user_id,
                    progress_callback=progress_callback
                )
                logger.info(f"Returned from create_environment_with_progress: {result}")  
                result['success'] = success
                result['env_id'] = env_id
                result['message'] = message
                # Signal completion
                progress_queue.put(None)
            except Exception as e:
                logger.error(f"Error in creation thread: {e}", exc_info=True)
                result['message'] = str(e)
                result['success'] = False
            finally:
                logger.info("Thread: About to put None in queue")  
                progress_queue.put(None)
                logger.info("Thread: None has been put in queue")  
        
        logger.info(f"Creating thread...")
        # Start the creation in a background thread
        creation_thread = threading.Thread(target=run_creation)
        creation_thread.start()
        logger.info(f"Thread started...")

        # Stream progress updates as they come in
        STOP_TRIGGERED = False
        while not STOP_TRIGGERED:
            try:
                if STOP_TRIGGERED:
                    logger.info(f"Stop detected, exiting.")
                    break
                logger.info(f"Waiting for update...")
                # Wait for progress updates (timeout prevents hanging)
                update = progress_queue.get(timeout=650)
                progress = update.get('progress', 0)
                
                if update is None or progress == 100:
                    STOP_TRIGGERED = True
                    creation_thread.join(timeout=1.0)
                    logger.info(f"No update or 100%... result: {result}")
                    # Creation is complete
                    if result.get('success', False):
                        logger.info(f"Success.")
                        yield f"data: {json.dumps({'step': 'Complete', 'progress': 100, 'message': result['message'], 'env_id': result['env_id'], 'complete': True})}\n\n"
                        logger.info(f"Stop flag set on success.")
                        break
                    else:
                        logger.info(f"Error.")
                        yield f"data: {json.dumps({'step': 'Error', 'progress': 100, 'message': result['message'], 'error': True})}\n\n"
                        logger.info(f"Stop flag set on error.")
                        break
                    break
                else:
                    logger.info(f"Update: {str(update)}")
                    # Yield the progress update
                    yield f"data: {json.dumps(update)}\n\n"
                    
            except queue.Empty:
                # Timeout - check if thread is still alive
                if not creation_thread.is_alive():
                    yield f"data: {json.dumps({'step': 'Error', 'progress': 0, 'message': 'Creation process ended unexpectedly', 'error': True})}\n\n"
                    break
                # Send a keepalive
                yield ": keepalive\n\n"
        
        # Ensure thread completes
        creation_thread.join(timeout=5)
    
    logger.info(f"Starting stream for user {user_id}...")
    return Response(
        stream_with_context(generate(user_id=user_id)),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )