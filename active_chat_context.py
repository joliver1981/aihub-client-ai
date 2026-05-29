"""
ContextVars holding the currently active chat context.

Set by the chat request handler before invoking GeneralAgent so that
module-level @tool functions (which can't closure-capture per-request
state) can resolve which conversation and user they're running for.

Usage:
    from active_chat_context import bind_active_chat, get_active_conversation_id

    with bind_active_chat(conversation_id="abc123", user_id=42):
        agent.invoke(...)   # tools called inside can read the context

    # In a tool:
    conv_id = get_active_conversation_id()
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional

_conversation_id: ContextVar[Optional[str]] = ContextVar(
    "active_conversation_id", default=None
)
_user_id: ContextVar[Optional[int]] = ContextVar(
    "active_user_id", default=None
)
_agent_id: ContextVar[Optional[int]] = ContextVar(
    "active_agent_id", default=None
)


def get_active_conversation_id() -> Optional[str]:
    return _conversation_id.get()


def get_active_user_id() -> Optional[int]:
    return _user_id.get()


def get_active_agent_id() -> Optional[int]:
    return _agent_id.get()


@contextmanager
def bind_active_chat(
    conversation_id: Optional[str],
    user_id: Optional[int],
    agent_id: Optional[int] = None,
):
    t1 = _conversation_id.set(conversation_id)
    t2 = _user_id.set(user_id)
    t3 = _agent_id.set(agent_id)
    try:
        yield
    finally:
        _conversation_id.reset(t1)
        _user_id.reset(t2)
        _agent_id.reset(t3)
