"""
Session state manager for the data collection agent.

Persists per-session collection state as JSON files in `data/collection_sessions/`.
Follows the established platform pattern from `onboarding_state.py` (file lock,
data directory, load/save with default str serializer).

Each session is one JSON file containing:
  - identity (session_id, config_id, user_id)
  - status and current section
  - all collected field values, organized by section
  - section completion status, validation errors
  - the chat history for page-refresh resume

State is the source of truth for what the user has provided. The agent rebuilds
its system prompt from the state each turn, so it never relies on LLM memory.
"""

import os
import json
import uuid
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from threading import Lock
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# Storage location follows the onboarding_state.py pattern: data/ under APP_ROOT
SESSIONS_DIR = os.path.join(
    os.getenv('APP_ROOT', os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'data',
    'collection_sessions',
)

_file_lock = Lock()


# Status values
STATUS_IN_PROGRESS = 'in_progress'
STATUS_REVIEW = 'review'
STATUS_SUBMITTED = 'submitted'
STATUS_DRAFT = 'draft'
STATUS_SUBMISSION_FAILED = 'submission_failed'

# Section status values
SECTION_NOT_STARTED = 'not_started'
SECTION_IN_PROGRESS = 'in_progress'
SECTION_COMPLETE = 'complete'


@dataclass
class CollectionSession:
    """
    Represents a single user's data collection session.

    Persisted to disk as JSON. Loaded fresh on each request — there is no
    in-memory session cache (so multi-process deployments work correctly).
    """
    session_id: str
    config_id: str
    user_id: str
    status: str = STATUS_IN_PROGRESS
    current_section_id: Optional[str] = None
    collected_data: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    section_status: Dict[str, str] = field(default_factory=dict)
    validation_errors: Dict[str, List[str]] = field(default_factory=dict)
    chat_history: List[Dict[str, str]] = field(default_factory=list)
    submission_log: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = ''
    updated_at: str = ''
    submitted_at: Optional[str] = None

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> 'CollectionSession':
        # Be tolerant of older session files that may be missing newer fields
        return cls(
            session_id=data.get('session_id', ''),
            config_id=data.get('config_id', ''),
            user_id=data.get('user_id', ''),
            status=data.get('status', STATUS_IN_PROGRESS),
            current_section_id=data.get('current_section_id'),
            collected_data=data.get('collected_data', {}) or {},
            section_status=data.get('section_status', {}) or {},
            validation_errors=data.get('validation_errors', {}) or {},
            chat_history=data.get('chat_history', []) or [],
            submission_log=data.get('submission_log', []) or [],
            created_at=data.get('created_at', ''),
            updated_at=data.get('updated_at', ''),
            submitted_at=data.get('submitted_at'),
        )

    def get_field_value(self, section_id: str, field_id: str) -> Any:
        return (self.collected_data.get(section_id) or {}).get(field_id)

    def set_field_value(self, section_id: str, field_id: str, value: Any):
        if section_id not in self.collected_data:
            self.collected_data[section_id] = {}
        self.collected_data[section_id][field_id] = value
        # Mark the section as in_progress if it wasn't started
        if self.section_status.get(section_id) == SECTION_NOT_STARTED or section_id not in self.section_status:
            self.section_status[section_id] = SECTION_IN_PROGRESS

    def append_chat(self, role: str, content: str, metadata: Optional[Dict] = None):
        entry = {'role': role, 'content': content, 'ts': datetime.utcnow().isoformat()}
        if metadata:
            entry['metadata'] = metadata
        self.chat_history.append(entry)


def _ensure_sessions_dir():
    if not os.path.exists(SESSIONS_DIR):
        os.makedirs(SESSIONS_DIR, exist_ok=True)


def _session_path(session_id: str) -> str:
    # Sanitize session_id (it should already be a UUID, but defense in depth)
    safe_id = ''.join(c for c in session_id if c.isalnum() or c in '_-')
    return os.path.join(SESSIONS_DIR, f"{safe_id}.json")


def create_session(config_id: str, user_id: str, initial_section_id: Optional[str] = None) -> CollectionSession:
    """Create a new session and persist it. Returns the session object."""
    now = datetime.utcnow().isoformat()
    session = CollectionSession(
        session_id=str(uuid.uuid4()),
        config_id=config_id,
        user_id=str(user_id),
        status=STATUS_IN_PROGRESS,
        current_section_id=initial_section_id,
        created_at=now,
        updated_at=now,
    )
    save_session(session)
    logger.info(f"Created session {session.session_id} for user {user_id}, config {config_id}")
    return session


def load_session(session_id: str) -> Optional[CollectionSession]:
    """Load a session from disk. Returns None if not found or invalid."""
    _ensure_sessions_dir()
    path = _session_path(session_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return CollectionSession.from_dict(data)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in session {session_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error loading session {session_id}: {e}")
        return None


def save_session(session: CollectionSession):
    """Persist a session to disk (thread-safe)."""
    _ensure_sessions_dir()
    session.updated_at = datetime.utcnow().isoformat()
    path = _session_path(session.session_id)
    try:
        with _file_lock:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(session.to_dict(), f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Error saving session {session.session_id}: {e}")


def delete_session(session_id: str) -> bool:
    """Delete a session file. Returns True on success."""
    path = _session_path(session_id)
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"Deleted session {session_id}")
            return True
    except Exception as e:
        logger.error(f"Error deleting session {session_id}: {e}")
    return False


def get_user_sessions(user_id: str, config_id: Optional[str] = None,
                      include_submitted: bool = False) -> List[CollectionSession]:
    """
    Return all sessions for a user, optionally filtered by config_id.

    Excludes submitted sessions by default (so the UI can offer "resume your
    in-progress session" without showing finished ones).
    """
    _ensure_sessions_dir()
    user_id_str = str(user_id)
    out = []
    try:
        for fn in os.listdir(SESSIONS_DIR):
            if not fn.endswith('.json'):
                continue
            session = load_session(fn[:-5])
            if not session:
                continue
            if session.user_id != user_id_str:
                continue
            if config_id and session.config_id != config_id:
                continue
            if not include_submitted and session.status == STATUS_SUBMITTED:
                continue
            out.append(session)
    except Exception as e:
        logger.error(f"Error listing user sessions: {e}")
    # Most recent first
    out.sort(key=lambda s: s.updated_at, reverse=True)
    return out


def update_field(session_id: str, section_id: str, field_id: str, value: Any) -> Optional[CollectionSession]:
    """
    Update a single field in a session. Saves and returns the updated session.
    Returns None if the session doesn't exist.

    Note: this does NOT run validation — that's the validation_engine's job.
    The agent's `update_field` tool calls validation_engine first, then this.
    """
    session = load_session(session_id)
    if not session:
        return None
    session.set_field_value(section_id, field_id, value)
    save_session(session)
    return session


def set_section_status(session_id: str, section_id: str, status: str) -> Optional[CollectionSession]:
    """Update a section's status. Saves and returns the updated session."""
    session = load_session(session_id)
    if not session:
        return None
    session.section_status[section_id] = status
    save_session(session)
    return session


def set_current_section(session_id: str, section_id: str) -> Optional[CollectionSession]:
    """Move the current section pointer (used for back/edit). Saves and returns."""
    session = load_session(session_id)
    if not session:
        return None
    session.current_section_id = section_id
    # If this section was already complete and we're going back, mark in_progress
    if session.section_status.get(section_id) == SECTION_COMPLETE:
        session.section_status[section_id] = SECTION_IN_PROGRESS
    save_session(session)
    return session


def set_status(session_id: str, status: str) -> Optional[CollectionSession]:
    """Update the top-level session status."""
    session = load_session(session_id)
    if not session:
        return None
    session.status = status
    if status == STATUS_SUBMITTED:
        session.submitted_at = datetime.utcnow().isoformat()
    save_session(session)
    return session


def append_chat_message(session_id: str, role: str, content: str,
                        metadata: Optional[Dict] = None) -> Optional[CollectionSession]:
    """Append a message to the session's chat history. Saves and returns."""
    session = load_session(session_id)
    if not session:
        return None
    session.append_chat(role, content, metadata)
    save_session(session)
    return session


def append_submission_log(session_id: str, entry: Dict) -> Optional[CollectionSession]:
    """
    Append an entry to the submission log (per-action results from the
    completion pipeline). Saves and returns.
    """
    session = load_session(session_id)
    if not session:
        return None
    entry = dict(entry)
    entry.setdefault('ts', datetime.utcnow().isoformat())
    session.submission_log.append(entry)
    save_session(session)
    return session
