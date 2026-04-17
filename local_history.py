"""
Local Conversation History Manager for AI Hub
==============================================

Stores conversation history and query patterns locally on the user's machine.
History is stored as plain JSON files and NEVER leaves the local environment.

Privacy Model:
- History stored in plain JSON files on local machine
- History NEVER transmitted to cloud or used for training
- User has full control over their data
- Easy to export, backup, or delete

Features:
- Conversation continuation with full context
- Query frequency tracking for smart suggestions
- Pinned/favorite queries for quick re-run
- Configurable retention periods
- Export and clear capabilities
- Rich content preservation (tables, lists, etc.)

Usage:
    from local_history import get_history_manager
    
    history = get_history_manager()
    
    # Save a conversation message
    history.add_message(conversation_id, agent_id, role, content)
    
    # Get recent conversations for an agent
    recent = history.get_recent_conversations(agent_id, limit=5)
    
    # Get frequent queries for quick re-run
    frequent = history.get_frequent_queries(agent_id, limit=10)
"""

import os
import json
import hashlib
import uuid
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Union
from dataclasses import dataclass, asdict, field

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

# Default staleness threshold in minutes - conversations older than this
# will be considered "stale" and a new conversation will be created
DEFAULT_CONVERSATION_STALE_MINUTES = 30


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class Message:
    """A single message in a conversation."""
    id: str
    role: str  # 'user' or 'assistant'
    content: Any  # Can be string or dict (for rich content)
    timestamp: str
    content_type: str = 'text'  # 'text', 'rich_content', or 'json'
    tool_calls: Optional[List[Dict]] = None
    tokens: Optional[Dict[str, int]] = None
    attachments: Optional[List[str]] = None
    
    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Message':
        return cls(
            id=data.get('id', str(uuid.uuid4())[:8]),
            role=data['role'],
            content=data['content'],
            timestamp=data.get('timestamp', datetime.now(timezone.utc).isoformat()),
            content_type=data.get('content_type', 'text'),
            tool_calls=data.get('tool_calls'),
            tokens=data.get('tokens'),
            attachments=data.get('attachments')
        )


@dataclass
class ConversationMeta:
    """Metadata about a conversation (stored in index)."""
    id: str
    agent_id: int
    user_id: int
    title: str
    preview: str
    message_count: int
    created_at: str
    updated_at: str
    is_pinned: bool = False
    tags: Optional[List[str]] = None
    
    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}
    
    @classmethod
    def from_dict(cls, data: dict) -> 'ConversationMeta':
        return cls(
            id=data['id'],
            agent_id=data['agent_id'],
            user_id=data.get('user_id', 0),
            title=data.get('title', 'Untitled'),
            preview=data.get('preview', ''),
            message_count=data.get('message_count', 0),
            created_at=data.get('created_at', datetime.now(timezone.utc).isoformat()),
            updated_at=data.get('updated_at', datetime.now(timezone.utc).isoformat()),
            is_pinned=data.get('is_pinned', False),
            tags=data.get('tags')
        )


@dataclass
class Query:
    """A tracked query for re-run functionality."""
    id: str
    agent_id: int
    user_id: int
    text: str
    frequency: int
    last_used: str
    first_used: str
    is_pinned: bool = False
    tags: Optional[List[str]] = None
    
    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Query':
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            id=data['id'],
            agent_id=data['agent_id'],
            user_id=data.get('user_id', 0),
            text=data['text'],
            frequency=data.get('frequency', 1),
            last_used=data.get('last_used', now),
            first_used=data.get('first_used', now),
            is_pinned=data.get('is_pinned', False),
            tags=data.get('tags')
        )


# =============================================================================
# Local History Manager
# =============================================================================

class LocalHistoryManager:
    """
    Manages locally-stored conversation history.
    
    Privacy Model:
    - History stored in plain JSON files on local machine
    - History NEVER transmitted to cloud or used for training
    - User can export/clear their own history
    
    File Structure:
        /data/history/
            index.json                  # Conversation index + query stats
            conversations/
                {conv_id}.json          # Individual conversation messages
            exports/                    # User-requested exports
    """
    
    # Default settings
    DEFAULT_SETTINGS = {
        'retention_days': 90,           # 0 = forever
        'auto_save_queries': True,      # Save unique queries automatically
        'min_query_length': 10,         # Don't save very short queries
        'max_conversations': 1000,      # Max total conversations
        'max_conversations_per_agent': 100,  # Max per agent
        'max_queries': 500,             # Max queries to track
    }
    
    def __init__(self, data_dir: str = None):
        """
        Initialize the history manager.
        
        Args:
            data_dir: Base data directory. Defaults to ./data or AIHUB_DATA_DIR env var
        """
        # Use APP_ROOT env var for PyInstaller compatibility (set by installer)
        default_base = os.getenv('APP_ROOT', os.path.dirname(os.path.abspath(__file__)))
        self.data_dir = Path(data_dir or os.path.join(default_base, 'data'))
        self.history_dir = self.data_dir / 'history'
        self.conversations_dir = self.history_dir / 'conversations'
        self.exports_dir = self.history_dir / 'exports'
        self.index_file = self.history_dir / 'index.json'
        
        # Ensure directories exist
        self._ensure_directories()
        
        # Cache for index (loaded on first access)
        self._index_cache = None
    
    def _ensure_directories(self):
        """Create required directories."""
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.conversations_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
    
    # =========================================================================
    # Index Management
    # =========================================================================
    
    def _load_index(self) -> dict:
        """Load the history index."""
        if self._index_cache is not None:
            return self._index_cache
        
        if not self.index_file.exists():
            self._index_cache = {
                'conversations': {},
                'queries': {},
                'settings': self.DEFAULT_SETTINGS.copy(),
                'meta': {
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'version': '1.0'
                }
            }
            return self._index_cache
        
        try:
            with open(self.index_file, 'r', encoding='utf-8') as f:
                self._index_cache = json.load(f)
            
            # Ensure all keys exist
            if 'conversations' not in self._index_cache:
                self._index_cache['conversations'] = {}
            if 'queries' not in self._index_cache:
                self._index_cache['queries'] = {}
            if 'settings' not in self._index_cache:
                self._index_cache['settings'] = self.DEFAULT_SETTINGS.copy()
            
            return self._index_cache
            
        except Exception as e:
            logger.error(f"Error loading history index: {e}")
            self._index_cache = {
                'conversations': {},
                'queries': {},
                'settings': self.DEFAULT_SETTINGS.copy(),
                'meta': {
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'version': '1.0'
                }
            }
            return self._index_cache
    
    def _save_index(self):
        """Save the history index."""
        if self._index_cache is None:
            return
        
        try:
            # Write atomically (write to temp, then rename)
            temp_file = self.index_file.with_suffix('.tmp')
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(self._index_cache, f, indent=2, default=str)
            temp_file.replace(self.index_file)
            
        except Exception as e:
            logger.error(f"Error saving history index: {e}")
            raise
    
    def _invalidate_cache(self):
        """Invalidate the index cache to force reload."""
        self._index_cache = None
    
    def get_settings(self) -> dict:
        """Get history settings."""
        index = self._load_index()
        return index.get('settings', self.DEFAULT_SETTINGS.copy())
    
    def update_settings(self, **kwargs):
        """Update history settings."""
        index = self._load_index()
        index['settings'].update(kwargs)
        self._save_index()
    
    # =========================================================================
    # Conversation Management
    # =========================================================================
    
    def _get_conversation_file(self, conversation_id: str) -> Path:
        """Get path to conversation file."""
        return self.conversations_dir / f"{conversation_id}.json"
    
    def _load_conversation(self, conversation_id: str) -> Optional[dict]:
        """Load a full conversation with messages."""
        conv_file = self._get_conversation_file(conversation_id)
        
        if not conv_file.exists():
            return None
        
        try:
            with open(conv_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading conversation {conversation_id}: {e}")
            return None
    
    def _save_conversation(self, conversation_id: str, data: dict):
        """Save a conversation."""
        conv_file = self._get_conversation_file(conversation_id)
        
        try:
            temp_file = conv_file.with_suffix('.tmp')
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)
            temp_file.replace(conv_file)
            
        except Exception as e:
            logger.error(f"Error saving conversation {conversation_id}: {e}")
            raise
    
    def create_conversation(
        self,
        agent_id: int,
        user_id: int = 0,
        title: str = None,
        tags: List[str] = None
    ) -> str:
        """
        Create a new conversation.
        
        Args:
            agent_id: ID of the agent
            user_id: ID of the user
            title: Optional title (auto-generated if not provided)
            tags: Optional tags for organization
            
        Returns:
            Conversation ID
        """
        conversation_id = f"conv_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        
        # Create conversation data
        conversation_data = {
            'id': conversation_id,
            'agent_id': agent_id,
            'user_id': user_id,
            'created_at': now,
            'messages': []
        }
        self._save_conversation(conversation_id, conversation_data)
        
        # Add to index
        index = self._load_index()
        index['conversations'][conversation_id] = {
            'id': conversation_id,
            'agent_id': agent_id,
            'user_id': user_id,
            'title': title or 'New Conversation',
            'preview': '',
            'message_count': 0,
            'created_at': now,
            'updated_at': now,
            'is_pinned': False,
            'tags': tags
        }
        self._save_index()
        
        # Enforce per-agent limit
        self._enforce_agent_conversation_limit(agent_id)
        
        return conversation_id
    
    def _enforce_agent_conversation_limit(self, agent_id: int):
        """Remove oldest conversations if agent has too many."""
        settings = self.get_settings()
        max_per_agent = settings.get('max_conversations_per_agent', 100)
        
        index = self._load_index()
        agent_conversations = [
            (conv_id, meta) 
            for conv_id, meta in index['conversations'].items()
            if meta.get('agent_id') == agent_id and not meta.get('is_pinned')
        ]
        
        if len(agent_conversations) > max_per_agent:
            # Sort by updated_at, oldest first
            agent_conversations.sort(key=lambda x: x[1].get('updated_at', ''))
            
            # Remove oldest until under limit
            to_remove = len(agent_conversations) - max_per_agent
            for conv_id, _ in agent_conversations[:to_remove]:
                self._delete_conversation_internal(conv_id)
    
    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: Any,
        content_type: str = None,
        tool_calls: List[Dict] = None,
        tokens: Dict[str, int] = None,
        attachments: List[str] = None
    ) -> str:
        """
        Add a message to a conversation.
        
        Args:
            conversation_id: Conversation ID
            role: 'user' or 'assistant'
            content: Message content (string or dict for rich content)
            content_type: 'text', 'rich_content', or 'json' (auto-detected if None)
            tool_calls: Optional list of tool calls
            tokens: Optional token counts
            attachments: Optional list of attachment references
            
        Returns:
            Message ID
        """
        conversation = self._load_conversation(conversation_id)
        if conversation is None:
            raise ValueError(f"Conversation {conversation_id} not found")
        
        message_id = f"msg_{len(conversation['messages']) + 1:04d}"
        now = datetime.now(timezone.utc).isoformat()
        
        # Auto-detect content type if not specified
        if content_type is None:
            if isinstance(content, dict):
                if content.get('type') == 'rich_content' or 'blocks' in content:
                    content_type = 'rich_content'
                else:
                    content_type = 'json'
            else:
                content_type = 'text'
        
        message = {
            'id': message_id,
            'role': role,
            'content': content,
            'content_type': content_type,
            'timestamp': now
        }
        
        if tool_calls:
            message['tool_calls'] = tool_calls
        if tokens:
            message['tokens'] = tokens
        if attachments:
            message['attachments'] = attachments
        
        conversation['messages'].append(message)
        self._save_conversation(conversation_id, conversation)
        
        # Update index
        index = self._load_index()
        if conversation_id in index['conversations']:
            meta = index['conversations'][conversation_id]
            meta['message_count'] = len(conversation['messages'])
            meta['updated_at'] = now
            
            # Update preview from first user message
            if role == 'user' and not meta.get('preview'):
                # Handle both string and dict content for preview
                preview_text = content if isinstance(content, str) else str(content)[:100]
                meta['preview'] = preview_text[:100] + ('...' if len(preview_text) > 100 else '')
            
            # Auto-generate title from first user message
            if meta.get('title') == 'New Conversation' and role == 'user':
                title_text = content if isinstance(content, str) else str(content)
                meta['title'] = self._generate_title(title_text)
            
            self._save_index()
        
        # Track query if it's a user message (only for text content)
        settings = self.get_settings()
        if role == 'user' and settings.get('auto_save_queries', True):
            query_text = content if isinstance(content, str) else ''
            if len(query_text) >= settings.get('min_query_length', 10):
                self._track_query(
                    agent_id=conversation.get('agent_id', 0),
                    user_id=conversation.get('user_id', 0),
                    text=query_text
                )
        
        return message_id
    
    def _generate_title(self, content: str) -> str:
        """Generate a title from content."""
        if not isinstance(content, str):
            content = str(content)
        content = content.strip()
        
        # Try to find first sentence
        for delimiter in ['. ', '? ', '! ', '\n']:
            if delimiter in content[:100]:
                return content[:content.index(delimiter) + 1].strip()
        
        # Fall back to truncation
        if len(content) > 50:
            return content[:47] + '...'
        return content
    
    def get_conversation(self, conversation_id: str) -> Optional[dict]:
        """
        Get a full conversation with all messages.
        
        Args:
            conversation_id: Conversation ID
            
        Returns:
            Conversation dict with messages, or None if not found
        """
        return self._load_conversation(conversation_id)
    
    def get_conversation_messages(
        self,
        conversation_id: str,
        limit: int = None,
        offset: int = 0
    ) -> List[dict]:
        """
        Get messages from a conversation with pagination.
        
        Args:
            conversation_id: Conversation ID
            limit: Max messages to return
            offset: Starting offset
            
        Returns:
            List of message dicts
        """
        conversation = self._load_conversation(conversation_id)
        if conversation is None:
            return []
        
        messages = conversation.get('messages', [])
        
        if offset:
            messages = messages[offset:]
        if limit:
            messages = messages[:limit]
        
        return messages
    
    def get_recent_conversations(
        self,
        agent_id: int = None,
        user_id: int = None,
        limit: int = 10,
        include_empty: bool = False
    ) -> List[ConversationMeta]:
        """
        Get recent conversations.
        
        Args:
            agent_id: Optional filter by agent
            user_id: Optional filter by user
            limit: Max conversations to return
            include_empty: If False (default), exclude conversations with 0 messages
                          (unless they are pinned)
            
        Returns:
            List of ConversationMeta objects, most recent first
        """
        index = self._load_index()
        conversations = list(index.get('conversations', {}).values())
        
        # Filter by agent/user
        if agent_id is not None:
            conversations = [c for c in conversations if c.get('agent_id') == agent_id]
        if user_id is not None:
            conversations = [c for c in conversations if c.get('user_id') == user_id]
        
        # Filter out empty conversations (unless pinned or include_empty=True)
        if not include_empty:
            conversations = [
                c for c in conversations 
                if c.get('message_count', 0) > 0 or c.get('is_pinned', False)
            ]
        
        # Sort by updated_at descending
        conversations.sort(key=lambda c: c.get('updated_at', ''), reverse=True)
        
        # Limit
        conversations = conversations[:limit]
        
        return [ConversationMeta.from_dict(c) for c in conversations]
    
    def get_conversations_for_dashboard(self, user_id: int = None, limit: int = 5, include_empty: bool = False) -> List[dict]:
        """
        Get recent conversations formatted for dashboard display.
        Includes agent name lookup-friendly format.
        
        Args:
            user_id: Optional filter by user
            limit: Max conversations to return
            include_empty: If False (default), exclude conversations with 0 messages
                          (unless they are pinned)
            
        Returns:
            List of conversation dicts with display info
        """
        index = self._load_index()
        conversations = list(index.get('conversations', {}).values())
        
        if user_id is not None:
            conversations = [c for c in conversations if c.get('user_id') == user_id]
        
        # Filter out empty conversations (unless pinned or include_empty=True)
        if not include_empty:
            conversations = [
                c for c in conversations 
                if c.get('message_count', 0) > 0 or c.get('is_pinned', False)
            ]
        
        conversations.sort(key=lambda c: c.get('updated_at', ''), reverse=True)
        conversations = conversations[:limit]
        
        return [{
            'id': c['id'],
            'agent_id': c['agent_id'],
            'title': c.get('title', 'Untitled'),
            'preview': c.get('preview', ''),
            'updated_at': c.get('updated_at'),
            'message_count': c.get('message_count', 0),
            'is_pinned': c.get('is_pinned', False)
        } for c in conversations]
    
    def update_conversation_title(self, conversation_id: str, title: str):
        """Update a conversation's title."""
        index = self._load_index()
        if conversation_id in index['conversations']:
            index['conversations'][conversation_id]['title'] = title
            self._save_index()
    
    def pin_conversation(self, conversation_id: str, pinned: bool = True):
        """Pin or unpin a conversation."""
        index = self._load_index()
        if conversation_id in index['conversations']:
            index['conversations'][conversation_id]['is_pinned'] = pinned
            self._save_index()
    
    def _delete_conversation_internal(self, conversation_id: str):
        """Internal delete without index reload."""
        # Remove file
        conv_file = self._get_conversation_file(conversation_id)
        if conv_file.exists():
            conv_file.unlink()
        
        # Remove from cached index
        if self._index_cache and conversation_id in self._index_cache.get('conversations', {}):
            del self._index_cache['conversations'][conversation_id]
    
    def delete_conversation(self, conversation_id: str):
        """Delete a conversation."""
        self._delete_conversation_internal(conversation_id)
        self._save_index()
    
    def delete_all_conversations_for_agent(self, agent_id: int):
        """Delete all conversations for a specific agent."""
        index = self._load_index()
        to_delete = [
            conv_id for conv_id, meta in index['conversations'].items()
            if meta.get('agent_id') == agent_id
        ]
        
        for conv_id in to_delete:
            self._delete_conversation_internal(conv_id)
        
        self._save_index()
        return len(to_delete)
    
    def delete_empty_conversations(self, user_id: int = None, agent_id: int = None) -> int:
        """
        Delete conversations that have no messages.
        Useful for cleaning up conversations that were created but never used.
        Does NOT delete pinned conversations even if they are empty.
        
        Args:
            user_id: Optional filter by user
            agent_id: Optional filter by agent
            
        Returns:
            Number of conversations deleted
        """
        index = self._load_index()
        
        # Find empty conversations (message_count == 0 and not pinned)
        to_delete = []
        for conv_id, meta in index['conversations'].items():
            # Skip if doesn't match filters
            if user_id is not None and meta.get('user_id') != user_id:
                continue
            if agent_id is not None and meta.get('agent_id') != agent_id:
                continue
            
            # Skip if pinned
            if meta.get('is_pinned', False):
                continue
            
            # Delete if no messages
            if meta.get('message_count', 0) == 0:
                to_delete.append(conv_id)
        
        for conv_id in to_delete:
            self._delete_conversation_internal(conv_id)
        
        if to_delete:
            self._save_index()
            logger.info(f"Deleted {len(to_delete)} empty conversations")
        
        return len(to_delete)
    
    # =========================================================================
    # Query Tracking
    # =========================================================================
    
    def _normalize_query(self, text: str) -> str:
        """Normalize query text for comparison."""
        return ' '.join(text.lower().split())
    
    def _query_hash(self, text: str) -> str:
        """Generate a hash for query deduplication."""
        normalized = self._normalize_query(text)
        return hashlib.md5(normalized.encode()).hexdigest()[:12]
    
    def _track_query(self, agent_id: int, user_id: int, text: str):
        """Track a query for frequency/recency."""
        query_id = f"qry_{self._query_hash(text)}"
        now = datetime.now(timezone.utc).isoformat()
        
        index = self._load_index()
        queries = index.get('queries', {})
        
        if query_id in queries:
            # Update existing
            queries[query_id]['frequency'] += 1
            queries[query_id]['last_used'] = now
        else:
            # Create new
            queries[query_id] = {
                'id': query_id,
                'agent_id': agent_id,
                'user_id': user_id,
                'text': text,
                'frequency': 1,
                'last_used': now,
                'first_used': now,
                'is_pinned': False,
                'tags': None
            }
        
        # Enforce max queries limit
        settings = self.get_settings()
        max_queries = settings.get('max_queries', 500)
        
        if len(queries) > max_queries:
            # Remove oldest non-pinned queries
            sorted_queries = sorted(
                [(k, v) for k, v in queries.items() if not v.get('is_pinned')],
                key=lambda x: x[1].get('last_used', '')
            )
            for query_id_to_remove, _ in sorted_queries[:len(queries) - max_queries]:
                del queries[query_id_to_remove]
        
        index['queries'] = queries
        self._save_index()
    
    def get_frequent_queries(
        self,
        agent_id: int = None,
        user_id: int = None,
        limit: int = 10
    ) -> List[Query]:
        """
        Get frequently used queries.
        
        Args:
            agent_id: Optional filter by agent
            user_id: Optional filter by user
            limit: Max queries to return
            
        Returns:
            List of Query objects, most frequent first
        """
        index = self._load_index()
        queries = list(index.get('queries', {}).values())
        
        # Filter
        if agent_id is not None:
            queries = [q for q in queries if q.get('agent_id') == agent_id]
        if user_id is not None:
            queries = [q for q in queries if q.get('user_id') == user_id]
        
        # Sort by frequency descending, then recency
        queries.sort(key=lambda q: (q.get('frequency', 0), q.get('last_used', '')), reverse=True)
        
        return [Query.from_dict(q) for q in queries[:limit]]
    
    def get_recent_queries(
        self,
        agent_id: int = None,
        user_id: int = None,
        limit: int = 10
    ) -> List[Query]:
        """
        Get recently used queries.
        
        Args:
            agent_id: Optional filter by agent
            user_id: Optional filter by user  
            limit: Max queries to return
            
        Returns:
            List of Query objects, most recent first
        """
        index = self._load_index()
        queries = list(index.get('queries', {}).values())
        
        # Filter
        if agent_id is not None:
            queries = [q for q in queries if q.get('agent_id') == agent_id]
        if user_id is not None:
            queries = [q for q in queries if q.get('user_id') == user_id]
        
        # Sort by last_used descending
        queries.sort(key=lambda q: q.get('last_used', ''), reverse=True)
        
        return [Query.from_dict(q) for q in queries[:limit]]
    
    def get_pinned_queries(
        self,
        agent_id: int = None,
        user_id: int = None
    ) -> List[Query]:
        """Get pinned/favorite queries."""
        index = self._load_index()
        queries = [q for q in index.get('queries', {}).values() if q.get('is_pinned')]
        
        if agent_id is not None:
            queries = [q for q in queries if q.get('agent_id') == agent_id]
        if user_id is not None:
            queries = [q for q in queries if q.get('user_id') == user_id]
        
        return [Query.from_dict(q) for q in queries]
    
    def get_queries_for_dashboard(self, user_id: int = None, limit: int = 5) -> List[dict]:
        """
        Get queries formatted for dashboard display.
        Shows pinned first, then frequent.
        
        Args:
            user_id: Optional filter by user
            limit: Max queries to return
            
        Returns:
            List of query dicts with display info
        """
        index = self._load_index()
        queries = list(index.get('queries', {}).values())
        
        if user_id is not None:
            queries = [q for q in queries if q.get('user_id') == user_id]
        
        # Separate pinned and unpinned
        pinned = [q for q in queries if q.get('is_pinned')]
        unpinned = [q for q in queries if not q.get('is_pinned')]
        
        # Sort unpinned by frequency
        unpinned.sort(key=lambda q: q.get('frequency', 0), reverse=True)
        
        # Combine: pinned first, then frequent
        combined = pinned + unpinned
        combined = combined[:limit]
        
        return [{
            'id': q['id'],
            'agent_id': q['agent_id'],
            'text': q['text'],
            'frequency': q.get('frequency', 1),
            'is_pinned': q.get('is_pinned', False),
            'last_used': q.get('last_used')
        } for q in combined]
    
    def pin_query(self, query_id: str, pinned: bool = True):
        """Pin or unpin a query."""
        index = self._load_index()
        if query_id in index.get('queries', {}):
            index['queries'][query_id]['is_pinned'] = pinned
            self._save_index()
    
    def delete_query(self, query_id: str):
        """Delete a tracked query."""
        index = self._load_index()
        if query_id in index.get('queries', {}):
            del index['queries'][query_id]
            self._save_index()
    
    # =========================================================================
    # Maintenance & Export
    # =========================================================================
    
    def prune_old_conversations(self, days: int = None) -> int:
        """
        Remove conversations older than retention period.
        
        Args:
            days: Override retention days (uses settings if not provided)
            
        Returns:
            Number of conversations removed
        """
        settings = self.get_settings()
        retention_days = days if days is not None else settings.get('retention_days', 90)
        
        if retention_days <= 0:
            return 0  # Retention disabled
        
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        cutoff_str = cutoff.isoformat()
        
        index = self._load_index()
        to_remove = []
        
        for conv_id, meta in index.get('conversations', {}).items():
            if not meta.get('is_pinned') and meta.get('updated_at', '') < cutoff_str:
                to_remove.append(conv_id)
        
        for conv_id in to_remove:
            self._delete_conversation_internal(conv_id)
        
        if to_remove:
            self._save_index()
        
        return len(to_remove)
    
    def export_history(
        self,
        format: str = 'json',
        agent_id: int = None,
        include_messages: bool = True
    ) -> str:
        """
        Export history to file.
        
        Args:
            format: 'json' (only json supported currently)
            agent_id: Optional filter by agent
            include_messages: Include full message content
            
        Returns:
            Path to exported file
        """
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        
        index = self._load_index()
        export_data = {
            'exported_at': datetime.now(timezone.utc).isoformat(),
            'conversations': [],
            'queries': list(index.get('queries', {}).values())
        }
        
        # Filter and optionally include messages
        for conv_id, meta in index.get('conversations', {}).items():
            if agent_id is not None and meta.get('agent_id') != agent_id:
                continue
            
            conv_export = dict(meta)
            
            if include_messages:
                full_conv = self._load_conversation(conv_id)
                if full_conv:
                    conv_export['messages'] = full_conv.get('messages', [])
            
            export_data['conversations'].append(conv_export)
        
        export_file = self.exports_dir / f"history_export_{timestamp}.json"
        with open(export_file, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2, default=str)
        
        return str(export_file)
    
    def clear_all_history(self, confirm: bool = False):
        """
        Clear all history data.
        
        Args:
            confirm: Must be True to actually clear
        """
        if not confirm:
            raise ValueError("Must pass confirm=True to clear all history")
        
        # Remove all conversation files
        for conv_file in self.conversations_dir.glob('*.json'):
            conv_file.unlink()
        
        # Reset index
        self._index_cache = {
            'conversations': {},
            'queries': {},
            'settings': self.get_settings(),  # Preserve settings
            'meta': {
                'created_at': datetime.now(timezone.utc).isoformat(),
                'version': '1.0',
                'cleared_at': datetime.now(timezone.utc).isoformat()
            }
        }
        self._save_index()
    
    def get_storage_info(self) -> dict:
        """Get information about history storage for display."""
        index = self._load_index()
        
        # Calculate storage size
        total_size = 0
        if self.index_file.exists():
            total_size += self.index_file.stat().st_size
        for conv_file in self.conversations_dir.glob('*.json'):
            total_size += conv_file.stat().st_size
        
        return {
            'location': str(self.history_dir.absolute()),
            'conversation_count': len(index.get('conversations', {})),
            'query_count': len(index.get('queries', {})),
            'storage_bytes': total_size,
            'storage_mb': round(total_size / (1024 * 1024), 2),
            'settings': self.get_settings(),
            'privacy': {
                'stored_locally': True,
                'cloud_sync': False,
                'used_for_training': False
            }
        }


# =============================================================================
# Global Instance
# =============================================================================

_history_manager: Optional[LocalHistoryManager] = None


def get_history_manager(data_dir: str = None) -> LocalHistoryManager:
    """Get or create the global history manager instance."""
    global _history_manager
    if _history_manager is None:
        _history_manager = LocalHistoryManager(data_dir)
    return _history_manager


def reset_history_manager():
    """Reset the global instance (useful for testing)."""
    global _history_manager
    _history_manager = None


# =============================================================================
# Convenience Functions for Integration
# =============================================================================

def save_conversation_message(
    conversation_id: str,
    role: str,
    content: Any,
    content_type: str = None,
    **kwargs
) -> str:
    """
    Save a message to a conversation.
    
    Args:
        conversation_id: Conversation ID
        role: 'user' or 'assistant'
        content: Message content (string or dict for rich content)
        content_type: 'text', 'rich_content', or 'json' (auto-detected if None)
        **kwargs: Additional message fields (tool_calls, tokens, etc.)
        
    Returns:
        Message ID
    """
    return get_history_manager().add_message(
        conversation_id, 
        role, 
        content, 
        content_type=content_type,
        **kwargs
    )


def get_or_create_conversation(
    agent_id: int, 
    user_id: int = 0,
    stale_minutes: int = DEFAULT_CONVERSATION_STALE_MINUTES,
    force_new: bool = False
) -> str:
    """
    Get a recent non-stale conversation for an agent or create a new one.
    
    A conversation is considered "stale" if it hasn't been updated within
    the stale_minutes threshold. This prevents multiple separate chat sessions
    from being merged into one conversation.
    
    Args:
        agent_id: Agent ID
        user_id: User ID
        stale_minutes: Minutes of inactivity before conversation is stale (default 30)
        force_new: If True, always create a new conversation
        
    Returns:
        Conversation ID
    """
    manager = get_history_manager()
    
    # If force_new is set, always create a new conversation
    if force_new:
        return manager.create_conversation(agent_id=agent_id, user_id=user_id)
    
    recent = manager.get_recent_conversations(agent_id=agent_id, user_id=user_id, limit=1)
    
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


def create_new_conversation(agent_id: int, user_id: int = 0, title: str = None) -> str:
    """
    Always create a new conversation. Use this when explicitly starting a new session.
    
    Args:
        agent_id: Agent ID
        user_id: User ID
        title: Optional title for the conversation
        
    Returns:
        Conversation ID
    """
    return get_history_manager().create_conversation(
        agent_id=agent_id, 
        user_id=user_id,
        title=title
    )


def get_recent_conversations(agent_id: int = None, limit: int = 10) -> List[ConversationMeta]:
    """Get recent conversations for an agent."""
    return get_history_manager().get_recent_conversations(agent_id=agent_id, limit=limit)


def get_frequent_queries(agent_id: int = None, limit: int = 10) -> List[Query]:
    """Get frequently used queries for an agent."""
    return get_history_manager().get_frequent_queries(agent_id=agent_id, limit=limit)


def get_recent_queries(agent_id: int = None, limit: int = 10) -> List[Query]:
    """Get recently used queries for an agent."""
    return get_history_manager().get_recent_queries(agent_id=agent_id, limit=limit)


def is_history_enabled() -> bool:
    """Check if history is enabled (for integration checks)."""
    # This would integrate with your preferences system
    # For now, always returns True - integrate with your UserPreferences
    return True
