"""
Enhanced request tracking that works across threads.
Maintains backward compatibility with the original RequestTracking API.
"""

from flask import g, has_request_context
import uuid
import threading
import contextvars
from typing import Optional, Any, Dict
from contextlib import contextmanager
import functools
import logging

logger = logging.getLogger(__name__)

# Context variables that can be propagated across threads
_user_id_context: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar('user_id', default=None)
_request_id_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar('request_id', default=None)
_module_name_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar('module_name', default=None)

# Store contexts for each request/thread for propagation
_stored_contexts: Dict[str, contextvars.Context] = {}
_contexts_lock = threading.Lock()


class RequestTracking:
    """
    Enhanced request tracking that works across threads.
    Uses contextvars for thread propagation while maintaining Flask g compatibility.
    """
    
    @staticmethod
    def set_tracking(user_request_id: Optional[str] = None, 
                     module_name: Optional[str] = None,
                     user_id: Optional[Any] = None) -> str:
        """
        Set tracking info in both Flask's request context and contextvars.
        This ensures the context can be propagated to child threads.
        
        Args:
            user_request_id: Unique ID for the request
            module_name: Name of the module handling the request
            user_id: ID of the user making the request
        """
        if user_request_id is None:
            user_request_id = str(uuid.uuid4())
        
        # Set in contextvars (works across threads when properly propagated)
        _request_id_context.set(user_request_id)
        if module_name:
            _module_name_context.set(module_name)
        if user_id is not None:
            _user_id_context.set(user_id)
        
        # Store the context for later propagation
        with _contexts_lock:
            _stored_contexts[user_request_id] = contextvars.copy_context()
        
        # Also set in Flask context if available (for backward compatibility)
        if has_request_context():
            g.user_request_id = user_request_id
            if module_name:
                g.module_name = module_name
            if user_id is not None:
                g.user_id = user_id
        else:
            # Fallback to thread-local storage (backward compatibility)
            if not hasattr(threading.current_thread(), '_request_tracking'):
                threading.current_thread()._request_tracking = {}
            
            threading.current_thread()._request_tracking['user_request_id'] = user_request_id
            if module_name:
                threading.current_thread()._request_tracking['module_name'] = module_name
            if user_id is not None:
                threading.current_thread()._request_tracking['user_id'] = user_id
        
        logger.debug(f"Set tracking - request_id: {user_request_id}, module: {module_name}, user_id: {user_id}")
        return user_request_id
    
    @staticmethod
    def get_user_request_id() -> Optional[str]:
        """Get the current request's tracking ID."""
        # Try contextvars first (works across threads)
        request_id = _request_id_context.get()
        if request_id is not None:
            return request_id
        
        # Then try Flask context
        if has_request_context():
            request_id = getattr(g, 'user_request_id', None)
            if request_id is not None:
                return request_id
        
        # Finally try thread-local fallback
        tracking = getattr(threading.current_thread(), '_request_tracking', {})
        return tracking.get('user_request_id')
    
    @staticmethod
    def get_module_name() -> Optional[str]:
        """Get the current request's module name."""
        # Try contextvars first
        module_name = _module_name_context.get()
        if module_name is not None:
            return module_name
        
        # Then try Flask context
        if has_request_context():
            module_name = getattr(g, 'module_name', None)
            if module_name is not None:
                return module_name
        
        # Finally try thread-local fallback
        tracking = getattr(threading.current_thread(), '_request_tracking', {})
        return tracking.get('module_name')
    
    @staticmethod
    def get_user_id() -> Optional[Any]:
        """
        Get the current request's user ID.
        Returns None if no user context is set.
        """
        # Try contextvars first (works across threads)
        user_id = _user_id_context.get()
        if user_id is not None:
            return user_id
        
        # Then try Flask context
        if has_request_context():
            user_id = getattr(g, 'user_id', None)
            if user_id is not None:
                return user_id
        
        # Finally try thread-local fallback
        tracking = getattr(threading.current_thread(), '_request_tracking', {})
        return tracking.get('user_id')
    
    @staticmethod
    def set_user_id(user_id: Any):
        """
        Set just the user ID without affecting other tracking.
        Useful for setting user context in authenticated routes.
        """
        # Set in contextvars
        _user_id_context.set(user_id)
        
        # Also set in Flask context if available
        if has_request_context():
            g.user_id = user_id
        else:
            # Thread-local fallback
            if not hasattr(threading.current_thread(), '_request_tracking'):
                threading.current_thread()._request_tracking = {}
            threading.current_thread()._request_tracking['user_id'] = user_id
        
        logger.debug(f"Set user_id: {user_id}")
    
    @staticmethod
    def clear_tracking():
        """Clear tracking from all storage locations."""
        # Clear contextvars
        _request_id_context.set(None)
        _module_name_context.set(None)
        _user_id_context.set(None)
        
        # Clear Flask context
        if has_request_context():
            if hasattr(g, 'user_request_id'):
                delattr(g, 'user_request_id')
            if hasattr(g, 'module_name'):
                delattr(g, 'module_name')
            if hasattr(g, 'user_id'):
                delattr(g, 'user_id')
        
        # Clear thread-local
        if hasattr(threading.current_thread(), '_request_tracking'):
            delattr(threading.current_thread(), '_request_tracking')
    
    @staticmethod
    @contextmanager
    def with_user_context(user_id: Any):
        """
        Context manager to temporarily set user context.
        Useful for background tasks or API calls.
        
        Usage:
            with RequestTracking.with_user_context(user_id):
                # Code that needs user context
                agent.run(message)
        """
        old_user_id = RequestTracking.get_user_id()
        RequestTracking.set_user_id(user_id)
        try:
            yield
        finally:
            if old_user_id is not None:
                RequestTracking.set_user_id(old_user_id)
            else:
                # Clear user_id if it wasn't set before
                _user_id_context.set(None)
                if has_request_context() and hasattr(g, 'user_id'):
                    delattr(g, 'user_id')
                tracking = getattr(threading.current_thread(), '_request_tracking', {})
                if 'user_id' in tracking:
                    del tracking['user_id']
    
    @staticmethod
    def get_current_context() -> contextvars.Context:
        """
        Get a copy of the current context that can be used to propagate to threads.
        """
        return contextvars.copy_context()
    
    @staticmethod
    def run_in_context(context: contextvars.Context, func, *args, **kwargs):
        """
        Run a function in a specific context.
        Useful for executing code in a thread with the original request context.
        """
        return context.run(func, *args, **kwargs)
    
    @staticmethod
    def cleanup_stored_context(request_id: str):
        """Clean up stored context for a request to prevent memory leaks."""
        with _contexts_lock:
            if request_id in _stored_contexts:
                del _stored_contexts[request_id]
                logger.debug(f"Cleaned up context for request {request_id}")


class ContextAwareThread(threading.Thread):
    """
    A Thread subclass that automatically propagates context variables.
    Use this instead of threading.Thread when you need to maintain user context.
    """
    
    def __init__(self, *args, **kwargs):
        # Capture the current context before creating the thread
        self._context = contextvars.copy_context()
        
        # Also capture Flask context if available
        self._flask_user_id = None
        self._flask_request_id = None
        self._flask_module_name = None
        
        if has_request_context():
            self._flask_user_id = getattr(g, 'user_id', None)
            self._flask_request_id = getattr(g, 'user_request_id', None)
            self._flask_module_name = getattr(g, 'module_name', None)
        
        super().__init__(*args, **kwargs)
        
        logger.debug(f"Created ContextAwareThread with user_id: {self._flask_user_id or _user_id_context.get()}")
    
    def run(self):
        """Run the thread's target in the captured context"""
        
        def _run_with_context():
            # Set Flask-captured values in contextvars if they exist
            if self._flask_user_id is not None:
                _user_id_context.set(self._flask_user_id)
            if self._flask_request_id is not None:
                _request_id_context.set(self._flask_request_id)
            if self._flask_module_name is not None:
                _module_name_context.set(self._flask_module_name)
            
            # Also set in thread-local for backward compatibility
            if any([self._flask_user_id, self._flask_request_id, self._flask_module_name]):
                if not hasattr(threading.current_thread(), '_request_tracking'):
                    threading.current_thread()._request_tracking = {}
                
                if self._flask_user_id is not None:
                    threading.current_thread()._request_tracking['user_id'] = self._flask_user_id
                if self._flask_request_id is not None:
                    threading.current_thread()._request_tracking['user_request_id'] = self._flask_request_id
                if self._flask_module_name is not None:
                    threading.current_thread()._request_tracking['module_name'] = self._flask_module_name
            
            # Run the actual target
            if self._target:
                self._target(*self._args, **self._kwargs)
        
        # Run everything in the captured context
        self._context.run(_run_with_context)


def with_request_context(func):
    """
    Decorator to ensure a function runs with the current request context.
    Use this for functions that will be executed in new threads.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Capture the current context at call time
        context = contextvars.copy_context()
        
        # Also capture Flask context if available
        flask_context = {}
        if has_request_context():
            flask_context['user_id'] = getattr(g, 'user_id', None)
            flask_context['request_id'] = getattr(g, 'user_request_id', None)
            flask_context['module_name'] = getattr(g, 'module_name', None)
        
        def run_with_context():
            # Restore Flask context values
            for key, value in flask_context.items():
                if value is not None:
                    if key == 'user_id':
                        _user_id_context.set(value)
                    elif key == 'request_id':
                        _request_id_context.set(value)
                    elif key == 'module_name':
                        _module_name_context.set(value)
            
            return func(*args, **kwargs)
        
        # Run the function in the captured context
        return context.run(run_with_context)
    
    return wrapper


def ensure_user_context(default_user_id="system"):
    """
    Decorator to ensure a function has a valid user context.
    If no user_id is present, uses the default.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current_user_id = RequestTracking.get_user_id()
            
            if current_user_id is None:
                logger.warning(f"No user context for {func.__name__}, using default: {default_user_id}")
                # Use context manager to set temporary user context
                with RequestTracking.with_user_context(default_user_id):
                    return func(*args, **kwargs)
            else:
                return func(*args, **kwargs)
        
        return wrapper
    return decorator


# Backward compatibility - if someone imports the old class
__all__ = ['RequestTracking', 'ContextAwareThread', 'with_request_context', 'ensure_user_context']
