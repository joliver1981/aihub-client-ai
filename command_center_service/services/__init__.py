"""
Command Center Service — Session Manager
============================================
Manages conversation sessions with local JSON file persistence.
Each session maps to a LangGraph thread_id for state persistence.
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
        self.is_pinned = False
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
            "is_pinned": self.is_pinned,
        }
        if self.user_context:
            result["user"] = self.user_context.to_dict()
        return result


class ChatHistoryStore:
    """File-backed storage for session metadata and messages."""

    def __init__(self, data_dir: Path):
        self.history_dir = data_dir / "chat_history"
        self.sessions_dir = self.history_dir / "sessions"
        self._index_cache: Optional[dict] = None
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    @property
    def index_file(self) -> Path:
        return self.history_dir / "index.json"

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

    def _session_file(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"

    def save_session_meta(self, session: Session):
        index = self._load_index()
        index["sessions"][session.session_id] = session.to_dict()
        self._save_index()

    def delete_session(self, session_id: str):
        index = self._load_index()
        index["sessions"].pop(session_id, None)
        self._save_index()
        msg_file = self._session_file(session_id)
        if msg_file.exists():
            try:
                msg_file.unlink()
            except Exception as e:
                logger.error(f"Error deleting session file {session_id}: {e}")

    def delete_all_sessions(self):
        """Delete all sessions."""
        index = self._load_index()
        index["sessions"] = {}
        self._save_index()
        # Delete all session message files
        for session_file in self.sessions_dir.glob("*.json"):
            try:
                session_file.unlink()
            except Exception as e:
                logger.error(f"Error deleting session file {session_file}: {e}")

    def load_all_sessions(self) -> dict[str, Session]:
        index = self._load_index()
        sessions: dict[str, Session] = {}
        for sid, meta in index.get("sessions", {}).items():
            session = Session(session_id=sid, title=meta.get("title", "New Chat"))
            session.created_at = meta.get("created_at", session.created_at)
            session.updated_at = meta.get("updated_at", session.updated_at)
            session.message_count = meta.get("message_count", 0)
            session.is_pinned = meta.get("is_pinned", False)
            user_data = meta.get("user")
            if user_data:
                try:
                    session.user_context = UserContext.from_dict(user_data)
                except Exception:
                    pass
            sessions[sid] = session
        logger.info(f"Restored {len(sessions)} sessions from disk")
        return sessions

    def append_message(self, session_id: str, role: str, content: str) -> dict:
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


class SessionManager:
    """Session store with local JSON file persistence."""

    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir is None:
            data_dir = Path(__file__).parent.parent / "data"
        self._store = ChatHistoryStore(data_dir)
        self._sessions: dict[str, Session] = self._store.load_all_sessions()
        self._backfill_titles()

    def _backfill_titles(self):
        """Generate titles for existing sessions still named 'New Chat'."""
        count = 0
        for sid, session in self._sessions.items():
            if session.title != "New Chat":
                continue
            try:
                messages = self._store.load_messages(sid)
                first_user = next((m for m in messages if m.get("role") == "user"), None)
                if first_user:
                    text = first_user.get("content", "").strip()
                    if text:
                        title = text[:50].strip()
                        if len(text) > 50:
                            title = title.rsplit(' ', 1)[0] + '…'
                        session.title = title
                        self._store.save_session_meta(session)
                        count += 1
            except Exception:
                pass
        if count:
            logger.info(f"Backfilled titles for {count} sessions")

    def create_session(self, title: str = "New Chat", user_context: Optional[UserContext] = None) -> Session:
        session_id = str(uuid.uuid4())
        session = Session(session_id=session_id, title=title, user_context=user_context)
        self._sessions[session_id] = session
        self._store.save_session_meta(session)
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
            self._store.save_session_meta(session)

    def list_sessions(self) -> list[dict]:
        sessions = sorted(
            self._sessions.values(),
            key=lambda s: (s.is_pinned, s.updated_at),
            reverse=True,
        )
        return [s.to_dict() for s in sessions]

    # ── Ownership-filtered access (BUG-R3-001/002/003/005/006/007 fix) ─────
    # Sessions may have been created BEFORE user_context started being
    # persisted; for those legacy sessions we only allow visibility to
    # admin/developer roles (role >= 2). Regular users see strictly their
    # own rows, filtered by both user_id and tenant_id.
    @staticmethod
    def _matches_owner(session: "Session", user_id: Optional[int],
                       tenant_id: Optional[int], role: int = 0) -> bool:
        """Decide whether a caller should see/modify this session.

        Fail-closed rules:
          - Cross-tenant access is ALWAYS blocked, including for admins.
            Tenant isolation is an absolute boundary — an admin of
            tenant 2 must not see tenant 1 data.
          - Within the caller's own tenant, admins/devs (role >= 2) see
            every session, including legacy/unstamped rows.
          - Within the caller's own tenant, regular users see only their
            own sessions (matched by user_id).
          - Callers that omit user_id or tenant_id see nothing. "No
            identity" means "not the owner", never "skip the check".
        """
        if user_id is None or tenant_id is None:
            return False

        ctx = session.user_context
        if ctx is None:
            # Legacy/unstamped session. The owner is unknown and therefore
            # so is the tenant. We cannot safely grant access — that would
            # cross tenant boundaries. Hide these from everyone, including
            # admins. A one-time migration should stamp legacy rows with a
            # default owner/tenant before deployment (see
            # e2e_app_tests/.../migrate_legacy_sessions.py).
            return False

        try:
            owner_uid = int(ctx.user_id)
            owner_tid = int(ctx.tenant_id)
            req_uid = int(user_id)
            req_tid = int(tenant_id)
        except (TypeError, ValueError):
            return False

        # Absolute cross-tenant block.
        if owner_tid != req_tid:
            return False

        # Within tenant: admin sees all; regular users see only their own.
        if role >= 2:
            return True
        return owner_uid == req_uid

    def list_sessions_for(self, user_id: Optional[int], tenant_id: Optional[int],
                          role: int = 0) -> list[dict]:
        sessions = sorted(
            (s for s in self._sessions.values()
             if self._matches_owner(s, user_id, tenant_id, role)),
            key=lambda s: (s.is_pinned, s.updated_at),
            reverse=True,
        )
        return [s.to_dict() for s in sessions]

    def get_session_for(self, session_id: str, user_id: Optional[int],
                        tenant_id: Optional[int], role: int = 0) -> Optional["Session"]:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if not self._matches_owner(session, user_id, tenant_id, role):
            return None
        return session

    def attach_user_context_if_missing(self, session_id: str,
                                       user_context: Optional[UserContext]):
        """Populate user_context on a session the first time we see it. This
        stamps ownership on freshly-created sessions so subsequent ownership
        checks can distinguish them from legacy rows."""
        if user_context is None:
            return
        session = self._sessions.get(session_id)
        if session is None:
            return
        if session.user_context is None:
            session.user_context = user_context
            self._store.save_session_meta(session)

    def pin_session(self, session_id: str, pinned: bool) -> bool:
        session = self._sessions.get(session_id)
        if session:
            session.is_pinned = pinned
            self._store.save_session_meta(session)
            return True
        return False

    def delete_session(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
            self._store.delete_session(session_id)
            logger.info(f"Deleted session: {session_id}")
            return True
        return False

    def delete_all_sessions(self):
        """Delete all sessions."""
        self._store.delete_all_sessions()
        self._sessions = {}
        logger.info("Deleted all sessions")

    def add_message(self, session_id: str, role: str, content: str):
        session = self._sessions.get(session_id)
        if not session:
            return
        self._store.append_message(session_id, role, content)
        session.touch()  # increments message_count + updates timestamp
        self._store.save_session_meta(session)

    def get_messages(self, session_id: str, limit: int = 0) -> list[dict]:
        msgs = self._store.get_messages(session_id)
        if limit > 0:
            return msgs[-limit:]
        return msgs

    def get_session_state(self, session_id: str) -> dict:
        """Load persisted session state (active delegation, preferences, etc.)."""
        state_file = self._store.sessions_dir / f"{session_id}_state.json"
        if state_file.exists():
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading session state for {session_id}: {e}")
        return {}

    def save_session_state(self, session_id: str, state: dict):
        """Persist session state between graph invocations."""
        state_file = self._store.sessions_dir / f"{session_id}_state.json"
        try:
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Error saving session state for {session_id}: {e}")
