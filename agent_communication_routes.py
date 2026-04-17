# agent_communication_routes.py

from flask import Blueprint, jsonify, request
from flask_login import login_required
import json
from CommonUtils import get_db_connection_string
import pyodbc
from datetime import datetime
import os

agent_comm_bp = Blueprint('agent_comm', __name__)

@agent_comm_bp.route('/api/agent/communications/history', methods=['GET'])
@login_required
def get_agent_communications():
    """Get communication history between agents"""
    try:
        agent_id = request.args.get('agent_id', type=int)
        limit = request.args.get('limit', 50, type=int)
        
        conn_str = get_db_connection_string()
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()

        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        query = """
        SELECT TOP (?) 
            c.request_id,
            c.from_agent_id,
            a1.description as from_agent_name,
            c.to_agent_id,
            a2.description as to_agent_name,
            c.message,
            c.context,
            c.response,
            c.status,
            c.execution_time_ms,
            c.created_date,
            c.completed_date
        FROM [dbo].[AgentCommunications] c
        JOIN [dbo].[Agents] a1 ON c.from_agent_id = a1.id
        JOIN [dbo].[Agents] a2 ON c.to_agent_id = a2.id
        WHERE 1=1
        """
        
        params = [limit]
        
        if agent_id:
            query += " AND (c.from_agent_id = ? OR c.to_agent_id = ?)"
            params.extend([agent_id, agent_id])
        
        query += " ORDER BY c.created_date DESC"
        
        cursor.execute(query, params)
        
        communications = []
        for row in cursor.fetchall():
            communications.append({
                'request_id': str(row.request_id),
                'from_agent': {
                    'id': row.from_agent_id,
                    'name': row.from_agent_name
                },
                'to_agent': {
                    'id': row.to_agent_id,
                    'name': row.to_agent_name
                },
                'message': row.message,
                'context': json.loads(row.context) if row.context else None,
                'response': json.loads(row.response) if row.response else None,
                'status': row.status,
                'execution_time_ms': row.execution_time_ms,
                'created_date': row.created_date.isoformat() if row.created_date else None,
                'completed_date': row.completed_date.isoformat() if row.completed_date else None
            })
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'communications': communications,
            'count': len(communications)
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@agent_comm_bp.route('/api/agent/workflows', methods=['GET'])
@login_required
def get_agent_workflows():
    """Get saved agent workflows"""
    try:
        conn_str = get_db_connection_string()
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()

        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        cursor.execute("""
        SELECT 
            w.id,
            w.workflow_id,
            w.name,
            w.description,
            w.workflow_definition,
            w.created_by,
            u.name as created_by_name,
            w.created_date,
            w.last_executed_date,
            w.execution_count
        FROM [dbo].[AgentWorkflows] w
        JOIN [dbo].[User] u ON w.created_by = u.id
        ORDER BY w.created_date DESC
        """)
        
        workflows = []
        for row in cursor.fetchall():
            workflows.append({
                'id': row.id,
                'workflow_id': str(row.workflow_id),
                'name': row.name,
                'description': row.description,
                'workflow_definition': json.loads(row.workflow_definition),
                'created_by': {
                    'id': row.created_by,
                    'name': row.created_by_name
                },
                'created_date': row.created_date.isoformat(),
                'last_executed_date': row.last_executed_date.isoformat() if row.last_executed_date else None,
                'execution_count': row.execution_count
            })
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'workflows': workflows
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@agent_comm_bp.route('/api/agent/workflows', methods=['POST'])
@login_required
def create_agent_workflow():
    """Create a new agent workflow"""
    try:
        data = request.get_json()

        try:
            current_user_id = current_user.id
        except:
            current_user_id = 'unknown'
        
        conn_str = get_db_connection_string()
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", os.getenv('API_KEY'))
        
        cursor.execute("""
        INSERT INTO [dbo].[AgentWorkflows] 
        (workflow_id, name, description, workflow_definition, created_by)
        VALUES (NEWID(), ?, ?, ?, ?)
        """, [
            data.get('name', 'Unnamed Workflow'),
            data.get('description', ''),
            json.dumps(data['workflow_definition']),
            current_user_id
        ])
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({'status': 'success', 'message': 'Workflow created successfully'})
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@agent_comm_bp.route('/api/agent/communication/test', methods=['POST'])
@login_required
def test_agent_communication():
    """Test communication between two agents"""
    try:
        data = request.get_json()
        from_agent_id = data['from_agent_id']
        to_agent_id = data['to_agent_id']
        message = data['message']
        context = data.get('context', {})
        
        # Import what we need
        from agent_communication_tool import _execute_agent_task, AGENT_REGISTRY, log_communication_start, log_communication_complete
        from GeneralAgent import GeneralAgent
        import json
        import uuid
        from datetime import datetime

        # Create communication request
        request_id = str(uuid.uuid4())
        start_time = datetime.now()

        # Log the communication attempt to database
        comm_id = log_communication_start(
            request_id=request_id,
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            message=message,
            context=context
        )
        
        # Make sure both agents are loaded and registered
        # Load the source agent if not already in registry
        if from_agent_id not in AGENT_REGISTRY:
            try:
                source_agent = GeneralAgent(agent_id=from_agent_id)
                # The agent should auto-register itself if the communication tool is enabled
            except Exception as e:
                error_msg = f"Failed to load source agent {from_agent_id}: {str(e)}"
                log_communication_complete(
                    comm_id=comm_id,
                    status='error',
                    error_message=error_msg,
                    execution_time_ms=int((datetime.now() - start_time).total_seconds() * 1000)
                )
                return jsonify({
                    'status': 'error',
                    'message': error_msg
                }), 500
        
        # Load the target agent if not already in registry
        if to_agent_id not in AGENT_REGISTRY:
            try:
                target_agent = GeneralAgent(agent_id=to_agent_id)
            except Exception as e:
                error_msg = f'Failed to load target agent {to_agent_id}: {str(e)}'
                log_communication_complete(
                    comm_id=comm_id,
                    status='error',
                    error_message=error_msg,
                    execution_time_ms=int((datetime.now() - start_time).total_seconds() * 1000)
                )
                return jsonify({
                    'status': 'error',
                    'message': error_msg
                }), 500
        
        # Check if target agent exists in registry
        if to_agent_id not in AGENT_REGISTRY:
            error_msg = f'Agent {to_agent_id} is not currently active'
            log_communication_complete(
                comm_id=comm_id,
                status='error',
                error_message=error_msg,
                execution_time_ms=int((datetime.now() - start_time).total_seconds() * 1000)
            )
            return jsonify({
                'status': 'error',
                'message': error_msg
            }), 404
        
        target_agent_info = AGENT_REGISTRY[to_agent_id]
        
        # Check if target agent is enabled
        if not target_agent_info.get('enabled', True):
            error_msg = f'Agent {to_agent_id} is currently disabled'
            log_communication_complete(
                comm_id=comm_id,
                status='error',
                error_message=error_msg,
                execution_time_ms=int((datetime.now() - start_time).total_seconds() * 1000)
            )
            return jsonify({
                'status': 'error',
                'message': error_msg
            }), 400
        
        # Execute the agent task directly
        try:
            response = _execute_agent_task(target_agent_info, message, context)
            execution_time_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            log_communication_complete(
                comm_id=comm_id,
                status='success',
                response=response,
                execution_time_ms=execution_time_ms
            )
            
            result = {
                'status': 'success',
                'request_id': request_id,
                'from_agent': to_agent_id,
                'response': response,
                'execution_time': f'{execution_time_ms}ms'
            }
            
            return jsonify({
                'status': 'success',
                'result': result
            })
            
        except Exception as e:
            error_msg = f'Agent communication failed: {str(e)}'
            log_communication_complete(
                comm_id=comm_id,
                status='error',
                error_message=error_msg,
                execution_time_ms=int((datetime.now() - start_time).total_seconds() * 1000)
            )
            return jsonify({
                'status': 'error',
                'message': error_msg
            }), 500
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': str(e)}), 500

# Add to your main app.py:
# from agent_communication_routes import agent_comm_bp
# app.register_blueprint(agent_comm_bp)
