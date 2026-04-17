"""
Builder Service — Session Manager
====================================
Manages conversation sessions with local JSON file persistence.
Each session maps to a LangGraph thread_id for state persistence.

File Structure:
    data/chat_history/
        index.json                  # Session metadata index
        sessions/{session_id}.json  # Messages per session
"""

import json
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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

    @classmethod
    def from_dict(cls, data: dict) -> "UserContext":
        return cls(
            user_id=data["user_id"],
            role=data["role"],
            tenant_id=data["tenant_id"],
            username=data["username"],
            name=data["name"],
        )


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

    def set_user_context(self, user_context: UserContext):
        """Set the user context for this session."""
        self.user_context = user_context

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


# ─── Chat History Store ──────────────────────────────────────────────────


class ChatHistoryStore:
    """
    File-backed storage for session metadata and messages.
    Uses atomic writes (temp file + rename) for crash safety.

    File layout:
        {data_dir}/chat_history/index.json
        {data_dir}/chat_history/sessions/{session_id}.json
    """

    def __init__(self, data_dir: Path):
        self.history_dir = data_dir / "chat_history"
        self.sessions_dir = self.history_dir / "sessions"
        self._index_cache: Optional[dict] = None

        # Ensure directories exist
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    @property
    def index_file(self) -> Path:
        return self.history_dir / "index.json"

    # ── Index management ──

    def _load_index(self) -> dict:
        if self._index_cache is not None:
            return self._index_cache

        if not self.index_file.exists():
            self._index_cache = {"version": "1.0", "sessions": {}}
            return self._index_cache

        try:
            with open(self.index_file, "r", encoding="utf-8") as f:
                self._index_cache = json.load(f)
            if "sessions" not in self._index_cache:
                self._index_cache["sessions"] = {}
            return self._index_cache
        except Exception as e:
            logger.error(f"Error loading chat history index: {e}")
            self._index_cache = {"version": "1.0", "sessions": {}}
            return self._index_cache

    def _save_index(self):
        if self._index_cache is None:
            return
        try:
            temp_file = self.index_file.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self._index_cache, f, indent=2, default=str)
            temp_file.replace(self.index_file)
        except Exception as e:
            logger.error(f"Error saving chat history index: {e}")

    # ── Session persistence ──

    def _session_file(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"

    def save_session_meta(self, session: Session):
        """Persist session metadata to the index."""
        index = self._load_index()
        index["sessions"][session.session_id] = session.to_dict()
        self._save_index()

    def delete_session(self, session_id: str):
        """Remove session from index and delete its message file."""
        index = self._load_index()
        index["sessions"].pop(session_id, None)
        self._save_index()

        msg_file = self._session_file(session_id)
        if msg_file.exists():
            try:
                msg_file.unlink()
            except Exception as e:
                logger.error(f"Error deleting session file {session_id}: {e}")

    def load_all_sessions(self) -> dict[str, Session]:
        """Restore all sessions from disk. Called once at startup."""
        index = self._load_index()
        sessions: dict[str, Session] = {}

        for sid, meta in index.get("sessions", {}).items():
            session = Session(
                session_id=sid,
                title=meta.get("title", "New Chat"),
            )
            session.created_at = meta.get("created_at", session.created_at)
            session.updated_at = meta.get("updated_at", session.updated_at)
            session.message_count = meta.get("message_count", 0)

            # Restore user context if present
            user_data = meta.get("user")
            if user_data:
                try:
                    session.user_context = UserContext.from_dict(user_data)
                except Exception:
                    pass

            sessions[sid] = session

        logger.info(f"Restored {len(sessions)} sessions from disk")
        return sessions

    # ── Message persistence ──

    def _load_messages(self, session_id: str) -> list[dict]:
        msg_file = self._session_file(session_id)
        if not msg_file.exists():
            return []
        try:
            with open(msg_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("messages", [])
        except Exception as e:
            logger.error(f"Error loading messages for session {session_id}: {e}")
            return []

    def _save_messages(self, session_id: str, messages: list[dict]):
        msg_file = self._session_file(session_id)
        try:
            temp_file = msg_file.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump({"session_id": session_id, "messages": messages}, f, indent=2, default=str)
            temp_file.replace(msg_file)
        except Exception as e:
            logger.error(f"Error saving messages for session {session_id}: {e}")

    def append_message(self, session_id: str, role: str, content: str) -> dict:
        """Append a message and persist to disk. Returns the message dict."""
        messages = self._load_messages(session_id)
        msg = {
            "id": f"msg_{len(messages) + 1:04d}",
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        messages.append(msg)
        self._save_messages(session_id, messages)
        return msg

    def get_messages(self, session_id: str) -> list[dict]:
        return self._load_messages(session_id)


# ─── Session Manager ──────────────────────────────────────────────────


class SessionManager:
    """
    Session store with local JSON file persistence.
    Sessions and messages survive service restarts.
    """

    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir is None:
            data_dir = Path(__file__).parent.parent / "data"

        self._store = ChatHistoryStore(data_dir)
        self._sessions: dict[str, Session] = self._store.load_all_sessions()

    def create_session(self, title: str = "New Chat", user_context: Optional[UserContext] = None) -> Session:
        session_id = str(uuid.uuid4())
        session = Session(session_id=session_id, title=title, user_context=user_context)
        self._sessions[session_id] = session
        self._store.save_session_meta(session)
        logger.info(f"Created session: {session_id}" + (f" for user {user_context.username}" if user_context else ""))
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
            self._store.save_session_meta(session)

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
            self._store.delete_session(session_id)
            logger.info(f"Deleted session: {session_id}")
            return True
        return False

    # ── Message management ──

    def add_message(self, session_id: str, role: str, content: str):
        """Add a message to a session and persist to disk."""
        session = self._sessions.get(session_id)
        if not session:
            return
        self._store.append_message(session_id, role, content)
        # Update session metadata
        session.updated_at = datetime.now(timezone.utc).isoformat()
        self._store.save_session_meta(session)

    def get_messages(self, session_id: str) -> list[dict]:
        """Get all messages for a session."""
        return self._store.get_messages(session_id)
