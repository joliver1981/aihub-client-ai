"""
Simple Admin Tier & Usage Page
Shows current tier, license key, and usage vs limits
"""

from flask import Blueprint, render_template, jsonify, request, g
from flask_login import login_required, current_user
import pyodbc
import os
import logging
from logging.handlers import WatchedFileHandler
import requests
from functools import wraps
import time
import threading
from CommonUtils import get_cloud_db_connection_string,  get_db_connection_string, rotate_logs_on_startup, get_log_path
import config as cfg


rotate_logs_on_startup(os.getenv('ADMIN_TIER_USAGE_LOG', get_log_path('admin_tier_usage_log.txt')))

# Configure logging
logger = logging.getLogger("AdminTierUsage")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler = WatchedFileHandler(filename=os.getenv('ADMIN_TIER_USAGE_LOG', get_log_path('admin_tier_usage_log.txt')), encoding='utf-8')
handler.setFormatter(formatter)
logger.addHandler(handler)


# Create blueprint
admin_tier_bp = Blueprint('admin_tier', __name__, url_prefix='/admin/tier')


# ============================================================================
# MODULE-LEVEL CACHE FOR TIER DATA
# ============================================================================
# This cache persists across requests, unlike Flask's g object which is per-request.
# This prevents hitting the Cloud API on every single page load.

_tier_cache = {
    'data': None,
    'timestamp': 0,
    'lock': threading.Lock()
}

# Cache TTL in seconds
# Shorter = more API calls but fresher data
# Longer = fewer API calls but potentially stale data
TIER_CACHE_TTL = int(cfg.TIER_CACHE_TTL or 900)


def invalidate_tier_cache():
    """
    Invalidate the tier cache, forcing a refresh on next access.
    Call this when you know tier data has changed, e.g.:
    - After subscription upgrade/downgrade
    - After settings change
    - After admin override
    """
    global _tier_cache
    with _tier_cache['lock']:
        _tier_cache['data'] = None
        _tier_cache['timestamp'] = 0
        logger.info("Tier cache invalidated")


def get_tier_cache_status():
    """
    Get current cache status for debugging/monitoring.
    Returns dict with cache state info.
    """
    global _tier_cache
    current_time = time.time()
    age = current_time - _tier_cache['timestamp'] if _tier_cache['timestamp'] > 0 else None
    
    return {
        'has_data': _tier_cache['data'] is not None,
        'age_seconds': round(age, 1) if age else None,
        'ttl_seconds': TIER_CACHE_TTL,
        'is_expired': age > TIER_CACHE_TTL if age else True,
        'next_refresh_in': round(TIER_CACHE_TTL - age, 1) if age and age < TIER_CACHE_TTL else 0
    }


# ============================================================================
# DECORATORS & HELPERS
# ============================================================================

def admin_required(f):
    """Decorator to ensure only admins can access"""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not hasattr(current_user, 'role') or current_user.role < 3:
            return "Access denied", 403
        return f(*args, **kwargs)
    return decorated_function

def get_connection_string():
    """Get database connection string"""
    return get_db_connection_string()

@admin_tier_bp.route('/')
@admin_required
def index():
    """Main tier and usage page"""
    return render_template('admin_tier_usage.html')


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_agent_user_env_info():
    try:
        import time
        tenant_id = os.getenv('API_KEY')

        # Time the cloud DB call
        start = time.time()

        # Get request usage stats
        connCloud = pyodbc.connect(get_cloud_db_connection_string())
        cursorCloud = connCloud.cursor()
        cursorCloud.execute("EXEC tenant.sp_setTenantContext ?", tenant_id)
        print(f"TIMING: Cloud Connecting took {time.time() - start:.2f}s")
        logger.debug(f"TIMING: Connecting took {time.time() - start:.2f}s")
        cursorCloud.execute("""
            SELECT COUNT(DISTINCT RequestId) as request_count
            FROM PlatformUsageLog 
            WHERE TokensUsed > 0 
            AND RequestTimestamp >= DATEADD(month, DATEDIFF(month, 0, GETUTCDATE()), 0)
            AND RequestTimestamp < DATEADD(month, DATEDIFF(month, 0, GETUTCDATE()) + 1, 0)
        """)
        usage_req_row = cursorCloud.fetchone()
        current_requests = usage_req_row.request_count
        cursorCloud.close()
        connCloud.close()
        print(f"TIMING: Cloud DB took {time.time() - start:.2f}s")
        logger.debug(f"TIMING: Cloud DB took {time.time() - start:.2f}s")

        # Get local stats
        conn = pyodbc.connect(get_db_connection_string())
        cursor = conn.cursor()

        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", tenant_id)
        
        # Get current usage
        cursor.execute("""
            SELECT 
                (SELECT COUNT(*) FROM AgentEnvironments WHERE is_deleted = 0) as env_count,
                (SELECT COUNT(*) FROM Agents WHERE [enabled] = 1) as agent_count,
                (SELECT COUNT(*) FROM AgentTools WHERE custom_tool = 1 AND [enabled] = 1) as tool_count,
                (SELECT COUNT(*) FROM [User]) as user_count
        """)
        
        usage_row = cursor.fetchone()
        current_usage = {
            'environments': usage_row.env_count,
            'agents': usage_row.agent_count,
            'custom_tools': usage_row.tool_count,
            'users': usage_row.user_count,
            'requests': current_requests
        }

        # Get detailed user statistics
        cursor.execute("""
            SELECT 
                COUNT(*) as total_users,
                SUM(CASE WHEN role = 3 THEN 1 ELSE 0 END) as admin_count,
                SUM(CASE WHEN role = 2 THEN 1 ELSE 0 END) as developer_count,
                SUM(CASE WHEN role = 1 THEN 1 ELSE 0 END) as user_count
            FROM [User]
        """)

        user_stats_row = cursor.fetchone()
        user_statistics = {
            'total': user_stats_row.total_users if user_stats_row else 0,
            'by_role': {
                'admins': user_stats_row.admin_count if user_stats_row else 0,
                'developers': user_stats_row.developer_count if user_stats_row else 0,
                'users': user_stats_row.user_count if user_stats_row else 0
            },
            'activity': {
                'today': 0,  # Not tracking logins in your current schema
                'last_7_days': 0,
                'last_30_days': 0
            }
        }
        
        cursor.close()
        conn.close()
        logger.debug(f"TIMING: Local DB took {time.time() - start:.2f}s")
    except Exception as e:
        print(str(e))
        user_statistics = {}
        current_usage = {}

    return user_statistics, current_usage

def merge_settings_with_tier_features(tier_features, settings):
    """
    Merge settings with tier_features, with settings taking precedence.
    Settings can override tier features for custom tenant configurations.
    
    Priority: settings > tier_features
    
    Args:
        tier_features: Base limits/features from subscription tier
        settings: Tenant-specific overrides from TenantSettings
        
    Returns:
        Merged dict with settings overriding tier_features where present
    """
    # Start with tier_features as base
    merged = tier_features.copy()
    
    # Define mappings between settings and tier_features keys
    # Settings uses different naming conventions, so we need to map them
    setting_mappings = {
        # Limit mappings
        'max_users': 'max_users',
        'max_agents': 'max_agents',
        'max_custom_tools': 'max_custom_tools',
        'max_environments': 'max_environments',
        'max_requests': 'max_requests',
        # Feature flag mappings
        'documents_enabled': 'documents_enabled',
        'environments_enabled': 'environments_enabled',
        'workflows_enabled': 'workflows_enabled',
        'enterprise_features_enabled': 'enterprise_features_enabled'
    }
    
    # Override with values from settings if they exist and are not None
    overrides_applied = []
    for settings_key, tier_key in setting_mappings.items():
        if settings_key in settings and settings[settings_key] is not None:
            old_value = merged.get(tier_key)
            new_value = settings[settings_key]
            
            # Only override if the value is different
            if old_value != new_value:
                merged[tier_key] = new_value
                overrides_applied.append(f"{tier_key}: {old_value} -> {new_value}")
    
    # Log overrides for debugging
    if overrides_applied:
        logger.info(f"Settings overrides applied: {', '.join(overrides_applied)}")
    
    return merged


def get_subscription_limits_from_cloud(force_refresh=False):
    """
    Get subscription limits and features from Cloud API.
    Uses in-memory cache with TTL to avoid hitting API on every request.
    
    Args:
        force_refresh: If True, bypass cache and fetch fresh data
    
    Returns:
        Dict with merged tier_features, subscription info, and settings.
        Returns cached/stale data on API errors to prevent page failures.
    """
    global _tier_cache
    
    current_time = time.time()
    cache_age = current_time - _tier_cache['timestamp']
    
    # Check if cache is valid (not expired and has data)
    if not force_refresh:
        if (_tier_cache['data'] is not None and cache_age < TIER_CACHE_TTL):
            logger.debug(f"Using cached tier data (age: {cache_age:.1f}s)")
            return _tier_cache['data']
    
    # Need to refresh - acquire lock to prevent thundering herd
    # (multiple threads all trying to refresh at once)
    with _tier_cache['lock']:
        # Double-check after acquiring lock (another thread may have refreshed while we waited)
        cache_age = time.time() - _tier_cache['timestamp']
        if not force_refresh:
            if (_tier_cache['data'] is not None and cache_age < TIER_CACHE_TTL):
                logger.debug(f"Using cached tier data after lock (age: {cache_age:.1f}s)")
                return _tier_cache['data']
        
        # Actually fetch from Cloud API
        try:
            tenant_id = os.getenv('API_KEY')
            api_url = os.getenv('AI_HUB_API_URL', '').rstrip('/')
            
            if not api_url:
                logger.error("AI_HUB_API_URL not configured")
                # Return stale cache if available
                if _tier_cache['data'] is not None:
                    logger.warning("Returning stale cache due to missing API URL")
                return _tier_cache['data']
            
            logger.debug(f"Fetching fresh tier data from Cloud API")
            
            # Make request to Cloud API with configured timeout
            response = requests.get(
                f"{api_url}/api/tenant/subscription-info",
                headers={'X-License-Key': tenant_id},
                timeout=cfg.CLOUD_API_REQUESTS_TIMEOUT
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    # Extract tier_features and settings
                    tier_features = data.get('tier_features', {})
                    settings = data.get('settings', {})
                    
                    # Merge with settings taking precedence
                    merged_features = merge_settings_with_tier_features(tier_features, settings)
                    
                    # Replace tier_features with merged version
                    data['tier_features'] = merged_features
                    data['original_tier_features'] = tier_features  # Keep original for reference
                    
                    # Update cache
                    _tier_cache['data'] = data
                    _tier_cache['timestamp'] = time.time()
                    
                    logger.info(f"Tier cache refreshed successfully (TTL: {TIER_CACHE_TTL}s)")
                    return data
            
            logger.warning(f"Failed to get subscription info: {response.status_code}")
            # Return stale cache if available
            if _tier_cache['data'] is not None:
                logger.warning(f"Returning stale cache (age: {cache_age:.1f}s) due to API error")
            return _tier_cache['data']
            
        except requests.exceptions.Timeout:
            logger.warning(f"Cloud API request timed out")
            # Return stale cache if available - this prevents page hangs
            if _tier_cache['data'] is not None:
                logger.warning(f"Returning stale cache (age: {cache_age:.1f}s) due to timeout")
            return _tier_cache['data']
            
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Cloud API connection error: {e}")
            if _tier_cache['data'] is not None:
                logger.warning(f"Returning stale cache (age: {cache_age:.1f}s) due to connection error")
            return _tier_cache['data']
            
        except Exception as e:
            logger.error(f"Error fetching subscription info: {e}")
            # Return stale cache if available
            if _tier_cache['data'] is not None:
                logger.warning(f"Returning stale cache (age: {cache_age:.1f}s) due to error")
            return _tier_cache['data']


def get_cached_tier_data(force_refresh=False):
    """
    Get both limits (from Cloud API with settings overrides) and usage (from local DB).
    
    This function combines:
    - Cached Cloud API data (tier features, subscription info)
    - Fresh local database data (current usage counts)
    
    Also caches the combined result in Flask's g object for the duration of the 
    current request to avoid redundant processing within a single request.
    
    Args:
        force_refresh: If True, bypass Cloud API cache and fetch fresh data
    
    Returns dict with:
    - tier_features: The merged limits/features (settings override tier defaults)
    - current_usage: The actual current usage from local DB
    - subscription: Subscription details
    - settings: Original settings for reference
    """
    # Check if already processed in this request (g object cache)
    # This prevents multiple calls within the same request from re-processing
    if not force_refresh and hasattr(g, 'tier_data'):
        return g.tier_data
    
    # Get limits from Cloud API (uses TTL cache internally)
    subscription_data = get_subscription_limits_from_cloud(force_refresh=force_refresh)
    
    if not subscription_data or not subscription_data.get('success'):
        logger.warning("Could not load subscription limits from Cloud API")
        return None
    
    # Get current usage from local database (always fresh)
    user_statistics, current_usage = get_agent_user_env_info()
    
    # Combine into single cached object
    tier_data = {
        'tier_features': subscription_data.get('tier_features', {}),  # Already merged with settings
        'settings': subscription_data.get('settings', {}),
        'subscription': subscription_data.get('subscription', {}),
        'tenant_info': subscription_data.get('tenant_info', {}),
        'current_usage': current_usage,
        'user_statistics': user_statistics,
        'original_tier_features': subscription_data.get('original_tier_features', {})  # Before settings override
    }
    
    # Cache in g object for this request
    g.tier_data = tier_data
    
    return tier_data

# ============================================================================
# USAGE TIER DECORATORS
# ============================================================================

def tier_allows_feature(feature_name):
    """
    OPTION 1: Feature-based decorator
    Check if current subscription tier allows access to a specific feature.
    
    Usage:
        @tier_allows_feature('environments')
        @tier_allows_feature('workflows')
        @tier_allows_feature('documents')
    
    Features are checked from merged tier_features (with settings overrides applied).
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Load tier data if not already loaded
            tier_data = get_cached_tier_data()
            
            if not tier_data:
                logger.warning("Could not load tier data")
                return jsonify({
                    'status': 'error',
                    'message': 'Unable to verify subscription tier'
                }), 503
            
            # Check if feature is enabled in merged tier_features (settings already applied)
            tier_features = tier_data.get('tier_features', {})
            feature_key = f"{feature_name}_enabled"
            
            if not tier_features.get(feature_key, False):
                return jsonify({
                    'status': 'error',
                    'message': f'Your subscription tier does not include access to {feature_name}',
                    'feature_required': feature_name,
                    'upgrade_required': True
                }), 403
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def tier_allows_resource(resource_type):
    """
    OPTION 2: Resource limit decorator
    Check if current usage is within limits for a specific resource type.
    
    Usage:
        @tier_allows_resource('agents')
        @tier_allows_resource('custom_tools')
        @tier_allows_resource('environments')
        @tier_allows_resource('users')
    
    Checks current usage (from local DB) against limits (from Cloud API with settings overrides).
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Load tier data if not already loaded
            tier_data = get_cached_tier_data()
            
            if not tier_data:
                logger.warning("Could not load tier data")
                return jsonify({
                    'status': 'error',
                    'message': 'Unable to verify subscription limits'
                }), 503
            
            # Get limits from merged tier_features (settings already applied)
            tier_features = tier_data.get('tier_features', {})
            
            # Get current usage from local database
            current_usage = tier_data.get('current_usage', {})
            
            # Map resource type to limit key
            limit_key = f"max_{resource_type}"
            usage_key = resource_type
            
            max_allowed = tier_features.get(limit_key)
            usage_count = current_usage.get(usage_key, 0)
            
            # Check if unlimited (-1 or "Unlimited")
            if max_allowed == -1 or max_allowed == "Unlimited":
                return f(*args, **kwargs)
            
            # Check if at or over limit
            if usage_count >= max_allowed:
                return jsonify({
                    'status': 'error',
                    'message': f'Resource limit reached: You have {usage_count}/{max_allowed} {resource_type}',
                    'resource_type': resource_type,
                    'current_usage': usage_count,
                    'max_allowed': max_allowed,
                    'upgrade_required': True
                }), 403
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def require_tier(minimum_tier):
    """
    OPTION 3: Tier level decorator
    Require a minimum subscription tier level.
    
    Usage:
        @require_tier('professional')  # Requires professional or higher
        @require_tier('enterprise')    # Requires enterprise tier
    
    Tier hierarchy: free < starter < professional < enterprise
    """
    tier_hierarchy = {
        'free': 0,
        'developer': 0,  # Alias for free
        'basic': 1,
        'trial': 2,      # Same level as professional
        'professional': 2,
        'enterprise': 3
    }
    
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            tier_data = get_cached_tier_data()
            
            if not tier_data:
                logger.warning("Could not load tier data")
                return jsonify({
                    'status': 'error',
                    'message': 'Subscription verification failed'
                }), 503
            
            subscription = tier_data.get('subscription', {})
            current_tier = subscription.get('current_tier', 'free')
            
            current_tier_level = tier_hierarchy.get(current_tier.lower(), 0)
            required_tier_level = tier_hierarchy.get(minimum_tier.lower(), 0)
            
            if current_tier_level < required_tier_level:
                return jsonify({
                    'status': 'error',
                    'message': f'This feature requires a {minimum_tier} subscription or higher',
                    'current_tier': current_tier,
                    'required_tier': minimum_tier,
                    'upgrade_required': True
                }), 403
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def check_usage_limits(resource_checks=None):
    """
    OPTION 4: Multi-resource decorator
    Check multiple resource limits and features at once.
    
    Usage:
        @check_usage_limits({
            'features': ['environments', 'workflows'],
            'resources': ['agents', 'custom_tools']
        })
    
    Checks against merged tier_features (with settings overrides applied).
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not resource_checks:
                return f(*args, **kwargs)
            
            # Load tier data if not already loaded
            tier_data = get_cached_tier_data()
            
            if not tier_data:
                logger.warning("Could not load tier data")
                return jsonify({
                    'status': 'error',
                    'message': 'Unable to verify subscription'
                }), 503
            
            # Use merged tier_features (settings already applied)
            tier_features = tier_data.get('tier_features', {})
            current_usage = tier_data.get('current_usage', {})
            
            # Check features
            if 'features' in resource_checks:
                for feature in resource_checks['features']:
                    feature_key = f"{feature}_enabled"
                    if not tier_features.get(feature_key, False):
                        return jsonify({
                            'status': 'error',
                            'message': f'Your subscription does not include {feature}',
                            'feature_required': feature,
                            'upgrade_required': True
                        }), 403
            
            # Check resource limits
            if 'resources' in resource_checks:
                for resource in resource_checks['resources']:
                    limit_key = f"max_{resource}"
                    max_allowed = tier_features.get(limit_key)
                    usage_count = current_usage.get(resource, 0)
                    
                    # Skip if unlimited
                    if max_allowed == -1 or max_allowed == "Unlimited":
                        continue
                    
                    if usage_count >= max_allowed:
                        return jsonify({
                            'status': 'error',
                            'message': f'Limit reached for {resource}: {usage_count}/{max_allowed}',
                            'resource_type': resource,
                            'current_usage': usage_count,
                            'max_allowed': max_allowed,
                            'upgrade_required': True
                        }), 403
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# ============================================================================
# ROUTES
# ============================================================================

# ============================================================================
# CLOUD + LOCAL USAGE API ROUTE
# ============================================================================

@admin_tier_bp.route('/api/subscription-info', methods=['GET'])
@admin_required
def get_subscription_info_from_cloud():
    """
    Get comprehensive subscription information from Cloud API.
    This route proxies the request to the Cloud API using the AI_HUB_API_URL.
    
    The returned tier_features are already merged with settings overrides.
    
    Query params:
        force_refresh: If 'true', bypass cache and fetch fresh data
    
    Returns:
        JSON with complete subscription details including:
        - Tenant information
        - Subscription tier details
        - Feature flags and limits (merged: settings override tier_features)
        - Billing information
        - Settings and overrides
        - Current usage (from local database)
    """
    try:
        # Check if force refresh requested
        force_refresh = request.args.get('force_refresh', 'false').lower() == 'true'
        
        if force_refresh:
            invalidate_tier_cache()
        
        tenant_id = os.getenv('API_KEY')
        api_url = os.getenv('AI_HUB_API_URL', '').rstrip('/')
        
        if not api_url:
            logger.error("AI_HUB_API_URL environment variable not set")
            return jsonify({
                'status': 'error',
                'message': 'Cloud API URL not configured'
            }), 500
        
        if not tenant_id:
            logger.error("API_KEY (license_key) not found in environment")
            return jsonify({
                'status': 'error',
                'message': 'License key not configured'
            }), 500
        
        # Build the full API endpoint URL
        endpoint_url = f"{api_url}/api/tenant/subscription-info"
        
        logger.info(f"Fetching subscription info from Cloud API: {endpoint_url}")
        
        # Make request to Cloud API for limits and features
        response = requests.get(
            endpoint_url,
            headers={'X-License-Key': tenant_id},
            timeout=int(cfg.CLOUD_API_REQUESTS_TIMEOUT)
        )
        
        # Check response status
        if response.status_code == 200:
            data = response.json()
            
            # Merge settings with tier_features (settings take precedence)
            if data.get('success'):
                tier_features = data.get('tier_features', {})
                settings = data.get('settings', {})
                
                # Merge with settings taking precedence
                merged_features = merge_settings_with_tier_features(tier_features, settings)
                
                # Replace tier_features with merged version
                data['tier_features'] = merged_features
                data['original_tier_features'] = tier_features  # Keep original for reference
            
            # Add current usage from local database
            try:
                user_statistics, current_usage = get_agent_user_env_info()
                data['current_usage'] = current_usage
                data['user_statistics'] = user_statistics
            except Exception as usage_error:
                logger.warning(f"Could not fetch local usage data: {usage_error}")
                data['current_usage'] = {}
                data['user_statistics'] = {}
            
            # Add cache status info
            data['cache_status'] = get_tier_cache_status()
            
            return jsonify(data), 200
            
        elif response.status_code == 404:
            return jsonify({
                'status': 'error',
                'message': 'Tenant not found with provided license key'
            }), 404
            
        elif response.status_code == 401:
            return jsonify({
                'status': 'error',
                'message': 'Invalid license key'
            }), 401
            
        else:
            logger.error(f"Cloud API returned status {response.status_code}: {response.text}")
            return jsonify({
                'status': 'error',
                'message': f'Cloud API error: {response.status_code}'
            }), response.status_code
            
    except requests.exceptions.Timeout:
        logger.error("Timeout connecting to Cloud API")
        return jsonify({
            'status': 'error',
            'message': 'Request to Cloud API timed out'
        }), 504
        
    except requests.exceptions.ConnectionError as conn_error:
        logger.error(f"Connection error to Cloud API: {conn_error}")
        return jsonify({
            'status': 'error',
            'message': 'Could not connect to Cloud API'
        }), 503
        
    except Exception as e:
        logger.error(f"Error fetching subscription info from Cloud API: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@admin_tier_bp.route('/api/cache-status', methods=['GET'])
@admin_required
def get_cache_status():
    """Get current tier cache status for debugging/monitoring"""
    return jsonify({
        'status': 'success',
        'cache': get_tier_cache_status()
    })


@admin_tier_bp.route('/api/cache-invalidate', methods=['POST'])
@admin_required
def invalidate_cache():
    """Manually invalidate tier cache (admin only)"""
    invalidate_tier_cache()
    return jsonify({
        'status': 'success',
        'message': 'Tier cache invalidated'
    })


@admin_tier_bp.route('/api/stats', methods=['GET'])
@admin_required
def get_tier_stats():
    """Get tier information and current usage"""
    try:
        # MUST POINT TO CLOUD DB FOR SUBSCRIPTION INFO
        tenant_id = os.getenv('API_KEY')
        conn = pyodbc.connect(get_cloud_db_connection_string())
        cursor = conn.cursor()
        
        # Set tenant context
        cursor.execute("EXEC tenant.sp_setTenantContext ?", tenant_id)
        
        # Get current subscription and tier
        cursor.execute("""
            SELECT 
                t.tier_name,
                t.display_name,
                t.max_environments,
                t.max_agents,
                t.max_custom_tools,
                t.max_users,
				t.max_requests,
                t.environments_enabled,
                t.workflows_enabled,
                t.documents_enabled,
				t.enterprise_features_enabled,
                t.monthly_price,
                s.status,
                s.start_date,
                s.end_date,
                s.next_billing_date
            FROM TenantSubscriptions s
            INNER JOIN SubscriptionTiers t ON s.tier_id = t.id
            WHERE s.status = 'active'
            ORDER BY s.start_date DESC
        """)
        
        subscription = cursor.fetchone()
        
        if subscription:
            # Generate license key
            license_key = tenant_id
            
            tier_info = {
                'tier_name': subscription.tier_name,
                'display_name': subscription.display_name,
                'license_key': license_key,
                'status': subscription.status,
                'next_billing': subscription.next_billing_date.strftime('%Y-%m-%d') if subscription.next_billing_date else None,
                'monthly_price': float(subscription.monthly_price) if subscription.monthly_price else 0
            }
            
            # Base limits from tier
            limits = {
                'environments': subscription.max_environments,
                'agents': subscription.max_agents,
                'custom_tools': subscription.max_custom_tools,
                'users': subscription.max_users if hasattr(subscription, 'max_users') else 1,
                'requests': subscription.max_requests if hasattr(subscription, 'max_requests') else 100
            }

            # Features
            features = {
                'environments': bool(subscription.environments_enabled),
                'workflows': bool(subscription.workflows_enabled),
                'documents': bool(subscription.documents_enabled),
                'enterprise_features': bool(subscription.enterprise_features_enabled)
            }
            
            # Check for overrides in TenantSettings
            cursor.execute("""
                SELECT setting_key, setting_value, setting_type
                FROM TenantSettings
                WHERE setting_key IN ('max_environments', 'max_agents', 'max_custom_tools', 'max_users', 'documents_enabled', 'workflows_enabled', 'environments_enabled', 'max_requests', 'enterprise_features_enabled')
            """)

            overrides_applied = {
                'limits': [],
                'features': []
            }
            
            for row in cursor.fetchall():
                key = row.setting_key
                value = row.setting_value

                # Type conversion
                if row.setting_type == 'boolean':
                    value = value.lower() in ('true', '1', 'yes')
                elif row.setting_type == 'integer':
                    value = int(value)
                elif row.setting_type == 'float' or row.setting_type == 'decimal':
                    value = float(value)
                    
                # Apply overrides to appropriate dictionary
                if key.startswith('max_'):
                    # It's a limit override
                    limit_key = key.replace('max_', '')
                    if limit_key in limits:
                        old_value = limits[limit_key]
                        limits[limit_key] = value
                        overrides_applied['limits'].append(f"{key}: {old_value} -> {value}")
                        
                elif key.endswith('_enabled'):
                    # It's a feature override
                    feature_key = key.replace('_enabled', '')
                    if feature_key in features:
                        old_value = features[feature_key]
                        features[feature_key] = value
                        overrides_applied['features'].append(f"{key}: {old_value} -> {value}")

            # Log overrides if any were applied (for debugging)
            if overrides_applied['limits'] or overrides_applied['features']:
                print(f"TenantSettings overrides applied for tenant {tenant_id}:")
                if overrides_applied['limits']:
                    print(f"  Limits: {', '.join(overrides_applied['limits'])}")
                if overrides_applied['features']:
                    print(f"  Features: {', '.join(overrides_applied['features'])}")
        else:
            # No subscription - free tier defaults
            tier_info = {
                'tier_name': 'free',
                'display_name': 'Free Tier',
                'license_key': f"{tenant_id}",
                'status': 'none',
                'next_billing': None,
                'monthly_price': 0
            }
            limits = {
                'environments': 0,
                'agents': 3,
                'custom_tools': 2,
                'users': 1,
                'requests': 100
            }
            features = {
                'environments': False,
                'workflows': False,
                'documents': False,
                'enterprise_features': False
            }

        cursor.close()
        conn.close()

        # Point to normal and potentially local db
        user_statistics, current_usage = get_agent_user_env_info()
        
        return jsonify({
            'status': 'success',
            'tier': tier_info,
            'limits': limits,
            'usage': current_usage,
            'features': features,
            'user_statistics': user_statistics
        })
        
    except Exception as e:
        logger.error(f"Error getting tier stats: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@admin_tier_bp.route('/api/users/list', methods=['GET'])
@admin_required
def get_users_list():
    """Get list of all users"""
    try:
        tenant_id = os.getenv('API_KEY')
        conn = pyodbc.connect(get_db_connection_string())
        cursor = conn.cursor()
        
        cursor.execute("EXEC tenant.sp_setTenantContext ?", tenant_id)
        
        cursor.execute("""
            SELECT 
                id,
                user_name,
                name,
                email,
                phone,
                role
            FROM [User]
            ORDER BY id DESC
        """)
        
        users = []
        for row in cursor.fetchall():
            users.append({
                'id': row.id,
                'email': row.email or 'N/A',
                'username': row.user_name,
                'full_name': row.name,
                'phone': row.phone or 'N/A',
                'role': ['', 'User', 'Developer', 'Admin'][row.role] if row.role <= 3 else 'Unknown',
                'role_value': row.role
            })
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'users': users
        })
        
    except Exception as e:
        logger.error(f"Error getting users list: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ============================================================================
# FEATURE FLAGS — Admin API
# ============================================================================

@admin_tier_bp.route('/api/feature-flags', methods=['GET'])
@admin_required
def get_feature_flags():
    """Get all feature flags with two-tier resolution details."""
    try:
        from feature_flags import get_effective_flags
        flags = get_effective_flags()
        return jsonify({'status': 'success', 'flags': flags})
    except Exception as e:
        logger.error(f"Error getting feature flags: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_tier_bp.route('/api/feature-flags', methods=['PUT'])
@admin_required
def update_feature_flags():
    """Update local feature flags (admin-only). Cloud flags cannot be changed here."""
    try:
        from feature_flags import set_local_flags, get_effective_flags
        data = request.get_json()
        if not data:
            return jsonify({'status': 'error', 'message': 'Missing request body'}), 400

        # Accept either {"flags": {...}} or direct {"key": value} format
        flags_to_set = data.get('flags', data) if 'flags' in data else data
        set_local_flags(flags_to_set)
        # Return the resolved effective state after update
        flags = get_effective_flags()
        return jsonify({'status': 'success', 'flags': flags})
    except Exception as e:
        logger.error(f"Error updating feature flags: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
