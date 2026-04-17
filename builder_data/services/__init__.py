"""
Builder Data — Session Manager & DataFrame Store
===================================================
Manages conversation sessions and temporary DataFrame storage.
"""

import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class UserContext:
    """User context from the main Flask app."""

    def __init__(self, user_id: int, role: int, tenant_id: int, username: str, name: str):
        self.user_id = user_id
        self.role = role
        self.tenant_id = tenant_id
        self.username = username
        self.name = name

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "role": self.role,
            "tenant_id": self.tenant_id,
            "username": self.username,
            "name": self.name,
        }


class Session:
    """A single conversation session."""

    def __init__(self, session_id: str, title: str = "New Chat", user_context: Optional[UserContext] = None):
        self.session_id = session_id
        self.title = title
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = self.created_at
        self.message_count = 0
        self.user_context = user_context

    def touch(self):
        self.updated_at = datetime.now(timezone.utc).isoformat()
        self.message_count += 1

    def to_dict(self) -> dict:
        result = {
            "session_id": self.session_id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": self.message_count,
        }
        if self.user_context:
            result["user"] = self.user_context.to_dict()
        return result


class SessionManager:
    """In-memory session store."""

    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def create_session(self, title: str = "New Chat", user_context: Optional[UserContext] = None) -> Session:
        session_id = str(uuid.uuid4())
        session = Session(session_id=session_id, title=title, user_context=user_context)
        self._sessions[session_id] = session
        logger.info(f"Created session: {session_id}")
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def get_or_create(self, session_id: Optional[str] = None) -> Session:
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        return self.create_session()

    def update_title(self, session_id: str, title: str):
        session = self._sessions.get(session_id)
        if session:
            session.title = title
            session.touch()

    def list_sessions(self) -> list[dict]:
        sessions = sorted(
            self._sessions.values(),
            key=lambda s: s.updated_at,
            reverse=True,
        )
        return [s.to_dict() for s in sessions]

    def delete_session(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
            logger.info(f"Deleted session: {session_id}")
            return True
        return False


class DataFrameStore:
    """
    In-memory store for DataFrames produced by pipeline steps and quality operations.
    Provides temporary storage so the AI agent can reference results across turns.
    """

    def __init__(self):
        self._frames: Dict[str, pd.DataFrame] = {}
        self._metadata: Dict[str, Dict[str, Any]] = {}

    def store(self, key: str, df: pd.DataFrame, metadata: Optional[Dict] = None) -> str:
        """Store a DataFrame with an optional metadata dict. Returns the key."""
        if not key:
            key = f"df_{uuid.uuid4().hex[:8]}"
        self._frames[key] = df
        self._metadata[key] = metadata or {}
        self._metadata[key]["stored_at"] = datetime.now(timezone.utc).isoformat()
        self._metadata[key]["rows"] = len(df)
        self._metadata[key]["columns"] = list(df.columns)
        return key

    def get(self, key: str) -> Optional[pd.DataFrame]:
        return self._frames.get(key)

    def get_metadata(self, key: str) -> Optional[Dict]:
        return self._metadata.get(key)

    def list_keys(self) -> List[str]:
        return list(self._frames.keys())

    def delete(self, key: str) -> bool:
        if key in self._frames:
            del self._frames[key]
            self._metadata.pop(key, None)
            return True
        return False

    def clear(self):
        self._frames.clear()
        self._metadata.clear()
