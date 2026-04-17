"""
Local History API Routes for AI Hub
====================================

API endpoints for managing conversation history stored locally.
All data stays on the local machine - never transmitted to cloud.

Routes:
    GET  /api/history/conversations              - List recent conversations
    GET  /api/history/conversations/<id>         - Get conversation with messages
    POST /api/history/conversations              - Create new conversation
    PUT  /api/history/conversations/<id>         - Update conversation (title, pin)
    DELETE /api/history/conversations/<id>       - Delete conversation
    
    GET  /api/history/queries                    - List tracked queries
    PUT  /api/history/queries/<id>               - Update query (pin)
    DELETE /api/history/queries/<id>             - Delete query
    POST /api/history/queries/<id>/run           - Re-run a query
    
    GET  /api/history/dashboard                  - Dashboard summary data
    GET  /api/history/storage                    - Storage info
    POST /api/history/export                     - Export history
    POST /api/history/clear                      - Clear all history
    POST /api/history/prune                      - Prune old conversations
"""

from flask import Blueprint, request, jsonify, g
from flask_login import login_required, current_user
from datetime import datetime, timezone, timedelta
from typing import Any
import logging

logger = logging.getLogger(__name__)

# Create blueprint
history_bp = Blueprint('history', __name__, url_prefix='/api/history')

# Default staleness threshold in minutes
DEFAULT_CONVERSATION_STALE_MINUTES = 30


# =============================================================================
# Preferences Integration
# =============================================================================

def get_history_preference(preference_key: str, default_value=None):
    """
    Get a history-related preference for the current user.
    Uses the application's preferences system.
    
    Args:
        preference_key: The preference key (e.g., 'history_enabled')
        default_value: Default if preference not found
        
    Returns:
        The preference value with proper type conversion
    """
    try:
        from preferences_routes import get_preference
        return get_preference(current_user.id, preference_key, default_value)
    except ImportError:
        logger.warning("preferences_routes not available, using default")
        return default_value
    except Exception as e:
        logger.error(f"Error getting preference {preference_key}: {e}")
        return default_value


def is_history_enabled() -> bool:
    """
    Check if conversation history is enabled for the current user.
    Uses the 'history_enabled' preference.
    
    Returns:
        True if history is enabled, False otherwise
    """
    return get_history_preference('history_enabled', True)


def get_history_settings() -> dict:
    """
    Get all history-related settings for the current user.
    
    Returns:
        Dict with all history settings
    """
    return {
        'enabled': get_history_preference('history_enabled', True),
        'retention_days': int(get_history_preference('history_retention_days', 90) or 90),
        'save_queries': get_history_preference('history_save_queries', True),
        'max_conversations': int(get_history_preference('history_max_conversations', 100) or 100),
        'show_on_dashboard': get_history_preference('history_show_on_dashboard', True),
    }


def get_history_manager():
    """
    Get or create history manager for this request.
    Applies user's preference settings.
    """
    if not hasattr(g, 'history_manager'):
        from local_history import get_history_manager as get_manager
        manager = get_manager()
        
        # Apply user's settings to manager
        try:
            settings = get_history_settings()
            manager.update_settings(
                retention_days=settings['retention_days'],
                auto_save_queries=settings['save_queries'],
                max_conversations_per_agent=settings['max_conversations']
            )
        except Exception as e:
            logger.warning(f"Could not apply user settings to history manager: {e}")
        
        g.history_manager = manager
    
    return g.history_manager


# =============================================================================
# Conversation Routes
# =============================================================================

@history_bp.route('/conversations', methods=['GET'])
@login_required
def list_conversations():
    """
    List recent conversations.
    
    Query params:
        agent_id: Filter by agent (optional)
        limit: Max results (default 20)
    """
    if not is_history_enabled():
        return jsonify({'conversations': [], 'enabled': False})
    
    try:
        manager = get_history_manager()
        
        agent_id = request.args.get('agent_id', type=int)
        limit = request.args.get('limit', 20, type=int)
        
        conversations = manager.get_recent_conversations(
            agent_id=agent_id,
            user_id=current_user.id,
            limit=min(limit, 100)  # Cap at 100
        )
        
        return jsonify({
            'conversations': [c.to_dict() for c in conversations],
            'enabled': True
        })
        
    except Exception as e:
        logger.error(f"Error listing conversations: {e}")
        return jsonify({'error': str(e)}), 500


@history_bp.route('/conversations/<conversation_id>', methods=['GET'])
@login_required
def get_conversation(conversation_id):
    """Get a single conversation with all messages."""
    if not is_history_enabled():
        return jsonify({'error': 'History disabled'}), 400
    
    try:
        manager = get_history_manager()
        conversation = manager.get_conversation(conversation_id)
        
        if not conversation:
            return jsonify({'error': 'Conversation not found'}), 404
        
        # Verify user owns this conversation
        if conversation.get('user_id') != current_user.id and current_user.role < 3:
            return jsonify({'error': 'Access denied'}), 403
        
        return jsonify(conversation)
        
    except Exception as e:
        logger.error(f"Error getting conversation {conversation_id}: {e}")
        return jsonify({'error': str(e)}), 500


@history_bp.route('/conversations', methods=['POST'])
@login_required
def create_conversation():
    """
    Create a new conversation.
    
    Body:
        agent_id: Required agent ID
        title: Optional title
        tags: Optional list of tags
    """
    if not is_history_enabled():
        return jsonify({'error': 'History disabled'}), 400
    
    try:
        data = request.get_json() or {}
        
        agent_id = data.get('agent_id')
        if not agent_id:
            return jsonify({'error': 'agent_id required'}), 400
        
        manager = get_history_manager()
        conversation_id = manager.create_conversation(
            agent_id=agent_id,
            user_id=current_user.id,
            title=data.get('title'),
            tags=data.get('tags')
        )
        
        return jsonify({
            'conversation_id': conversation_id,
            'message': 'Conversation created'
        }), 201
        
    except Exception as e:
        logger.error(f"Error creating conversation: {e}")
        return jsonify({'error': str(e)}), 500


@history_bp.route('/conversations/<conversation_id>', methods=['PUT'])
@login_required
def update_conversation(conversation_id):
    """
    Update a conversation.
    
    Body:
        title: New title (optional)
        is_pinned: Pin status (optional)
    """
    if not is_history_enabled():
        return jsonify({'error': 'History disabled'}), 400
    
    try:
        manager = get_history_manager()
        
        # Verify conversation exists and user owns it
        conversation = manager.get_conversation(conversation_id)
        if not conversation:
            return jsonify({'error': 'Conversation not found'}), 404
        
        if conversation.get('user_id') != current_user.id and current_user.role < 3:
            return jsonify({'error': 'Access denied'}), 403
        
        data = request.get_json() or {}
        
        if 'title' in data:
            manager.update_conversation_title(conversation_id, data['title'])
        
        if 'is_pinned' in data:
            manager.pin_conversation(conversation_id, data['is_pinned'])
        
        return jsonify({'message': 'Conversation updated'})
        
    except Exception as e:
        logger.error(f"Error updating conversation {conversation_id}: {e}")
        return jsonify({'error': str(e)}), 500


@history_bp.route('/conversations/<conversation_id>', methods=['DELETE'])
@login_required
def delete_conversation(conversation_id):
    """Delete a conversation."""
    if not is_history_enabled():
        return jsonify({'error': 'History disabled'}), 400
    
    try:
        manager = get_history_manager()
        
        # Verify conversation exists and user owns it
        conversation = manager.get_conversation(conversation_id)
        if not conversation:
            return jsonify({'error': 'Conversation not found'}), 404
        
        if conversation.get('user_id') != current_user.id and current_user.role < 3:
            return jsonify({'error': 'Access denied'}), 403
        
        manager.delete_conversation(conversation_id)
        
        return jsonify({'message': 'Conversation deleted'})
        
    except Exception as e:
        logger.error(f"Error deleting conversation {conversation_id}: {e}")
        return jsonify({'error': str(e)}), 500


@history_bp.route('/conversations/<conversation_id>/messages', methods=['POST'])
@login_required
def add_message(conversation_id):
    """
    Add a message to a conversation.
    
    Body:
        role: 'user' or 'assistant'
        content: Message content (string or dict for rich content)
        content_type: 'text', 'rich_content', or 'json' (optional, auto-detected)
        tool_calls: Optional tool calls
        tokens: Optional token counts
    """
    if not is_history_enabled():
        return jsonify({'error': 'History disabled'}), 400
    
    try:
        manager = get_history_manager()
        
        # Verify conversation exists
        conversation = manager.get_conversation(conversation_id)
        if not conversation:
            return jsonify({'error': 'Conversation not found'}), 404
        
        data = request.get_json() or {}
        
        if 'role' not in data or 'content' not in data:
            return jsonify({'error': 'role and content required'}), 400
        
        message_id = manager.add_message(
            conversation_id=conversation_id,
            role=data['role'],
            content=data['content'],
            content_type=data.get('content_type'),
            tool_calls=data.get('tool_calls'),
            tokens=data.get('tokens'),
            attachments=data.get('attachments')
        )
        
        return jsonify({
            'message_id': message_id,
            'message': 'Message added'
        }), 201
        
    except Exception as e:
        logger.error(f"Error adding message to {conversation_id}: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# Query Routes
# =============================================================================

@history_bp.route('/queries', methods=['GET'])
@login_required
def list_queries():
    """
    List tracked queries.
    
    Query params:
        agent_id: Filter by agent (optional)
        type: 'frequent', 'recent', or 'pinned' (default 'frequent')
        limit: Max results (default 20)
    """
    if not is_history_enabled():
        return jsonify({'queries': [], 'enabled': False})
    
    try:
        manager = get_history_manager()
        
        agent_id = request.args.get('agent_id', type=int)
        query_type = request.args.get('type', 'frequent')
        limit = request.args.get('limit', 20, type=int)
        
        if query_type == 'pinned':
            queries = manager.get_pinned_queries(
                agent_id=agent_id,
                user_id=current_user.id
            )
        elif query_type == 'recent':
            queries = manager.get_recent_queries(
                agent_id=agent_id,
                user_id=current_user.id,
                limit=min(limit, 100)
            )
        else:  # frequent
            queries = manager.get_frequent_queries(
                agent_id=agent_id,
                user_id=current_user.id,
                limit=min(limit, 100)
            )
        
        return jsonify({
            'queries': [q.to_dict() for q in queries],
            'enabled': True
        })
        
    except Exception as e:
        logger.error(f"Error listing queries: {e}")
        return jsonify({'error': str(e)}), 500


@history_bp.route('/queries/<query_id>', methods=['PUT'])
@login_required
def update_query(query_id):
    """
    Update a query (pin/unpin).
    
    Body:
        is_pinned: Pin status
    """
    if not is_history_enabled():
        return jsonify({'error': 'History disabled'}), 400
    
    try:
        manager = get_history_manager()
        data = request.get_json() or {}
        
        if 'is_pinned' in data:
            manager.pin_query(query_id, data['is_pinned'])
        
        return jsonify({'message': 'Query updated'})
        
    except Exception as e:
        logger.error(f"Error updating query {query_id}: {e}")
        return jsonify({'error': str(e)}), 500


@history_bp.route('/queries/<query_id>', methods=['DELETE'])
@login_required
def delete_query(query_id):
    """Delete a tracked query."""
    if not is_history_enabled():
        return jsonify({'error': 'History disabled'}), 400
    
    try:
        manager = get_history_manager()
        manager.delete_query(query_id)
        
        return jsonify({'message': 'Query deleted'})
        
    except Exception as e:
        logger.error(f"Error deleting query {query_id}: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# Dashboard & Utility Routes
# =============================================================================

@history_bp.route('/dashboard', methods=['GET'])
@login_required
def dashboard_data():
    """
    Get history data formatted for dashboard display.
    
    Returns recent conversations and queries for the current user.
    Respects 'history_show_on_dashboard' preference.
    """
    settings = get_history_settings()
    
    if not settings['enabled'] or not settings['show_on_dashboard']:
        return jsonify({
            'enabled': False,
            'conversations': [],
            'queries': []
        })
    
    try:
        manager = get_history_manager()
        
        conversations = manager.get_conversations_for_dashboard(
            user_id=current_user.id,
            limit=5
        )
        
        queries = []
        if settings['save_queries']:
            queries = manager.get_queries_for_dashboard(
                user_id=current_user.id,
                limit=5
            )
        
        return jsonify({
            'enabled': True,
            'conversations': conversations,
            'queries': queries
        })
        
    except Exception as e:
        logger.error(f"Error getting dashboard data: {e}")
        return jsonify({'error': str(e)}), 500


@history_bp.route('/settings', methods=['GET'])
@login_required
def get_settings():
    """Get current history settings for the user."""
    try:
        settings = get_history_settings()
        return jsonify({
            'status': 'success',
            'settings': settings
        })
    except Exception as e:
        logger.error(f"Error getting history settings: {e}")
        return jsonify({'error': str(e)}), 500


@history_bp.route('/storage', methods=['GET'])
@login_required
def storage_info():
    """Get storage information about history."""
    try:
        manager = get_history_manager()
        info = manager.get_storage_info()
        
        # Add user's settings to response
        info['user_settings'] = get_history_settings()
        
        return jsonify(info)
        
    except Exception as e:
        logger.error(f"Error getting storage info: {e}")
        return jsonify({'error': str(e)}), 500


@history_bp.route('/export', methods=['POST'])
@login_required
def export_history():
    """
    Export history to file.
    
    Body:
        agent_id: Optional filter by agent
        include_messages: Include full messages (default true)
    """
    try:
        manager = get_history_manager()
        data = request.get_json() or {}
        
        export_path = manager.export_history(
            agent_id=data.get('agent_id'),
            include_messages=data.get('include_messages', True)
        )
        
        return jsonify({
            'message': 'History exported',
            'path': export_path
        })
        
    except Exception as e:
        logger.error(f"Error exporting history: {e}")
        return jsonify({'error': str(e)}), 500


@history_bp.route('/clear', methods=['POST'])
@login_required
def clear_history():
    """
    Clear all history.
    
    Body:
        confirm: Must be true to proceed
    """
    try:
        data = request.get_json() or {}
        
        if not data.get('confirm'):
            return jsonify({'error': 'Confirmation required'}), 400
        
        manager = get_history_manager()
        manager.clear_all_history(confirm=True)
        
        return jsonify({'message': 'History cleared'})
        
    except Exception as e:
        logger.error(f"Error clearing history: {e}")
        return jsonify({'error': str(e)}), 500


@history_bp.route('/prune', methods=['POST'])
@login_required
def prune_history():
    """
    Prune old conversations based on retention settings.
    
    Body:
        days: Override retention days (optional, uses user preference if not provided)
    """
    try:
        manager = get_history_manager()
        data = request.get_json() or {}
        
        # Use user's preference if days not specified
        days = data.get('days')
        if days is None:
            settings = get_history_settings()
            days = settings['retention_days']
        
        removed = manager.prune_old_conversations(days=days)
        
        return jsonify({
            'message': f'Pruned {removed} conversations',
            'removed': removed
        })
        
    except Exception as e:
        logger.error(f"Error pruning history: {e}")
        return jsonify({'error': str(e)}), 500


@history_bp.route('/cleanup-empty', methods=['POST'])
@login_required
def cleanup_empty_conversations():
    """
    Delete conversations that have no messages.
    Useful for cleaning up conversations that were created but never used.
    
    Body:
        agent_id: Optional - only cleanup for specific agent
    """
    try:
        manager = get_history_manager()
        data = request.get_json() or {}
        
        agent_id = data.get('agent_id')
        
        removed = manager.delete_empty_conversations(
            user_id=current_user.id,
            agent_id=agent_id
        )
        
        return jsonify({
            'message': f'Removed {removed} empty conversations',
            'removed': removed
        })
        
    except Exception as e:
        logger.error(f"Error cleaning up empty conversations: {e}")
        return jsonify({'error': str(e)}), 500


@history_bp.route('/agent/<int:agent_id>/clear', methods=['POST'])
@login_required
def clear_agent_history(agent_id):
    """
    Clear all history for a specific agent.
    
    Body:
        confirm: Must be true to proceed
    """
    try:
        data = request.get_json() or {}
        
        if not data.get('confirm'):
            return jsonify({'error': 'Confirmation required'}), 400
        
        manager = get_history_manager()
        removed = manager.delete_all_conversations_for_agent(agent_id)
        
        return jsonify({
            'message': f'Cleared {removed} conversations for agent {agent_id}',
            'removed': removed
        })
        
    except Exception as e:
        logger.error(f"Error clearing agent history: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# Helper Functions for Chat Integration
# =============================================================================

def save_chat_message(
    conversation_id: str,
    role: str,
    content: Any,
    content_type: str = None,
    user_id: int = None,
    **kwargs
) -> str:
    """
    Helper function for chat pages to save messages.
    Call this from your existing chat handlers.
    
    Supports rich content (tables, lists, etc.) by preserving the full structure.
    
    Args:
        conversation_id: Conversation ID (create one first if needed)
        role: 'user' or 'assistant'
        content: Message content (string or dict for rich content)
        content_type: 'text', 'rich_content', or 'json' (auto-detected if None)
        user_id: User ID for preference lookup (optional, uses current_user if available)
        **kwargs: Additional fields (tool_calls, tokens)
        
    Returns:
        Message ID, or None if history is disabled
    """
    # Determine user ID
    uid = user_id
    if uid is None:
        try:
            from flask_login import current_user as flask_current_user
            uid = flask_current_user.id
        except Exception:
            pass
    
    # Check if history is enabled for this user
    if uid is not None:
        try:
            from preferences_routes import get_preference
            if not get_preference(uid, 'history_enabled', True):
                return None
        except Exception:
            pass  # If we can't check, assume enabled
    
    try:
        from local_history import get_history_manager
        manager = get_history_manager()
        return manager.add_message(
            conversation_id, 
            role, 
            content, 
            content_type=content_type,
            **kwargs
        )
    except Exception as e:
        logger.error(f"Error saving chat message: {e}")
        return None


def get_or_create_conversation(
    agent_id: int,
    user_id: int,
    check_enabled: bool = True,
    stale_minutes: int = DEFAULT_CONVERSATION_STALE_MINUTES
) -> str:
    """
    Get a recent non-stale conversation for agent or create new one.
    
    A conversation is considered "stale" if it hasn't been updated within
    the stale_minutes threshold. This prevents multiple separate chat sessions
    from being merged into one conversation.
    
    Args:
        agent_id: Agent ID
        user_id: User ID
        check_enabled: Whether to check if history is enabled (default True)
        stale_minutes: Minutes of inactivity before conversation is stale (default 30)
        
    Returns:
        Conversation ID, or None if history is disabled
    """
    # Check if history is enabled
    if check_enabled:
        try:
            from preferences_routes import get_preference
            if not get_preference(user_id, 'history_enabled', True):
                return None
        except Exception:
            pass
    
    try:
        from local_history import get_history_manager
        manager = get_history_manager()
        
        recent = manager.get_recent_conversations(
            agent_id=agent_id,
            user_id=user_id,
            limit=1
        )
        
        if recent:
            # Check if the conversation is stale
            last_updated = recent[0].updated_at
            try:
                # Parse the ISO timestamp
                if isinstance(last_updated, str):
                    # Handle both with and without timezone
                    if last_updated.endswith('Z'):
                        last_updated = last_updated[:-1] + '+00:00'
                    last_updated_dt = datetime.fromisoformat(last_updated)
                else:
                    last_updated_dt = last_updated
                
                # Ensure timezone awareness
                if last_updated_dt.tzinfo is None:
                    last_updated_dt = last_updated_dt.replace(tzinfo=timezone.utc)
                
                now = datetime.now(timezone.utc)
                time_since_update = now - last_updated_dt
                
                # If conversation is not stale, return it
                if time_since_update < timedelta(minutes=stale_minutes):
                    return recent[0].id
                
            except Exception as e:
                logger.warning(f"Error checking conversation staleness: {e}")
                # On error, create a new conversation to be safe
                pass
        
        # Either no recent conversation or it's stale - create new
        return manager.create_conversation(agent_id=agent_id, user_id=user_id)
        
    except Exception as e:
        logger.error(f"Error getting/creating conversation: {e}")
        return None


def create_new_conversation(
    agent_id: int,
    user_id: int,
    title: str = None,
    check_enabled: bool = True
) -> str:
    """
    Always create a new conversation. Use this when explicitly starting a new session.
    
    Args:
        agent_id: Agent ID
        user_id: User ID
        title: Optional title for the conversation
        check_enabled: Whether to check if history is enabled (default True)
        
    Returns:
        Conversation ID, or None if history is disabled
    """
    # Check if history is enabled
    if check_enabled:
        try:
            from preferences_routes import get_preference
            if not get_preference(user_id, 'history_enabled', True):
                return None
        except Exception:
            pass
    
    try:
        from local_history import get_history_manager
        manager = get_history_manager()
        return manager.create_conversation(
            agent_id=agent_id, 
            user_id=user_id,
            title=title
        )
    except Exception as e:
        logger.error(f"Error creating new conversation: {e}")
        return None


def is_history_enabled_for_user(user_id: int) -> bool:
    """
    Check if history is enabled for a specific user.
    Useful when current_user is not available.
    
    Args:
        user_id: The user ID to check
        
    Returns:
        True if history is enabled
    """
    try:
        from preferences_routes import get_preference
        return get_preference(user_id, 'history_enabled', True)
    except Exception:
        return True  # Default to enabled


def delete_empty_conversations(user_id: int = None, agent_id: int = None) -> int:
    """
    Delete conversations that have no messages.
    Useful for cleaning up conversations that were created but never used.
    
    Args:
        user_id: Optional filter by user
        agent_id: Optional filter by agent
        
    Returns:
        Number of conversations deleted
    """
    try:
        from local_history import get_history_manager
        manager = get_history_manager()
        return manager.delete_empty_conversations(user_id=user_id, agent_id=agent_id)
    except Exception as e:
        logger.error(f"Error deleting empty conversations: {e}")
        return 0
