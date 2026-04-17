"""
Request tracking using Flask's request context.
This provides globally accessible storage that's unique per request.
"""

from flask import g, has_request_context
import uuid
from typing import Optional, Any
from contextlib import contextmanager

class RequestTracking:
    """
    Uses Flask's 'g' object for request-scoped global storage.
    The 'g' object is unique per request and thread-safe.
    """
    
    @staticmethod
    def set_tracking(user_request_id: Optional[str] = None, 
                     module_name: Optional[str] = None,
                     user_id: Optional[Any] = None) -> str:
        """
        Set tracking info in Flask's request context.
        This is globally accessible but unique per request.
        
        Args:
            user_request_id: Unique ID for the request
            module_name: Name of the module handling the request
            user_id: ID of the user making the request
        """
        if not has_request_context():
            # Fallback for non-Flask contexts (like background jobs)
            import threading
            if not hasattr(threading.current_thread(), '_request_tracking'):
                threading.current_thread()._request_tracking = {}
            
            if user_request_id is None:
                user_request_id = str(uuid.uuid4())
            
            threading.current_thread()._request_tracking['user_request_id'] = user_request_id
            if module_name:
                threading.current_thread()._request_tracking['module_name'] = module_name
            if user_id is not None:
                threading.current_thread()._request_tracking['user_id'] = user_id
            return user_request_id
        
        # In Flask request context
        if user_request_id is None:
            user_request_id = str(uuid.uuid4())
        
        g.user_request_id = user_request_id
        if module_name:
            g.module_name = module_name
        if user_id is not None:
            g.user_id = user_id
        
        return user_request_id
    
    @staticmethod
    def get_user_request_id() -> Optional[str]:
        """Get the current request's tracking ID."""
        if not has_request_context():
            # Fallback for non-Flask contexts
            import threading
            tracking = getattr(threading.current_thread(), '_request_tracking', {})
            return tracking.get('user_request_id')
        
        return getattr(g, 'user_request_id', None)
    
    @staticmethod
    def get_module_name() -> Optional[str]:
        """Get the current request's module name."""
        if not has_request_context():
            # Fallback for non-Flask contexts
            import threading
            tracking = getattr(threading.current_thread(), '_request_tracking', {})
            return tracking.get('module_name')
        
        return getattr(g, 'module_name', None)
    
    @staticmethod
    def get_user_id() -> Optional[Any]:
        """
        Get the current request's user ID.
        Returns None if no user context is set.
        """
        if not has_request_context():
            # Fallback for non-Flask contexts
            import threading
            tracking = getattr(threading.current_thread(), '_request_tracking', {})
            return tracking.get('user_id')
        
        return getattr(g, 'user_id', None)
    
    @staticmethod
    def set_user_id(user_id: Any):
        """
        Set just the user ID without affecting other tracking.
        Useful for setting user context in authenticated routes.
        """
        if not has_request_context():
            import threading
            if not hasattr(threading.current_thread(), '_request_tracking'):
                threading.current_thread()._request_tracking = {}
            threading.current_thread()._request_tracking['user_id'] = user_id
        else:
            g.user_id = user_id
    
    @staticmethod
    def clear_tracking():
        """Clear tracking (usually not needed as Flask cleans up g automatically)."""
        if not has_request_context():
            import threading
            if hasattr(threading.current_thread(), '_request_tracking'):
                delattr(threading.current_thread(), '_request_tracking')
            return
        
        if hasattr(g, 'user_request_id'):
            delattr(g, 'user_request_id')
        if hasattr(g, 'module_name'):
            delattr(g, 'module_name')
        if hasattr(g, 'user_id'):
            delattr(g, 'user_id')

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
                if not has_request_context():
                    import threading
                    tracking = getattr(threading.current_thread(), '_request_tracking', {})
                    if 'user_id' in tracking:
                        del tracking['user_id']
                else:
                    if hasattr(g, 'user_id'):
                        delattr(g, 'user_id')
                        