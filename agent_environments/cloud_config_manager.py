"""
Cloud Configuration Manager
Handles fetching configuration from cloud database
"""

import pyodbc
import logging
from datetime import datetime, timedelta
from typing import Dict
from CommonUtils import get_cloud_db_connection_string

class CloudConfigManager:
    """Manages configuration from cloud database"""
    
    def __init__(self, tenant_id: int):
        self.connection_string = get_cloud_db_connection_string()
        self.tenant_id = tenant_id
        self.cache = {}
        self.cache_timestamp = None
        self.cache_ttl = timedelta(minutes=5)  # Cache for 5 minutes
        self.logger = logging.getLogger(__name__)
        
    def get_tenant_settings(self, force_refresh: bool = False) -> Dict:
        """Get all settings for tenant from cloud database"""
        
        # Check cache
        if not force_refresh and self._is_cache_valid():
            return self.cache
            
        try:
            conn = pyodbc.connect(self.connection_string)
            cursor = conn.cursor()
            
            # Set tenant context
            cursor.execute("EXEC tenant.sp_setTenantContext ?", self.tenant_id)
            
            # Get subscription info
            cursor.execute("""
                SELECT 
                    t.tier_name,
                    t.display_name,
                    t.max_environments,
                    t.max_agents,
                    t.max_custom_tools,
                    t.environments_enabled,
                    t.workflows_enabled,
                    t.documents_enabled,
                    s.status as subscription_status
                FROM TenantSubscriptions s
                INNER JOIN SubscriptionTiers t ON s.tier_id = t.id
                WHERE s.status = 'active'
                ORDER BY s.start_date DESC
            """)
            
            subscription = cursor.fetchone()
            
            if subscription:
                settings = {
                    'tier': subscription.tier_name,
                    'tier_display': subscription.display_name,
                    'max_environments': subscription.max_environments,
                    'max_agents': subscription.max_agents,
                    'max_custom_tools': subscription.max_custom_tools,
                    'environments_enabled': bool(subscription.environments_enabled),
                    'workflows_enabled': bool(subscription.workflows_enabled),
                    'documents_enabled': bool(subscription.documents_enabled),
                    'subscription_status': subscription.subscription_status,
                    'max_packages_per_env': 50,  # Default, can override in TenantSettings
                    'max_env_size_mb': 500  # Default
                }
            else:
                # Default to free tier if no subscription
                settings = {
                    'tier': 'free',
                    'tier_display': 'Free',
                    'max_environments': 1,
                    'max_agents': 1,
                    'max_custom_tools': 1,
                    'environments_enabled': False,
                    'workflows_enabled': False,
                    'documents_enabled': False,
                    'subscription_status': 'none',
                    'max_packages_per_env': 100,
                    'max_env_size_mb': 10
                }
            
            # Get any custom settings that might override defaults
            cursor.execute("""
                SELECT setting_key, setting_value, setting_type
                FROM TenantSettings
            """)
            
            for row in cursor.fetchall():
                key = row.setting_key
                value = row.setting_value
                
                # Type conversion
                if row.setting_type == 'boolean':
                    value = value.lower() in ('true', '1', 'yes')
                elif row.setting_type == 'integer':
                    value = int(value)
                    
                settings[key] = value
            
            # Update cache
            self.cache = settings
            self.cache_timestamp = datetime.now()
            
            return settings
            
        except Exception as e:
            self.logger.error(f"Error fetching tenant settings: {e}")
            # Return cached version if available, otherwise defaults
            return self.cache if self.cache else self._get_default_settings()
        finally:
            if 'conn' in locals():
                conn.close()
    
    def _is_cache_valid(self) -> bool:
        """Check if cache is still valid"""
        if not self.cache_timestamp:
            return False
        return datetime.now() - self.cache_timestamp < self.cache_ttl
    
    def _get_default_settings(self) -> Dict:
        """Get default free tier settings"""
        return {
            'tier': 'free',
            'tier_display': 'Free',
            'max_environments': 0,
            'max_agents': 3,
            'max_custom_tools': 2,
            'environments_enabled': False,
            'workflows_enabled': False,
            'documents_enabled': False,
            'subscription_status': 'none',
            'max_packages_per_env': 0,
            'max_env_size_mb': 0
        }
