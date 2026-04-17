"""
email_processing_routes.py - Routes for Email Processing History

Provides API endpoints and UI pages for viewing email processing history.
This complements the email_agent_dispatcher.py service.
"""

from flask import Blueprint, request, jsonify, render_template
from flask_login import login_required
from role_decorators import api_key_or_session_required
import logging
import os
from datetime import datetime, timedelta
from CommonUtils import get_db_connection, get_executor_api_base_url

logger = logging.getLogger(__name__)

# Create blueprint
email_processing_bp = Blueprint('email_processing', __name__)


def safe_isoformat(value):
    """
    Safely convert a datetime value to ISO format string.
    Handles both datetime objects and strings (some DB drivers return strings).
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value  # Already a string
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return str(value)


def get_tenant_context():
    """Get database connection with tenant context set."""
    conn = get_db_connection()
    cursor = conn.cursor()
    api_key = os.environ.get('API_KEY', '')
    cursor.execute("EXEC tenant.sp_setTenantContext ?", (api_key,))
    return conn, cursor


# =============================================================================
# UI Routes
# =============================================================================

@email_processing_bp.route('/email-processing/history')
@api_key_or_session_required(min_role=2)
def email_processing_history_page():
    """Render the email processing history page."""
    return render_template('email_processing_history.html')


@email_processing_bp.route('/email-processing/history/<int:agent_id>')
@api_key_or_session_required(min_role=2)
def agent_email_processing_history_page(agent_id):
    """Render the email processing history page for a specific agent."""
    return render_template('email_processing_history.html', agent_id=agent_id)


# =============================================================================
# API Routes
# =============================================================================

@email_processing_bp.route('/api/email-processing/history', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_processing_history():
    """
    Get email processing history.
    
    Query params:
        agent_id: Optional - filter by agent
        status: Optional - filter by status (completed, failed, skipped)
        type: Optional - filter by processing type
        days: Optional - number of days to look back (default: 7)
        limit: Optional - max records (default: 100)
        offset: Optional - pagination offset (default: 0)
    """
    try:
        conn, cursor = get_tenant_context()
        
        # Parse query parameters
        agent_id = request.args.get('agent_id', type=int)
        status = request.args.get('status')
        proc_type = request.args.get('type')
        days = request.args.get('days', 7, type=int)
        limit = min(request.args.get('limit', 100, type=int), 500)
        offset = request.args.get('offset', 0, type=int)
        
        # Build query
        where_clauses = ["pe.processed_at >= ?"]
        params = [datetime.now() - timedelta(days=days)]
        
        if agent_id:
            where_clauses.append("pe.agent_id = ?")
            params.append(agent_id)
        
        if status:
            where_clauses.append("pe.processing_status = ?")
            params.append(status)
        
        if proc_type:
            where_clauses.append("pe.processing_type = ?")
            params.append(proc_type)
        
        where_sql = " AND ".join(where_clauses)
        
        # Get total count
        count_sql = f"""
            SELECT COUNT(*) 
            FROM AgentProcessedEmails pe
            WHERE {where_sql}
        """
        cursor.execute(count_sql, params)
        total_count = cursor.fetchone()[0]
        
        # Get records
        query_sql = f"""
            SELECT 
                pe.id,
                pe.agent_id,
                a.description as agent_name,
                pe.event_id,
                pe.sender_email,
                pe.sender_name,
                pe.subject,
                pe.received_at,
                pe.processed_at,
                pe.processing_type,
                pe.processing_status,
                pe.response_message_id,
                pe.workflow_execution_id,
                pe.error_message,
                pe.processing_duration_ms,
                pe.retry_count
            FROM AgentProcessedEmails pe
            LEFT JOIN Agents a ON pe.agent_id = a.id
            WHERE {where_sql}
            ORDER BY pe.processed_at DESC
            OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
        """
        params.extend([offset, limit])
        
        cursor.execute(query_sql, params)
        
        records = []
        for row in cursor.fetchall():
            records.append({
                'id': row[0],
                'agent_id': row[1],
                'agent_name': row[2] or f'Agent {row[1]}',
                'event_id': row[3],
                'sender_email': row[4],
                'sender_name': row[5],
                'subject': row[6],
                'received_at': safe_isoformat(row[7]),
                'processed_at': safe_isoformat(row[8]),
                'processing_type': row[9],
                'processing_status': row[10],
                'response_message_id': row[11],
                'workflow_execution_id': row[12],
                'error_message': row[13],
                'processing_duration_ms': row[14],
                'retry_count': row[15]
            })
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'records': records,
            'total_count': total_count,
            'limit': limit,
            'offset': offset,
            'has_more': offset + len(records) < total_count
        })
        
    except Exception as e:
        logger.error(f"Error getting processing history: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@email_processing_bp.route('/api/email-processing/stats', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_processing_stats():
    """
    Get email processing statistics.
    
    Query params:
        agent_id: Optional - filter by agent
        days: Optional - number of days (default: 7)
    """
    try:
        conn, cursor = get_tenant_context()
        
        agent_id = request.args.get('agent_id', type=int)
        days = request.args.get('days', 7, type=int)
        
        cutoff = datetime.now() - timedelta(days=days)
        
        # Base WHERE clause
        where_base = "processed_at >= ?"
        params_base = [cutoff]
        
        if agent_id:
            where_base += " AND agent_id = ?"
            params_base.append(agent_id)
        
        # Get counts by status
        cursor.execute(f"""
            SELECT 
                processing_status,
                COUNT(*) as count
            FROM AgentProcessedEmails
            WHERE {where_base}
            GROUP BY processing_status
        """, params_base)
        
        status_counts = {}
        for row in cursor.fetchall():
            status_counts[row[0]] = row[1]
        
        # Get counts by type
        cursor.execute(f"""
            SELECT 
                processing_type,
                COUNT(*) as count
            FROM AgentProcessedEmails
            WHERE {where_base}
            GROUP BY processing_type
        """, params_base)
        
        type_counts = {}
        for row in cursor.fetchall():
            type_counts[row[0]] = row[1]
        
        # Get daily counts for chart
        cursor.execute(f"""
            SELECT 
                CAST(processed_at AS DATE) as day,
                processing_status,
                COUNT(*) as count
            FROM AgentProcessedEmails
            WHERE {where_base}
            GROUP BY CAST(processed_at AS DATE), processing_status
            ORDER BY day
        """, params_base)
        
        daily_data = {}
        for row in cursor.fetchall():
            day_str = safe_isoformat(row[0]) if row[0] else 'Unknown'
            if day_str not in daily_data:
                daily_data[day_str] = {'completed': 0, 'failed': 0, 'skipped': 0}
            daily_data[day_str][row[1]] = row[2]
        
        # Get average processing time
        cursor.execute(f"""
            SELECT AVG(processing_duration_ms)
            FROM AgentProcessedEmails
            WHERE {where_base} AND processing_duration_ms IS NOT NULL
        """, params_base)
        
        avg_duration = cursor.fetchone()[0]
        
        # Get agent breakdown
        cursor.execute(f"""
            SELECT 
                pe.agent_id,
                a.description as agent_name,
                COUNT(*) as total,
                SUM(CASE WHEN pe.processing_status = 'completed' THEN 1 ELSE 0 END) as completed,
                SUM(CASE WHEN pe.processing_status = 'failed' THEN 1 ELSE 0 END) as failed
            FROM AgentProcessedEmails pe
            LEFT JOIN Agents a ON pe.agent_id = a.id
            WHERE pe.processed_at >= ?
            GROUP BY pe.agent_id, a.description
            ORDER BY total DESC
        """, [cutoff])
        
        agent_breakdown = []
        for row in cursor.fetchall():
            agent_breakdown.append({
                'agent_id': row[0],
                'agent_name': row[1] or f'Agent {row[0]}',
                'total': row[2],
                'completed': row[3],
                'failed': row[4]
            })
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'period_days': days,
            'status_counts': status_counts,
            'type_counts': type_counts,
            'daily_data': daily_data,
            'avg_processing_time_ms': int(avg_duration) if avg_duration else None,
            'agent_breakdown': agent_breakdown,
            'total_processed': sum(status_counts.values())
        })
        
    except Exception as e:
        logger.error(f"Error getting processing stats: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@email_processing_bp.route('/api/email-processing/record/<int:record_id>', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_processing_record(record_id):
    """Get details of a specific processing record."""
    try:
        conn, cursor = get_tenant_context()
        
        cursor.execute("""
            SELECT 
                pe.*,
                a.description as agent_name
            FROM AgentProcessedEmails pe
            LEFT JOIN Agents a ON pe.agent_id = a.id
            WHERE pe.id = ?
        """, (record_id,))
        
        row = cursor.fetchone()
        
        if not row:
            return jsonify({'success': False, 'error': 'Record not found'}), 404
        
        columns = [desc[0] for desc in cursor.description]
        record = dict(zip(columns, row))
        
        # Convert datetime fields
        for key in ['received_at', 'processed_at', 'acknowledged_at']:
            if record.get(key):
                record[key] = safe_isoformat(record[key])
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'record': record
        })
        
    except Exception as e:
        logger.error(f"Error getting processing record: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@email_processing_bp.route('/api/email-processing/retry/<int:record_id>', methods=['POST'])
@api_key_or_session_required(min_role=2)
def retry_processing(record_id):
    """Retry a failed processing record."""
    try:
        conn, cursor = get_tenant_context()
        
        # Get the record
        cursor.execute("""
            SELECT agent_id, event_id, message_key, processing_type, retry_count
            FROM AgentProcessedEmails
            WHERE id = ? AND processing_status = 'failed'
        """, (record_id,))
        
        row = cursor.fetchone()
        
        if not row:
            return jsonify({
                'success': False, 
                'error': 'Record not found or not in failed status'
            }), 404
        
        agent_id, event_id, message_key, proc_type, retry_count = row
        
        # Update retry count
        cursor.execute("""
            UPDATE AgentProcessedEmails
            SET retry_count = retry_count + 1,
                processing_status = 'pending'
            WHERE id = ?
        """, (record_id,))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        # TODO: Actually re-trigger the processing
        # This would require fetching the email again and re-processing
        
        return jsonify({
            'success': True,
            'message': 'Record queued for retry',
            'retry_count': retry_count + 1
        })
        
    except Exception as e:
        logger.error(f"Error retrying processing: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@email_processing_bp.route('/api/email-processing/dispatcher/status', methods=['GET'])
@api_key_or_session_required(min_role=2)
def get_dispatcher_status():
    """Get the status of the email dispatcher service (via executor service)."""
    try:
        import requests
        
        executor_url = get_executor_api_base_url()
        response = requests.get(f"{executor_url}/api/email-dispatcher/status", timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            return jsonify({
                'success': True,
                'running': data.get('running', False),
                'enabled': data.get('enabled', False),
                'started_at': data.get('started_at'),
                'last_poll': data.get('last_poll'),
                'total_polls': data.get('total_polls', 0),
                'total_processed': data.get('total_processed', 0),
                'total_errors': data.get('total_errors', 0),
                'poll_interval': data.get('poll_interval', 30)
            })
        else:
            return jsonify({
                'success': True,
                'running': False,
                'message': 'Executor service not available'
            })
        
    except requests.exceptions.ConnectionError:
        return jsonify({
            'success': True,
            'running': False,
            'message': 'Executor service not running'
        })
    except Exception as e:
        logger.error(f"Error getting dispatcher status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@email_processing_bp.route('/api/email-processing/dispatcher/start', methods=['POST'])
@api_key_or_session_required(min_role=2)
def start_dispatcher():
    """Start the email dispatcher service (via executor service)."""
    try:
        import requests
        
        executor_url = get_executor_api_base_url()
        response = requests.post(f"{executor_url}/api/email-dispatcher/start", timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            return jsonify({
                'success': True,
                'message': data.get('message', 'Dispatcher started'),
                'stats': data.get('stats', {})
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to start dispatcher'
            }), 500
        
    except requests.exceptions.ConnectionError:
        return jsonify({
            'success': False,
            'error': 'Executor service not running. Start the executor service first.'
        }), 503
    except Exception as e:
        logger.error(f"Error starting dispatcher: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@email_processing_bp.route('/api/email-processing/dispatcher/stop', methods=['POST'])
@api_key_or_session_required(min_role=2)
def stop_dispatcher():
    """Stop the email dispatcher service (via executor service)."""
    try:
        import requests
        
        executor_url = get_executor_api_base_url()
        response = requests.post(f"{executor_url}/api/email-dispatcher/stop", timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            return jsonify({
                'success': True,
                'message': data.get('message', 'Dispatcher stopped')
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to stop dispatcher'
            }), 500
        
    except requests.exceptions.ConnectionError:
        return jsonify({
            'success': False,
            'error': 'Executor service not running'
        }), 503
    except Exception as e:
        logger.error(f"Error stopping dispatcher: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
