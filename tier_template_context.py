"""
Tier-based Template Context Processor (Correct Version)

This version uses ONLY data from the Cloud API (/api/subscription-info).
No hardcoded tier features or limits.

Key Points:
1. All tier data comes from get_cached_tier_data() which calls Cloud API
2. The API returns tier_features already merged with settings overrides
3. enterprise_features_enabled controls workflow_scheduling and other enterprise features
4. nlq_agents_enabled is hardcoded to True (not restricted yet)
"""

from flask import g
import logging
import os

logger = logging.getLogger(__name__)


def get_tier_features_for_template():
    """
    Get tier features from Cloud API for use in templates.
    Uses the already-merged tier_features from the API (settings override defaults).
    
    Returns dict with feature flags derived from Cloud API data.
    """
    # Check if tier data is already cached in the request context
    if hasattr(g, 'tier_data'):
        tier_data = g.tier_data
    else:
        # Use cached tier data if available — avoid blocking on cloud DB calls
        # The full tier page load (with usage counts) populates the complete cache
        from admin_tier_usage import _tier_cache
        tier_data = _tier_cache.get('data')
        
        if not tier_data:
            # Cache is empty (first request after restart)
            # Try a quick fetch from Cloud API with short timeout — don't block page loads
            try:
                import requests as _req
                api_url = os.environ.get('AI_HUB_API_URL', '').rstrip('/')
                api_key = os.environ.get('API_KEY', '')
                if api_url and api_key:
                    resp = _req.get(
                        f"{api_url}/api/tenant/subscription-info",
                        headers={'X-License-Key': api_key},
                        timeout=3  # Short timeout — don't block page loads
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get('success'):
                            from admin_tier_usage import merge_settings_with_tier_features
                            tier_features = data.get('tier_features', {})
                            settings = data.get('settings', {})
                            merged = merge_settings_with_tier_features(tier_features, settings)
                            tier_data = {
                                'success': True,  # Required by get_cached_tier_data() check
                                'tier_features': merged,
                                'settings': settings,
                                'subscription': data.get('subscription', {}),
                                'tenant_info': data.get('tenant_info', {}),
                                'current_usage': {},
                                'user_statistics': {},
                                'original_tier_features': tier_features,  # Before settings override
                            }
                            _tier_cache['data'] = tier_data
                            import time
                            _tier_cache['timestamp'] = time.time()
            except Exception as e:
                logger.warning(f"Quick tier fetch failed (will use defaults): {e}")
                tier_data = None
    
    if not tier_data:
        logger.warning("Unable to load tier data from Cloud API")
        return get_default_tier_context()
    
    # Get current tier info
    subscription = tier_data.get('subscription', {})
    current_tier = subscription.get('current_tier', 'free').lower()
    
    # Get tier_features (already merged with settings overrides from Cloud API)
    tier_features = tier_data.get('tier_features', {})
    
    # Calculate menu visibility based on API features
    menu_visibility = calculate_menu_visibility(tier_features, current_tier, subscription)
    
    # Calculate trial info if applicable
    trial_info = calculate_trial_info(current_tier, subscription)
    
    return {
        'tier_features': tier_features,
        'subscription': subscription,
        'current_usage': tier_data.get('current_usage', {}),
        'user_statistics': tier_data.get('user_statistics', {}),
        'current_tier': current_tier,
        'menu_visibility': menu_visibility,
        'trial_info': trial_info
    }


def calculate_menu_visibility(tier_features, current_tier, subscription):
    """
    Calculate what menu items should be visible based on tier_features from Cloud API.
    
    Logic:
    - workflows_enabled: Controls if workflows menu appears
    - enterprise_features_enabled: Controls workflow scheduling and enterprise features
    - documents_enabled: Controls if documents menu appears
    - environments_enabled: Controls if environments menu appears
    - Free tier: Show workflows with "DEV" badge, hide scheduling
    - Trial: Show everything
    """
    
    # Get feature flags from Cloud API
    workflows_enabled = tier_features.get('workflows_enabled', False)
    documents_enabled = tier_features.get('documents_enabled', False)
    environments_enabled = tier_features.get('environments_enabled', False)
    enterprise_features_enabled = tier_features.get('enterprise_features_enabled', False)
    
    # NLQ agents - hardcoded to True for now (not restricted)
    nlq_agents_enabled = True
    
    # Determine if this is developer mode (free tier with workflows but no enterprise features)
    is_developer_mode = (current_tier == 'free' and workflows_enabled and not enterprise_features_enabled)
    
    # Multi-user support (free tier is single user)
    multi_user_enabled = (current_tier != 'free')
    
    return {
        # Core features - always shown
        'show_agents': True,
        'show_nlq_agents': nlq_agents_enabled,
        'show_custom_tools': True,
        
        # Tier-dependent features from API
        'show_environments': environments_enabled,
        'show_workflows': workflows_enabled,
        'show_workflow_scheduling': enterprise_features_enabled,  # Controlled by enterprise_features
        'show_documents': documents_enabled,
        
        # User management
        'show_user_management': multi_user_enabled,
        
        # Enterprise-only features
        'show_enterprise_features': enterprise_features_enabled,
        
        # Special modes
        'developer_mode': is_developer_mode,
        'is_trial': current_tier == 'trial'
    }


def calculate_trial_info(current_tier, subscription):
    """
    Calculate trial expiration info if on trial tier.
    """
    if current_tier != 'trial':
        return None
    
    trial_info = {'is_trial': True}
    
    # Calculate days remaining
    next_billing = subscription.get('next_billing_date')
    if next_billing:
        from datetime import datetime
        try:
            if isinstance(next_billing, str):
                next_billing_date = datetime.fromisoformat(next_billing.replace('Z', '+00:00'))
            else:
                next_billing_date = next_billing
            
            days_remaining = (next_billing_date - datetime.now()).days
            trial_info['days_remaining'] = max(0, days_remaining)  # Don't go negative
            
        except Exception as e:
            logger.error(f"Error calculating trial days: {e}")
            trial_info['days_remaining'] = None
    else:
        trial_info['days_remaining'] = None
    
    return trial_info


def get_default_tier_context():
    """
    Return safe defaults when unable to load from Cloud API.
    Assumes free tier with minimal access.
    """
    return {
        'tier_features': {
            'environments_enabled': False,
            'workflows_enabled': False,
            'documents_enabled': False,
            'enterprise_features_enabled': False,
            'max_agents': 3,
            'max_environments': 0,
            'max_custom_tools': 2,
            'max_users': 1
        },
        'subscription': {
            'current_tier': 'free',
            'tier_display_name': 'Free Tier'
        },
        'current_usage': {
            'agents': 0,
            'environments': 0,
            'custom_tools': 0,
            'users': 0
        },
        'user_statistics': {},
        'current_tier': 'free',
        'menu_visibility': {
            'show_agents': True,
            'show_nlq_agents': True,
            'show_custom_tools': True,
            'show_environments': False,
            'show_workflows': False,
            'show_workflow_scheduling': False,
            'show_documents': False,
            'show_user_management': False,
            'show_enterprise_features': False,
            'developer_mode': False,
            'is_trial': False
        },
        'trial_info': None
    }


def create_tier_context_processor():
    """
    Create a Flask context processor that injects tier information into all templates.
    
    Usage in app.py:
        from tier_template_context import create_tier_context_processor
        app.context_processor(create_tier_context_processor())
    """
    def tier_context():
        try:
            tier_info = get_tier_features_for_template()
            
            tier_features = tier_info.get('tier_features', {})
            subscription = tier_info.get('subscription', {})
            current_tier = tier_info.get('current_tier', 'free')
            menu_visibility = tier_info.get('menu_visibility', {})
            trial_info = tier_info.get('trial_info')
            
            # Build context dict
            context = {
                # === MENU VISIBILITY FLAGS ===
                # These control what appears in the navigation menu
                
                'SHOW_AGENTS': menu_visibility.get('show_agents', True),
                'SHOW_ENVIRONMENTS': menu_visibility.get('show_environments', False),
                'SHOW_WORKFLOWS': menu_visibility.get('show_workflows', False),
                'SHOW_WORKFLOW_SCHEDULING': menu_visibility.get('show_workflow_scheduling', False),
                'SHOW_DOCUMENTS': menu_visibility.get('show_documents', False),
                'SHOW_NLQ_AGENTS': menu_visibility.get('show_nlq_agents', True),
                'SHOW_CUSTOM_TOOLS': menu_visibility.get('show_custom_tools', True),
                'SHOW_USER_MANAGEMENT': menu_visibility.get('show_user_management', False),
                'SHOW_ENTERPRISE_FEATURES': menu_visibility.get('show_enterprise_features', False),
                
                # For backward compatibility with your existing code
                'SHOW_WORKFLOW_FEATURES': menu_visibility.get('show_workflows', False),
                'SHOW_DOCUMENT_FEATURES': menu_visibility.get('show_documents', False),
                'AGENT_ENVIRONMENTS_ENABLED': menu_visibility.get('show_environments', False),
                
                # === SPECIAL FLAGS ===
                'IS_DEVELOPER_MODE': menu_visibility.get('developer_mode', False),
                'IS_TRIAL': menu_visibility.get('is_trial', False),
                'TRIAL_DAYS_REMAINING': trial_info.get('days_remaining') if trial_info else None,
                'SHOW_TRIAL_BANNER': trial_info is not None,
                
                # === TIER INFORMATION ===
                'CURRENT_TIER': current_tier,
                'TIER_DISPLAY_NAME': subscription.get('tier_display_name', 'Free'),
                'TIER_FEATURES': tier_features,
                'SUBSCRIPTION_INFO': subscription,
                
                # === USAGE INFORMATION ===
                'CURRENT_USAGE': tier_info.get('current_usage', {}),
                'USER_STATISTICS': tier_info.get('user_statistics', {}),
                
                # === UI FLAGS ===
                'USE_MODERN_CHAT_UI': getattr(__import__('config'), 'USE_MODERN_CHAT_UI', True),

                # === HELPER FUNCTIONS ===
                'tier_allows': lambda feature: tier_features.get(f'{feature}_enabled', False),
                'tier_limit': lambda resource: tier_features.get(f'max_{resource}', 0),
                'is_unlimited': lambda resource: tier_features.get(f'max_{resource}', 0) == -1,
                
                # === LIMIT CHECKING ===
                'at_limit': lambda resource: (
                    tier_info.get('current_usage', {}).get(resource, 0) >= 
                    tier_features.get(f'max_{resource}', 0)
                    if tier_features.get(f'max_{resource}', 0) not in [-1, 'Unlimited']
                    else False
                ),
                'usage_percent': lambda resource: (
                    int(tier_info.get('current_usage', {}).get(resource, 0) / 
                        tier_features.get(f'max_{resource}', 1) * 100)
                    if tier_features.get(f'max_{resource}', 0) not in [-1, 'Unlimited', 0]
                    else 0
                ),
            }
            
            return context
            
        except Exception as e:
            logger.error(f"Error in tier context processor: {e}")
            import traceback
            traceback.print_exc()
            
            # Return safe defaults on error
            return {
                'SHOW_AGENTS': True,
                'SHOW_ENVIRONMENTS': False,
                'SHOW_WORKFLOWS': False,
                'SHOW_WORKFLOW_SCHEDULING': False,
                'SHOW_DOCUMENTS': False,
                'SHOW_NLQ_AGENTS': True,
                'SHOW_CUSTOM_TOOLS': True,
                'SHOW_USER_MANAGEMENT': False,
                'SHOW_ENTERPRISE_FEATURES': False,
                'SHOW_WORKFLOW_FEATURES': False,
                'SHOW_DOCUMENT_FEATURES': False,
                'AGENT_ENVIRONMENTS_ENABLED': False,
                'IS_DEVELOPER_MODE': False,
                'IS_TRIAL': False,
                'TRIAL_DAYS_REMAINING': None,
                'SHOW_TRIAL_BANNER': False,
                'CURRENT_TIER': 'free',
                'TIER_DISPLAY_NAME': 'Free Tier',
                'TIER_FEATURES': {},
                'SUBSCRIPTION_INFO': {},
                'CURRENT_USAGE': {},
                'USER_STATISTICS': {},
                'USE_MODERN_CHAT_UI': getattr(__import__('config'), 'USE_MODERN_CHAT_UI', True),
                'tier_allows': lambda feature: False,
                'tier_limit': lambda resource: 0,
                'is_unlimited': lambda resource: False,
                'at_limit': lambda resource: False,
                'usage_percent': lambda resource: 0,
            }
    
    return tier_context
